"""
Evaluation harness for AURA SmartReco (Rev5 §5.13).

Compares three retrieval systems on pre-declared journeys:
  - popularity:   Top N by rating * is_popular (baseline)
  - semantic:     Pure vector similarity (no BM25, no ranking)
  - aura:         Full hybrid (vector + BM25 + RRF + deterministic ranking)

Usage:
    python scripts/evaluate.py --offline
    python scripts/evaluate.py --offline --k 5
"""
import argparse
import json
import sys
import os
from typing import List, Dict, Tuple
from collections import defaultdict

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.basicConfig(level=logging.WARNING)

from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.models.product import Product
from app.services.retrieval import hybrid_retrieve, build_retrieval_query
from app.services.ranking import rank_candidates
from app.services.signals import build_user_profile
from app.core.vector_store import get_products_collection
from app.core.embeddings import MeshEmbeddingUnavailable


# ============================================================
# Journey Definitions (pre-declared ground truth)
# ============================================================
# Each journey specifies:
#   - profile: user's behavioral profile (category_scores, skill_scores, difficulty_preference)
#   - ground_truth: set of product IDs the user actually wants
#   - description: human-readable label

JOURNEYS = [
    {
        "name": "python_beginner",
        "description": "Beginner interested in Python fundamentals",
        "profile": {
            "category_scores": {"Python & Data": 1.0, "Backend Dev": 0.6},
            "skill_scores": {"Python": 1.0, "SQL": 0.5, "FastAPI": 0.3},
            "difficulty_preference": 1.0,  # Beginner
            "excluded_product_ids": [],
        },
        "ground_truth": [8, 26],  # Python for Data Science, Python Testing
    },
    {
        "name": "ai_advanced",
        "description": "Advanced user interested in AI/ML systems",
        "profile": {
            "category_scores": {"AI & Agents": 1.0, "AI & NLP": 0.8},
            "skill_scores": {"Machine Learning": 1.0, "Transformers": 0.9, "LangGraph": 0.7},
            "difficulty_preference": 3.0,  # Advanced
            "excluded_product_ids": [],
        },
        "ground_truth": [1, 3, 20],  # Agentic AI, GenAI, NLP Transformers
    },
    {
        "name": "backend_intermediate",
        "description": "Intermediate backend developer",
        "profile": {
            "category_scores": {"Backend Dev": 1.0, "Python & Systems": 0.8},
            "skill_scores": {"FastAPI": 1.0, "AsyncIO": 0.8, "Microservices": 0.7},
            "difficulty_preference": 2.0,  # Intermediate
            "excluded_product_ids": [],
        },
        "ground_truth": [11, 17, 5],  # FastAPI, AsyncIO, Full-Stack LLM
    },
    {
        "name": "data_engineering",
        "description": "Data engineering and pipelines focus",
        "profile": {
            "category_scores": {"Data Engineering": 1.0, "Python & Data": 0.6},
            "skill_scores": {"Apache Airflow": 1.0, "Spark": 0.9, "ETL": 0.8},
            "difficulty_preference": 2.5,
            "excluded_product_ids": [],
        },
        "ground_truth": [15],  # Data Engineering Pipelines
    },
    {
        "name": "general_interest",
        "description": "Broad interest across categories",
        "profile": {
            "category_scores": {"AI & Agents": 0.8, "Python & Data": 0.7, "Backend Dev": 0.6},
            "skill_scores": {"Python": 1.0, "Machine Learning": 0.6, "FastAPI": 0.5},
            "difficulty_preference": 1.5,
            "excluded_product_ids": [],
        },
        "ground_truth": [8, 3, 11],  # Python Data Sci, GenAI, FastAPI
    },
]


# ============================================================
# System Implementations
# ============================================================

def popularity_baseline(db: Session, k: int) -> List[int]:
    """Baseline: top N by rating * popularity flag."""
    products = db.query(Product).all()
    scored = []
    for p in products:
        score = (p.rating or 0.0) * (2.0 if p.is_popular else 1.0) * (1.5 if p.is_trending else 1.0)
        scored.append((p.id, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [pid for pid, _ in scored[:k]]


def semantic_only(db: Session, profile: Dict, k: int) -> Tuple[List[int], bool]:
    """Vector-only, no BM25 fusion, no deterministic ranking."""
    try:
        query = build_retrieval_query(profile)
        collection = get_products_collection()
        results = collection.query(query_texts=[query], n_results=k)
        ids = results.get("ids", [[]])[0]
        return [int(pid) for pid in ids], False
    except (MeshEmbeddingUnavailable, Exception) as e:
        return [], True  # degraded


def aura_full(db: Session, profile: Dict, k: int) -> Tuple[List[int], bool]:
    """Full AURA pipeline: hybrid retrieval + deterministic ranking."""
    query = build_retrieval_query(profile)
    result = hybrid_retrieve(db, query, profile, k=k * 2)
    ranked = rank_candidates(db, result["candidates"], profile, limit=k)
    return [r["product_id"] for r in ranked], result.get("degraded", False)


# ============================================================
# Metrics
# ============================================================

def precision_at_k(recommended: List[int], relevant: set, k: int) -> float:
    if not recommended or k == 0:
        return 0.0
    top_k = recommended[:k]
    hits = sum(1 for pid in top_k if pid in relevant)
    return hits / k


def recall_at_k(recommended: List[int], relevant: set, k: int) -> float:
    if not relevant:
        return 0.0
    top_k = recommended[:k]
    hits = sum(1 for pid in top_k if pid in relevant)
    return hits / len(relevant)


def ndcg_at_k(recommended: List[int], relevant: set, k: int) -> float:
    """Normalized Discounted Cumulative Gain @ K."""
    if not relevant:
        return 0.0
    top_k = recommended[:k]
    dcg = sum(1.0 / math.log2(i + 2) for i, pid in enumerate(top_k) if pid in relevant)
    # Ideal: all relevant items at top positions
    ideal_hits = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))
    return dcg / idcg if idcg > 0 else 0.0


def diversity(recommended: List[int], db: Session) -> float:
    """Fraction of distinct categories in the top-K list."""
    if not recommended:
        return 0.0
    products = db.query(Product).filter(Product.id.in_(recommended)).all()
    categories = {p.category for p in products if p.category}
    return len(categories) / max(len(recommended), 1)


import math


# ============================================================
# Main Harness
# ============================================================

def run_evaluation(k: int = 5, verbose: bool = True) -> Dict:
    """Run the full evaluation across all journeys and systems."""
    results = {
        "popularity": {"P": [], "R": [], "NDCG": [], "div": [], "degraded": 0},
        "semantic": {"P": [], "R": [], "NDCG": [], "div": [], "degraded": 0},
        "aura": {"P": [], "R": [], "NDCG": [], "div": [], "degraded": 0},
    }

    with SessionLocal() as db:
        for journey in JOURNEYS:
            name = journey["name"]
            profile = journey["profile"]
            relevant = set(journey["ground_truth"])

            if verbose:
                print(f"\n[{name}] {journey['description']}")
                print(f"  Ground truth: {sorted(relevant)}")

            # Popularity baseline
            pop = popularity_baseline(db, k)
            results["popularity"]["P"].append(precision_at_k(pop, relevant, k))
            results["popularity"]["R"].append(recall_at_k(pop, relevant, k))
            results["popularity"]["NDCG"].append(ndcg_at_k(pop, relevant, k))
            results["popularity"]["div"].append(diversity(pop, db))
            if verbose:
                print(f"  popularity : {pop}")

            # Semantic-only
            sem, sem_deg = semantic_only(db, profile, k)
            if sem_deg:
                results["semantic"]["degraded"] += 1
            results["semantic"]["P"].append(precision_at_k(sem, relevant, k))
            results["semantic"]["R"].append(recall_at_k(sem, relevant, k))
            results["semantic"]["NDCG"].append(ndcg_at_k(sem, relevant, k))
            results["semantic"]["div"].append(diversity(sem, db))
            if verbose:
                print(f"  semantic   : {sem}{' (DEGRADED)' if sem_deg else ''}")

            # AURA full
            aura, aura_deg = aura_full(db, profile, k)
            if aura_deg:
                results["aura"]["degraded"] += 1
            results["aura"]["P"].append(precision_at_k(aura, relevant, k))
            results["aura"]["R"].append(recall_at_k(aura, relevant, k))
            results["aura"]["NDCG"].append(ndcg_at_k(aura, relevant, k))
            results["aura"]["div"].append(diversity(aura, db))
            if verbose:
                print(f"  aura       : {aura}{' (DEGRADED)' if aura_deg else ''}")

    return results


def aggregate(results: Dict) -> Dict:
    """Compute means for each metric across journeys."""
    agg = {}
    for system, metrics in results.items():
        agg[system] = {}
        for metric_name, values in metrics.items():
            if metric_name == "degraded":
                # degraded is an int counter, not a list
                agg[system][metric_name] = values if isinstance(values, int) else 0
            elif isinstance(values, list) and values and isinstance(values[0], (int, float)):
                agg[system][metric_name] = sum(values) / len(values)
            else:
                agg[system][metric_name] = 0
    return agg


def print_table(agg: Dict, k: int) -> None:
    """Pretty-print the results table."""
    print("\n" + "=" * 70)
    print(f"EVALUATION RESULTS @ K={k}")
    print("=" * 70)
    print(f"{'System':<14} {'P@K':>8} {'R@K':>8} {'NDCG@K':>8} {'Diversity':>10} {'Degraded':>10}")
    print("-" * 70)
    for system in ["popularity", "semantic", "aura"]:
        d = agg[system]
        print(
            f"{system:<14} "
            f"{d['P']:>8.3f} "
            f"{d['R']:>8.3f} "
            f"{d['NDCG']:>8.3f} "
            f"{d['div']:>10.3f} "
            f"{int(d['degraded']):>10}"
        )
    print("-" * 70)

    # Improvement over popularity baseline
    pop_p = agg["popularity"]["P"]
    pop_r = agg["popularity"]["R"]
    pop_n = agg["popularity"]["NDCG"]
    aura_p = agg["aura"]["P"]
    aura_r = agg["aura"]["R"]
    aura_n = agg["aura"]["NDCG"]

    print(f"\nImprovement of AURA vs popularity baseline:")
    def pct(a, b):
        if b == 0:
            return "+∞" if a > 0 else "0"
        return f"{((a - b) / b) * 100:+.1f}%"

    print(f"  Precision : {pct(aura_p, pop_p)} ({pop_p:.3f} → {aura_p:.3f})")
    print(f"  Recall    : {pct(aura_r, pop_r)} ({pop_r:.3f} → {aura_r:.3f})")
    print(f"  NDCG      : {pct(aura_n, pop_n)} ({pop_n:.3f} → {aura_n:.3f})")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="AURA SmartReco evaluation harness")
    parser.add_argument("--offline", action="store_true", required=True,
                        help="Run in offline mode (required)")
    parser.add_argument("--k", type=int, default=5, help="K for @K metrics (default 5)")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of table")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-journey output")
    args = parser.parse_args()

    print(f"AURA SmartReco Evaluation (offline mode, K={args.k})")
    print(f"Journeys: {len(JOURNEYS)}")

    results = run_evaluation(k=args.k, verbose=not args.quiet)
    agg = aggregate(results)

    if args.json:
        print(json.dumps(agg, indent=2))
    else:
        print_table(agg, args.k)


if __name__ == "__main__":
    main()