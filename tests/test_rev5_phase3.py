"""
Phase 3 exit-gate tests (G3.1–G3.6):
- G3.1 graph has exactly 7 nodes
- G3.2 mark-and-defer coalesces multiple mid-run triggers
- G3.3 sweep reclaims lease-expired runs
- G3.4 validation catches prohibited claims + ID equality
- G3.5 LLM-off mode still produces recommendations (template fallback)
- G3.6 atomic claim: only one caller wins per queued run
"""
import time
import pytest
from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from app.core.database import SessionLocal
from app.models.user import User
from app.models.event import Event
from app.models.agent_run import AgentRun
from app.agent.graph_v2 import node_count, run_agent
from app.agent.nodes_v2 import validate, PROHIBITED_PATTERNS
from app.services.trigger_engine import enqueue_run
from app.services.dispatcher import claim_run
from app.jobs.stale_run_sweep import sweep_stale_runs


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# ============================================================
# G3.1: Graph structure
# ============================================================

def test_g31_graph_has_7_nodes():
    """G3.1: len(nodes) == 7 (no phantom nodes)."""
    assert node_count() == 7, f"Expected 7 nodes, got {node_count()}"


# ============================================================
# G3.2: Mark-and-defer coalesces mid-run triggers
# ============================================================

def test_g32_mark_and_defer_coalesces(db):
    """
    G3.2: A run in flight, followed by 2 mid-run triggers with different hashes,
    coalesces into ONE pending_profile_hash=C. Max in-flight per user == 1.
    """
    # Create user
    user = User(email=f"coalesce_{int(time.time())}@test.com", hashed_password="pw")
    db.add(user)
    db.commit()
    db.refresh(user)

    # Enqueue and claim first run (simulates dispatcher)
    r1 = enqueue_run(db, user.id, "hash_A", "manual")
    assert r1["status"] == "enqueued"
    run_id = r1["run_id"]

    # Dispatcher claims it -> status=running
    db.execute(
        update(AgentRun)
        .where(AgentRun.id == run_id)
        .values(status="running")
    )
    db.commit()

    # Mid-run trigger with hash_B: should mark for refresh, NOT create new run
    r2 = enqueue_run(db, user.id, "hash_B", "behavior_changed")
    assert r2["status"] == "refresh_requested"

    # Mid-run trigger with hash_C: should UPDATE (not INSERT) the same slot
    r3 = enqueue_run(db, user.id, "hash_C", "behavior_changed")
    assert r3["status"] == "refresh_requested"

    # Verify: still only ONE queued/running run for this user
    active_count = (
        db.query(AgentRun)
        .filter(
            AgentRun.user_id == user.id,
            AgentRun.status.in_(["queued", "running"]),
        )
        .count()
    )
    assert active_count == 1, f"Expected exactly 1 active run, got {active_count}"

    # Verify the pending_profile_hash coalesced to the LATEST hash
    db.refresh(db.get(AgentRun, run_id))
    active_run = db.get(AgentRun, run_id)
    assert active_run.pending_profile_hash == "hash_C"
    assert active_run.refresh_requested is True


# ============================================================
# G3.3: Sweep reclaims lease-expired runs
# ============================================================

def test_g33_sweep_reclaims_stale_runs(db):
    """G3.3: sweep marks running+lease-expired as failed, freeing the slot."""
    user = User(email=f"sweep_{int(time.time())}@test.com", hashed_password="pw")
    db.add(user)
    db.commit()
    db.refresh(user)

    # Create a running run with lease expired 1 minute ago
    expired_lease = datetime.now(timezone.utc) - timedelta(minutes=1)
    stale_run = AgentRun(
        user_id=user.id,
        profile_hash="stale_hash",
        status="running",
        lease_until=expired_lease,
    )
    db.add(stale_run)
    db.commit()
    db.refresh(stale_run)
    stale_id = stale_run.id

    # Sweep should reclaim it
    reclaimed = sweep_stale_runs()
    assert reclaimed >= 1

    # Verify status changed to failed
    db.expire_all()
    updated = db.get(AgentRun, stale_id)
    assert updated.status == "failed"
    assert updated.last_error == "lease_expired"

    # Verify the slot is now free (a new enqueue should succeed)
    result = enqueue_run(db, user.id, "new_hash", "after_sweep")
    assert result["status"] == "enqueued"


# ============================================================
# G3.4: Validation (prohibited claims + ID equality)
# ============================================================

def test_g34_validation_catches_prohibited_claims():
    """G3.4: narratives with invented prices/urgency fail validation."""
    state = {
        "narrative": "Act now! Only $99 for this limited time offer!",
        "product_ids": [1, 2],
        "retry_count": 0,
    }
    result = validate(state)
    assert result["validation_passed"] is False
    assert result["retry_count"] == 1
    assert "prohibited claim" in result["critique_feedback"].lower()


def test_g34_validation_passes_clean_narrative():
    """G3.4: clean narrative passes validation."""
    state = {
        "narrative": "Based on your interest in Python, these courses cover FastAPI and ML basics.",
        "product_ids": [1, 2, 3],
        "retry_count": 0,
    }
    result = validate(state)
    assert result["validation_passed"] is True
    assert result["critique_feedback"] is None


def test_g34_validation_rejects_empty_narrative():
    """G3.4: empty narrative fails validation."""
    state = {
        "narrative": "   ",
        "product_ids": [1],
        "retry_count": 0,
    }
    result = validate(state)
    assert result["validation_passed"] is False


# ============================================================
# G3.5: LLM-off mode produces recommendations (template fallback)
# ============================================================

def test_g35_llm_off_produces_recommendation(db, monkeypatch):
    """G3.5: With LLM_ENABLED=false, the graph still produces a rec (template)."""
    # Force LLM off
    monkeypatch.setattr("app.agent.nodes_v2._llm_enabled", lambda: False)

    user = User(email=f"llm_off_{int(time.time())}@test.com", hashed_password="pw")
    db.add(user)
    db.commit()
    db.refresh(user)

    # Add some events so analyze has something to work with
    now = datetime.now(timezone.utc)
    for i in range(6):
        db.add(Event(
            user_id=user.id, session_id="sess_off",
            event_type="course_view",
            payload_json={"category": "Python"},
            created_at=now - timedelta(hours=i),
        ))
    db.commit()

    # Create a queued run
    r = enqueue_run(db, user.id, "hash_off", "manual")
    assert r["status"] == "enqueued"
    run_id = r["run_id"]

    # Claim it
    db.execute(
        update(AgentRun).where(AgentRun.id == run_id).values(status="running")
    )
    db.commit()

    # Run the graph (fully offline, no LLM calls)
    result = run_agent(run_id, user.id, "hash_off", "manual")

    # Verify the graph completed and produced a recommendation
    assert result.get("recommendation_id") is not None, \
        f"Graph should produce a recommendation offline, got: {result}"
    assert result.get("narrative"), "Narrative should not be empty"

    # Verify the run is marked done (not failed)
    db.expire_all()
    final_run = db.get(AgentRun, run_id)
    assert final_run.status == "done", f"Run status should be 'done', got {final_run.status}"
    assert final_run.degraded is True, "Offline run should be marked degraded"


# ============================================================
# G3.6: Atomic claim
# ============================================================

def test_g36_atomic_claim_only_one_winner(db):
    """
    G3.6: Only one caller wins the claim.
    
    We can't have two queued runs for the same user (C6 partial unique index),
    so we test the claim race by creating two separate users, each with one
    queued run, and verify claim_run() is idempotent for the SAME run_id.
    """
    user_a = User(email=f"claim_a_{int(time.time())}@test.com", hashed_password="pw")
    user_b = User(email=f"claim_b_{int(time.time())}@test.com", hashed_password="pw")
    db.add_all([user_a, user_b])
    db.commit()
    db.refresh(user_a)
    db.refresh(user_b)

    # Create one queued run per user (single-flight invariant satisfied)
    run_a = AgentRun(user_id=user_a.id, profile_hash="a", status="queued")
    run_b = AgentRun(user_id=user_b.id, profile_hash="b", status="queued")
    db.add_all([run_a, run_b])
    db.commit()
    db.refresh(run_a)
    db.refresh(run_b)

    # First claim on run_a should succeed
    assert claim_run(run_a.id) is True

    # Second claim on the SAME run_a should fail (status is now 'running')
    assert claim_run(run_a.id) is False

    # Claim on run_b should succeed (different run, different user)
    assert claim_run(run_b.id) is True

    # Verify final statuses
    db.expire_all()
    assert db.get(AgentRun, run_a.id).status == "running"
    assert db.get(AgentRun, run_b.id).status == "running"