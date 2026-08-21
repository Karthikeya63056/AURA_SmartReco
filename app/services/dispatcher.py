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
import queue
import threading
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import update

from app.core.database import SessionLocal
from app.models.agent_run import AgentRun
from app.agent.graph_v2 import run_agent

logger = logging.getLogger(__name__)

LEASE_MINUTES = 5
MAX_FOLLOW_UPS = 2
RECOVERY_BATCH_LIMIT = 100

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


def _put_or_drop(run_id: int) -> None:
    """Runs on the event-loop thread; queue-full must never crash the loop."""
    try:
        _job_queue.put_nowait(run_id)
    except queue.Full:
        # Row remains 'queued' in DB; recover_orphaned_runs / sweep re-enqueue later.
        logger.warning(f"[dispatcher] job queue full, run {run_id} left queued")


def submit_run_sync(run_id: int) -> bool:
    """Thread-safe submit from a sync context (e.g., FastAPI threadpool)."""
    if (
        _loop_ref is None
        or _job_queue is None
        or _loop_ref.is_closed()
    ):
        # NOT dropped: the row stays 'queued' and recover_orphaned_runs /
        # stale_run_sweep will pick it up (#22).
        logger.warning(
            f"[dispatcher] loop not started, run {run_id} stays queued for recovery"
        )
        return False
    if _job_queue.full():
        logger.warning(f"[dispatcher] job queue full, run {run_id} left queued")
        return False
    try:
        _loop_ref.call_soon_threadsafe(_put_or_drop, run_id)
    except RuntimeError:
        # Loop closed mid-submit: leave the row queued for recovery.
        logger.warning(f"[dispatcher] loop unavailable, run {run_id} left queued")
        return False
    return True


def recover_orphaned_runs() -> int:
    """
    Startup recovery (#23): re-submit runs orphaned by a previous crash.

    - All 'queued' rows (oldest first, cap 100) are pushed to the dispatcher.
    - 'done' rows still carrying pending_profile_hash get their deferred
      follow-up enqueued via maybe_enqueue_follow_up.

    Returns:
        Number of run ids successfully submitted.
    """
    submitted = 0

    with SessionLocal() as session:
        queued_ids: List[int] = [
            row.id
            for row in (
                session.query(AgentRun.id)
                .filter(AgentRun.status == "queued")
                .order_by(AgentRun.created_at.asc())
                .limit(RECOVERY_BATCH_LIMIT)
                .all()
            )
        ]
    for run_id in queued_ids:
        try:
            if submit_run_sync(run_id):
                submitted += 1
        except Exception as e:
            logger.error(f"[dispatcher] recovery submit failed for run {run_id}: {e}")

    with SessionLocal() as session:
        follow_up_ids: List[int] = [
            row.id
            for row in (
                session.query(AgentRun.id)
                .filter(
                    AgentRun.status == "done",
                    AgentRun.pending_profile_hash.isnot(None),
                )
                .order_by(AgentRun.created_at.asc())
                .limit(RECOVERY_BATCH_LIMIT)
                .all()
            )
        ]
    for run_id in follow_up_ids:
        try:
            new_id = maybe_enqueue_follow_up(run_id)
            if new_id is not None and submit_run_sync(new_id):
                submitted += 1
        except Exception as e:
            logger.error(
                f"[dispatcher] recovery follow-up failed for run {run_id}: {e}"
            )

    logger.info(f"[dispatcher] recovered {submitted} orphaned run(s)")
    return submitted


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


def _renew_lease_loop(run_id: int, stop_event: threading.Event) -> None:
    """
    #24: extend the lease every LEASE_MINUTES/2 while status == 'running',
    so long runs are not reclaimed as stale by the sweep.
    """
    interval = (LEASE_MINUTES * 60) / 2.0
    while not stop_event.wait(interval):
        try:
            with SessionLocal() as session:
                session.execute(
                    update(AgentRun)
                    .where(AgentRun.id == run_id, AgentRun.status == "running")
                    .values(
                        lease_until=datetime.now(timezone.utc)
                        + timedelta(minutes=LEASE_MINUTES)
                    )
                )
                session.commit()
        except Exception as e:
            logger.warning(f"[dispatcher] lease renewal failed for run {run_id}: {e}")


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

    # #24: keep the lease alive for the duration of the run
    stop_lease_renewal = threading.Event()
    lease_thread = threading.Thread(
        target=_renew_lease_loop,
        args=(run_id, stop_lease_renewal),
        name=f"lease-renew-{run_id}",
        daemon=True,
    )
    lease_thread.start()

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
    finally:
        stop_lease_renewal.set()
        lease_thread.join(timeout=1.0)


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
    q = asyncio.Queue(maxsize=1000)  # local ref: immune to global reassignment
    _job_queue = q
    logger.info("[dispatcher] loop started")
    while True:
        run_id = await q.get()
        try:
            await asyncio.to_thread(claim_and_run, run_id)
            follow_up_id = await asyncio.to_thread(maybe_enqueue_follow_up, run_id)
            if follow_up_id is not None:
                q.put_nowait(follow_up_id)  # safe: on app loop thread
        except asyncio.CancelledError:
            try:
                q.task_done()
            except ValueError:
                pass
            raise
        except Exception as e:
            logger.error(f"[dispatcher] loop error on run {run_id}: {e}", exc_info=True)
            try:
                q.task_done()
            except ValueError:
                pass
        else:
            try:
                q.task_done()
            except ValueError:
                pass


_dispatcher_task: Optional[asyncio.Task] = None
_dispatcher_loop: Optional[asyncio.AbstractEventLoop] = None


def start() -> asyncio.Task:
    """Start the dispatcher as a background task (call from lifespan).

    Loop-aware and idempotent: reuses the running task only when it was
    created on the CURRENT event loop; otherwise (fresh loop, e.g. a new
    TestClient portal or worker) a new task is started.
    """
    global _dispatcher_task, _dispatcher_loop
    current = asyncio.get_running_loop()
    if (
        _dispatcher_task is not None
        and not _dispatcher_task.done()
        and _dispatcher_loop is current
    ):
        return _dispatcher_task
    _dispatcher_task = asyncio.create_task(loop())
    _dispatcher_loop = current
    return _dispatcher_task