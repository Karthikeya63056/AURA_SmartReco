import logging
from typing import Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.database import get_db
from app.dependencies import get_current_user_optional, get_current_user
from app.models.user import User
from app.services.recommendation_service import RecommendationService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/recommendations", tags=["Recommendations"])


@router.get("")
async def get_recommendations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_optional)
):
    """
    Get active recommendation narrative and recommended course products.
    Falls back to popular courses for cold-start (<3 events) users.
    Uses run_in_threadpool to ensure DB queries don't block the async event loop.
    """
    user_id = current_user.id if current_user else 2  # Demo user fallback
    rec = await run_in_threadpool(RecommendationService.get_active, db, user_id)
    return rec


@router.post("/refresh")
async def force_refresh_recommendation(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Manually force trigger the LangGraph recommendation agent.
    """
    rec_result = await RecommendationService.generate_and_store(
        db=db,
        user_id=current_user.id,
        trigger_reason="manual"
    )
    return rec_result
