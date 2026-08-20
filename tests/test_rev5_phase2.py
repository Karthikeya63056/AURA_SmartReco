"""
Phase 2 exit-gate tests:
- Signal processing (decay, profile hash stability)
- Hybrid retrieval (RRF fusion, degraded mode)
- Deterministic ranking (score formula, breakdown)
- 5-gate trigger (each gate fails independently)
- Single-flight enqueue (IntegrityError → refresh_requested)
"""
import pytest
import time
from datetime import datetime, timedelta, timezone
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal
from app.models.event import Event
from app.models.product import Product
from app.models.user import User
from app.models.agent_run import AgentRun
from app.services.signals import build_user_profile, recency_decay, compute_profile_hash
from app.services.retrieval import hybrid_retrieve, build_retrieval_query
from app.services.ranking import rank_candidates
from app.services.trigger_engine import evaluate_5gate_trigger, enqueue_run


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# ============================================================
# Signal Processing Tests
# ============================================================

def test_recency_decay_half_life():
    """G2.1: 72-hour half-life decay is correct."""
    now = datetime.now(timezone.utc)
    
    # Event 72 hours ago should decay to ~0.5
    old_event = now - timedelta(hours=72)
    decay = recency_decay(old_event, now)
    assert 0.45 <= decay <= 0.55, f"72h decay should be ~0.5, got {decay}"
    
    # Event now should decay to 1.0
    decay_now = recency_decay(now, now)
    assert decay_now == 1.0, f"Now decay should be 1.0, got {decay_now}"
    
    # Event 144 hours ago (2 half-lives) should decay to ~0.25
    very_old = now - timedelta(hours=144)
    decay_very_old = recency_decay(very_old, now)
    assert 0.20 <= decay_very_old <= 0.30, f"144h decay should be ~0.25, got {decay_very_old}"


def test_profile_hash_stability(db):
    """G2.2: Profile hash is stable for same input, changes when events change."""
    # Create test user
    user = User(email=f"hash_test_{int(time.time())}@test.com", hashed_password="pw")
    db.add(user)
    db.commit()
    db.refresh(user)
    
    # Add some events
    now = datetime.now(timezone.utc)
    db.add(Event(
        user_id=user.id, session_id="sess1", event_type="course_view",
        payload_json={"category": "Python"}, created_at=now - timedelta(hours=1)
    ))
    db.commit()
    
    # Build profile twice - should get same hash
    profile1 = build_user_profile(db, user.id, now=now)
    profile2 = build_user_profile(db, user.id, now=now)
    assert profile1["profile_hash"] == profile2["profile_hash"]
    
    # Add a new event - hash should change
    db.add(Event(
        user_id=user.id, session_id="sess1", event_type="wishlist",
        payload_json={"category": "AI"}, created_at=now
    ))
    db.commit()
    
    profile3 = build_user_profile(db, user.id, now=now)
    assert profile3["profile_hash"] != profile1["profile_hash"]


# ============================================================
# Hybrid Retrieval Tests
# ============================================================

def test_hybrid_retrieval_returns_candidates(db):
    """G2.3: Hybrid retrieval returns candidates with fused scores."""
    profile = {
        "category_scores": {"Python": 1.0},
        "skill_scores": {"Machine Learning": 0.5},
        "difficulty_preference": 2.0,
        "excluded_product_ids": [],
    }
    query = build_retrieval_query(profile)
    
    result = hybrid_retrieve(db, query, profile, k=10)
    
    assert "candidates" in result
    assert "degraded" in result
    assert isinstance(result["candidates"], list)
    
    if result["candidates"]:
        cand = result["candidates"][0]
        assert "product_id" in cand
        assert "fused_score" in cand
        assert "similarity" in cand
        assert "bm25_score" in cand


def test_hybrid_retrieval_diversity(db):
    """G2.4: Diversity constraint (max 2 per category) is enforced."""
    profile = {
        "category_scores": {"Python": 1.0, "AI": 0.8, "Data Science": 0.6},
        "skill_scores": {},
        "excluded_product_ids": [],
    }
    query = "courses"
    
    result = hybrid_retrieve(db, query, profile, k=50, max_per_category=2)
    
    # Count products per category
    category_counts = {}
    for cand in result["candidates"]:
        cat = cand["category"]
        category_counts[cat] = category_counts.get(cat, 0) + 1
    
    # Verify no category exceeds limit
    for cat, count in category_counts.items():
        assert count <= 2, f"Category {cat} has {count} items, exceeds max 2"


# ============================================================
# Deterministic Ranking Tests
# ============================================================

def test_ranking_deterministic(db):
    """G2.5: Ranking is deterministic (same input → same output)."""
    # Create a simple profile
    profile = {
        "category_scores": {"Python": 1.0},
        "skill_scores": {"FastAPI": 0.8},
        "difficulty_preference": 2.0,
    }
    
    # Retrieve candidates
    result = hybrid_retrieve(db, "Python FastAPI", profile, k=5)
    if not result["candidates"]:
        pytest.skip("No candidates retrieved")
    
    # Rank twice - should get same order
    ranked1 = rank_candidates(db, result["candidates"], profile)
    ranked2 = rank_candidates(db, result["candidates"], profile)
    
    assert len(ranked1) == len(ranked2)
    for r1, r2 in zip(ranked1, ranked2):
        assert r1["product_id"] == r2["product_id"]
        assert abs(r1["final_score"] - r2["final_score"]) < 0.001


def test_ranking_breakdown_present(db):
    """G2.6: Ranking breakdown includes all components."""
    profile = {
        "category_scores": {"AI": 1.0},
        "skill_scores": {"Neural Networks": 0.9},
        "difficulty_preference": 2.5,
    }
    
    result = hybrid_retrieve(db, "AI neural networks", profile, k=3)
    if not result["candidates"]:
        pytest.skip("No candidates retrieved")
    
    ranked = rank_candidates(db, result["candidates"], profile)
    
    if ranked:
        breakdown = ranked[0]["breakdown"]
        assert "retrieval" in breakdown
        assert "interest" in breakdown
        assert "skill_gap" in breakdown
        assert "difficulty" in breakdown
        assert "popularity" in breakdown
        
        # All values should be in [0, 1]
        for key, value in breakdown.items():
            assert 0.0 <= value <= 1.0, f"Breakdown {key}={value} out of range"


# ============================================================
# 5-Gate Trigger Tests
# ============================================================

def test_trigger_min_events_gate(db):
    """G2.7: min_events gate blocks when < 5 events."""
    user = User(email=f"min_events_{int(time.time())}@test.com", hashed_password="pw")
    db.add(user)
    db.commit()
    db.refresh(user)
    
    # Add only 3 events
    now = datetime.now(timezone.utc)
    for i in range(3):
        db.add(Event(
            user_id=user.id, session_id="sess1", event_type="course_view",
            payload_json={"category": "Python"}, created_at=now - timedelta(hours=i)
        ))
    db.commit()
    
    result = evaluate_5gate_trigger(db, user.id)
    
    assert result["should_enqueue"] is False
    assert "min_events" in result["skip_reasons"]
    assert result["gates"]["min_events"] is False


def test_trigger_cooldown_gate(db):
    """G2.8: cooldown gate blocks when < 90s since last run."""
    user = User(email=f"cooldown_{int(time.time())}@test.com", hashed_password="pw")
    db.add(user)
    db.commit()
    db.refresh(user)
    
    # Add enough events
    now = datetime.now(timezone.utc)
    for i in range(10):
        db.add(Event(
            user_id=user.id, session_id="sess1", event_type="course_view",
            payload_json={"category": "Python"}, created_at=now - timedelta(hours=i)
        ))
    db.commit()
    
    # Add a recent completed run (60s ago, < 90s cooldown)
    db.add(AgentRun(
        user_id=user.id,
        profile_hash="abc123",
        status="done",
        created_at=now - timedelta(seconds=60),
    ))
    db.commit()
    
    result = evaluate_5gate_trigger(db, user.id)
    
    assert result["should_enqueue"] is False
    assert "cooldown_elapsed" in result["skip_reasons"]
    assert result["gates"]["cooldown_elapsed"] is False


def test_trigger_budget_gate(db):
    """G2.9: budget gate blocks when daily spend >= cap."""
    from app.config import settings
    
    user = User(email=f"budget_{int(time.time())}@test.com", hashed_password="pw")
    db.add(user)
    db.commit()
    db.refresh(user)
    
    now = datetime.now(timezone.utc)
    
    # Add old completed run (2 hours ago) - this is the "last_run" for gate comparisons
    db.add(AgentRun(
        user_id=user.id,
        profile_hash="old_hash",
        status="done",
        created_at=now - timedelta(hours=2),
    ))
    
    # Add 10 events AFTER the old run (1 hour ago to now)
    # This ensures min_events gate passes (10 >= 5)
    for i in range(10):
        db.add(Event(
            user_id=user.id, session_id="sess1", event_type="course_view",
            payload_json={"category": "Python"}, created_at=now - timedelta(hours=1) + timedelta(minutes=i*5)
        ))
    
    # Add runs that exceed daily budget (status="failed" but still count toward spend)
    budget = getattr(settings, "LLM_DAILY_BUDGET_USD", 1.00)
    db.add(AgentRun(
        user_id=user.id,
        profile_hash="run1",
        status="failed",
        cost_usd=budget * 0.6,
        created_at=now - timedelta(hours=1),
    ))
    db.add(AgentRun(
        user_id=user.id,
        profile_hash="run2",
        status="failed",
        cost_usd=budget * 0.6,
        created_at=now - timedelta(minutes=30),
    ))
    db.commit()
    
    result = evaluate_5gate_trigger(db, user.id)
    
    assert result["should_enqueue"] is False
    assert "budget_exhausted" in result["skip_reasons"]
    assert result["gates"]["budget_available"] is False


# ============================================================
# Single-Flight Enqueue Tests
# ============================================================

def test_single_flight_enqueue_success(db):
    """G2.10: First enqueue succeeds with status='enqueued'."""
    user = User(email=f"enqueue_{int(time.time())}@test.com", hashed_password="pw")
    db.add(user)
    db.commit()
    db.refresh(user)
    
    result = enqueue_run(db, user.id, "hash1", "manual")
    
    assert result["status"] == "enqueued"
    assert "run_id" in result
    
    # Verify the run exists
    run = db.query(AgentRun).filter(AgentRun.id == result["run_id"]).first()
    assert run is not None
    assert run.status == "queued"
    assert run.user_id == user.id


def test_single_flight_duplicate_marks_refresh(db):
    """G2.11: Second enqueue marks active run for refresh (single-flight)."""
    user = User(email=f"refresh_{int(time.time())}@test.com", hashed_password="pw")
    db.add(user)
    db.commit()
    db.refresh(user)
    
    # First enqueue
    result1 = enqueue_run(db, user.id, "hash1", "manual")
    assert result1["status"] == "enqueued"
    run_id = result1["run_id"]
    
    # Simulate dispatcher claiming the run
    run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
    run.status = "running"
    db.commit()
    
    # Second enqueue with different profile_hash (user browsed more)
    result2 = enqueue_run(db, user.id, "hash2", "behavior_changed")
    
    assert result2["status"] == "refresh_requested"
    assert result2["run_id"] == run_id
    
    # Verify the active run was marked for refresh
    db.refresh(run)
    assert run.refresh_requested is True
    assert run.pending_profile_hash == "hash2"