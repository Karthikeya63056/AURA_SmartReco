import asyncio
import logging
import re
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

# Domains that should NEVER receive digest emails (seeded / placeholder / fake)
PLACEHOLDER_EMAIL_DOMAINS = {
    "example.com",
    "smartreco.ai",
    "aura.com",
    "test.com",
    "localhost",
    "fake.com",
    "mailinator.com",
}

# Basic RFC-5322-ish check — catches obvious garbage like "guest" or "@@.com"
EMAIL_RE = re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$", re.IGNORECASE)


def _is_sendable_email(email: str) -> bool:
    """Return True only for real, owned email addresses we can actually deliver to."""
    if not email or not isinstance(email, str):
        return False
    email = email.strip()
    if not EMAIL_RE.match(email):
        return False
    domain = email.split("@", 1)[1].lower()
    return domain not in PLACEHOLDER_EMAIL_DOMAINS


async def _run_daily_digest_body() -> int:
    """
    Core digest logic: find active users, generate recommendations, send emails.
    Skips placeholder/invalid emails so we never mail seed accounts or bounce.
    """
    logger.info("Starting Daily Digest Job...")
    db: Session = SessionLocal()
    processed_count = 0
    skipped_count = 0

    try:
        twenty_four_hours_ago = datetime.now(timezone.utc) - timedelta(hours=24)

        active_user_ids = db.query(Event.user_id).filter(
            Event.created_at >= twenty_four_hours_ago
        ).distinct().all()

        user_ids = [uid[0] for uid in active_user_ids]
        logger.info(f"Found {len(user_ids)} active users for daily digest")

        batch_size = 10
        for i in range(0, len(user_ids), batch_size):
            batch = user_ids[i:i + batch_size]
            for uid in batch:
                user = db.query(User).filter(User.id == uid).first()
                if not user:
                    continue

                # Skip placeholder / fake / non-sendable emails
                if not _is_sendable_email(user.email):
                    logger.info(f"[Digest] Skipping non-sendable email for user #{uid}: {user.email!r}")
                    skipped_count += 1
                    continue

                try:
                    rec_dict = await RecommendationService.generate_and_store(
                        db=db,
                        user_id=uid,
                        trigger_reason="daily_digest"
                    )

                    products = []
                    for pid in rec_dict.get("product_ids", []):
                        p = get_product(db, pid)
                        if p:
                            products.append(p)

                    courses_data = [
                        {
                            "id": p.id,
                            "title": p.title,
                            "category": p.category,
                            "price": p.price,
                            "level": getattr(p, "level", ""),
                        }
                        for p in products
                    ]

                    send_daily_digest_email(
                        user_email=user.email,
                        user_name=user.full_name or user.email.split("@")[0] or "Learner",
                        narrative=rec_dict.get("narrative", ""),
                        courses=courses_data,
                    )
                    processed_count += 1
                    logger.info(f"[Digest] Sent email to {user.email} (user #{uid})")
                except Exception as user_err:
                    logger.error(f"Error processing digest for user {uid}: {str(user_err)}")

            if i + batch_size < len(user_ids):
                await asyncio.sleep(1.0)

        logger.info(
            f"Completed Daily Digest Job. "
            f"Processed={processed_count}, Skipped={skipped_count}, Total={len(user_ids)}."
        )
        return processed_count
    except Exception as e:
        logger.error(f"Error in run_daily_digest_job: {str(e)}")
        return processed_count
    finally:
        db.close()


def _run_daily_digest_in_thread() -> int:
    """Synchronous entrypoint for threadpool workers."""
    return asyncio.run(_run_daily_digest_body())


async def run_daily_digest_job() -> int:
    """Async public API — offloads full job to threadpool so main loop stays responsive."""
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
        replace_existing=True,
    )
    scheduler.start()
    logger.info("APScheduler initialized: Daily Digest job scheduled for 09:00 AM daily.")