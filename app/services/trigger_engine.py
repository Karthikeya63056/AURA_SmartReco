import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session

from app.models.event import Event
from app.models.product import Product
from app.models.recommendation import Recommendation
from app.models.user_profile import UserProfile
from app.core.cache import cache

logger = logging.getLogger(__name__)

COOLDOWN_MINUTES = 10
HIGH_INTENT_TYPES = {"wishlist", "enroll_preview", "syllabus_view"}


def compute_behavior_hash(events: List[Event]) -> str:
    """Compute deterministic hash of user's recent events."""
    raw_str = "|".join([f"{e.event_type}:{json.dumps(e.payload_json, sort_keys=True)}" for e in events])
    return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()


class TriggerEngine:
    """Evaluates smart trigger conditions to optimize LLM invocation."""

    @staticmethod
    def evaluate_trigger(
        db: Session,
        user_id: int,
        current_session_id: Optional[str] = None,
        manual_force: bool = False
    ) -> Dict[str, Any]:
        """
        Evaluates 6 trigger conditions in priority order.
        Returns dict with keys: should_run_agent, trigger_reason, cold_start, products (for cold start).
        """
        # 6. Manual refresh bypasses standard cooldown/behavior guards
        if manual_force:
            return {"should_run_agent": True, "trigger_reason": "manual", "cold_start": False}

        now = datetime.utcnow()
        ten_minutes_ago = now - timedelta(minutes=COOLDOWN_MINUTES)
        fifteen_minutes_ago = now - timedelta(minutes=15)
        two_hours_ago = now - timedelta(hours=2)

        # Fetch last recommendation
        last_rec = db.query(Recommendation).filter(
            Recommendation.user_id == user_id
        ).order_by(Recommendation.created_at.desc()).first()

        # Cooldown guard: 10 minutes
        if last_rec and last_rec.created_at > ten_minutes_ago:
            return {
                "should_run_agent": False,
                "trigger_reason": "cooldown_active",
                "cold_start": False
            }

        # Count total events efficiently without pulling full dataset into memory
        total_events = db.query(Event).filter(Event.user_id == user_id).count()

        # 1. Cold-start condition: < 3 total events
        if total_events < 3:
            popular_courses = db.query(Product).filter(
                (Product.is_popular == True) | (Product.is_trending == True)
            ).limit(5).all()
            return {
                "should_run_agent": False,
                "trigger_reason": "cold_start",
                "cold_start": True,
                "products": popular_courses
            }

        # Fetch top 20 recent events for hash & intent analysis
        recent_events = db.query(Event).filter(Event.user_id == user_id).order_by(Event.created_at.desc()).limit(20).all()

        # Check behavior hash for duplication avoidance
        current_hash = compute_behavior_hash(recent_events)
        user_profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
        if user_profile and user_profile.behavior_hash == current_hash and last_rec and last_rec.is_active:
            return {
                "should_run_agent": False,
                "trigger_reason": "behavior_unchanged",
                "cold_start": False
            }

        # 2. High-intent action signal in last 15 min
        recent_15m_events = [e for e in recent_events if e.created_at >= fifteen_minutes_ago]
        has_high_intent = any(e.event_type in HIGH_INTENT_TYPES for e in recent_15m_events)
        if has_high_intent:
            return {"should_run_agent": True, "trigger_reason": "high_intent", "cold_start": False}

        # 3. Session event threshold (>= 5 events in current session)
        if current_session_id:
            session_event_count = db.query(Event).filter(
                Event.user_id == user_id,
                Event.session_id == current_session_id
            ).count()
            if session_event_count >= 5:
                return {"should_run_agent": True, "trigger_reason": "event_threshold", "cold_start": False}

        # 4. Search signal (>= 2 searches in last 15 min)
        search_count = sum(1 for e in recent_15m_events if e.event_type == "search")
        if search_count >= 2:
            return {"should_run_agent": True, "trigger_reason": "search_signal", "cold_start": False}

        # 4b. Ignored-recommendation trigger: last 2 recs have no clicks but at least 1 dismiss
        last_two_recs = db.query(Recommendation).filter(
            Recommendation.user_id == user_id
        ).order_by(Recommendation.created_at.desc()).limit(2).all()

        if len(last_two_recs) >= 2:
            rec_ids_str = [str(r.id) for r in last_two_recs]
            click_events = db.query(Event).filter(
                Event.event_type == "rec_click",
                Event.user_id == user_id,
            ).all()
            clicks_on_recs = sum(
                1 for e in click_events
                if str(e.payload_json.get("recommendation_id", "")) in rec_ids_str
            )

            dismiss_events = db.query(Event).filter(
                Event.event_type == "rec_dismiss",
                Event.user_id == user_id,
            ).all()
            dismisses_on_recs = sum(
                1 for e in dismiss_events
                if str(e.payload_json.get("recommendation_id", "")) in rec_ids_str
            )

            if clicks_on_recs == 0 and dismisses_on_recs > 0:
                logger.info(f"[TriggerEngine] User {user_id} ignored last 2 recs (0 clicks, {dismisses_on_recs} dismisses)")
                return {"should_run_agent": True, "trigger_reason": "ignored_recommendations", "cold_start": False}

        # 5. Staleness (>= 2 hours since last recommendation)
        if not last_rec or last_rec.created_at <= two_hours_ago:
            return {"should_run_agent": True, "trigger_reason": "staleness", "cold_start": False}

        return {"should_run_agent": False, "trigger_reason": "skip", "cold_start": False}
