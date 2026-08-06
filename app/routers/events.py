import logging
from typing import Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.schemas.event import EventBatchRequest
from app.models.event import Event
from app.models.user import User
from app.core.cache import cache
from app.services.trigger_engine import TriggerEngine
from app.dependencies import get_current_user_optional

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/events", tags=["Events"])

RATE_LIMIT_MAX = 100
RATE_LIMIT_WINDOW_SEC = 60


def _check_rate_limit(user_key: str) -> bool:
    """Check in-memory rate limit per user/session."""
    cache_key = f"rate_limit:{user_key}"
    current_count = cache.get(cache_key) or 0
    if current_count >= RATE_LIMIT_MAX:
        return False
    cache.set(cache_key, current_count + 1, ttl_seconds=RATE_LIMIT_WINDOW_SEC)
    return True


@router.post("/batch", status_code=status.HTTP_201_CREATED)
def ingest_event_batch(
    payload: EventBatchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_optional)
):
    """
    Ingest a batch of user interaction events.
    Enforces rate limiting, idempotency key deduplication, and evaluates Smart Triggers.
    """
    if not payload.events:
        return {"status": "success", "ingested": 0, "trigger": {"should_run_agent": False}}

    # Determine user_id (authenticated user or fallback to demo user ID 2)
    user_id = current_user.id if current_user else 2
    session_id = payload.events[0].session_id if payload.events else "default_session"

    # Rate limiting check
    rate_key = f"user_{user_id}" if current_user else f"sess_{session_id}"
    if not _check_rate_limit(rate_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Event rate limit exceeded. Max 100 events per minute."
        )

    ingested_count = 0
    for item in payload.events:
        # Deduplication check via idempotency_key
        if item.idempotency_key:
            existing = db.query(Event).filter(Event.idempotency_key == item.idempotency_key).first()
            if existing:
                continue

        event_obj = Event(
            user_id=user_id,
            session_id=item.session_id,
            event_type=item.event_type,
            payload_json=item.payload_json,
            idempotency_key=item.idempotency_key
        )
        db.add(event_obj)
        ingested_count += 1

    db.commit()

    # Evaluate Trigger Engine
    trigger_result = TriggerEngine.evaluate_trigger(
        db=db,
        user_id=user_id,
        current_session_id=session_id
    )

    return {
        "status": "success",
        "ingested": ingested_count,
        "trigger": trigger_result
    }
