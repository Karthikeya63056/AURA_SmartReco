import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, get_db
from app.core.cache import cache
from app.dependencies import get_admin_user, get_current_user_optional
from app.models.user import User
from app.models.recommendation import Recommendation
from app.models.user_profile import UserProfile
from app.models.event import Event

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")

router = APIRouter(prefix="/admin", tags=["Admin"])

# Cooldown key for manual digest trigger (prevents cost exhaustion / DoS)
DIGEST_COOLDOWN_KEY = "admin:digest_cooldown"
DIGEST_COOLDOWN_SECONDS = 3600  # 1 hour


def _fetch_agent_trace_data(user_id: int, db: Optional[Session] = None) -> Dict[str, Any]:
    """
    Synchronous database query helper for agent trace inspection.

    When called from a threadpool worker it must NOT receive the request-scoped
    session (it belongs to the event-loop thread), so it opens its own unless
    an explicit session is supplied (tests / SSR page rendering).
    """
    owns_session = db is None
    if owns_session:
        db = SessionLocal()
    try:
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
    finally:
        if owns_session:
            db.close()


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
    admin: User = Depends(get_admin_user)
):
    """
    Inspect recent agent execution trace, user profile, and recommendation metadata.
    Uses run_in_threadpool for non-blocking DB access.
    """
    trace_data = await run_in_threadpool(_fetch_agent_trace_data, user_id)
    return trace_data


def _compute_recommendation_outcomes(db: Optional[Session] = None) -> Dict[str, Any]:
    """Compute click/dismiss/CTR metrics for recommendations (last 30 days).

    Opens its own session when called from a threadpool worker; accepts an
    explicit session for tests / synchronous SSR rendering.
    """
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import func, cast, String

    owns_session = db is None
    if owns_session:
        db = SessionLocal()
    try:
        thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)

        # Get recent recommendations
        recent_recs_query = db.query(Recommendation).filter(
            Recommendation.created_at >= thirty_days_ago
        )
        total_recs = recent_recs_query.count()

        # The detail table is intentionally bounded for rendering; its count is
        # not used as the overall CTR denominator.
        recent_recs = recent_recs_query.order_by(Recommendation.created_at.desc()).limit(20).all()

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
        for rec in recent_recs:
            rec_id_str = str(rec.id)

            click_count = db.query(Event).filter(
                Event.event_type == "rec_click",
                Event.created_at >= thirty_days_ago,
                cast(func.json_extract(Event.payload_json, "$.recommendation_id"), String) == rec_id_str,
            ).count()

            dismiss_count = db.query(Event).filter(
                Event.event_type == "rec_dismiss",
                Event.created_at >= thirty_days_ago,
                cast(func.json_extract(Event.payload_json, "$.recommendation_id"), String) == rec_id_str,
            ).count()

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
    finally:
        if owns_session:
            db.close()


@router.get("/outcomes")
async def get_recommendation_outcomes(
    admin: User = Depends(get_admin_user)
):
    """
    Recommendation performance metrics: clicks, dismisses, and CTR.
    """
    outcomes = await run_in_threadpool(_compute_recommendation_outcomes)
    return outcomes


# ============================================================
# Rev5 Phase 4: Agent Runs observability
# ============================================================
from app.models.agent_run import AgentRun
from sqlalchemy import func, desc
import json as _json


def _compute_agent_run_stats(db: Session) -> dict:
    """Aggregate stats for the agent runs dashboard."""
    total_runs = db.query(AgentRun).count()
    by_status = {
        row[0]: row[1]
        for row in db.query(AgentRun.status, func.count(AgentRun.id))
        .group_by(AgentRun.status)
        .all()
    }
    by_trigger = {
        row[0] or "(none)": row[1]
        for row in db.query(AgentRun.trigger_reason, func.count(AgentRun.id))
        .group_by(AgentRun.trigger_reason)
        .all()
    }
    total_cost = (
        db.query(func.coalesce(func.sum(AgentRun.cost_usd), 0.0)).scalar() or 0.0
    )
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    daily_cost = (
        db.query(func.coalesce(func.sum(AgentRun.cost_usd), 0.0))
        .filter(AgentRun.created_at >= today_start)
        .scalar() or 0.0
    )
    return {
        "total_runs": total_runs,
        "by_status": by_status,
        "by_trigger": by_trigger,
        "total_cost_usd": round(total_cost, 4),
        "daily_cost_usd": round(daily_cost, 4),
    }


@router.get("/agent-runs", response_class=HTMLResponse)
def page_admin_agent_runs(
    request: Request,
    status_filter: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """Admin: agent run list with skip reasons, deferrals, cost."""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if not user.is_admin:
        return RedirectResponse(url="/dashboard", status_code=302)

    query = db.query(AgentRun).order_by(desc(AgentRun.created_at))
    if status_filter:
        query = query.filter(AgentRun.status == status_filter)
    runs = query.limit(200).all()

    # Parse JSON fields for display
    display_runs = []
    for r in runs:
        try:
            skip_reasons = _json.loads(r.skip_reasons_json) if r.skip_reasons_json else None
        except Exception:
            skip_reasons = r.skip_reasons_json
        try:
            scores = _json.loads(r.candidate_scores_json) if r.candidate_scores_json else None
        except Exception:
            scores = r.candidate_scores_json
        display_runs.append({
            "id": r.id,
            "user_id": r.user_id,
            "status": r.status,
            "trigger_reason": r.trigger_reason,
            "skip_reasons": skip_reasons,
            "candidate_count": len(scores) if isinstance(scores, list) else 0,
            "model_used": r.model_used,
            "tokens": r.tokens,
            "cost_usd": r.cost_usd,
            "latency_ms": r.latency_ms,
            "retry_count": r.retry_count,
            "degraded": r.degraded,
            "refresh_requested": r.refresh_requested,
            "follow_up_count": r.follow_up_count,
            "last_error": r.last_error,
            "created_at": r.created_at,
        })

    stats = _compute_agent_run_stats(db)

    return templates.TemplateResponse(
        request,
        "admin/agent_runs.html",
        {
            "user": user,
            "runs": display_runs,
            "stats": stats,
            "status_filter": status_filter,
        },
    )