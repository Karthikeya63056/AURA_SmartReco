import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import List
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.database import SessionLocal
from app.models.user import User
from app.models.event import Event
from app.services.recommendation_service import RecommendationService
from app.services.email_service import send_daily_digest_email
from app.services.product_service import get_product

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def _run_daily_digest_body() -> int:
    """
    Core digest logic: find active users, generate recommendations, send emails.
    Uses its own DB session. Intended to run inside a dedicated event loop
    (either the scheduler loop via thread offload, or asyncio.run in a worker).
    """
    logger.info("Starting Daily Digest Job...")
    db: Session = SessionLocal()
    processed_count = 0

    try:
        twenty_four_hours_ago = datetime.now(timezone.utc) - timedelta(hours=24)

        # Find distinct user IDs who had events in last 24h
        active_user_ids = db.query(Event.user_id).filter(
            Event.created_at >= twenty_four_hours_ago
        ).distinct().all()

        user_ids = [uid[0] for uid in active_user_ids]
        logger.info(f"Found {len(user_ids)} active users for daily digest")

        # Process in batches of 10
        batch_size = 10
        for i in range(0, len(user_ids), batch_size):
            batch = user_ids[i:i + batch_size]
            for uid in batch:
                user = db.query(User).filter(User.id == uid).first()
                if not user:
                    continue

                try:
                    # Generate fresh recommendation via agent
                    rec_dict = await RecommendationService.generate_and_store(
                        db=db,
                        user_id=uid,
                        trigger_reason="daily_digest"
                    )

                    # Fetch recommended course objects
                    products = [
                        get_product(db, pid)
                        for pid in rec_dict.get("product_ids", [])
                        if get_product(db, pid)
                    ]
                    courses_data = [
                        {"title": p.title, "category": p.category, "price": p.price}
                        for p in products
                    ]

                    # Send email
                    send_daily_digest_email(
                        user_email=user.email,
                        user_name=user.full_name or "Learner",
                        narrative=rec_dict.get("narrative", ""),
                        courses=courses_data
                    )
                    processed_count += 1
                except Exception as user_err:
                    logger.error(f"Error processing digest for user {uid}: {str(user_err)}")

            # Rate limit sleep between batches
            if i + batch_size < len(user_ids):
                await asyncio.sleep(1.0)

        logger.info(f"Completed Daily Digest Job. Processed {processed_count} users.")
        return processed_count
    except Exception as e:
        logger.error(f"Error in run_daily_digest_job: {str(e)}")
        return processed_count
    finally:
        db.close()


def _run_daily_digest_in_thread() -> int:
    """
    Synchronous entrypoint for threadpool workers.
    Spins up a private event loop so sync SQLAlchemy work never blocks the
    FastAPI main loop, while still allowing await on the LangGraph agent.
    """
    return asyncio.run(_run_daily_digest_body())


async def run_daily_digest_job() -> int:
    """
    Async public API for the daily digest.

    Offloads the full job (sync DB + async agent + email) to a threadpool worker
    so callers on the main event loop (admin endpoint, APScheduler) stay responsive.
    """
    logger.info("Dispatching Daily Digest Job to threadpool...")
    return await run_in_threadpool(_run_daily_digest_in_thread)


def start_scheduler():
    """Start APScheduler background job (runs daily at 9:00 AM)."""
    scheduler.add_job(
        run_daily_digest_job,
        trigger="cron",
        hour=9,
        minute=0,
        id="daily_digest_job",
        replace_existing=True
    )
    scheduler.start()
    logger.info("APScheduler initialized: Daily Digest job scheduled for 09:00 AM daily.")
