import json
import re
import logging
from typing import Dict, Any
from sqlalchemy.orm import Session

from app.config import settings
from app.agent.state import AgentState
from app.agent.prompts import BEHAVIOR_ANALYSIS_PROMPT, EVALUATOR_PROMPT, PERSUASIVE_PROMPT, QUERY_REWRITE_PROMPT, NARRATIVE_FIX_INSTRUCTION, REASON_GENERATION_PROMPT
from app.core.llm import generate_chat_completion, get_llm_client
from app.services.product_service import search_products_vector, get_product
from app.models.recommendation import Recommendation
from app.models.user_profile import UserProfile
from app.models.product import Product
from app.core.cache import cache
from app.core.database import SessionLocal

logger = logging.getLogger(__name__)


def sanitize_prompt_input(text: str, max_len: int = 500) -> str:
    """
    Truncate and escape curly braces so user-controlled data cannot
    break .format() or inject prompt instructions.
    """
    if text is None:
        return ""
    text = str(text)[:max_len]
    # Escape braces so they become literal characters in the final prompt
    return text.replace("{", "{{").replace("}", "}}")


# Map free-form LLM interest labels → actual Product.category values in seed data.
# Without this, Chroma `$in` filters on raw interests (e.g. "Artificial Intelligence")
# never match catalog categories (e.g. "AI & Agents") and always return 0 hits.
# NOTE: Expand this map in the future as new catalog categories are added to seed data.
# The unfiltered search fallback (in retrieve_candidates_node) covers unmapped interests.
CATEGORY_MAP = {
    "Artificial Intelligence": "AI & Agents",
    "AI": "AI & Agents",
    "Agentic AI": "AI & Agents",
    "Agents": "AI & Agents",
    "LangGraph": "AI & Agents",
    "LangChain": "AI & Agents",
    "Generative AI": "AI & Agents",
    "GenAI": "AI & Agents",
    "Machine Learning": "AI & Machine Learning",
    "Deep Learning": "AI & Machine Learning",
    "Neural Networks": "AI & Machine Learning",
    "Computer Vision": "AI & Vision",
    "Vision": "AI & Vision",
    "NLP": "AI & NLP",
    "Natural Language Processing": "AI & NLP",
    "Recommendation Systems": "Recommendation AI",
    "Recommendations": "Recommendation AI",
    "AI Security": "AI Security",
    "Security": "AI Security",
    "Edge AI": "AI & Edge",
    "Software Engineering": "Web Dev & AI",
    "Web Development": "Web Dev",
    "Full Stack": "Web Dev & AI",
    "Frontend": "Web Dev",
    "Backend": "Backend Dev",
    "Backend Development": "Backend Dev",
    "API Development": "Backend Dev",
    "Python": "Python & Data",
    "Data Science": "Python & Data",
    "Data Engineering": "Data Engineering",
    "Databases": "Database & AI",
    "SQL": "Database & AI",
    "Vector Databases": "Database & AI",
    "MLOps": "MLOps & Cloud",
    "DevOps": "MLOps & Cloud",
    "Cloud Computing": "MLOps & Cloud",
    "Kubernetes": "MLOps & Cloud",
    "Product Management": "Product & AI",
    "Testing": "Python & Testing",
    "Systems Programming": "Python & Systems",
}


def _map_interests_to_categories(interests: list) -> list:
    """Translate LLM interest strings into catalog category names (deduped)."""
    mapped: list = []
    for interest in interests:
        if not interest or not isinstance(interest, str):
            continue
        if interest in CATEGORY_MAP:
            mapped.append(CATEGORY_MAP[interest])
            continue
        # Case-insensitive exact key match
        interest_lower = interest.lower().strip()
        exact = next(
            (v for k, v in CATEGORY_MAP.items() if k.lower() == interest_lower),
            None,
        )
        if exact:
            mapped.append(exact)
            continue
        # Partial bidirectional match
        for key, value in CATEGORY_MAP.items():
            key_lower = key.lower()
            if key_lower in interest_lower or interest_lower in key_lower:
                mapped.append(value)
                break
    # Preserve order while deduping
    return list(dict.fromkeys(mapped))


def _extract_json(text: str) -> dict:
    """
    Extract the first valid JSON object from LLM response text.
    
    Handles:
    - Pure JSON responses
    - JSON wrapped in ```json ... ``` markdown fences
    - JSON preceded/followed by explanatory text or thinking blocks
    - Braces inside JSON string values (e.g. {"reason": "looking for {AI}"})
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

    # Strategy 3: Find the outermost { and } block using find() and rfind()
    # This fixes the bug where a naive brace counter breaks on braces inside strings.
    start_idx = stripped.find("{")
    end_idx = stripped.rfind("}")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        candidate = stripped[start_idx:end_idx + 1]
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            pass

    # Strategy 4: Return empty dict as final fallback
    logger.warning(f"Could not extract JSON from LLM response (first 200 chars): {stripped[:200]}")
    return {}


def _build_recurring_pattern_summary(events: list) -> str:
    """
    Analyzes user event log history (up to 50 events / 7 days) to compute
    recurring behavioral patterns (search terms, product categories, action counts).
    Returns a bulleted summary string.
    """
    if not events:
        return "No distinct recurring patterns detected over recent activity."

    search_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    action_counts: dict[str, int] = {}

    for e in events:
        if hasattr(e, "event_type"):
            e_type = getattr(e, "event_type", "")
            payload = getattr(e, "payload_json", {}) or {}
        elif isinstance(e, dict):
            e_type = e.get("event_type", "")
            payload = e.get("payload_json", {}) or {}
        else:
            continue

        # Count search queries
        if e_type == "search" or "query" in payload or "search_query" in payload:
            q = payload.get("query") or payload.get("search_query") or payload.get("term")
            if q and isinstance(q, str):
                norm_q = q.strip().lower()
                if norm_q:
                    search_counts[norm_q] = search_counts.get(norm_q, 0) + 1

        # Count categories
        cat = payload.get("category") or payload.get("product_category")
        if cat and isinstance(cat, str):
            category_counts[cat] = category_counts.get(cat, 0) + 1

        # Count high-intent actions / event types
        if e_type in {"wishlist", "syllabus_view", "enroll_preview", "click", "course_click", "search", "page_view"}:
            action_counts[e_type] = action_counts.get(e_type, 0) + 1

    lines = []

    # Format search queries >= 2
    for q, count in search_counts.items():
        if count >= 2:
            lines.append(f"- Searched for '{q}' {count} times.")

    # Format category views >= 2
    for cat, count in category_counts.items():
        if count >= 2:
            lines.append(f"- Viewed '{cat}' category {count} times.")

    # Format action counts >= 2
    action_labels = {
        "wishlist": "Saved courses to wishlist",
        "syllabus_view": "Viewed course syllabi",
        "enroll_preview": "Previewed course enrollment",
        "search": "Performed searches",
        "course_click": "Clicked course cards",
        "click": "Clicked course cards",
        "page_view": "Visited platform pages",
    }
    for act, count in action_counts.items():
        if count >= 2 and act != "search":
            label = action_labels.get(act, f"Performed {act}")
            lines.append(f"- {label} {count} times.")

    if not lines:
        return "No distinct recurring patterns detected over recent activity."

    return "\n".join(lines)


async def analyze_behavior_node(state: AgentState) -> Dict[str, Any]:
    """Node 1: Analyze user behavior events via Mesh API."""
    logger.info(f"[Node 1] Analyzing behavior for user {state['user_id']} using model {settings.DEFAULT_CHAT_MODEL}")
    
    events_summary = state.get("events_summary", "No recent event history available.")
    recurring_patterns = state.get("recurring_patterns", "No distinct recurring patterns detected over recent activity.")

    # Sanitize user-controlled data before .format()
    safe_events_summary = sanitize_prompt_input(events_summary, max_len=2000)
    safe_recurring_patterns = sanitize_prompt_input(recurring_patterns, max_len=1000)
    
    prompt = BEHAVIOR_ANALYSIS_PROMPT.format(
        events_summary=safe_events_summary,
        recurring_patterns=safe_recurring_patterns
    )

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

    # Category filter on initial pass — map LLM interests → real product categories
    interests = user_profile.get("interests", [])
    if interests and refetch_count == 0:
        mapped_categories = _map_interests_to_categories(interests)
        if mapped_categories:
            filters_list.append({"category": {"$in": mapped_categories[:3]}})
            logger.info(f"[Node 2] Mapped interests {interests} → categories {mapped_categories[:3]}")
        else:
            logger.info(f"[Node 2] No category mapping for interests {interests}; skipping category filter")

    # Check if drop_filters flag is set (refetch pass)
    drop_filters = state.get("drop_filters", False)
    if drop_filters:
        logger.info("[Node 2] drop_filters is True; skipping metadata filters to broaden candidate pool")
        where_filter = None
    elif len(filters_list) == 1:
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


def _generate_template_reasons(
    product_ids: list[int],
    user_profile: dict,
    search_query: str,
) -> list[str]:
    """Generate deterministic template-based fallback reasons (no LLM call)."""
    templates = [
        "Matched your search for '{query}'",
        "Aligns with your interest in {category}",
        "Appropriate for your {skill_level} level",
        "Highly relevant to your learning goals",
    ]
    skill_level = user_profile.get("skill_level", "Intermediate")
    interests = user_profile.get("interests", [])
    reasons: list[str] = []

    db: Session = SessionLocal()
    try:
        for i, pid in enumerate(product_ids):
            p = get_product(db, pid)
            category = p.category if p else (interests[0] if interests else "technology")
            template = templates[i % len(templates)]
            reason = template.format(
                query=search_query[:40] if search_query else "courses",
                category=category,
                skill_level=skill_level,
            )
            reasons.append(reason)
    finally:
        db.close()
    return reasons


def _generate_reasons_llm(
    product_ids: list[int],
    user_profile: dict,
    search_query: str,
) -> list[str] | None:
    """
    Use a lightweight LLM call to generate per-course reasons.
    Returns None on failure so the caller can fall back to templates.
    """
    db: Session = SessionLocal()
    courses_lines: list[str] = []
    try:
        for pid in product_ids:
            p = get_product(db, pid)
            if p:
                courses_lines.append(f"- {p.title} ({p.category}, {p.level})")
            else:
                courses_lines.append(f"- Course ID {pid}")
    finally:
        db.close()

    prompt = REASON_GENERATION_PROMPT.format(
        interests=sanitize_prompt_input(", ".join(user_profile.get("interests", [])), 300),
        skill_level=sanitize_prompt_input(user_profile.get("skill_level", "Intermediate"), 50),
        intent=sanitize_prompt_input(user_profile.get("intent", "Upskilling"), 80),
        search_query=sanitize_prompt_input(search_query, 200),
        courses_list=sanitize_prompt_input("\n".join(courses_lines), 1500),
    )

    try:
        response_text = generate_chat_completion(
            model=settings.DEFAULT_CHAT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=150,
        )
        reasons = _extract_json(response_text)
        if isinstance(reasons, list) and len(reasons) == len(product_ids):
            return [str(r)[:80] for r in reasons]
        logger.warning(f"[Reasons] LLM returned mismatched count: expected {len(product_ids)}, got {len(reasons) if isinstance(reasons, list) else 'non-list'}")
        return None
    except Exception as e:
        logger.warning(f"[Reasons] LLM reason generation failed: {e}")
        return None


async def evaluate_and_rerank_node(state: AgentState) -> Dict[str, Any]:
    """Node 3: Score candidate relevance & rerank via Mesh API."""
    user_profile = state.get("user_profile", {})
    candidates = state.get("candidates", [])
    trigger_reason = state.get("trigger_reason", "standard")
    search_query = state.get("search_query", "")

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
        interests=sanitize_prompt_input(", ".join(user_profile.get("interests", [])), 300),
        skill_level=sanitize_prompt_input(user_profile.get("skill_level", "Intermediate"), 50),
        intent=sanitize_prompt_input(user_profile.get("intent", "Upskilling"), 80),
        trigger_reason=sanitize_prompt_input(trigger_reason, 50),
        candidates_json=sanitize_prompt_input(json.dumps(candidates_summary, indent=2), 3000)
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

        # Generate per-course "why" reasons (LLM with template fallback)
        selected_ids = top_ids[:5]
        reasons = _generate_reasons_llm(selected_ids, user_profile, search_query)
        if reasons is None:
            reasons = _generate_template_reasons(selected_ids, user_profile, search_query)
            logger.info(f"[Node 3] Using template fallback reasons for {len(selected_ids)} courses")
        else:
            logger.info(f"[Node 3] Generated LLM reasons for {len(selected_ids)} courses")

        return {
            "quality_score": quality_score,
            "recommended_product_ids": selected_ids,
            "product_reasons": reasons,
            "metadata": metadata
        }
    except Exception as e:
        logger.error(f"Error in evaluate_and_rerank_node: {str(e)}")
        fallback_ids = [c["id"] for c in candidates[:3]] if candidates else [1, 2, 3]
        fallback_reasons = _generate_template_reasons(fallback_ids, user_profile, search_query)
        return {
            "quality_score": 70,
            "recommended_product_ids": fallback_ids,
            "product_reasons": fallback_reasons,
            "metadata": {"needs_refetch": False, "eval_reasoning": "Fallback evaluation"}
        }


async def generate_narrative_node(state: AgentState) -> Dict[str, Any]:
    """Node 4: Write persuasive AIDA narrative via Mesh API."""
    user_profile = state.get("user_profile", {})
    recommended_ids = state.get("recommended_product_ids", [])
    
    logger.info(f"[Node 4] Writing persuasive narrative for products {recommended_ids} using model {settings.MAIN_CHAT_MODEL}")

    db: Session = SessionLocal()
    courses_info = []
    product_titles = []
    try:
        for pid in recommended_ids:
            p = get_product(db, pid)
            if p:
                product_titles.append(p.title)
                courses_info.append(f"- **{p.title}** ({p.level} | {p.category}): {p.description}")
    finally:
        db.close()

    courses_text = "\n".join(courses_info) if courses_info else "Top curated AI courses."

    base_prompt = PERSUASIVE_PROMPT.format(
        intent=sanitize_prompt_input(user_profile.get("intent", "Upskilling"), 80),
        skill_level=sanitize_prompt_input(user_profile.get("skill_level", "Intermediate"), 50),
        interests=sanitize_prompt_input(", ".join(user_profile.get("interests", [])), 300),
        recommended_courses_text=sanitize_prompt_input(courses_text, 2000)
    )

    fix_instruction = state.get("critique_feedback", "")
    if fix_instruction:
        titles_str = ", ".join(product_titles[:3]) + ("..." if len(product_titles) > 3 else "")
        fix_text = NARRATIVE_FIX_INSTRUCTION.format(
            feedback=sanitize_prompt_input(fix_instruction, 300),
            titles=sanitize_prompt_input(titles_str, 300)
        )
        full_prompt = fix_text + "\n\n" + base_prompt
        logger.info(f"[Node 4] Regenerating narrative with critique feedback: {fix_instruction}")
    else:
        full_prompt = base_prompt

    try:
        narrative = generate_chat_completion(
            model=settings.MAIN_CHAT_MODEL,
            messages=[{"role": "user", "content": full_prompt}],
            temperature=0.7
        )
        return {
            "final_narrative": narrative,
            "critique_feedback": ""
        }
    except Exception as e:
        logger.error(f"Error in generate_narrative_node: {str(e)}")
        return {
            "final_narrative": "### Discover Your Next Learning Milestone\nBased on your recent activity, we've hand-picked top courses to accelerate your skills.",
            "critique_feedback": ""
        }


def _validate_narrative(narrative: str, product_ids: list[int], db: Session) -> tuple[bool, str]:
    """
    Returns (validation_passed, feedback_message).
    Checks narrative length (120–280 words) and grounding (at least 1 recommended title mentioned).
    """
    if not narrative:
        return False, "Generated narrative is empty."

    # Check length
    word_count = len(narrative.split())
    if word_count < 120:
        return False, f"Narrative is too short ({word_count} words). Minimum 120 words required."
    if word_count > 280:
        return False, f"Narrative is too long ({word_count} words). Maximum 280 words allowed."

    # Check grounding: load product titles for recommended IDs
    products = db.query(Product).filter(Product.id.in_(product_ids)).all() if product_ids else []
    titles = [p.title for p in products]

    narrative_lower = narrative.lower()
    found = [t for t in titles if t.lower() in narrative_lower]
    if not found and titles:
        return False, f"Narrative does not mention any of the recommended courses. Recommended titles: {', '.join(titles[:3])}."

    return True, ""


async def critique_narrative_node(state: AgentState) -> Dict[str, Any]:
    """
    Critique Node: Validates narrative length and grounding.
    If validation fails, sets feedback and increments critique_retry_count.
    """
    narrative = state.get("final_narrative", "")
    product_ids = state.get("recommended_product_ids", [])
    retry_count = state.get("critique_retry_count", 0)

    db: Session = SessionLocal()
    try:
        passed, feedback = _validate_narrative(narrative, product_ids, db)
        if passed:
            logger.info("[Critique Node] Narrative validation PASSED.")
            return {"validation_passed": True, "critique_feedback": ""}
        else:
            new_retry_count = retry_count + 1
            logger.warning(f"[Critique Node] Narrative validation FAILED (retry attempt #{new_retry_count}): {feedback}")
            return {
                "validation_passed": False,
                "critique_retry_count": new_retry_count,
                "critique_feedback": feedback,
            }
    finally:
        db.close()


async def store_node(state: AgentState) -> Dict[str, Any]:
    """Node 5: Persist recommendation to database & update cache."""
    user_id = state["user_id"]
    narrative = state.get("final_narrative", "")
    product_ids = state.get("recommended_product_ids", [])
    quality_score = state.get("quality_score", 80)
    trigger_reason = state.get("trigger_reason", "agent")
    refetch_count = state.get("refetch_count", 0)
    validation_passed = state.get("validation_passed", True)

    if not validation_passed:
        logger.warning(f"[Node 5] Storing narrative for user {user_id} despite failing critique validation (retries exhausted).")

    logger.info(f"[Node 5] Persisting recommendation for user {user_id}")

    db: Session = SessionLocal()
    try:
        # Deactivate old recommendations
        db.query(Recommendation).filter(
            Recommendation.user_id == user_id,
            Recommendation.is_active == True
        ).update({"is_active": False})

        product_reasons = state.get("product_reasons", [])

        rec = Recommendation(
            user_id=user_id,
            narrative=narrative,
            product_ids_json=product_ids,
            product_reasons=product_reasons,
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
            "product_reasons": product_reasons,
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



def _rewrite_query(
    original_query: str,
    interests: list[str],
    skill_level: str,
    intent: str,
    max_attempts: int = 2,
) -> str | None:
    """Call LLM to rewrite the search query for better retrieval."""
    if not original_query:
        return None

    safe_original_query = sanitize_prompt_input(original_query, max_len=300)
    safe_interests = sanitize_prompt_input(", ".join(interests) if interests else "unknown", max_len=300)
    safe_skill_level = sanitize_prompt_input(skill_level, max_len=50)
    safe_intent = sanitize_prompt_input(intent, max_len=80)

    # Build the prompt
    prompt = QUERY_REWRITE_PROMPT.format(
        original_query=safe_original_query,
        interests=safe_interests,
        skill_level=safe_skill_level,
        intent=safe_intent,
    )

    client = get_llm_client()
    for attempt in range(max_attempts):
        try:
            response = client.chat.completions.create(
                model=settings.DEFAULT_CHAT_MODEL,
                messages=[
                    {"role": "system", "content": "You are a helpful search query rewriter."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=30,
            )
            rewritten = response.choices[0].message.content.strip()
            # Sanitize: remove quotes, newlines, and truncate
            rewritten = rewritten.strip('"').strip("'").strip()
            # Limit to 10 words
            words = rewritten.split()
            if len(words) > 10:
                rewritten = " ".join(words[:10])
            if rewritten and len(rewritten) > 3:
                return rewritten
        except Exception as e:
            logger.warning(f"Query rewrite attempt {attempt+1} failed: {e}")
            continue
    return None


async def refetch_broaden_node(state: AgentState) -> Dict[str, Any]:
    """Node handling refetch: attempts query rewrite via LLM and sets drop_filters flag."""
    original_query = state.get("search_query", "")
    user_profile = state.get("user_profile", {}) or {}
    interests = user_profile.get("interests", [])
    skill_level = user_profile.get("skill_level", "intermediate")
    intent = user_profile.get("intent", "learning")

    # Attempt to rewrite the query
    rewritten_query = _rewrite_query(
        original_query=original_query,
        interests=interests,
        skill_level=skill_level,
        intent=intent,
    )

    # If rewrite succeeded, use it; otherwise fallback to original (but still drop filters)
    new_query = rewritten_query if rewritten_query else original_query
    refetch_count = state.get("refetch_count", 0) + 1

    if rewritten_query:
        logger.info(f"[Refetch Loop] Refetch #{refetch_count} triggered. Query rewritten from '{original_query}' to '{new_query}'. Dropping metadata filters.")
    else:
        logger.info(f"[Refetch Loop] Refetch #{refetch_count} triggered. Query rewrite fallback to original query '{original_query}'. Dropping metadata filters.")

    return {
        "refetch_count": refetch_count,
        "drop_filters": True,
        "search_query": new_query,
    }


# Alias for backward compatibility
refetch_node = refetch_broaden_node