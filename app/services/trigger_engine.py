import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session

from app.models.event import Event
from app.models.product import Product
from app.models.recommendation import Recommendation
from app.models.user_profile import UserProfile
from app.core.cache import cache

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
        Returns dict with keys: should_run_agent, trigger_reason, cold_start, products (for cold start).
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

        # Fetch top 20 recent events for hash & intent analysis
        recent_events = (
            db.query(Event)
            .filter(Event.user_id == user_id)
            .order_by(Event.created_at.desc())
            .limit(20)
            .all()
        )

        # Behavior hash: skip if nothing meaningful changed since last active rec
        current_hash = compute_behavior_hash(recent_events)
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