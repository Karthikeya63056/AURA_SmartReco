"""
Stale-run sweep (Rev5 §5.6).
Own APScheduler job, every 60s — same shape as the outbox reconciler,
NOT folded into the dispatcher loop.

Reclaims crash-orphaned runs:
    status='running' AND lease_until < now  =>  status='failed', last_error='lease_expired'

This frees the partial-unique single-flight slot so a crashed run
can never wedge a user forever. C5: opens its own session.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from app.core.database import SessionLocal
from app.models.agent_run import AgentRun

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 60
STALE_QUEUED_MINUTES = 5
STALE_QUEUED_CAP = 50

_scheduler = None


def sweep_stale_runs() -> int:
    """Reclaim all lease-expired running runs. Returns count reclaimed."""
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        result = session.execute(
            update(AgentRun)
            .where(
                AgentRun.status == "running",
                AgentRun.lease_until != None,  # noqa: E711
                AgentRun.lease_until < now,
            )
            .values(status="failed", last_error="lease_expired")
        )
        session.commit()
        reclaimed = result.rowcount
        if reclaimed:
            logger.info(f"[sweep] reclaimed {reclaimed} stale run(s)")

        # #22: re-enqueue queued runs orphaned by a crash before the loop
        # started or by a full job queue (submit_run_sync left them 'queued').
        try:
            from app.services import dispatcher  # avoid circular import

            stale_queued = (
                session.query(AgentRun)
                .filter(
                    AgentRun.status == "queued",
                    AgentRun.created_at < now - timedelta(minutes=STALE_QUEUED_MINUTES),
                )
                .order_by(AgentRun.created_at.asc())
                .limit(STALE_QUEUED_CAP)
                .all()
            )
            for run in stale_queued:
                try:
                    dispatcher.submit_run_sync(run.id)
                except Exception as e:
                    logger.warning(
                        f"[sweep] re-enqueue failed for run {run.id}: {e}"
                    )
            if stale_queued:
                logger.info(
                    f"[sweep] attempted re-enqueue of {len(stale_queued)} "
                    f"stale queued run(s)"
                )
        except Exception as e:
            logger.warning(f"[sweep] queued-run re-enqueue skipped: {e}")

        return reclaimed


def start_sweep_scheduler():
    """Start the sweep as its own APScheduler job (every 60s)."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    from apscheduler.schedulers.background import BackgroundScheduler

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        sweep_stale_runs,
        "interval",
        seconds=SWEEP_INTERVAL_SECONDS,
        id="stale_run_sweep",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.start()
    logger.info("[sweep] stale-run sweep scheduled every 60s")
    return _scheduler


def stop_sweep_scheduler():
    """Stop the sweep scheduler (call from lifespan shutdown)."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None