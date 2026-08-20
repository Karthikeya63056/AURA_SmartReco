"""
Bounded async event buffer with conflict-tolerant bulk flush.
C5-compliant: every threaded fn opens its own session.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any

from sqlalchemy import insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.database import SessionLocal
from app.models.event import Event

logger = logging.getLogger(__name__)

QUEUE_MAX = 10_000
FLUSH_INTERVAL = 2.0
FLUSH_CAPACITY = 500

_event_queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
dropped_events = 0


def push(event_data: Dict[str, Any]) -> bool:
    """
    Non-blocking push to event queue.
    
    Args:
        event_data: Dict with keys: user_id, session_id, event_type, 
                    payload_json, idempotency_key (optional), created_at (optional)
    
    Returns:
        True if accepted, False if dropped (queue full)
    """
    global dropped_events
    try:
        _event_queue.put_nowait(event_data)
        return True
    except asyncio.QueueFull:
        dropped_events += 1
        logger.warning(f"[EventBuffer] queue full, dropped event #{dropped_events}")
        return False


def drain(max_size: int = FLUSH_CAPACITY) -> List[Dict[str, Any]]:
    """
    Drain up to max_size events from the queue.
    
    Args:
        max_size: Maximum number of events to drain
    
    Returns:
        List of event data dicts
    """
    rows = []
    while len(rows) < max_size:
        try:
            rows.append(_event_queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return rows


def bulk_insert_events(rows: List[Dict[str, Any]]) -> int:
    """
    Bulk insert events with conflict-tolerant semantics.
    Opens its own session (C5: thread-bound).
    
    Args:
        rows: List of event data dicts
    
    Returns:
        Number of events actually ingested (from rowcount)
    """
    if not rows:
        return 0

    values = []
    for r in rows:
        values.append({
            "user_id": r["user_id"],
            "session_id": r["session_id"],
            "event_type": r["event_type"],
            "payload_json": r["payload_json"],
            "idempotency_key": r.get("idempotency_key") or str(uuid.uuid4()),
            "created_at": r.get("created_at") or datetime.now(timezone.utc),
        })

    with SessionLocal() as session:
        # Use SQLite-specific ON CONFLICT DO NOTHING
        stmt = sqlite_insert(Event).values(values).on_conflict_do_nothing(
            index_elements=["idempotency_key"]
        )
        result = session.execute(stmt)
        session.commit()
        ingested = result.rowcount
        if ingested < len(values):
            logger.info(
                f"[EventBuffer] bulk insert: {ingested}/{len(values)} "
                f"(skipped {len(values)-ingested} dupes)"
            )
        return ingested


async def flush_loop():
    """
    Long-lived consumer on the app loop.
    Drains queue every FLUSH_INTERVAL seconds and bulk-inserts via to_thread.
    """
    logger.info("[EventBuffer] flush loop started")
    while True:
        rows = drain(FLUSH_CAPACITY)
        if rows:
            try:
                ingested = await asyncio.to_thread(bulk_insert_events, rows)
                logger.debug(f"[EventBuffer] flushed {ingested} events")
            except Exception as e:
                logger.error(f"[EventBuffer] flush failed: {e}", exc_info=True)
        await asyncio.sleep(FLUSH_INTERVAL)