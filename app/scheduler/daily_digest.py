import asyncio
import logging
from datetime import datetime, timedelta
from typing import List
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.user import User
from app.models.event import Event
from app.services.recommendation_service import RecommendationService
from app.services.email_service import send_daily_digest_email
from app.services.product_service import get_product

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def run_daily_digest_job() -> int:
    """
    Find users active in last 24 hours, process in batches of 10 with 1s sleep,
    generate agent recommendations, and send digest emails.
    """
    logger.info("Starting Daily Digest Job...")
    db: Session = SessionLocal()
    processed_count = 0

    try:
        twenty_four_hours_ago = datetime.utcnow() - timedelta(hours=24)
        
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
                    products = [get_product(db, pid) for pid in rec_dict.get("product_ids", []) if get_product(db, pid)]
                    courses_data = [{"title": p.title, "category": p.category, "price": p.price} for p in products]

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
