import logging
from typing import Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.database import get_db
from app.dependencies import get_admin_user
from app.models.user import User
from app.models.recommendation import Recommendation
from app.models.user_profile import UserProfile
from app.models.event import Event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["Admin"])


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
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user)
):
    """Manually trigger daily digest batch job for all active users."""
    from app.scheduler.daily_digest import run_daily_digest_job
    results = await run_daily_digest_job()
    return {"status": "success", "processed": results}


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
