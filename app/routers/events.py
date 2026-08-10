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
from app.services.trigger_engine import TriggerEngine
from app.dependencies import get_current_user_optional, get_anonymous_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/events", tags=["Events"])

# In-memory rate limiting map: rate_key -> list of timestamps
_rate_limit_map: Dict[str, List[float]] = {}
RATE_LIMIT_WINDOW_SECONDS = 60
MAX_EVENTS_PER_WINDOW = 100
MAX_RATE_LIMIT_KEYS = 10_000  # Hard cap to prevent unbounded memory growth


def _check_rate_limit(rate_key: str, event_count: int = 1) -> bool:
    """
    Simple sliding-window rate limiter with a hard cap on dictionary size
    to prevent memory exhaustion from attacker-controlled keys.
    """
    now = time.time()
    event_count = max(1, int(event_count))

    # Evict oldest keys if we are at the hard limit
    if len(_rate_limit_map) >= MAX_RATE_LIMIT_KEYS and rate_key not in _rate_limit_map:
        # Remove the oldest key (first inserted)
        try:
            oldest_key = next(iter(_rate_limit_map))
            del _rate_limit_map[oldest_key]
        except StopIteration:
            pass

    timestamps = _rate_limit_map.get(rate_key, [])
    # Keep only timestamps within the window
    timestamps = [ts for ts in timestamps if now - ts <= RATE_LIMIT_WINDOW_SECONDS]

    if len(timestamps) + event_count > MAX_EVENTS_PER_WINDOW:
        _rate_limit_map[rate_key] = timestamps
        return False

    timestamps.extend([now] * event_count)
    _rate_limit_map[rate_key] = timestamps
    return True


@router.post("/batch", status_code=status.HTTP_201_CREATED)
def ingest_event_batch(
    payload: EventBatchRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_optional)
):
    """
    Ingest a batch of user interaction events.
    Enforces rate limiting, event-type allowlist, payload size limit,
    idempotency key deduplication, and evaluates Smart Triggers.
    """
    if not payload.events:
        return {"status": "success", "ingested": 0, "trigger": {"should_run_agent": False}}

    # Determine user_id: authenticated user, or a per-session anonymous account
    # keyed by the client's session_id so anonymous visitors never share one
    # global profile (cross-user recommendation contamination).
    session_id = payload.events[0].session_id if payload.events else "default_session"
    user = current_user or get_anonymous_user(db, session_id=session_id)
    user_id = user.id

    # Rate limiting key:
    # - Authenticated users → user_id (stable)
    # - Anonymous users → client IP (cannot be controlled by the client)
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

    # Batch deduplication check via idempotency_keys (eliminates N+1 DB queries)
    keys = [e.idempotency_key for e in payload.events if e.idempotency_key]
    existing_keys = set()
    if keys:
        existing_rows = db.query(Event.idempotency_key).filter(Event.idempotency_key.in_(keys)).all()
        existing_keys = {row[0] for row in existing_rows}

    ingested_count = 0
    for item in payload.events:
        # Defense-in-depth: re-check allowlist (schema already validates)
        if item.event_type not in ALLOWED_EVENT_TYPES:
            logger.warning(f"Rejected disallowed event_type: {item.event_type}")
            continue

        if item.idempotency_key:
            if item.idempotency_key in existing_keys:
                continue
            # Dedupe within this batch too: retried batches may repeat keys.
            # If we didn't add them here, the UNIQUE constraint on
            # idempotency_key would raise IntegrityError at commit time.
            existing_keys.add(item.idempotency_key)

        event_obj = Event(
            user_id=user_id,
            session_id=item.session_id,
            event_type=item.event_type,
            payload_json=item.payload_json,
            idempotency_key=item.idempotency_key,
            created_at=item.created_at
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
