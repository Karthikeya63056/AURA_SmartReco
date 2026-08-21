import asyncio
import logging
from typing import Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.database import get_db, SessionLocal
from app.dependencies import get_current_user_optional, get_current_user, get_anonymous_user
from app.models.user import User
from app.services.recommendation_service import RecommendationService
from app.core.cache import cache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/recommendations", tags=["Recommendations"])


@router.get("")
async def get_recommendations(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_optional)
):
    """
    Get active recommendation narrative and recommended course products.
    Falls back to popular courses for cold-start (<3 events) users.
    Uses run_in_threadpool to ensure DB queries don't block the async event loop.
    """
    user = current_user or get_anonymous_user(
        db, session_id=request.headers.get("X-Session-Id")
    )
    user_id = user.id
    rec = await run_in_threadpool(_get_active_sync, user_id)
    return rec


def _get_active_sync(user_id: int) -> Dict[str, Any]:
    """Read recommendations with a session owned by the worker thread."""
    db = SessionLocal()
    try:
        return RecommendationService.get_active(db, user_id)
    finally:
        db.close()


def _run_generate_and_store_sync(user_id: int, trigger_reason: str) -> Dict[str, Any]:
    """
    Run the async agent pipeline in a worker thread with its own event loop + DB session.

    generate_and_store is async (agent ainvoke) but also performs sync SQLAlchemy I/O.
    Running the whole pipeline off the main event loop prevents request blocking.
    A fresh SessionLocal is required because SQLAlchemy sessions are not thread-safe.
    """
    db = SessionLocal()
    try:
        return asyncio.run(
            RecommendationService.generate_and_store(
                db=db,
                user_id=user_id,
                trigger_reason=trigger_reason,
            )
        )
    finally:
        db.close()


@router.post("/refresh")
async def force_refresh_recommendation(
    current_user: User = Depends(get_current_user)
):
    """
    Manually force trigger the LangGraph recommendation agent.

    Offloads the full generate_and_store pipeline (sync DB + async agent) to a
    threadpool worker so the FastAPI event loop stays responsive.
    """
    cooldown_key = f"manual_refresh:{current_user.id}"
    if cache.get(cooldown_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Please wait before refreshing again.",
        )

    # Set the cooldown BEFORE running the pipeline so concurrent requests are
    # rejected while the agent is in flight; roll it back on failure.
    cache.set(cooldown_key, True, ttl_seconds=300)
    try:
        rec_result = await run_in_threadpool(
            _run_generate_and_store_sync,
            current_user.id,
            "manual",
        )
    except Exception:
        cache.delete(cooldown_key)
        logger.exception(f"Manual refresh failed for user {current_user.id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Recommendation refresh failed. Please try again.",
        )

    # Invalidate the v2 poll cache so both stacks agree on the active rec.
    cache.delete(f"active_rec:{current_user.id}")
    return rec_result
