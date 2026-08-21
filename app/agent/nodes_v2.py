"""
The 7 node functions of the agent graph.
Only `generate` (and optionally `refine`) call the LLM.
All other nodes are deterministic and testable offline.
"""
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from app.core.database import SessionLocal
from app.core import mesh
from app.core.cache import cache
from app.config import settings
from app.models.recommendation import Recommendation
from app.models.agent_run import AgentRun
from app.services.signals import build_user_profile
from app.services.retrieval import hybrid_retrieve, build_retrieval_query
from app.services.ranking import rank_candidates
from app.agent.prompts_v2 import GENERATE_NARRATIVE_PROMPT, REFINE_QUERY_PROMPT

logger = logging.getLogger(__name__)

# Quality thresholds for the grade node
STRONG_SCORE_THRESHOLD = 0.30
MIN_CANDIDATES = 3
TOP_N_FOR_NARRATIVE = 5

# Prohibited-claim patterns the narrative must NOT contain
PROHIBITED_PATTERNS = [
    r"\$\d",                      # invented prices ($X)
    r"\b\d+% off\b",              # discount claims
    r"\bguarantee(d)?\b",         # guarantees
    r"\blimited time\b",          # urgency
    r"\bact now\b",               # urgency
    r"\bonly \d+ (spots|seats)\b",# fake scarcity
    r"\bexpires?\b",              # deadlines
]


def _llm_enabled() -> bool:
    """Read LLM_ENABLED from settings or env (default True)."""
    try:
        from app.config import settings
        val = getattr(settings, "LLM_ENABLED", None)
        if val is not None:
            return bool(val)
    except Exception:
        pass
    return os.environ.get("LLM_ENABLED", "true").lower() in ("1", "true", "yes")


# ============================================================
# Node 1: analyze (deterministic, no LLM)
# ============================================================
def analyze(state: Dict[str, Any]) -> Dict[str, Any]:
    """Build the user's behavioral profile and initial retrieval query."""
    user_id = state["user_id"]
    with SessionLocal() as session:
        profile = build_user_profile(session, user_id)
    query = build_retrieval_query(profile)
    return {
        "profile": profile,
        "retrieval_query": query,
    }


# ============================================================
# Node 2: retrieve (deterministic, no LLM)
# ============================================================
def retrieve(state: Dict[str, Any]) -> Dict[str, Any]:
    """Hybrid retrieval + deterministic ranking."""
    user_id = state["user_id"]
    profile = state.get("profile") or {}
    query = state.get("retrieval_query") or build_retrieval_query(profile)

    with SessionLocal() as session:
        result = hybrid_retrieve(session, query, profile, k=20)
        candidates = result["candidates"]
        ranked = rank_candidates(session, candidates, profile, limit=10)

    return {
        "candidates": candidates,
        "ranked_candidates": ranked,
        "retrieval_degraded": result.get("degraded", False),
    }


# ============================================================
# Node 3: grade (deterministic, no LLM)
# ============================================================
def grade(state: Dict[str, Any]) -> Dict[str, Any]:
    """Quality-check the ranked candidates."""
    ranked = state.get("ranked_candidates") or []
    refine_count = state.get("refine_count", 0)

    if not ranked or len(ranked) < MIN_CANDIDATES:
        quality = "weak"
    else:
        top_score = ranked[0].get("final_score", 0.0)
        quality = "strong" if top_score >= STRONG_SCORE_THRESHOLD else "weak"

    # If we've already refined once, don't refine again
    if quality == "weak" and refine_count >= 1:
        quality = "exhausted"

    return {"retrieval_quality": quality}


# ============================================================
# Node 4: refine (optional LLM, deterministic fallback)
# ============================================================
def refine(state: Dict[str, Any]) -> Dict[str, Any]:
    """Broaden the retrieval query. Falls back deterministically on failure."""
    profile = state.get("profile") or {}
    original_query = state.get("retrieval_query") or ""
    new_query = None

    if _llm_enabled():
        try:
            top_categories = sorted(
                profile.get("category_scores", {}).items(),
                key=lambda x: x[1], reverse=True,
            )[:3]
            top_skills = sorted(
                profile.get("skill_scores", {}).items(),
                key=lambda x: x[1], reverse=True,
            )[:5]
            prompt = REFINE_QUERY_PROMPT.format(
                original_query=original_query,
                top_categories=", ".join(c for c, _ in top_categories) or "general",
                top_skills=", ".join(s for s, _ in top_skills) or "general",
            )
            new_query = mesh.chat([{"role": "user", "content": prompt}]).strip()
        except Exception as e:
            logger.warning(f"[refine] LLM query rewrite failed, using fallback: {e}")
            new_query = None

    # Deterministic fallback: use top categories only (broader than full query)
    if not new_query:
        top_categories = sorted(
            profile.get("category_scores", {}).items(),
            key=lambda x: x[1], reverse=True,
        )[:2]
        new_query = " ".join(c for c, _ in top_categories) or "popular courses"

    return {
        "retrieval_query": new_query,
        "refine_count": state.get("refine_count", 0) + 1,
    }


# ============================================================
# Node 5: generate (the ONLY creative LLM call)
# ============================================================
def _build_template_narrative(ranked: List[Dict[str, Any]], profile: Dict[str, Any]) -> str:
    """Deterministic fallback narrative when LLM is unavailable."""
    if not ranked:
        return "Here are some courses you might like."
    titles = [r.get("title", "a course") for r in ranked[:3]]
    return (
        f"Based on your recent activity, we recommend: "
        + ", ".join(titles)
        + ". These match your interests and skill level."
    )


def generate(state: Dict[str, Any]) -> Dict[str, Any]:
    """Write the narrative for the pre-selected shortlist. Math decides; LLM narrates."""
    profile = state.get("profile") or {}
    ranked = state.get("ranked_candidates") or []

    # product_ids come from the deterministic ranking, NOT the LLM
    shortlist = ranked[:TOP_N_FOR_NARRATIVE]
    product_ids = [r["product_id"] for r in shortlist]

    degraded = state.get("retrieval_degraded", False)
    narrative = None
    reasons = [f"Matches your interest in {r.get('category', 'this area')}." for r in shortlist]
    llm_tokens: Optional[int] = None
    llm_cost_usd: Optional[float] = None
    llm_latency_ms: Optional[int] = None

    if _llm_enabled() and shortlist:
        try:
            top_categories = sorted(
                profile.get("category_scores", {}).items(),
                key=lambda x: x[1], reverse=True,
            )[:3]
            top_skills = sorted(
                profile.get("skill_scores", {}).items(),
                key=lambda x: x[1], reverse=True,
            )[:5]
            course_list = "\n".join(
                f"{i+1}. {r.get('title')} ({r.get('category')}, {r.get('level')})"
                for i, r in enumerate(shortlist)
            )
            prompt = GENERATE_NARRATIVE_PROMPT.format(
                top_categories=", ".join(c for c, _ in top_categories) or "general",
                top_skills=", ".join(s for s, _ in top_skills) or "general",
                difficulty=profile.get("difficulty_preference", 1.5),
                course_list=course_list,
            )
            start = time.perf_counter()
            content = mesh.chat([{"role": "user", "content": prompt}])
            latency = int((time.perf_counter() - start) * 1000)

            # Cost capture: prefer real usage stats if the mesh module exposes
            # them (last_usage); otherwise estimate from character counts (~4 chars/token).
            usage = getattr(mesh, "last_usage", None)
            if isinstance(usage, dict) and usage.get("total_tokens"):
                tokens = int(usage["total_tokens"])
            else:
                tokens = len(prompt) // 4 + len(content) // 4
            llm_tokens = tokens
            llm_latency_ms = latency
            llm_cost_usd = tokens / 1000.0 * float(
                getattr(settings, "LLM_COST_PER_1K_TOKENS", 0.002) or 0.0
            )

            # Parse NARRATIVE: and REASONS:
            narrative = content
            m = re.search(r"NARRATIVE:\s*(.+?)(?:REASONS:|$)", content, re.S | re.I)
            if m:
                narrative = m.group(1).strip()
            logger.info(f"[generate] LLM narrative in {latency}ms")
        except Exception as e:
            logger.warning(f"[generate] LLM failed, using template: {e}")
            narrative = None
            degraded = True

    if not narrative:
        narrative = _build_template_narrative(shortlist, profile)
        degraded = True

    return {
        "narrative": narrative,
        "product_ids": product_ids,
        "product_reasons": reasons,
        "retrieval_degraded": degraded,
        "llm_cost_usd": llm_cost_usd,
        "llm_tokens": llm_tokens,
        "llm_latency_ms": llm_latency_ms,
    }


# ============================================================
# Node 6: validate (deterministic, no LLM)
# ============================================================
def validate(state: Dict[str, Any]) -> Dict[str, Any]:
    """Grounding check: prohibited claims + non-empty narrative."""
    narrative = state.get("narrative") or ""
    retry_count = state.get("retry_count", 0)

    problems = []
    if not narrative.strip():
        problems.append("empty narrative")
    for pattern in PROHIBITED_PATTERNS:
        if re.search(pattern, narrative, re.I):
            problems.append(f"prohibited claim: {pattern}")

    # Title grounding: the narrative must mention at least one shortlist title
    # (same TOP_N_FOR_NARRATIVE slice generate() narrates).
    ranked = state.get("ranked_candidates") or []
    shortlist_titles = [
        str(r.get("title") or "")
        for r in ranked[:TOP_N_FOR_NARRATIVE]
    ]
    shortlist_titles = [t for t in shortlist_titles if t]
    if shortlist_titles:
        narrative_lower = narrative.lower()
        mentioned = any(t.lower() in narrative_lower for t in shortlist_titles)
        if not mentioned:
            problems.append("narrative does not mention any recommended course title")

    if problems:
        return {
            "validation_passed": False,
            "retry_count": retry_count + 1,
            "critique_feedback": "; ".join(problems),
        }

    return {
        "validation_passed": True,
        "retry_count": retry_count,
        "critique_feedback": None,
    }


# ============================================================
# Node 7: persist (own session C5, cache delete-then-set)
# ============================================================
def persist(state: Dict[str, Any]) -> Dict[str, Any]:
    """Write recommendation + update agent_run; invalidate cache."""
    user_id = state["user_id"]
    run_id = state["run_id"]
    narrative = state.get("narrative") or ""
    product_ids = state.get("product_ids") or []
    product_reasons = state.get("product_reasons") or []
    ranked = state.get("ranked_candidates") or []
    degraded = state.get("retrieval_degraded", False)

    # Quality score from top ranked score
    top_score = ranked[0].get("final_score", 0.0) if ranked else 0.0
    quality_score = int(min(100, max(0, top_score * 100)))

    try:
        with SessionLocal() as session:
            # Deactivate previous active recommendations so only the newest
            # one is active (mirrors v1's store_node pattern).
            session.query(Recommendation).filter(
                Recommendation.user_id == user_id,
                Recommendation.is_active == True,  # noqa: E712
            ).update({"is_active": False})

            rec = Recommendation(
                user_id=user_id,
                narrative=narrative,
                product_ids_json=product_ids,
                quality_score=quality_score,
                trigger_reason=state.get("trigger_reason", "agent"),
                is_active=True,
            )
            # Set product_reasons if the column exists
            if hasattr(rec, "product_reasons"):
                rec.product_reasons = product_reasons
            session.add(rec)

            run = session.query(AgentRun).filter(AgentRun.id == run_id).first()
            if run:
                run.status = "done"
                run.recommendation_id = None  # set after flush
                run.degraded = degraded
                run.retry_count = state.get("retry_count", 0)
                run.candidate_scores_json = json.dumps(
                    [{k: r.get(k) for k in ("product_id", "final_score", "breakdown")} for r in ranked]
                )
                run.latency_ms = state.get("llm_latency_ms")
                run.cost_usd = state.get("llm_cost_usd")
                run.tokens = state.get("llm_tokens")
                run.model_used = getattr(settings, "DEFAULT_CHAT_MODEL", None)
            session.commit()
            session.refresh(rec)
            if run:
                run.recommendation_id = rec.id
                session.commit()
            recommendation_id = rec.id

        # Cache delete-then-set so next poll sees fresh rec (Rev5 §5.6)
        cache_key = f"active_rec:{user_id}"
        cache.delete(cache_key)
        cache.set(cache_key, {
            "id": recommendation_id,
            "narrative": narrative,
            "product_ids": product_ids,
            "product_reasons": product_reasons,
            "quality_score": quality_score,
        }, 900)

        return {
            "recommendation_id": recommendation_id,
            "quality_score": quality_score,
            "error_message": None,
        }
    except Exception as e:
        logger.error(f"[persist] failed: {e}", exc_info=True)
        # Mark run failed
        try:
            with SessionLocal() as session:
                run = session.query(AgentRun).filter(AgentRun.id == run_id).first()
                if run:
                    run.status = "failed"
                    run.last_error = str(e)
                    session.commit()
        except Exception:
            pass
        return {
            "recommendation_id": None,
            "quality_score": None,
            "error_message": str(e),
        }