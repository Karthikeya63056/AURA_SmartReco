import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session

from app.agent.graph import recommendation_agent
from app.agent.nodes import _build_recurring_pattern_summary
from app.models.event import Event
from app.models.product import Product
from app.models.recommendation import Recommendation
from app.services.trigger_engine import TriggerEngine, compute_behavior_hash
from app.core.cache import cache

try:
    from langsmith import traceable
except ImportError:
    def traceable(name: str = "", run_type: str = "chain"):
        def decorator(func):
            return func
        return decorator

logger = logging.getLogger(__name__)


def _load_products_in_order(db: Session, product_ids: List[int]) -> List[Product]:
    """Fetch recommendation products in one query while retaining stored order."""
    if not product_ids:
        return []
    products = db.query(Product).filter(Product.id.in_(product_ids)).all()
    product_map = {product.id: product for product in products}
    return [product_map[product_id] for product_id in product_ids if product_id in product_map]


def _product_to_dict(product: Product, reason: str = "") -> Dict[str, Any]:
    """Convert a Product ORM object to a plain dict (ORM objects must never be cached)."""
    return {
        "id": product.id,
        "title": product.title,
        "category": product.category,
        "level": product.level,
        "price": product.price,
        "rating": product.rating,
        "description": product.description,
        "skills_taught": product.skills_taught or [],
        "tags": product.tags or [],
        "metadata_json": product.metadata_json or {},
        "reason": reason,
    }


class RecommendationService:
    """Orchestrates trigger checks, agent execution, and recommendation retrieval."""

    @staticmethod
    def should_run(
        db: Session,
        user_id: int,
        session_id: Optional[str] = None,
        manual_force: bool = False
    ) -> Dict[str, Any]:
        """Check if trigger conditions met."""
        return TriggerEngine.evaluate_trigger(db, user_id, session_id, manual_force)

    @staticmethod
    @traceable(name="generate_recommendation", run_type="chain")
    async def generate_and_store(
        db: Session,
        user_id: int,
        trigger_reason: str = "manual"
    ) -> Dict[str, Any]:
        """
        Summarizes recent events, invokes LangGraph agent graph, and returns final recommendation.
        """
        logger.info(f"Triggering LangGraph agent for user {user_id} (Reason: {trigger_reason})")

        # Fetch up to 50 events from the last 7 days
        seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
        recent_events = db.query(Event).filter(
            Event.user_id == user_id,
            Event.created_at >= seven_days_ago
        ).order_by(Event.created_at.desc()).limit(50).all()

        # Fallback: if no events in last 7 days, fetch top 50 recent events
        if not recent_events:
            recent_events = db.query(Event).filter(
                Event.user_id == user_id
            ).order_by(Event.created_at.desc()).limit(50).all()

        events_summary_list = []
        for e in recent_events:
            events_summary_list.append(f"- Event: {e.event_type} | Payload: {json.dumps(e.payload_json)}")

        events_summary_str = "\n".join(events_summary_list) if events_summary_list else "User just arrived on platform."
        recurring_patterns_str = _build_recurring_pattern_summary(recent_events)
        # Match TriggerEngine exactly: hash the newest 20 events, regardless of age.
        behavior_events = db.query(Event).filter(
            Event.user_id == user_id
        ).order_by(Event.created_at.desc()).limit(20).all()
        current_behavior_hash = compute_behavior_hash(behavior_events)

        initial_state = {
            "user_id": user_id,
            "trigger_reason": trigger_reason,
            "current_behavior_hash": current_behavior_hash,
            "events_summary": events_summary_str,
            "recurring_patterns": recurring_patterns_str,
            "user_profile": {},
            "user_skills": [],
            "persuasion_style": "hybrid",
            "search_query": "",
            "candidates": [],
            "quality_score": 0,
            "refetch_count": 0,
            "final_narrative": "",
            "recommended_product_ids": [],
            "product_reasons": [],
            "metadata": {},
            "critique_retry_count": 0,
            "critique_feedback": "",
            "validation_passed": False
        }

        # Execute LangGraph workflow
        final_state = await recommendation_agent.ainvoke(initial_state)

        # Retrieve newly stored recommendation
        rec = db.query(Recommendation).filter(
            Recommendation.user_id == user_id,
            Recommendation.is_active == True
        ).first()

        # Self-correction counters live on the final graph state; surface them
        # under "metadata" so evaluation harnesses can actually measure them
        # (previously always 0 because the key was never returned).
        metadata = dict(final_state.get("metadata") or {})
        metadata.setdefault("refetch_count", final_state.get("refetch_count", 0))
        metadata.setdefault(
            "critique_retry_count", final_state.get("critique_retry_count", 0)
        )

        return {
            "id": rec.id if rec else 0,
            "narrative": final_state.get("final_narrative", ""),
            "product_ids": final_state.get("recommended_product_ids", []),
            "product_reasons": final_state.get("product_reasons", []),
            "quality_score": final_state.get("quality_score", 80),
            "trigger_reason": trigger_reason,
            "metadata": metadata,
        }

    @staticmethod
    def get_active(db: Session, user_id: int) -> Optional[Dict[str, Any]]:
        """
        Fetch active recommendation from cache or DB.
        If user has <3 events (cold start), return popular courses fallback.
        """
        # Try cache
        cached_rec = cache.get(f"active_rec:{user_id}")
        if cached_rec:
            # Attach product dicts with paired reasons
            pids = cached_rec.get("product_ids", [])
            reasons = cached_rec.setdefault("product_reasons", [])
            fetched = _load_products_in_order(db, pids)
            reason_by_product_id = {
                product_id: reasons[index]
                for index, product_id in enumerate(pids)
                if index < len(reasons)
            }
            cached_rec["products"] = [
                _product_to_dict(p, reason_by_product_id.get(p.id, ""))
                for p in fetched
            ]
            return cached_rec

        # Fetch from DB
        rec = db.query(Recommendation).filter(
            Recommendation.user_id == user_id,
            Recommendation.is_active == True
        ).order_by(Recommendation.created_at.desc()).first()

        if rec:
            pids = rec.product_ids_json or []
            reasons = rec.product_reasons or []
            fetched = _load_products_in_order(db, pids)
            reason_by_product_id = {
                product_id: reasons[index]
                for index, product_id in enumerate(pids)
                if index < len(reasons)
            }
            products = [
                _product_to_dict(p, reason_by_product_id.get(p.id, ""))
                for p in fetched
            ]
            result = {
                "id": rec.id,
                "narrative": rec.narrative,
                "product_ids": pids,
                "product_reasons": reasons,
                "products": products,
                "quality_score": rec.quality_score,
                "trigger_reason": rec.trigger_reason,
                "created_at": rec.created_at
            }
            cache.set(f"active_rec:{user_id}", result, ttl_seconds=3600)
            return result

        # Cold-Start Fallback if no recommendation exists yet
        # Deterministic ordering so "popular/trending" is stable across calls
        popular_products = db.query(Product).filter(
            (Product.is_popular == True) | (Product.is_trending == True)
        ).order_by(Product.id.asc()).limit(5).all()

        return {
            "id": 0,
            "narrative": "### Welcome to SmartReco 2026! 🚀\nExplore our most popular and trending courses tailored for tech professionals. As you browse, our AI agent will personalize recommendations specifically for your goals.",
            "product_ids": [p.id for p in popular_products],
            "product_reasons": [],
            "products": [_product_to_dict(p) for p in popular_products],
            "quality_score": 100,
            "trigger_reason": "cold_start_fallback",
            "created_at": None
        }
