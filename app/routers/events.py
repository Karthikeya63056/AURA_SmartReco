import logging
import time
from typing import Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.schemas.event import EventBatchRequest, ALLOWED_EVENT_TYPES
from app.models.event import Event
from app.models.user import User
from app.core.cache import cache
from app.core import event_buffer  # Rev5: async event buffer
from app.services import dispatcher  # Rev5 Phase 3: agent dispatcher
from app.services.trigger_engine import evaluate_5gate_trigger
from app.dependencies import get_current_user_optional, get_anonymous_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/events", tags=["Events"])

# In-memory rate limiting map: rate_key -> list of timestamps
_rate_limit_map: Dict[str, List[float]] = {}
RATE_LIMIT_WINDOW_SECONDS = 60
MAX_EVENTS_PER_WINDOW = 100
MAX_RATE_LIMIT_KEYS = 10_000  # Hard cap to prevent unbounded memory growth


def _check_rate_limit(rate_key: str, event_count: int = 1) -> bool:
    now = time.time()
    event_count = max(1, int(event_count))

    if len(_rate_limit_map) >= MAX_RATE_LIMIT_KEYS and rate_key not in _rate_limit_map:
        try:
            oldest_key = next(iter(_rate_limit_map))
            del _rate_limit_map[oldest_key]
        except StopIteration:
            pass

    timestamps = _rate_limit_map.get(rate_key, [])
    timestamps = [ts for ts in timestamps if now - ts <= RATE_LIMIT_WINDOW_SECONDS]

    if len(timestamps) + event_count > MAX_EVENTS_PER_WINDOW:
        _rate_limit_map[rate_key] = timestamps
        return False

    timestamps.extend([now] * event_count)
    _rate_limit_map[rate_key] = timestamps
    return True


@router.post("/batch", status_code=status.HTTP_202_ACCEPTED)
def ingest_event_batch(
    payload: EventBatchRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_optional)
):
    """
    Ingest a batch of user interaction events.
    Returns 202 immediately, pushes to async queue.
    Then evaluates the 5-gate trigger and submits enqueued runs to the dispatcher.
    """
    if not payload.events:
        return {"status": "queued", "accepted": 0}

    session_id = payload.events[0].session_id if payload.events else "default_session"
    user = current_user or get_anonymous_user(db, session_id=session_id)
    user_id = user.id

    if current_user:
        rate_key = f"user_{user_id}"
    else:
        client_ip = request.client.host if request.client else "unknown"
        rate_key = f"ip_{client_ip}"

    if not _check_rate_limit(rate_key, len(payload.events)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Event rate limit exceeded. Max 100 events per minute."
        )

    accepted = 0
    for item in payload.events:
        if item.event_type not in ALLOWED_EVENT_TYPES:
            logger.warning(f"Rejected disallowed event_type: {item.event_type}")
            continue
        if event_buffer.push({
            "user_id": user_id,
            "session_id": item.session_id,
            "event_type": item.event_type,
            "payload_json": item.payload_json,
            "idempotency_key": item.idempotency_key,
            "created_at": item.created_at,
        }):
            accepted += 1

    # Rev5 Phase 3: evaluate 5-gate trigger and submit enqueued runs (non-fatal)
    try:
        trigger_result = evaluate_5gate_trigger(db, user_id, session_id)
        if trigger_result.get("should_enqueue") and trigger_result.get("run_id"):
            dispatcher.submit_run_sync(trigger_result["run_id"])
    except Exception as e:
        logger.warning(f"[events] trigger evaluation failed (non-fatal): {e}")

    return {
        "status": "queued",
        "accepted": accepted
    }