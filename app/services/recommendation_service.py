import json
import logging
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session

from app.agent.graph import recommendation_agent
from app.models.event import Event
from app.models.product import Product
from app.models.recommendation import Recommendation
from app.services.product_service import get_product
from app.services.trigger_engine import TriggerEngine
from app.core.cache import cache

try:
    from langsmith import traceable
except ImportError:
    def traceable(name: str = "", run_type: str = "chain"):
        def decorator(func):
            return func
        return decorator

logger = logging.getLogger(__name__)


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

        # Fetch last 15 events to summarize
        recent_events = db.query(Event).filter(
            Event.user_id == user_id
        ).order_by(Event.created_at.desc()).limit(15).all()

        events_summary_list = []
        for e in recent_events:
            events_summary_list.append(f"- Event: {e.event_type} | Payload: {json.dumps(e.payload_json)}")

        events_summary_str = "\n".join(events_summary_list) if events_summary_list else "User just arrived on platform."

        initial_state = {
            "user_id": user_id,
            "trigger_reason": trigger_reason,
            "events_summary": events_summary_str,
            "user_profile": {},
            "search_query": "",
            "candidates": [],
            "quality_score": 0,
            "refetch_count": 0,
            "final_narrative": "",
            "recommended_product_ids": [],
            "metadata": {}
        }

        # Execute LangGraph workflow
        final_state = await recommendation_agent.ainvoke(initial_state)

        # Retrieve newly stored recommendation
        rec = db.query(Recommendation).filter(
            Recommendation.user_id == user_id,
            Recommendation.is_active == True
        ).first()

        return {
            "id": rec.id if rec else 0,
            "narrative": final_state.get("final_narrative", ""),
            "product_ids": final_state.get("recommended_product_ids", []),
            "quality_score": final_state.get("quality_score", 80),
            "trigger_reason": trigger_reason
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
            # Attach product objects
            pids = cached_rec.get("product_ids", [])
            fetched = [get_product(db, pid) for pid in pids]
            cached_rec["products"] = [p for p in fetched if p]
            return cached_rec

        # Fetch from DB
        rec = db.query(Recommendation).filter(
            Recommendation.user_id == user_id,
            Recommendation.is_active == True
        ).order_by(Recommendation.created_at.desc()).first()

        if rec:
            pids = rec.product_ids_json or []
            fetched = [get_product(db, pid) for pid in pids]
            products = [p for p in fetched if p]
            result = {
                "id": rec.id,
                "narrative": rec.narrative,
                "product_ids": pids,
                "products": products,
                "quality_score": rec.quality_score,
                "trigger_reason": rec.trigger_reason,
                "created_at": rec.created_at
            }
            cache.set(f"active_rec:{user_id}", result, ttl_seconds=3600)
            return result

        # Cold-Start Fallback if no recommendation exists yet
        popular_products = db.query(Product).filter(
            (Product.is_popular == True) | (Product.is_trending == True)
        ).limit(5).all()

        return {
            "id": 0,
            "narrative": "### Welcome to SmartReco 2026! 🚀\nExplore our most popular and trending courses tailored for tech professionals. As you browse, our AI agent will personalize recommendations specifically for your goals.",
            "product_ids": [p.id for p in popular_products],
            "products": popular_products,
            "quality_score": 100,
            "trigger_reason": "cold_start_fallback",
            "created_at": None
        }
