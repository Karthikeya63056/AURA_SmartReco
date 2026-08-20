"""
Agent job dispatcher (long-lived consumer on the app loop).

Lifecycle (Rev5 §5.6, C6):
  loop():
    run_id = await queue.get()
    await to_thread(claim_and_run, run_id)          # atomic claim + run graph
    follow_up_id = await to_thread(maybe_enqueue_follow_up, run_id)
    if follow_up_id: queue.put_nowait(follow_up_id) # bounded, sequential

Atomic claim: UPDATE agent_runs SET status='running', lease_until
              WHERE id=:id AND status='queued'; proceed only if rowcount==1.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import update

from app.core.database import SessionLocal
from app.models.agent_run import AgentRun
from app.agent.graph_v2 import run_agent

logger = logging.getLogger(__name__)

LEASE_MINUTES = 5
MAX_FOLLOW_UPS = 2

# Queue and loop references (created lazily when loop starts)
_job_queue: Optional[asyncio.Queue] = None
_loop_ref: Optional[asyncio.AbstractEventLoop] = None


def _get_queue() -> asyncio.Queue:
    """Get the queue, creating it if needed (must be called from event loop context)."""
    global _job_queue
    if _job_queue is None:
        _job_queue = asyncio.Queue(maxsize=1000)
    return _job_queue


async def submit_run(run_id: int) -> None:
    """Enqueue an agent run for processing (async context)."""
    queue = _get_queue()
    await queue.put(run_id)


def submit_run_sync(run_id: int) -> bool:
    """Thread-safe submit from a sync context (e.g., FastAPI threadpool)."""
    if _loop_ref is None or _job_queue is None:
        logger.warning("[dispatcher] loop not started, dropping run")
        return False
    _loop_ref.call_soon_threadsafe(_job_queue.put_nowait, run_id)
    return True


def claim_run(run_id: int) -> bool:
    """
    Atomically claim a queued run (queued -> running + lease).
    Returns True only if this caller won the claim (rowcount==1).
    """
    with SessionLocal() as session:
        now = datetime.now(timezone.utc)
        lease = now + timedelta(minutes=LEASE_MINUTES)
        result = session.execute(
            update(AgentRun)
            .where(AgentRun.id == run_id, AgentRun.status == "queued")
            .values(status="running", lease_until=lease)
        )
        session.commit()
        return result.rowcount == 1


def claim_and_run(run_id: int) -> None:
    """Claim the run and execute the agent graph (runs in a worker thread)."""
    if not claim_run(run_id):
        logger.info(f"[dispatcher] run {run_id} not claimable, skipping")
        return

    # Fetch run details in our own session (C5)
    with SessionLocal() as session:
        row = session.get(AgentRun, run_id)
        if row is None:
            return
        user_id = row.user_id
        profile_hash = row.profile_hash
        trigger_reason = row.trigger_reason or "agent"

    try:
        run_agent(run_id, user_id, profile_hash, trigger_reason)
    except Exception as e:
        logger.error(f"[dispatcher] run {run_id} crashed: {e}", exc_info=True)
        with SessionLocal() as session:
            row = session.get(AgentRun, run_id)
            if row is not None and row.status == "running":
                row.status = "failed"
                row.last_error = str(e)
                session.commit()


def maybe_enqueue_follow_up(run_id: int) -> Optional[int]:
    """
    After a completed run, if a newer profile arrived mid-run (mark-and-defer),
    enqueue ONE bounded follow-up. Returns the new run id, or None.
    """
    with SessionLocal() as session:
        row = session.get(AgentRun, run_id)
        if row is None or row.status != "done" or not row.pending_profile_hash:
            return None

        if row.follow_up_count >= MAX_FOLLOW_UPS:
            logger.info(f"[dispatcher] follow_up_cap reached for user {row.user_id}")
            row.pending_profile_hash = None
            row.refresh_requested = False
            session.commit()
            return None

        new_run = AgentRun(
            user_id=row.user_id,
            profile_hash=row.pending_profile_hash,
            status="queued",
            trigger_reason="refresh_requested",
            follow_up_count=row.follow_up_count + 1,
        )
        session.add(new_run)
        row.pending_profile_hash = None
        row.refresh_requested = False
        session.commit()
        session.refresh(new_run)
        logger.info(
            f"[dispatcher] enqueued follow-up run {new_run.id} for user {row.user_id}"
        )
        return new_run.id


async def loop() -> None:
    """Long-lived dispatcher consumer (C1 — runs on the app loop)."""
    global _job_queue, _loop_ref
    _loop_ref = asyncio.get_running_loop()
    _job_queue = asyncio.Queue(maxsize=1000)  # Create queue in correct loop context
    logger.info("[dispatcher] loop started")
    while True:
        run_id = await _job_queue.get()
        try:
            await asyncio.to_thread(claim_and_run, run_id)
            follow_up_id = await asyncio.to_thread(maybe_enqueue_follow_up, run_id)
            if follow_up_id is not None:
                _job_queue.put_nowait(follow_up_id)  # safe: on app loop thread
        except Exception as e:
            logger.error(f"[dispatcher] loop error on run {run_id}: {e}", exc_info=True)
        finally:
            _job_queue.task_done()


def start() -> asyncio.Task:
    """Start the dispatcher as a background task (call from lifespan)."""
    return asyncio.create_task(loop())