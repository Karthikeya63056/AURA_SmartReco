"""
Deterministic ranking service.
Scores retrieved candidates with a weighted formula — no LLM.

Formula (weights sum to 1.0):
  final = 0.40*retrieval + 0.25*interest + 0.15*skill_gap
        + 0.10*difficulty + 0.10*popularity

Every component is explainable via the returned breakdown dict.
"""
import logging
from typing import Dict, List, Any, Optional

from sqlalchemy.orm import Session

from app.models.product import Product

logger = logging.getLogger(__name__)

# Ranking weights (sum to 1.0)
W_RETRIEVAL = 0.40
W_INTEREST = 0.25
W_SKILL_GAP = 0.15
W_DIFFICULTY = 0.10
W_POPULARITY = 0.10

# Difficulty levels mapped to numeric values
DIFFICULTY_MAP = {"Beginner": 1.0, "Intermediate": 2.0, "Advanced": 3.0}


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _retrieval_score(candidates: List[Dict[str, Any]]) -> Dict[int, float]:
    """
    Normalize fused retrieval scores to [0,1] relative to the max in the set.
    
    Returns:
        {product_id: normalized_retrieval_score}
    """
    if not candidates:
        return {}
    max_fused = max((c.get("fused_score", 0.0) for c in candidates), default=0.0)
    if max_fused <= 0:
        return {c["product_id"]: 0.0 for c in candidates}
    return {
        c["product_id"]: _clamp(c.get("fused_score", 0.0) / max_fused)
        for c in candidates
    }


def _interest_fit(category: Optional[str], profile: Dict[str, Any]) -> float:
    """
    How strongly the user is interested in this product's category.
    Normalized against the user's top category score.
    """
    category_scores = profile.get("category_scores", {})
    if not category or category not in category_scores:
        return 0.0
    max_score = max(category_scores.values(), default=0.0)
    if max_score <= 0:
        return 0.0
    return _clamp(category_scores[category] / max_score)


def _skill_gap_fit(skills: List[str], profile: Dict[str, Any]) -> float:
    """
    Overlap between the product's taught skills and the user's skill interests.
    """
    skill_scores = profile.get("skill_scores", {})
    if not skills or not skill_scores:
        return 0.0
    
    matched = 0
    total_weight = 0.0
    max_skill = max(skill_scores.values(), default=0.0)
    if max_skill <= 0:
        return 0.0
    
    for skill in skills:
        if skill in skill_scores and skill_scores[skill] > 0:
            matched += 1
            total_weight += skill_scores[skill] / max_skill
    
    if not skills:
        return 0.0
    # Fraction of the product's skills that match user interest (weighted)
    return _clamp(total_weight / len(skills))


def _difficulty_fit(level: Optional[str], profile: Dict[str, Any]) -> float:
    """
    How well the product's difficulty matches the user's preference (1-3 scale).
    fit = 1 - |preference - level| / 2
    """
    pref = profile.get("difficulty_preference", 1.5)
    level_val = DIFFICULTY_MAP.get(level or "", 1.5)
    return _clamp(1.0 - abs(pref - level_val) / 2.0)


def _popularity_score(product: Product) -> float:
    """
    Composite popularity/quality signal from rating + popular/trending flags.
    """
    rating = (product.rating or 0.0) / 5.0
    popular = 1.0 if product.is_popular else 0.0
    trending = 1.0 if product.is_trending else 0.0
    return _clamp(0.5 * rating + 0.25 * popular + 0.25 * trending)


def rank_candidates(
    db: Session,
    candidates: List[Dict[str, Any]],
    profile: Dict[str, Any],
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """
    Deterministically rank retrieved candidates.
    
    Args:
        db: Database session (to load full product fields)
        candidates: Output of retrieval.hybrid_retrieve()["candidates"]
        profile: Output of signals.build_user_profile()
        limit: Max ranked results to return
    
    Returns:
        List of ranked dicts, each with:
          product_id, final_score, breakdown{retrieval, interest, skill_gap,
          difficulty, popularity}, title, category, level
    """
    if not candidates:
        return []
    
    # Load full product fields for scoring
    ids = [c["product_id"] for c in candidates]
    products = db.query(Product).filter(Product.id.in_(ids)).all()
    products_by_id = {p.id: p for p in products}
    
    retrieval_scores = _retrieval_score(candidates)
    
    ranked = []
    for cand in candidates:
        pid = cand["product_id"]
        product = products_by_id.get(pid)
        if product is None:
            continue
        
        retrieval = retrieval_scores.get(pid, 0.0)
        interest = _interest_fit(product.category, profile)
        skill_gap = _skill_gap_fit(product.skills_taught or [], profile)
        difficulty = _difficulty_fit(product.level, profile)
        popularity = _popularity_score(product)
        
        final = (
            W_RETRIEVAL * retrieval
            + W_INTEREST * interest
            + W_SKILL_GAP * skill_gap
            + W_DIFFICULTY * difficulty
            + W_POPULARITY * popularity
        )
        
        ranked.append({
            "product_id": pid,
            "final_score": _clamp(final),
            "breakdown": {
                "retrieval": round(retrieval, 4),
                "interest": round(interest, 4),
                "skill_gap": round(skill_gap, 4),
                "difficulty": round(difficulty, 4),
                "popularity": round(popularity, 4),
            },
            "title": product.title,
            "category": product.category,
            "level": product.level,
        })
    
    ranked.sort(key=lambda x: x["final_score"], reverse=True)
    return ranked[:limit]