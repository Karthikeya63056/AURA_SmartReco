import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.models.event import Event
from app.models.product import Product
from app.models.recommendation import Recommendation
from app.models.user_profile import UserProfile
from app.models.user import User
from app.models.agent_run import AgentRun
from app.core.cache import cache
from app.services.signals import build_user_profile

logger = logging.getLogger(__name__)

COOLDOWN_MINUTES = 10

# Strong purchase / curriculum intent
STRONG_INTENT_TYPES = {"wishlist", "enroll_preview", "syllabus_view"}

# Softer exploration signals that still justify a re-run (gated by cooldown + behavior_hash)
SOFT_INTENT_TYPES = {"faq_expand", "instructor_view", "share"}

# Union used by the high_intent trigger path
HIGH_INTENT_TYPES = STRONG_INTENT_TYPES | SOFT_INTENT_TYPES


def _as_utc(value: datetime) -> datetime:
    """Normalize legacy/SQLite naive timestamps before Python comparisons."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def compute_behavior_hash(events: List[Event]) -> str:
    """Compute deterministic hash of user's recent events."""
    raw_str = "|".join(
        [f"{e.event_type}:{json.dumps(e.payload_json, sort_keys=True)}" for e in events]
    )
    return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()


class TriggerEngine:
    """Evaluates smart trigger conditions to optimize LLM invocation."""

    @staticmethod
    def evaluate_trigger(
        db: Session,
        user_id: int,
        current_session_id: Optional[str] = None,
        manual_force: bool = False,
    ) -> Dict[str, Any]:
        """
        Evaluates trigger conditions in priority order.
        Returns dict with keys: should_run_agent, trigger_reason, cold_start, products (for coldstart).
        """
        # Manual refresh bypasses standard cooldown/behavior guards
        if manual_force:
            return {"should_run_agent": True, "trigger_reason": "manual", "cold_start": False}

        now = datetime.now(timezone.utc)
        ten_minutes_ago = now - timedelta(minutes=COOLDOWN_MINUTES)
        fifteen_minutes_ago = now - timedelta(minutes=15)
        two_hours_ago = now - timedelta(hours=2)
        twenty_four_hours_ago = now - timedelta(hours=24)

        # Fetch last recommendation
        last_rec = (
            db.query(Recommendation)
            .filter(Recommendation.user_id == user_id)
            .order_by(Recommendation.created_at.desc())
            .first()
        )

        # Cooldown guard: 10 minutes (also limits soft-signal spam)
        if last_rec and _as_utc(last_rec.created_at) > ten_minutes_ago:
            return {
                "should_run_agent": False,
                "trigger_reason": "cooldown_active",
                "cold_start": False,
            }

        # Count total events efficiently without pulling full dataset into memory
        total_events = db.query(Event).filter(Event.user_id == user_id).count()

        # 1. Cold-start condition: < 3 total events
        if total_events < 3:
            # Deterministic ordering so the fallback list is stable across calls
            popular_courses = (
                db.query(Product)
                .filter((Product.is_popular == True) | (Product.is_trending == True))
                .order_by(Product.id.asc())
                .limit(5)
                .all()
            )
            return {
                "should_run_agent": False,
                "trigger_reason": "cold_start",
                "cold_start": True,
                "products": popular_courses,
            }

        # Fetch top 200 recent events (desc) — wide window so the 15-minute
        # intent scan (#63) sees bursts larger than 20 events
        recent_events = (
            db.query(Event)
            .filter(Event.user_id == user_id)
            .order_by(Event.created_at.desc())
            .limit(200)
            .all()
        )

        # Behavior hash: skip if nothing meaningful changed since last active rec
        # (hash semantics preserved: newest 20 events only)
        current_hash = compute_behavior_hash(recent_events[:20])
        user_profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
        if (
            user_profile
            and user_profile.behavior_hash == current_hash
            and last_rec
            and last_rec.is_active
        ):
            return {
                "should_run_agent": False,
                "trigger_reason": "behavior_unchanged",
                "cold_start": False,
            }

        recent_15m_events = [
            event for event in recent_events if _as_utc(event.created_at) >= fifteen_minutes_ago
        ]

        # 2a. Strong high-intent (wishlist / enroll / syllabus)
        has_strong_intent = any(e.event_type in STRONG_INTENT_TYPES for e in recent_15m_events)
        if has_strong_intent:
            return {
                "should_run_agent": True,
                "trigger_reason": "high_intent",
                "cold_start": False,
            }

        # 2b. Soft intent (FAQ / instructor / share) — still triggers, distinct reason for traces
        has_soft_intent = any(e.event_type in SOFT_INTENT_TYPES for e in recent_15m_events)
        if has_soft_intent:
            return {
                "should_run_agent": True,
                "trigger_reason": "soft_intent",
                "cold_start": False,
            }

        # 3. Session event threshold (>= 5 events in current session,
        #    bounded to the last 24h as a server-side safety net: a client that
        #    never rotates its session id must not turn this into a lifetime
        #    counter that fires the LLM agent forever)
        if current_session_id:
            session_event_count = (
                db.query(Event)
                .filter(
                    Event.user_id == user_id,
                    Event.session_id == current_session_id,
                    Event.created_at >= twenty_four_hours_ago,
                )
                .count()
            )
            if session_event_count >= 5:
                return {
                    "should_run_agent": True,
                    "trigger_reason": "event_threshold",
                    "cold_start": False,
                }

        # 4. Search signal (>= 2 searches in last 15 min)
        search_count = sum(1 for e in recent_15m_events if e.event_type == "search")
        if search_count >= 2:
            return {
                "should_run_agent": True,
                "trigger_reason": "search_signal",
                "cold_start": False,
            }

        # 4b. Ignored-recommendation trigger: last 2 recs have no clicks but at least 1 dismiss
        last_two_recs = (
            db.query(Recommendation)
            .filter(Recommendation.user_id == user_id)
            .order_by(Recommendation.created_at.desc())
            .limit(2)
            .all()
        )

        if len(last_two_recs) >= 2:
            rec_ids = {r.id for r in last_two_recs}
            seven_days_ago = now - timedelta(days=7)

            def _rec_id_of(e: Event) -> Optional[int]:
                """Typed parse — '15' must not match recommendation id 1 or 5."""
                raw = e.payload_json.get("recommendation_id")
                try:
                    return int(raw)
                except (TypeError, ValueError):
                    return None

            click_events = (
                db.query(Event)
                .filter(Event.event_type == "rec_click", Event.user_id == user_id)
                .filter(Event.created_at >= seven_days_ago)
                .limit(50)
                .all()
            )
            clicks_on_recs = sum(
                1 for e in click_events if _rec_id_of(e) in rec_ids
            )

            dismiss_events = (
                db.query(Event)
                .filter(Event.event_type == "rec_dismiss", Event.user_id == user_id)
                .filter(Event.created_at >= seven_days_ago)
                .limit(50)
                .all()
            )
            dismisses_on_recs = sum(
                1 for e in dismiss_events if _rec_id_of(e) in rec_ids
            )

            if clicks_on_recs == 0 and dismisses_on_recs > 0:
                logger.info(
                    f"[TriggerEngine] User {user_id} ignored last 2 recs "
                    f"(0 clicks, {dismisses_on_recs} dismisses)"
                )
                return {
                    "should_run_agent": True,
                    "trigger_reason": "ignored_recommendations",
                    "cold_start": False,
                }

        # 5. Staleness (>= 2 hours since last recommendation)
        if not last_rec or _as_utc(last_rec.created_at) <= two_hours_ago:
            return {
                "should_run_agent": True,
                "trigger_reason": "staleness",
                "cold_start": False,
            }

        return {"should_run_agent": False, "trigger_reason": "skip", "cold_start": False}


# ============================================================
# Rev5: 5-Gate Trigger Policy + Single-Flight Enqueue (C6)
# ============================================================

def enqueue_run(
    db: Session,
    user_id: int,
    profile_hash: str,
    trigger_reason: str,
) -> Dict[str, Any]:
    """
    Attempt single-flight enqueue of an agent run.
    
    Uses the partial unique index on agent_runs(user_id) WHERE status IN ('queued','running')
    to enforce at-most-one in-flight run per user.
    
    Returns:
      {"status": "enqueued", "run_id": int}
      {"status": "refresh_requested", "run_id": int}
      {"status": "already_in_flight"}
    """
    try:
        new_run = AgentRun(
            user_id=user_id,
            profile_hash=profile_hash,
            status="queued",
            trigger_reason=trigger_reason,
            follow_up_count=0,
        )
        db.add(new_run)
        db.commit()
        db.refresh(new_run)
        logger.info(f"[TriggerEngine] Enqueued agent run {new_run.id} for user {user_id}")
        return {"status": "enqueued", "run_id": new_run.id}
    except IntegrityError:
        db.rollback()
        # Find the active run and mark it for refresh
        active_run = (
            db.query(AgentRun)
            .filter(
                AgentRun.user_id == user_id,
                AgentRun.status.in_(["queued", "running"]),
            )
            .first()
        )
        if active_run:
            active_run.pending_profile_hash = profile_hash
            active_run.refresh_requested = True
            db.commit()
            logger.info(
                f"[TriggerEngine] Marked active run {active_run.id} for refresh (user {user_id})"
            )
            return {"status": "refresh_requested", "run_id": active_run.id}
        return {"status": "already_in_flight"}


def evaluate_5gate_trigger(
    db: Session,
    user_id: int,
    current_session_id: Optional[str] = None,
    manual_force: bool = False,
) -> Dict[str, Any]:
    """
    5-gate trigger policy with single-flight enqueue.
    
    Gates (evaluated in order):
      1. min_events >= 5 new since last run
      2. cooldown >= 90s since last run
      3. profile_hash changed (vs last completed run)
      4. daily spend < cap
      5. user active & not admin
    
    If all gates pass, determines trigger reason via existing intent logic,
    then attempts single-flight enqueue.
    
    Returns:
      {
        "should_enqueue": bool,
        "trigger_reason": str,
        "skip_reasons": list[str],
        "run_id": int|None,
        "enqueue_status": str|None,
        "gates": dict[str, bool]
      }
    """
    from app.config import settings
    
    gates_passed: Dict[str, bool] = {}
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return {
            "should_enqueue": False,
            "skip_reasons": ["user_not_found"],
            "gates": gates_passed,
        }
    
    # Gate 5: user active & not admin (check first - cheap)
    gates_passed["user_active"] = user.is_active and not user.is_admin
    if not gates_passed["user_active"]:
        return {
            "should_enqueue": False,
            "skip_reasons": ["user_inactive_or_admin"],
            "gates": gates_passed,
        }
    
    now = datetime.now(timezone.utc)
    
    # Last completed run (for cooldown + profile_hash comparison)
    last_run = (
        db.query(AgentRun)
        .filter(AgentRun.user_id == user_id, AgentRun.status == "done")
        .order_by(AgentRun.created_at.desc())
        .first()
    )
    
    # Gate 2: cooldown >= 90s
    cooldown_seconds = getattr(settings, "REC_COOLDOWN_SECONDS", 90)
    if last_run:
        last_created = _as_utc(last_run.created_at)
        elapsed = (now - last_created).total_seconds()
        gates_passed["cooldown_elapsed"] = elapsed >= cooldown_seconds
    else:
        gates_passed["cooldown_elapsed"] = True
    
    if not gates_passed["cooldown_elapsed"]:
        return {
            "should_enqueue": False,
            "skip_reasons": ["cooldown_elapsed"],
            "gates": gates_passed,
        }
    
    # Gate 1: min_events >= 5 new since last run
    min_events = getattr(settings, "REC_MIN_EVENTS", 5)
    if last_run:
        new_events_count = (
            db.query(Event)
            .filter(Event.user_id == user_id, Event.created_at > last_run.created_at)
            .count()
        )
    else:
        new_events_count = db.query(Event).filter(Event.user_id == user_id).count()
    
    gates_passed["min_events"] = new_events_count >= min_events
    if not gates_passed["min_events"]:
        return {
            "should_enqueue": False,
            "skip_reasons": ["min_events"],
            "gates": gates_passed,
        }
    
    # Gate 3: profile_hash changed
    profile = build_user_profile(db, user_id, now=now)
    if last_run and last_run.profile_hash == profile["profile_hash"]:
        gates_passed["behavior_changed"] = False
        return {
            "should_enqueue": False,
            "skip_reasons": ["behavior_unchanged"],
            "gates": gates_passed,
        }
    gates_passed["behavior_changed"] = True
    
    # Gate 4: daily spend < cap (global, since Mesh API budget is shared)
    budget_usd = getattr(settings, "LLM_DAILY_BUDGET_USD", 1.00)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    daily_spend = (
        db.query(func.coalesce(func.sum(AgentRun.cost_usd), 0.0))
        .filter(AgentRun.created_at >= today_start)
        .scalar()
    )
    gates_passed["budget_available"] = daily_spend < budget_usd
    if not gates_passed["budget_available"]:
        return {
            "should_enqueue": False,
            "skip_reasons": ["budget_exhausted"],
            "gates": gates_passed,
        }
    
    # All gates passed - determine trigger reason via existing logic
    intent_result = TriggerEngine.evaluate_trigger(
        db, user_id, current_session_id, manual_force
    )
    if not intent_result.get("should_run_agent"):
        return {
            "should_enqueue": False,
            "skip_reasons": [intent_result.get("trigger_reason", "no_intent")],
            "trigger_reason": intent_result.get("trigger_reason", "no_intent"),
            "gates": gates_passed,
        }
    
    trigger_reason = intent_result["trigger_reason"]
    
    # Single-flight enqueue
    enqueue_result = enqueue_run(db, user_id, profile["profile_hash"], trigger_reason)
    return {
        "should_enqueue": enqueue_result["status"] == "enqueued",
        "trigger_reason": trigger_reason,
        "skip_reasons": [enqueue_result["status"]] if enqueue_result["status"] != "enqueued" else [],
        "run_id": enqueue_result.get("run_id"),
        "enqueue_status": enqueue_result["status"],
        "gates": gates_passed,
    }