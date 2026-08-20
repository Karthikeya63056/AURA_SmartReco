"""
Hybrid retrieval orchestrator.
Fuses dense vector search (ChromaDB) with sparse keyword search (BM25)
using Reciprocal Rank Fusion (RRF, k=60).

Pipeline:
  1. Vector search (semantic similarity)
  2. BM25 search (keyword exact match)
  3. RRF fusion (k=60)
  4. Metadata filters (category/level)
  5. Exclusions (dismissed/owned/seen)
  6. Diversity (max N per category)

Degrades gracefully: vector down => BM25-only with degraded=True.
"""
import logging
from typing import Dict, List, Any, Optional, Set

from sqlalchemy.orm import Session

from app.core import bm25
from app.core.vector_store import get_products_collection
from app.core.embeddings import MeshEmbeddingUnavailable
from app.models.product import Product

logger = logging.getLogger(__name__)

# Reciprocal Rank Fusion constant (standard value)
RRF_K = 60


def build_retrieval_query(profile: Dict[str, Any]) -> str:
    """
    Build a text retrieval query from the user's behavioral profile.
    Combines top categories and skills into a search string.
    
    Args:
        profile: Output of signals.build_user_profile()
    
    Returns:
        Query string for vector + BM25 search
    """
    parts = []
    
    # Top categories by score
    category_scores = profile.get("category_scores", {})
    top_categories = sorted(
        category_scores.items(), key=lambda x: x[1], reverse=True
    )[:3]
    parts.extend([cat for cat, score in top_categories if score > 0])
    
    # Top skills by score
    skill_scores = profile.get("skill_scores", {})
    top_skills = sorted(
        skill_scores.items(), key=lambda x: x[1], reverse=True
    )[:5]
    parts.extend([skill for skill, score in top_skills if score > 0])
    
    query = " ".join(parts)
    return query if query else "popular courses"


def _vector_search(query: str, k: int) -> List[Dict[str, Any]]:
    """
    Dense vector search via ChromaDB.
    
    Returns:
        List of {product_id, rank, similarity} dicts, or [] on failure.
    
    Raises:
        MeshEmbeddingUnavailable: If embeddings are blocked (402).
        Exception: Any other vector search error.
    """
    collection = get_products_collection()
    results = collection.query(query_texts=[query], n_results=k)
    
    ids = results.get("ids", [[]])[0]
    distances = results.get("distances", [[]])[0]
    
    candidates = []
    for rank, (pid, dist) in enumerate(zip(ids, distances)):
        # Cosine distance -> similarity (clamp to [0,1])
        similarity = max(0.0, 1.0 - float(dist))
        candidates.append({
            "product_id": int(pid),
            "rank": rank,
            "similarity": similarity,
        })
    return candidates


def _bm25_search(query: str, k: int) -> List[Dict[str, Any]]:
    """
    Sparse keyword search via BM25.
    
    Returns:
        List of {product_id, rank, score} dicts.
    """
    results = bm25.search(query, k=k)
    return [
        {"product_id": pid, "rank": rank, "score": score}
        for rank, (pid, score) in enumerate(results)
    ]


def _rrf_fuse(
    vector_results: List[Dict[str, Any]],
    bm25_results: List[Dict[str, Any]],
) -> Dict[int, float]:
    """
    Reciprocal Rank Fusion (RRF, k=60).
    Combines two ranked lists into a single fused score.
    
    RRF_score(pid) = sum over lists of 1 / (RRF_K + rank + 1)
    
    Returns:
        {product_id: fused_score}
    """
    fused: Dict[int, float] = {}
    
    for cand in vector_results:
        pid = cand["product_id"]
        fused[pid] = fused.get(pid, 0.0) + 1.0 / (RRF_K + cand["rank"] + 1)
    
    for cand in bm25_results:
        pid = cand["product_id"]
        fused[pid] = fused.get(pid, 0.0) + 1.0 / (RRF_K + cand["rank"] + 1)
    
    return fused


def hybrid_retrieve(
    db: Session,
    query: str,
    profile: Optional[Dict[str, Any]] = None,
    k: int = 20,
    category: Optional[str] = None,
    level: Optional[str] = None,
    exclude_ids: Optional[Set[int]] = None,
    max_per_category: int = 2,
) -> Dict[str, Any]:
    """
    Run hybrid retrieval: vector + BM25 + RRF fusion + filters + diversity.
    
    Args:
        db: Database session
        query: Retrieval query string
        profile: Optional behavioral profile (for exclusions)
        k: Number of candidates to retrieve per leg
        category: Optional category filter
        level: Optional level filter
        exclude_ids: Optional set of product IDs to exclude
        max_per_category: Diversity cap (max results per category)
    
    Returns:
        {
          "candidates": [ {product_id, fused_score, similarity, bm25_score,
                           title, category, level} ... ] sorted by fused_score,
          "degraded": bool,
          "semantic_status": "SEMANTIC" | "DEGRADED",
        }
    """
    profile = profile or {}
    exclude_ids = set(exclude_ids or [])
    
    # Merge profile exclusions (dismissed/owned)
    excluded_from_profile = set(profile.get("excluded_product_ids", []))
    exclude_ids |= excluded_from_profile
    
    # --- Leg 1: Vector search (may degrade) ---
    degraded = False
    vector_results: List[Dict[str, Any]] = []
    try:
        vector_results = _vector_search(query, k)
    except (MeshEmbeddingUnavailable, Exception) as e:
        logger.warning(f"[Retrieval] Vector search unavailable, BM25-only: {e}")
        degraded = True
    
    # --- Leg 2: BM25 search (always available) ---
    bm25_results = _bm25_search(query, k)
    
    # --- Fuse with RRF ---
    fused = _rrf_fuse(vector_results, bm25_results)
    
    # Build lookup maps for breakdown
    vector_by_id = {c["product_id"]: c.get("similarity", 0.0) for c in vector_results}
    bm25_by_id = {c["product_id"]: c.get("score", 0.0) for c in bm25_results}
    
    # --- Load product metadata for filters + diversity ---
    candidate_ids = list(fused.keys())
    if not candidate_ids:
        return {"candidates": [], "degraded": degraded,
                "semantic_status": "DEGRADED" if degraded else "SEMANTIC"}
    
    products = (
        db.query(Product)
        .filter(Product.id.in_(candidate_ids))
        .all()
    )
    products_by_id = {p.id: p for p in products}
    
    # --- Apply filters + exclusions, then sort by fused score ---
    scored = []
    for pid, fused_score in fused.items():
        if pid in exclude_ids:
            continue
        product = products_by_id.get(pid)
        if product is None:
            continue
        if category and product.category != category:
            continue
        if level and product.level != level:
            continue
        scored.append({
            "product_id": pid,
            "fused_score": fused_score,
            "similarity": vector_by_id.get(pid, 0.0),
            "bm25_score": bm25_by_id.get(pid, 0.0),
            "title": product.title,
            "category": product.category,
            "level": product.level,
        })
    
    scored.sort(key=lambda x: x["fused_score"], reverse=True)
    
    # --- Enforce diversity (max N per category) ---
    selected = []
    category_counts: Dict[str, int] = {}
    for cand in scored:
        cat = cand["category"]
        if category_counts.get(cat, 0) >= max_per_category:
            continue
        selected.append(cand)
        category_counts[cat] = category_counts.get(cat, 0) + 1
    
    return {
        "candidates": selected,
        "degraded": degraded,
        "semantic_status": "DEGRADED" if degraded else "SEMANTIC",
    }