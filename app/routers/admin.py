import logging
from typing import Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.database import get_db
from app.core.cache import cache
from app.dependencies import get_admin_user
from app.models.user import User
from app.models.recommendation import Recommendation
from app.models.user_profile import UserProfile
from app.models.event import Event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["Admin"])

# Cooldown key for manual digest trigger (prevents cost exhaustion / DoS)
DIGEST_COOLDOWN_KEY = "admin:digest_cooldown"
DIGEST_COOLDOWN_SECONDS = 3600  # 1 hour


def _fetch_agent_trace_data(db: Session, user_id: int) -> Dict[str, Any]:
    """Synchronous database query helper for agent trace inspection."""
    profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    recent_events = db.query(Event).filter(Event.user_id == user_id).order_by(Event.created_at.desc()).limit(20).all()
    recommendations = db.query(Recommendation).filter(Recommendation.user_id == user_id).order_by(Recommendation.created_at.desc()).limit(5).all()

    return {
        "user_id": user_id,
        "profile": {
            "interests": profile.interests_json if profile else [],
            "skill_level": profile.skill_level if profile else "Unknown",
            "intent": profile.intent if profile else "Unknown",
            "last_calculated_at": profile.last_calculated_at if profile else None
        },
        "event_count": len(recent_events),
        "recent_events": [
            {
                "id": e.id,
                "type": e.event_type,
                "session_id": e.session_id,
                "created_at": e.created_at,
                "payload": e.payload_json
            } for e in recent_events
        ],
        "recommendation_traces": [
            {
                "id": r.id,
                "trigger_reason": r.trigger_reason,
                "quality_score": r.quality_score,
                "refetch_count": r.refetch_count,
                "is_active": r.is_active,
                "product_ids": r.product_ids_json,
                "metadata": r.metadata_json,
                "created_at": r.created_at
            } for r in recommendations
        ]
    }


@router.post("/run-digest-now")
async def trigger_daily_digest_now(
    background_tasks: BackgroundTasks,
    admin: User = Depends(get_admin_user)
):
    """
    Manually trigger daily digest batch job for all active users.

    - Returns immediately (queued via BackgroundTasks).
    - Enforces a 1-hour cooldown to prevent cost exhaustion / DoS.
    """
    # Cooldown check
    if cache.get(DIGEST_COOLDOWN_KEY):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Daily digest was recently triggered. Please wait up to 1 hour before retrying."
        )

    # Set cooldown immediately so concurrent requests are blocked
    cache.set(DIGEST_COOLDOWN_KEY, True, ttl_seconds=DIGEST_COOLDOWN_SECONDS)

    from app.scheduler.daily_digest import run_daily_digest_job

    # Queue the heavy job in the background so the HTTP response is instant
    background_tasks.add_task(run_daily_digest_job)

    logger.info(f"Admin {admin.id} queued daily digest job (1-hour cooldown activated)")
    return {
        "status": "queued",
        "message": "Daily digest job has been queued and will run in the background."
    }


@router.get("/agent-trace/{user_id}")
async def get_agent_trace(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user)
):
    """
    Inspect recent agent execution trace, user profile, and recommendation metadata.
    Uses run_in_threadpool for non-blocking DB access.
    """
    trace_data = await run_in_threadpool(_fetch_agent_trace_data, db, user_id)
    return trace_data


def _compute_recommendation_outcomes(db: Session) -> Dict[str, Any]:
    """Compute click/dismiss/CTR metrics for recommendations (last 30 days)."""
    from datetime import datetime, timedelta
    from sqlalchemy import func, case, cast, String

    thirty_days_ago = datetime.utcnow() - timedelta(days=30)

    # Get recent recommendations
    recent_recs = db.query(Recommendation).filter(
        Recommendation.created_at >= thirty_days_ago
    ).order_by(Recommendation.created_at.desc()).limit(50).all()

    total_recs = len(recent_recs)

    # Count all rec_click and rec_dismiss events
    total_clicks = db.query(Event).filter(
        Event.event_type == "rec_click",
        Event.created_at >= thirty_days_ago,
    ).count()

    total_dismisses = db.query(Event).filter(
        Event.event_type == "rec_dismiss",
        Event.created_at >= thirty_days_ago,
    ).count()

    overall_ctr = round(
        (total_clicks / total_recs * 100) if total_recs > 0 else 0, 1
    )

    # Per-recommendation breakdown
    rec_metrics = []
    for rec in recent_recs[:20]:  # Top 20 most recent
        rec_id_str = str(rec.id)

        clicks = db.query(Event).filter(
            Event.event_type == "rec_click",
        ).all()
        click_count = sum(
            1 for e in clicks
            if str(e.payload_json.get("recommendation_id", "")) == rec_id_str
        )

        dismisses = db.query(Event).filter(
            Event.event_type == "rec_dismiss",
        ).all()
        dismiss_count = sum(
            1 for e in dismisses
            if str(e.payload_json.get("recommendation_id", "")) == rec_id_str
        )

        interactions = click_count + dismiss_count
        ctr = round((click_count / interactions * 100) if interactions > 0 else 0, 1)

        rec_metrics.append({
            "id": rec.id,
            "user_id": rec.user_id,
            "trigger_reason": rec.trigger_reason,
            "quality_score": rec.quality_score,
            "clicks": click_count,
            "dismisses": dismiss_count,
            "ctr": ctr,
            "created_at": rec.created_at,
        })

    return {
        "total_recs": total_recs,
        "total_clicks": total_clicks,
        "total_dismisses": total_dismisses,
        "overall_ctr": overall_ctr,
        "rec_metrics": rec_metrics,
    }


@router.get("/outcomes")
async def get_recommendation_outcomes(
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user)
):
    """
    Recommendation performance metrics: clicks, dismisses, and CTR.
    """
    outcomes = await run_in_threadpool(_compute_recommendation_outcomes, db)
    return outcomes