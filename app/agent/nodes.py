import json
import re
import logging
from typing import Dict, Any
from sqlalchemy.orm import Session

from app.config import settings
from app.agent.state import AgentState
from app.agent.prompts import BEHAVIOR_ANALYSIS_PROMPT, EVALUATOR_PROMPT, PERSUASIVE_PROMPT
from app.core.llm import generate_chat_completion
from app.services.product_service import search_products_vector, get_product
from app.models.recommendation import Recommendation
from app.models.user_profile import UserProfile
from app.models.product import Product
from app.core.cache import cache
from app.core.database import SessionLocal

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> dict:
    """
    Extract the first valid JSON object from LLM response text.
    
    Handles:
    - Pure JSON responses
    - JSON wrapped in ```json ... ``` markdown fences
    - JSON preceded/followed by explanatory text or thinking blocks
    - Multiple JSON objects (takes the first valid one)
    """
    if not text:
        return {}

    # Strategy 1: Try parsing the whole response directly
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        pass

    # Strategy 2: Extract from markdown code fences
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", stripped, re.DOTALL | re.IGNORECASE)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            pass

    # Strategy 3: Find the first { ... } block using brace matching
    start_idx = stripped.find("{")
    if start_idx != -1:
        depth = 0
        for i in range(start_idx, len(stripped)):
            if stripped[i] == "{":
                depth += 1
            elif stripped[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = stripped[start_idx:i + 1]
                    try:
                        return json.loads(candidate)
                    except (json.JSONDecodeError, ValueError):
                        break

    # Strategy 4: Return empty dict as final fallback
    logger.warning(f"Could not extract JSON from LLM response (first 200 chars): {stripped[:200]}")
    return {}


async def analyze_behavior_node(state: AgentState) -> Dict[str, Any]:
    """Node 1: Analyze user behavior events via Mesh API."""
    logger.info(f"[Node 1] Analyzing behavior for user {state['user_id']} using model {settings.DEFAULT_CHAT_MODEL}")
    
    events_summary = state.get("events_summary", "No recent event history available.")
    prompt = BEHAVIOR_ANALYSIS_PROMPT.format(events_summary=events_summary)

    try:
        response_text = generate_chat_completion(
            model=settings.DEFAULT_CHAT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3
        )
        data = _extract_json(response_text)
        
        user_profile = {
            "interests": data.get("interests", ["Artificial Intelligence", "Software Engineering"]),
            "skill_level": data.get("skill_level", "Intermediate"),
            "intent": data.get("intent", "Upskilling")
        }
        search_query = data.get("search_query", "artificial intelligence machine learning courses")

        return {
            "user_profile": user_profile,
            "search_query": search_query
        }
    except Exception as e:
        logger.error(f"Error in analyze_behavior_node: {str(e)}")
        return {
            "user_profile": {"interests": ["AI"], "skill_level": "Intermediate", "intent": "Upskilling"},
            "search_query": "AI machine learning courses"
        }


async def retrieve_candidates_node(state: AgentState) -> Dict[str, Any]:
    """Node 2: Retrieve course candidates from Chroma vector store using Mesh embeddings and metadata filtering."""
    search_query = state.get("search_query", "AI machine learning")
    refetch_count = state.get("refetch_count", 0)
    user_profile = state.get("user_profile", {})
    
    # Broaden query on refetch
    n_results = 20 if refetch_count > 0 else 15

    # Build metadata filters from user profile (retrieval polish bonus)
    filters_list = []
    skill_level = user_profile.get("skill_level")
    if skill_level and skill_level != "Unknown":
        filters_list.append({"level": skill_level})

    # Category filter on initial pass
    interests = user_profile.get("interests", [])
    if interests and refetch_count == 0:
        filters_list.append({"category": {"$in": interests[:3]}})

    if len(filters_list) == 1:
        where_filter = filters_list[0]
    elif len(filters_list) > 1:
        where_filter = {"$and": filters_list}
    else:
        where_filter = None

    logger.info(f"[Node 2] Vector search for '{search_query}' (n_results={n_results}, refetch_count={refetch_count}, where_filter={where_filter})")

    candidates = search_products_vector(
        query_text=search_query,
        n_results=n_results,
        where_filter=where_filter
    )

    # If filtered search returned 0 candidates, fallback to unfiltered search
    if not candidates and where_filter:
        logger.info("[Node 2] Filtered search returned 0 candidates, falling back to unfiltered search")
        candidates = search_products_vector(query_text=search_query, n_results=n_results)

    return {"candidates": candidates}


async def evaluate_and_rerank_node(state: AgentState) -> Dict[str, Any]:
    """Node 3: Score candidate relevance & rerank via Mesh API."""
    user_profile = state.get("user_profile", {})
    candidates = state.get("candidates", [])
    trigger_reason = state.get("trigger_reason", "standard")

    logger.info(f"[Node 3] Evaluating {len(candidates)} candidate courses using model {settings.DEFAULT_CHAT_MODEL}")

    candidates_summary = []
    for c in candidates:
        meta = c.get("metadata", {})
        candidates_summary.append({
            "id": c.get("id"),
            "title": meta.get("title"),
            "category": meta.get("category"),
            "level": meta.get("level"),
            "description_snippet": c.get("document", "")[:200]
        })

    prompt = EVALUATOR_PROMPT.format(
        interests=", ".join(user_profile.get("interests", [])),
        skill_level=user_profile.get("skill_level", "Intermediate"),
        intent=user_profile.get("intent", "Upskilling"),
        trigger_reason=trigger_reason,
        candidates_json=json.dumps(candidates_summary, indent=2)
    )

    try:
        response_text = generate_chat_completion(
            model=settings.DEFAULT_CHAT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        )
        data = _extract_json(response_text)
        
        quality_score = int(data.get("quality_score", 75))
        top_ids = data.get("top_product_ids", [c["id"] for c in candidates[:3]])
        needs_refetch = bool(data.get("needs_refetch", False))
        reasoning = data.get("reasoning", "")

        metadata = state.get("metadata", {})
        metadata.update({"needs_refetch": needs_refetch, "eval_reasoning": reasoning})

        return {
            "quality_score": quality_score,
            "recommended_product_ids": top_ids[:5],
            "metadata": metadata
        }
    except Exception as e:
        logger.error(f"Error in evaluate_and_rerank_node: {str(e)}")
        fallback_ids = [c["id"] for c in candidates[:3]] if candidates else [1, 2, 3]
        return {
            "quality_score": 70,
            "recommended_product_ids": fallback_ids,
            "metadata": {"needs_refetch": False, "eval_reasoning": "Fallback evaluation"}
        }


async def generate_narrative_node(state: AgentState) -> Dict[str, Any]:
    """Node 4: Write persuasive AIDA narrative via Mesh API."""
    user_profile = state.get("user_profile", {})
    recommended_ids = state.get("recommended_product_ids", [])
    
    logger.info(f"[Node 4] Writing persuasive narrative for products {recommended_ids} using model {settings.MAIN_CHAT_MODEL}")

    db: Session = SessionLocal()
    courses_info = []
    try:
        for pid in recommended_ids:
            p = get_product(db, pid)
            if p:
                courses_info.append(f"- **{p.title}** ({p.level} | {p.category}): {p.description}")
    finally:
        db.close()

    courses_text = "\n".join(courses_info) if courses_info else "Top curated AI courses."

    prompt = PERSUASIVE_PROMPT.format(
        intent=user_profile.get("intent", "Upskilling"),
        skill_level=user_profile.get("skill_level", "Intermediate"),
        interests=", ".join(user_profile.get("interests", [])),
        recommended_courses_text=courses_text
    )

    try:
        narrative = generate_chat_completion(
            model=settings.MAIN_CHAT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7
        )
        return {"final_narrative": narrative}
    except Exception as e:
        logger.error(f"Error in generate_narrative_node: {str(e)}")
        return {
            "final_narrative": "### Discover Your Next Learning Milestone\nBased on your recent activity, we've hand-picked top courses to accelerate your skills."
        }


async def store_node(state: AgentState) -> Dict[str, Any]:
    """Node 5: Persist recommendation to database & update cache."""
    user_id = state["user_id"]
    narrative = state.get("final_narrative", "")
    product_ids = state.get("recommended_product_ids", [])
    quality_score = state.get("quality_score", 80)
    trigger_reason = state.get("trigger_reason", "agent")
    refetch_count = state.get("refetch_count", 0)

    logger.info(f"[Node 5] Persisting recommendation for user {user_id}")

    db: Session = SessionLocal()
    try:
        # Deactivate old recommendations
        db.query(Recommendation).filter(
            Recommendation.user_id == user_id,
            Recommendation.is_active == True
        ).update({"is_active": False})

        rec = Recommendation(
            user_id=user_id,
            narrative=narrative,
            product_ids_json=product_ids,
            quality_score=quality_score,
            trigger_reason=trigger_reason,
            refetch_count=refetch_count,
            is_active=True,
            metadata_json=state.get("metadata", {})
        )
        db.add(rec)

        # Update User Profile
        user_profile = state.get("user_profile", {})
        profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
        if not profile:
            profile = UserProfile(user_id=user_id)
            db.add(profile)

        profile.interests_json = user_profile.get("interests", [])
        profile.skill_level = user_profile.get("skill_level", "Intermediate")
        profile.intent = user_profile.get("intent", "Upskilling")

        db.commit()

        # Invalidate/update cache (1 hour TTL)
        cache.set(f"active_rec:{user_id}", {
            "id": rec.id,
            "narrative": narrative,
            "product_ids": product_ids,
            "quality_score": quality_score,
            "trigger_reason": trigger_reason
        }, ttl_seconds=3600)

        return {"metadata": {**state.get("metadata", {}), "recommendation_id": rec.id}}
    except Exception as e:
        logger.error(f"Error in store_node: {str(e)}")
        db.rollback()
        raise e
    finally:
        db.close()
