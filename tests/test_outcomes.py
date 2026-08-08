import pytest
from datetime import datetime, timedelta
from app.models.event import Event
from app.models.recommendation import Recommendation
from app.models.user import User
from app.services.trigger_engine import TriggerEngine
from app.routers.admin import _compute_recommendation_outcomes


def test_rec_click_and_dismiss_event_ingestion(client, db_session):
    """Test that rec_click and rec_dismiss event types pass batch ingestion validation."""
    payload = {
        "events": [
            {
                "session_id": "test_outcome_session",
                "event_type": "rec_click",
                "payload_json": {"recommendation_id": 1, "product_id": 5},
                "idempotency_key": "key_rec_click_1"
            },
            {
                "session_id": "test_outcome_session",
                "event_type": "rec_dismiss",
                "payload_json": {"recommendation_id": 1},
                "idempotency_key": "key_rec_dismiss_1"
            }
        ]
    }
    response = client.post("/api/events/batch", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "success"
    assert data["ingested"] == 2


def test_compute_recommendation_outcomes(db_session):
    """Test outcome metrics calculation (total recs, clicks, dismisses, CTR)."""
    # Create test user
    user = User(email="outcome_user@example.com", hashed_password="pw", is_admin=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    # Create test recommendation
    rec = Recommendation(
        user_id=user.id,
        narrative="Test recommendation narrative for CTR tracking",
        product_ids_json=[1, 2],
        quality_score=85,
        trigger_reason="high_intent",
        is_active=True,
        created_at=datetime.utcnow() - timedelta(minutes=30)
    )
    db_session.add(rec)
    db_session.commit()
    db_session.refresh(rec)

    # Add 2 click events and 1 dismiss event
    e1 = Event(
        user_id=user.id,
        session_id="sess_1",
        event_type="rec_click",
        payload_json={"recommendation_id": rec.id, "product_id": 1},
        created_at=datetime.utcnow() - timedelta(minutes=20)
    )
    e2 = Event(
        user_id=user.id,
        session_id="sess_1",
        event_type="rec_click",
        payload_json={"recommendation_id": rec.id, "product_id": 2},
        created_at=datetime.utcnow() - timedelta(minutes=15)
    )
    e3 = Event(
        user_id=user.id,
        session_id="sess_1",
        event_type="rec_dismiss",
        payload_json={"recommendation_id": rec.id},
        created_at=datetime.utcnow() - timedelta(minutes=10)
    )
    db_session.add_all([e1, e2, e3])
    db_session.commit()

    outcomes = _compute_recommendation_outcomes(db_session)
    assert outcomes["total_recs"] >= 1
    assert outcomes["total_clicks"] >= 2
    assert outcomes["total_dismisses"] >= 1

    matching_rec = next((r for r in outcomes["rec_metrics"] if r["id"] == rec.id), None)
    assert matching_rec is not None
    assert matching_rec["clicks"] == 2
    assert matching_rec["dismisses"] == 1
    assert matching_rec["ctr"] == 66.7  # 2 clicks out of 3 interactions


def test_ignored_recommendations_trigger(db_session):
    """Test trigger engine fires when user ignores last 2 recommendations (dismissed without clicks)."""
    user = User(email="ignored_user@example.com", hashed_password="pw")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    now = datetime.utcnow()
    # Create 2 recommendations for user (older than 10m cooldown)
    r1 = Recommendation(
        user_id=user.id,
        narrative="Rec 1",
        product_ids_json=[1],
        quality_score=75,
        trigger_reason="staleness",
        is_active=False,
        created_at=now - timedelta(minutes=25)
    )
    r2 = Recommendation(
        user_id=user.id,
        narrative="Rec 2",
        product_ids_json=[2],
        quality_score=75,
        trigger_reason="staleness",
        is_active=True,
        created_at=now - timedelta(minutes=12)
    )
    db_session.add_all([r1, r2])
    db_session.commit()
    db_session.refresh(r1)
    db_session.refresh(r2)

    # Add 3 total events for user so cold-start doesn't trigger
    for i in range(3):
        db_session.add(Event(
            user_id=user.id,
            session_id="sess_ignored",
            event_type="page_view",
            payload_json={"path": "/"},
            created_at=now - timedelta(minutes=50)
        ))

    # Add dismiss event for r2, zero clicks
    db_session.add(Event(
        user_id=user.id,
        session_id="sess_ignored",
        event_type="rec_dismiss",
        payload_json={"recommendation_id": r2.id},
        created_at=now - timedelta(minutes=5)
    ))
    db_session.commit()

    trigger = TriggerEngine.evaluate_trigger(db_session, user_id=user.id)
    assert trigger["should_run_agent"] is True
    assert trigger["trigger_reason"] == "ignored_recommendations"
