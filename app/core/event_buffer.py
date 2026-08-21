"""
Bounded thread-safe event buffer with conflict-tolerant bulk flush.
C5-compliant: every threaded fn opens its own session.
"""
import asyncio
import logging
import queue
import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any

from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.database import SessionLocal
from app.models.event import Event

logger = logging.getLogger(__name__)

QUEUE_MAX = 10_000
FLUSH_INTERVAL = 2.0
FLUSH_CAPACITY = 500

_event_queue: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
dropped_events = 0


def push(event_data: Dict[str, Any]) -> bool:
    """
    Non-blocking push to event queue (thread-safe: callable from any thread).
    
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
    except queue.Full:
        dropped_events += 1
        logger.warning(f"[EventBuffer] queue full, dropped event #{dropped_events}")
        return False


def push_batch(rows: List[Dict[str, Any]]) -> int:
    """
    Best-effort push of many events; on overflow the oldest queued rows are
    evicted to make room (no silent loss of fresh data). 
    
    Args:
        rows: List of event data dicts
    
    Returns:
        Number of rows actually queued
    """
    pushed = 0
    for row in rows:
        if _push_evicting_oldest(row):
            pushed += 1
    return pushed


def _push_evicting_oldest(event_data: Dict[str, Any]) -> bool:
    global dropped_events
    try:
        _event_queue.put_nowait(event_data)
        return True
    except queue.Full:
        try:
            _event_queue.get_nowait()  # drop oldest to make room
        except queue.Empty:
            return False
        dropped_events += 1
        try:
            _event_queue.put_nowait(event_data)
            return True
        except queue.Full:
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
        except queue.Empty:
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


def flush_pending_sync(max_rows: int = FLUSH_CAPACITY) -> int:
    """
    Blocking flush: drain up to max_rows and bulk-insert immediately.
    
    Safe to call from a sync threadpool thread (opens its own session).
    Used by the events endpoint so freshly pushed batches are visible to
    trigger evaluation without waiting FLUSH_INTERVAL.
    
    On failure the drained rows are re-queued (best-effort) before raising.
    
    Returns:
        Number of events ingested
    """
    rows = drain(max_rows)
    if not rows:
        return 0
    try:
        ingested = bulk_insert_events(rows)
        logger.debug(f"[EventBuffer] sync flush inserted {ingested} events")
        return ingested
    except Exception:
        requeued = push_batch(rows)
        if requeued < len(rows):
            logger.warning(
                f"[EventBuffer] sync-flush requeue partial: "
                f"{requeued}/{len(rows)} recovered"
            )
        raise


async def drain_once() -> int:
    """
    Drain up to FLUSH_CAPACITY rows and bulk-insert via to_thread.
    Used by lifespan shutdown for a final loss-free flush.
    
    Returns:
        Number of events ingested
    """
    rows = drain(FLUSH_CAPACITY)
    if not rows:
        return 0
    return await asyncio.to_thread(bulk_insert_events, rows)


async def flush_loop():
    """
    Long-lived consumer on the app loop.
    Drains queue every FLUSH_INTERVAL seconds and bulk-inserts via to_thread.
    Failed rows are re-queued instead of being silently lost.
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
                requeued = push_batch(rows)
                logger.warning(
                    f"[EventBuffer] re-queued {requeued}/{len(rows)} rows "
                    f"after flush failure"
                )
        await asyncio.sleep(FLUSH_INTERVAL)
