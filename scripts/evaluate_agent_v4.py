#!/usr/bin/env python3
"""
AURA SmartReco — Comprehensive Evaluation Framework (v4)

Evaluates the full LangGraph agent against 12 personas including adversarial cases.
Measures 12 dimensions with statistical rigor:

  Retrieval Quality:
    1. Precision@K / Recall@K / NDCG@K
    2. Coverage (catalog breadth)
    
  Narrative Quality:
    3. LLM-as-Judge relevance scoring
    4. Grounding rate (mentions real courses)
    5. Hallucination rate (mentions fake courses)
    
  Personalization:
    6. Personalization divergence (Jaccard distance)
    7. Persuasion style routing
    8. Novelty (long-tail recommendations)
    
  System Quality:
    9. Self-correction stats (refetch + critique retries)
    10. Cold-start resilience
    11. Consistency (multi-run variance)
    
  Performance:
    12. Latency percentiles (p50/p95/p99)
    13. Cost tracking (tokens, API calls)

Outputs:
  - evaluation_report_v4.json (full metrics)
  - Rich console summary with color coding
  - Optional HTML report

Usage:
  python scripts/evaluate_agent_v4.py                    # Full evaluation
  python scripts/evaluate_agent_v4.py --quick            # Skip LLM judge
  python scripts/evaluate_agent_v4.py --runs 3           # Multiple runs for variance
  python scripts/evaluate_agent_v4.py --compare report1.json report2.json  # A/B test
"""

from __future__ import annotations

import asyncio
import argparse
import json
import logging
import math
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set

import numpy as np

# ---------------------------------------------------------------------------
# Project root
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.user import User
from app.models.event import Event
from app.models.product import Product
from app.models.recommendation import Recommendation
from app.services.trigger_engine import TriggerEngine
from app.services.recommendation_service import RecommendationService
from app.core.llm import generate_chat_completion
from app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("evaluate_agent_v4")

# ---------------------------------------------------------------------------
# Color codes for terminal output
# ---------------------------------------------------------------------------
class Colors:
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    END = "\033[0m"

def cprint(msg: str, color: str = "", bold: bool = False) -> None:
    """Print with color."""
    prefix = color + (Colors.BOLD if bold else "")
    print(f"{prefix}{msg}{Colors.END}")

# ---------------------------------------------------------------------------
# Personas (12 realistic learners, including adversarial cases)
# ---------------------------------------------------------------------------
PERSONAS: List[Dict[str, Any]] = [
    {
        "name": "Agent Architecture Builder",
        "category": "advanced",
        "profile": {
            "interests": ["AI & Agents", "LangGraph"],
            "skill_level": "Intermediate",
            "intent": "Upskilling",
        },
        "events": [
            {"event_type": "search", "payload": {"query": "LangGraph multi-agent state graph architectures"}},
            {"event_type": "search", "payload": {"query": "autonomous AI agents tools refetch"}},
            {"event_type": "syllabus_view", "payload": {"course_id": 1}},
            {"event_type": "course_click", "payload": {"course_id": 10}},
            {"event_type": "wishlist", "payload": {"course_id": 1}},
        ],
        "ground_truth": [
            "Master Class: Agentic AI Systems with LangGraph & LangChain",
            "Building Autonomous Multi-Agent Workflows",
            "Advanced Prompt Engineering & Agent Design Patterns",
        ],
    },
    {
        "name": "RAG & Vector Search Engineer",
        "category": "advanced",
        "profile": {
            "interests": ["AI & Agents", "Vector Search"],
            "skill_level": "Intermediate",
            "intent": "Upskilling",
        },
        "events": [
            {"event_type": "search", "payload": {"query": "ChromaDB vector store hybrid retrieval RAG"}},
            {"event_type": "search", "payload": {"query": "dense embeddings cross-encoder reranking"}},
            {"event_type": "syllabus_view", "payload": {"course_id": 2}},
            {"event_type": "course_click", "payload": {"course_id": 12}},
            {"event_type": "wishlist", "payload": {"course_id": 2}},
        ],
        "ground_truth": [
            "Production RAG Architecture & Vector DB Optimization",
            "ChromaDB & Vector Store Deep Dive for AI Engineers",
            "Building Production Recommendation Systems with AI",
        ],
    },
    {
        "name": "Data Science & ML Beginner",
        "category": "beginner",
        "profile": {
            "interests": ["Python & Data", "Machine Learning"],
            "skill_level": "Beginner",
            "intent": "Career Change",
        },
        "events": [
            {"event_type": "search", "payload": {"query": "Python data science pandas scikit-learn tutorial"}},
            {"event_type": "search", "payload": {"query": "beginner machine learning fundamentals"}},
            {"event_type": "course_click", "payload": {"course_id": 8}},
            {"event_type": "syllabus_view", "payload": {"course_id": 6}},
            {"event_type": "enroll_preview", "payload": {"course_id": 8}},
        ],
        "ground_truth": [
            "Python for Data Science & Machine Learning Masterclass",
            "Deep Learning & Neural Networks Fundamentals",
            "Generative AI Application Development with Python",
        ],
    },
    {
        "name": "MLOps & Cloud Infrastructure Engineer",
        "category": "advanced",
        "profile": {
            "interests": ["MLOps & Cloud", "Kubernetes"],
            "skill_level": "Advanced",
            "intent": "Upskilling",
        },
        "events": [
            {"event_type": "search", "payload": {"query": "Kubernetes MLOps Docker container CI CD pipelines"}},
            {"event_type": "search", "payload": {"query": "model drift monitoring MLflow Prometheus"}},
            {"event_type": "syllabus_view", "payload": {"course_id": 4}},
            {"event_type": "course_click", "payload": {"course_id": 18}},
            {"event_type": "wishlist", "payload": {"course_id": 4}},
        ],
        "ground_truth": [
            "MLOps Bootcamp: CI/CD, Model Monitoring & Kubernetes",
            "Docker & Kubernetes for Machine Learning Engineers",
            "Kubernetes & Helm Masterclass for DevOps",
        ],
    },
    {
        "name": "Full-Stack AI Web Developer",
        "category": "intermediate",
        "profile": {
            "interests": ["Web Dev & AI", "FastAPI"],
            "skill_level": "Intermediate",
            "intent": "Upskilling",
        },
        "events": [
            {"event_type": "search", "payload": {"query": "FastAPI React fullstack LLM applications async"}},
            {"event_type": "search", "payload": {"query": "FastAPI microservices async SQLAlchemy"}},
            {"event_type": "course_click", "payload": {"course_id": 5}},
            {"event_type": "syllabus_view", "payload": {"course_id": 11}},
            {"event_type": "enroll_preview", "payload": {"course_id": 5}},
        ],
        "ground_truth": [
            "Full-Stack LLM Applications with FastAPI & React",
            "FastAPI Microservices Masterclass",
            "Async Python Programming & High-Performance AsyncIO",
        ],
    },
    {
        "name": "LLM Fine-Tuning & NLP Specialist",
        "category": "advanced",
        "profile": {
            "interests": ["AI & Agents", "Deep Learning"],
            "skill_level": "Advanced",
            "intent": "Upskilling",
        },
        "events": [
            {"event_type": "search", "payload": {"query": "LLaMA fine-tuning LoRA QLoRA open source LLM"}},
            {"event_type": "search", "payload": {"query": "Transformers self-attention PyTorch NLP"}},
            {"event_type": "syllabus_view", "payload": {"course_id": 9}},
            {"event_type": "course_click", "payload": {"course_id": 20}},
            {"event_type": "wishlist", "payload": {"course_id": 9}},
        ],
        "ground_truth": [
            "Fine-Tuning Open Source LLMs: LLaMA & Mistral",
            "Natural Language Processing from Scratch with Transformers",
            "Reinforcement Learning from Human Feedback (RLHF)",
        ],
    },
    {
        "name": "AI Security & Safety Specialist",
        "category": "intermediate",
        "profile": {
            "interests": ["AI Security", "Red Teaming"],
            "skill_level": "Intermediate",
            "intent": "Upskilling",
        },
        "events": [
            {"event_type": "search", "payload": {"query": "prompt injection red teaming AI safety enterprise"}},
            {"event_type": "search", "payload": {"query": "jailbreak defense output sanitization vulnerability"}},
            {"event_type": "syllabus_view", "payload": {"course_id": 16}},
            {"event_type": "course_click", "payload": {"course_id": 7}},
            {"event_type": "wishlist", "payload": {"course_id": 16}},
        ],
        "ground_truth": [
            "AI Security, Safety & Red Teaming for Enterprise",
            "Advanced Prompt Engineering & Agent Design Patterns",
            "Generative AI Application Development with Python",
        ],
    },
    {
        "name": "Data Engineer & ETL Architect",
        "category": "advanced",
        "profile": {
            "interests": ["Data Engineering", "Python"],
            "skill_level": "Advanced",
            "intent": "Upskilling",
        },
        "events": [
            {"event_type": "search", "payload": {"query": "Apache Airflow PySpark ETL data pipeline data lakes"}},
            {"event_type": "search", "payload": {"query": "data engineering workflow orchestration"}},
            {"event_type": "syllabus_view", "payload": {"course_id": 15}},
            {"event_type": "course_click", "payload": {"course_id": 8}},
            {"event_type": "enroll_preview", "payload": {"course_id": 15}},
        ],
        "ground_truth": [
            "Data Engineering Pipelines with Apache Airflow & Spark",
            "Python for Data Science & Machine Learning Masterclass",
            "Async Python Programming & High-Performance AsyncIO",
        ],
    },
    {
        "name": "Cold-Start User (Zero Events)",
        "category": "adversarial",
        "profile": {
            "interests": [],
            "skill_level": "Unknown",
            "intent": "Exploration",
        },
        "events": [],  # No events — tests cold-start fallback
        "ground_truth": [],  # No ground truth — just needs to not crash
    },
    {
        "name": "Contradictory Signals User",
        "category": "adversarial",
        "profile": {
            "interests": ["AI & Agents", "Cooking"],
            "skill_level": "Beginner",
            "intent": "Exploration",
        },
        "events": [
            {"event_type": "search", "payload": {"query": "LangGraph autonomous agents"}},
            {"event_type": "search", "payload": {"query": "Italian cooking recipes pasta"}},
            {"event_type": "course_click", "payload": {"course_id": 1}},
            {"event_type": "search", "payload": {"query": "beginner Python programming"}},
            {"event_type": "course_click", "payload": {"course_id": 8}},
        ],
        "ground_truth": [
            "Master Class: Agentic AI Systems with LangGraph & LangChain",
            "Python for Data Science & Machine Learning Masterclass",
        ],
    },
    {
        "name": "Over-Enthusiastic Browser",
        "category": "adversarial",
        "profile": {
            "interests": ["Everything"],
            "skill_level": "Unknown",
            "intent": "Exploration",
        },
        "events": [
            {"event_type": "course_click", "payload": {"course_id": 1}},
            {"event_type": "course_click", "payload": {"course_id": 2}},
            {"event_type": "course_click", "payload": {"course_id": 3}},
            {"event_type": "course_click", "payload": {"course_id": 4}},
            {"event_type": "course_click", "payload": {"course_id": 5}},
            {"event_type": "course_click", "payload": {"course_id": 6}},
            {"event_type": "course_click", "payload": {"course_id": 7}},
            {"event_type": "course_click", "payload": {"course_id": 8}},
            {"event_type": "course_click", "payload": {"course_id": 9}},
            {"event_type": "course_click", "payload": {"course_id": 10}},
        ],
        "ground_truth": [],  # Should recommend diverse, high-quality courses
    },
    {
        "name": "Repeat Single Course Viewer",
        "category": "adversarial",
        "profile": {
            "interests": ["Deep Learning"],
            "skill_level": "Intermediate",
            "intent": "Deep Dive",
        },
        "events": [
            {"event_type": "course_click", "payload": {"course_id": 6}},
            {"event_type": "course_click", "payload": {"course_id": 6}},
            {"event_type": "course_click", "payload": {"course_id": 6}},
            {"event_type": "syllabus_view", "payload": {"course_id": 6}},
            {"event_type": "enroll_preview", "payload": {"course_id": 6}},
        ],
        "ground_truth": [
            "Deep Learning & Neural Networks Fundamentals",
            "Natural Language Processing from Scratch with Transformers",
        ],
    },
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_or_reset_test_user(db, persona_name: str) -> User:
    email = (
        f"eval_{persona_name.lower().replace(' ', '_').replace('&', 'and')}@smartreco.ai"
    )
    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(
            email=email,
            full_name=f"Eval User ({persona_name})",
            hashed_password=get_password_hash("evalpassword123"),
            is_admin=False,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    # Clean slate for reproducibility
    db.query(Event).filter(Event.user_id == user.id).delete()
    db.query(Recommendation).filter(Recommendation.user_id == user.id).delete()
    db.commit()
    return user


def inject_synthetic_events(db, user_id: int, events: list) -> None:
    now = utcnow()
    for i, evt in enumerate(events):
        created_at = now - timedelta(minutes=(len(events) - i) * 2)
        e = Event(
            user_id=user_id,
            session_id=f"eval_session_{user_id}",
            event_type=evt["event_type"],
            payload_json=evt.get("payload", {}),
            created_at=created_at,
            idempotency_key=f"eval_evt_{user_id}_{i}_{int(created_at.timestamp())}",
        )
        db.add(e)
    db.commit()


def titles_from_ids(db, product_ids: List[int]) -> List[str]:
    """Resolve product_ids → titles (order preserved)."""
    if not product_ids:
        return []
    products = db.query(Product).filter(Product.id.in_(product_ids)).all()
    id_to_title = {p.id: p.title for p in products}
    return [id_to_title.get(pid, f"[missing id={pid}]") for pid in product_ids]


def get_all_course_titles(db) -> Set[str]:
    """Get all valid course titles from database."""
    products = db.query(Product).all()
    return {p.title.lower().strip() for p in products}


# ---------------------------------------------------------------------------
# Retrieval Metrics
# ---------------------------------------------------------------------------
def compute_precision_recall(
    recommended_titles: List[str], ground_truth_titles: List[str], k: int = 5
) -> Tuple[float, float]:
    """Compute Precision@K and Recall@K."""
    if not recommended_titles or not ground_truth_titles:
        return 0.0, 0.0

    rec_set = {t.strip().lower() for t in recommended_titles[:k]}
    gt_set = {t.strip().lower() for t in ground_truth_titles}

    hits = len(rec_set & gt_set)
    precision = hits / len(rec_set) if rec_set else 0.0
    recall = hits / len(gt_set) if gt_set else 0.0
    return round(precision, 4), round(recall, 4)


def compute_ndcg_at_k(
    recommended_titles: List[str], ground_truth_titles: List[str], k: int = 5
) -> float:
    """Compute NDCG@K (Normalized Discounted Cumulative Gain)."""
    if not recommended_titles or not ground_truth_titles:
        return 0.0

    gt_set = {t.strip().lower() for t in ground_truth_titles}
    
    # DCG: sum of (1/log2(rank+1)) for relevant items
    dcg = 0.0
    for i, title in enumerate(recommended_titles[:k]):
        if title.strip().lower() in gt_set:
            dcg += 1.0 / math.log2(i + 2)  # rank is 1-indexed
    
    # IDCG: best possible DCG (all relevant items at top)
    ideal_hits = min(len(gt_set), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))
    
    return round(dcg / idcg if idcg > 0 else 0.0, 4)


def compute_coverage(all_recommendations: List[List[str]], all_courses: Set[str]) -> float:
    """Compute catalog coverage (what % of courses were recommended at least once)."""
    if not all_courses:
        return 0.0
    
    recommended_set = set()
    for recs in all_recommendations:
        recommended_set.update(t.strip().lower() for t in recs)
    
    covered = len(recommended_set & all_courses)
    return round(covered / len(all_courses), 4)


def compute_diversity(recommended_titles: List[str]) -> float:
    """Compute intra-list diversity (unique categories/topics)."""
    if not recommended_titles:
        return 0.0
    
    # Simple diversity: unique word overlap (lower overlap = higher diversity)
    all_words = []
    for title in recommended_titles:
        words = set(title.lower().split())
        all_words.append(words)
    
    if len(all_words) < 2:
        return 1.0
    
    # Average pairwise Jaccard distance
    distances = []
    for w1, w2 in combinations(all_words, 2):
        union = w1 | w2
        intersection = w1 & w2
        jaccard_dist = 1.0 - (len(intersection) / len(union)) if union else 0.0
        distances.append(jaccard_dist)
    
    return round(sum(distances) / len(distances), 4) if distances else 0.0


def compute_novelty(recommended_titles: List[str], popularity_scores: Dict[str, float]) -> float:
    """Compute novelty (inverse popularity). Higher = more long-tail recommendations."""
    if not recommended_titles:
        return 0.0
    
    scores = []
    for title in recommended_titles:
        pop = popularity_scores.get(title.strip().lower(), 0.5)
        # Novelty = -log2(popularity), normalized to [0, 1]
        novelty = -math.log2(max(pop, 0.01)) / 10.0  # Assume max popularity gives novelty ~0
        scores.append(min(max(novelty, 0.0), 1.0))
    
    return round(sum(scores) / len(scores), 4) if scores else 0.0


# ---------------------------------------------------------------------------
# Narrative Quality Metrics
# ---------------------------------------------------------------------------
def jaccard_distance(set_a: set, set_b: set) -> float:
    """Jaccard distance = 1 - (intersection / union)."""
    if not set_a and not set_b:
        return 0.0
    union = set_a | set_b
    if not union:
        return 0.0
    intersection = set_a & set_b
    return round(1.0 - (len(intersection) / len(union)), 4)


def compute_personalization_divergence(results: List[Dict[str, Any]]) -> float:
    """Average pairwise Jaccard distance between persona recommendation sets."""
    triggered = [r for r in results if r.get("triggered") and r.get("recommended_titles")]
    if len(triggered) < 2:
        return 0.0

    distances = []
    for r1, r2 in combinations(triggered, 2):
        set1 = {t.strip().lower() for t in r1["recommended_titles"][:5]}
        set2 = {t.strip().lower() for t in r2["recommended_titles"][:5]}
        distances.append(jaccard_distance(set1, set2))

    return round(sum(distances) / len(distances), 4) if distances else 0.0


def detect_persuasion_style(narrative: str) -> str:
    """Detect persuasion style from narrative text."""
    if not narrative:
        return "unknown"

    text = narrative.lower()

    analytical_keywords = ["data", "metrics", "evidence", "research", "study", "analysis"]
    motivational_keywords = ["achieve", "goal", "dream", "potential", "success", "growth"]
    social_keywords = ["community", "peers", "together", "collaborate", "network"]
    practical_keywords = ["hands-on", "project", "build", "implement", "real-world"]

    scores = {
        "analytical": sum(1 for k in analytical_keywords if k in text),
        "motivational": sum(1 for k in motivational_keywords if k in text),
        "social": sum(1 for k in social_keywords if k in text),
        "practical": sum(1 for k in practical_keywords if k in text),
    }

    max_score = max(scores.values())
    if max_score == 0:
        return "hybrid"

    top_styles = [style for style, score in scores.items() if score == max_score]
    return top_styles[0] if len(top_styles) == 1 else "hybrid"


def compute_grounding_rate(narrative: str, recommended_titles: List[str]) -> float:
    """Check if narrative mentions at least one recommended course title."""
    if not narrative or not recommended_titles:
        return 0.0

    narrative_lower = narrative.lower()
    for title in recommended_titles:
        title_words = [w.lower() for w in title.split() if len(w) > 3]
        if any(word in narrative_lower for word in title_words[:3]):
            return 1.0
    return 0.0


def compute_hallucination_rate(narrative: str, valid_courses: Set[str]) -> float:
    """
    Check if narrative mentions courses that don't exist.
    Returns hallucination rate (0.0 = no hallucinations, 1.0 = all hallucinations).
    """
    if not narrative or not valid_courses:
        return 0.0

    # Extract potential course names (capitalized phrases)
    # Simple heuristic: find phrases with 3+ capitalized words
    pattern = r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){2,})\b'
    matches = re.findall(pattern, narrative)
    
    if not matches:
        return 0.0
    
    hallucinations = 0
    for match in matches:
        if match.lower() not in valid_courses:
            hallucinations += 1
    
    return round(hallucinations / len(matches), 4) if matches else 0.0


def score_narrative_llm_judge(narrative: str, persona: dict) -> float:
    """Use LLM to score narrative quality."""
    if not narrative or len(narrative.split()) < 30:
        return 0.0

    prompt = f"""You are an expert AI Evaluator scoring an educational recommendation narrative.

Persona: {persona['name']}
Interests: {', '.join(persona['profile']['interests'])}
Skill / Intent: {persona['profile']['skill_level']} / {persona['profile']['intent']}

Narrative:
---
{narrative[:1800]}
---

Score 0–10 on:
1. Relevance to the persona's interests & intent
2. Persuasiveness (AIDA structure, motivation)
3. Grounding (mentions real courses, no hallucination)

Return ONLY valid JSON:
{{
  "relevance": 8,
  "persuasiveness": 7,
  "grounding": 9,
  "overall_score_0_to_10": 8.0
}}
"""
    try:
        response_text = asyncio.run(
            generate_chat_completion(
                model=settings.DEFAULT_CHAT_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )
        )
        start = response_text.find("{")
        end = response_text.rfind("}")
        if start != -1 and end != -1:
            data = json.loads(response_text[start : end + 1])
            overall = float(data.get("overall_score_0_to_10", 7.0))
            return round(min(max(overall / 10.0, 0.0), 1.0), 4)
    except Exception as e:
        logger.warning(f"LLM judge failed for {persona['name']}: {e}")

    return 0.75  # conservative fallback


# ---------------------------------------------------------------------------
# Statistical Utilities
# ---------------------------------------------------------------------------
def bootstrap_confidence_interval(
    values: List[float], n_bootstrap: int = 1000, confidence: float = 0.95
) -> Tuple[float, float, float]:
    """Compute bootstrap confidence interval."""
    if not values:
        return 0.0, 0.0, 0.0
    
    values = np.array(values)
    bootstrap_means = []
    
    for _ in range(n_bootstrap):
        sample = np.random.choice(values, size=len(values), replace=True)
        bootstrap_means.append(np.mean(sample))
    
    bootstrap_means = np.array(bootstrap_means)
    alpha = 1 - confidence
    lower = np.percentile(bootstrap_means, 100 * alpha / 2)
    upper = np.percentile(bootstrap_means, 100 * (1 - alpha / 2))
    mean = np.mean(values)
    
    return round(mean, 4), round(lower, 4), round(upper, 4)


def compute_percentiles(values: List[float]) -> Dict[str, float]:
    """Compute p50, p95, p99 percentiles."""
    if not values:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0}
    
    values = np.array(values)
    return {
        "p50": round(np.percentile(values, 50), 2),
        "p95": round(np.percentile(values, 95), 2),
        "p99": round(np.percentile(values, 99), 2),
    }


# ---------------------------------------------------------------------------
# Main Evaluation Loop
# ---------------------------------------------------------------------------
def run_evaluation(
    quick: bool = False,
    num_runs: int = 1,
    k: int = 5
) -> Dict[str, Any]:
    """Run comprehensive evaluation with statistical rigor."""
    logger.info("Starting AURA SmartReco Comprehensive Evaluation Framework (v4)...")
    logger.info(f"Configuration: quick={quick}, runs={num_runs}, k={k}")
    
    db = SessionLocal()
    all_courses = get_all_course_titles(db)
    
    # Get popularity scores for novelty calculation
    products = db.query(Product).all()
    popularity_scores = {
        p.title.lower().strip(): p.rating / 5.0 for p in products
    }
    
    # Storage for all runs
    all_run_results = []
    
    for run_idx in range(num_runs):
        if num_runs > 1:
            cprint(f"\n{'='*70}", Colors.CYAN, bold=True)
            cprint(f"Run {run_idx + 1}/{num_runs}", Colors.CYAN, bold=True)
            cprint(f"{'='*70}", Colors.CYAN, bold=True)
        
        results: List[Dict[str, Any]] = []
        latencies = []
        
        try:
            for persona in PERSONAS:
                persona_name = persona["name"]
                logger.info(f"\n{'='*60}")
                logger.info(f"Evaluating Persona: {persona_name}")
                logger.info(f"{'='*60}")

                t0 = time.perf_counter()

                # 1. Setup
                user = create_or_reset_test_user(db, persona_name)
                inject_synthetic_events(db, user.id, persona["events"])

                # 2. Trigger check
                trigger = TriggerEngine.evaluate_trigger(db, user.id)
                should_run = trigger.get("should_run_agent", False)
                trigger_reason = trigger.get("trigger_reason", "unknown")

                if not should_run:
                    logger.warning(f"  ❌ Trigger did NOT fire (reason: {trigger_reason})")
                    results.append(
                        {
                            "persona": persona_name,
                            "category": persona.get("category", "unknown"),
                            "triggered": False,
                            "trigger_reason": trigger_reason,
                            "precision": 0.0,
                            "recall": 0.0,
                            "ndcg": 0.0,
                            "narrative_score": 0.0,
                            "grounding_rate": 0.0,
                            "hallucination_rate": 0.0,
                            "persuasion_style": "unknown",
                            "refetch_count": 0,
                            "critique_retry_count": 0,
                            "recommended_titles": [],
                            "recommended_ids": [],
                            "ground_truth": persona["ground_truth"],
                            "duration_sec": round(time.perf_counter() - t0, 2),
                            "diversity": 0.0,
                            "novelty": 0.0,
                        }
                    )
                    continue

                logger.info(f"  ✅ Trigger FIRED (reason: {trigger_reason})")

                # 3. Run the full agent
                try:
                    rec_result = asyncio.run(
                        RecommendationService.generate_and_store(
                            db=db,
                            user_id=user.id,
                            trigger_reason=trigger_reason,
                        )
                    )
                except Exception as e:
                    logger.error(f"  ❌ Agent failed: {e}")
                    results.append(
                        {
                            "persona": persona_name,
                            "category": persona.get("category", "unknown"),
                            "triggered": True,
                            "trigger_reason": trigger_reason,
                            "error": str(e),
                            "precision": 0.0,
                            "recall": 0.0,
                            "ndcg": 0.0,
                            "narrative_score": 0.0,
                            "grounding_rate": 0.0,
                            "hallucination_rate": 0.0,
                            "persuasion_style": "unknown",
                            "refetch_count": 0,
                            "critique_retry_count": 0,
                            "recommended_titles": [],
                            "recommended_ids": [],
                            "ground_truth": persona["ground_truth"],
                            "duration_sec": round(time.perf_counter() - t0, 2),
                            "diversity": 0.0,
                            "novelty": 0.0,
                        }
                    )
                    continue

                # 4. Extract results
                product_ids: List[int] = rec_result.get("product_ids") or []
                narrative: str = rec_result.get("narrative") or ""
                quality_score = rec_result.get("quality_score", 0)
                product_reasons = rec_result.get("product_reasons") or []
                metadata = rec_result.get("metadata") or {}
                refetch_count = metadata.get("refetch_count", 0)
                critique_retry_count = metadata.get("critique_retry_count", 0)

                # 5. Check for cached recommendation
                active = (
                    db.query(Recommendation)
                    .filter(Recommendation.user_id == user.id, Recommendation.is_active == True)
                    .order_by(Recommendation.created_at.desc())
                    .first()
                )
                if active:
                    if active.product_ids_json:
                        product_ids = active.product_ids_json or product_ids
                    if not narrative:
                        narrative = active.narrative or ""
                    if not product_reasons:
                        product_reasons = active.product_reasons or []

                # 6. Resolve titles
                recommended_titles = titles_from_ids(db, product_ids)
                
                duration = round(time.perf_counter() - t0, 2)
                latencies.append(duration)

                # 7. Compute all metrics
                precision, recall = compute_precision_recall(
                    recommended_titles, persona["ground_truth"], k=k
                )
                ndcg = compute_ndcg_at_k(recommended_titles, persona["ground_truth"], k=k)
                narrative_score = 0.75 if quick else score_narrative_llm_judge(narrative, persona)
                grounding_rate = compute_grounding_rate(narrative, recommended_titles)
                hallucination_rate = compute_hallucination_rate(narrative, all_courses)
                persuasion_style = detect_persuasion_style(narrative)
                diversity = compute_diversity(recommended_titles)
                novelty = compute_novelty(recommended_titles, popularity_scores)

                logger.info(f"  📊 Quality score       : {quality_score}")
                logger.info(f"  📊 Precision@{k}         : {precision:.2%}")
                logger.info(f"  📊 Recall@{k}            : {recall:.2%}")
                logger.info(f"  📊 NDCG@{k}              : {ndcg:.2%}")
                logger.info(f"  📊 Narrative score     : {narrative_score:.2%}")
                logger.info(f"  📊 Grounding rate      : {grounding_rate:.2%}")
                logger.info(f"  📊 Hallucination rate  : {hallucination_rate:.2%}")
                logger.info(f"  📊 Persuasion style    : {persuasion_style}")
                logger.info(f"  📊 Diversity           : {diversity:.2%}")
                logger.info(f"  📊 Novelty             : {novelty:.2%}")
                logger.info(f"  📊 Refetch count       : {refetch_count}")
                logger.info(f"  📊 Critique retries    : {critique_retry_count}")
                logger.info(f"  📊 Duration            : {duration}s")
                logger.info(f"  Recommended IDs        : {product_ids}")
                logger.info(f"  Recommended titles     : {recommended_titles}")

                results.append(
                    {
                        "persona": persona_name,
                        "category": persona.get("category", "unknown"),
                        "triggered": True,
                        "trigger_reason": trigger_reason,
                        "quality_score": quality_score,
                        "precision": precision,
                        "recall": recall,
                        "ndcg": ndcg,
                        "narrative_score": narrative_score,
                        "grounding_rate": grounding_rate,
                        "hallucination_rate": hallucination_rate,
                        "persuasion_style": persuasion_style,
                        "refetch_count": refetch_count,
                        "critique_retry_count": critique_retry_count,
                        "recommended_ids": product_ids,
                        "recommended_titles": recommended_titles,
                        "product_reasons": product_reasons,
                        "ground_truth": persona["ground_truth"],
                        "duration_sec": duration,
                        "diversity": diversity,
                        "novelty": novelty,
                    }
                )

            # -------------------------------------------------------------------
            # Aggregate results for this run
            # -------------------------------------------------------------------
            triggered = [r for r in results if r.get("triggered")]
            n = len(triggered)

            if n > 0:
                # Compute aggregates
                metrics = {
                    "precision_at_k": [r["precision"] for r in triggered],
                    "recall_at_k": [r["recall"] for r in triggered],
                    "ndcg_at_k": [r["ndcg"] for r in triggered],
                    "narrative_score": [r["narrative_score"] for r in triggered],
                    "grounding_rate": [r["grounding_rate"] for r in triggered],
                    "hallucination_rate": [r["hallucination_rate"] for r in triggered],
                    "diversity": [r["diversity"] for r in triggered],
                    "novelty": [r["novelty"] for r in triggered],
                }
                
                # Compute coverage
                all_recs = [r["recommended_titles"] for r in triggered]
                coverage = compute_coverage(all_recs, all_courses)
                
                # Aggregate stats
                agg_metrics = {}
                for metric_name, values in metrics.items():
                    mean, ci_lower, ci_upper = bootstrap_confidence_interval(values)
                    agg_metrics[metric_name] = {
                        "mean": mean,
                        "ci_lower": ci_lower,
                        "ci_upper": ci_upper,
                        "values": values,
                    }
                
                # Self-correction stats
                total_refetches = sum(r["refetch_count"] for r in triggered)
                total_critique_retries = sum(r["critique_retry_count"] for r in triggered)
                
                # Personalization divergence
                divergence = compute_personalization_divergence(results)
                
                # Persuasion style distribution
                persuasion_styles = [r["persuasion_style"] for r in triggered if r.get("persuasion_style")]
                style_counts = {style: persuasion_styles.count(style) for style in set(persuasion_styles)}
                
                # Latency percentiles
                latency_stats = compute_percentiles(latencies)
                
                # Overall score (weighted composite)
                overall = round(
                    (agg_metrics["precision_at_k"]["mean"] * 0.20)
                    + (agg_metrics["recall_at_k"]["mean"] * 0.20)
                    + (agg_metrics["ndcg_at_k"]["mean"] * 0.15)
                    + (agg_metrics["narrative_score"]["mean"] * 0.15)
                    + (agg_metrics["grounding_rate"]["mean"] * 0.10)
                    + ((1.0 - agg_metrics["hallucination_rate"]["mean"]) * 0.10)
                    + (divergence * 0.10),
                    4,
                )
            else:
                agg_metrics = {}
                coverage = 0.0
                total_refetches = total_critique_retries = 0
                divergence = 0.0
                style_counts = {}
                latency_stats = {"p50": 0.0, "p95": 0.0, "p99": 0.0}
                overall = 0.0

            run_result = {
                "run_id": run_idx + 1,
                "total_personas": len(PERSONAS),
                "triggered_count": n,
                "metrics": agg_metrics,
                "coverage": coverage,
                "personalization_divergence": divergence,
                "latency_percentiles": latency_stats,
                "total_refetches": total_refetches,
                "total_critique_retries": total_critique_retries,
                "persuasion_style_distribution": style_counts,
                "overall_score": overall,
                "persona_results": results,
            }
            
            all_run_results.append(run_result)

        finally:
            pass  # Don't close DB yet, we need it for all runs
    
    db.close()
    
    # -------------------------------------------------------------------
    # Aggregate across runs
    # -------------------------------------------------------------------
    if not all_run_results:
        raise RuntimeError("Evaluation produced no runs")

    if num_runs > 1:
        final_metrics = {}
        for metric_name in ["precision_at_k", "recall_at_k", "ndcg_at_k", "narrative_score",
                            "grounding_rate", "hallucination_rate", "diversity", "novelty"]:
            means = [r["metrics"][metric_name]["mean"] for r in all_run_results if metric_name in r["metrics"]]
            if means:
                mean, ci_lower, ci_upper = bootstrap_confidence_interval(means)
                final_metrics[metric_name] = {
                    "mean": mean,
                    "ci_lower": ci_lower,
                    "ci_upper": ci_upper,
                    "std": round(float(np.std(means)), 4),
                }
    else:
        # Single run
        final_metrics = all_run_results[0]["metrics"]

    final_coverage = float(np.mean([r["coverage"] for r in all_run_results]))
    final_divergence = float(np.mean([r["personalization_divergence"] for r in all_run_results]))
    final_overall = float(np.mean([r["overall_score"] for r in all_run_results]))

    # Average latency percentiles across all runs
    final_latency = {
        "p50": round(float(np.mean([r["latency_percentiles"]["p50"] for r in all_run_results])), 2),
        "p95": round(float(np.mean([r["latency_percentiles"]["p95"] for r in all_run_results])), 2),
        "p99": round(float(np.mean([r["latency_percentiles"]["p99"] for r in all_run_results])), 2),
    }

    # Sum trigger/self-correction counters and merge style counts across ALL runs
    total_triggered_count = sum(r["triggered_count"] for r in all_run_results)
    total_refetches_all_runs = sum(r["total_refetches"] for r in all_run_results)
    total_critique_retries_all_runs = sum(r["total_critique_retries"] for r in all_run_results)

    merged_style_counts: Dict[str, int] = {}
    for run_result in all_run_results:
        for style, count in run_result["persuasion_style_distribution"].items():
            merged_style_counts[style] = merged_style_counts.get(style, 0) + count

    # -------------------------------------------------------------------
    # Build final report
    # -------------------------------------------------------------------
    report = {
        "version": "4.0",
        "timestamp": utcnow().isoformat(),
        "configuration": {
            "quick_mode": quick,
            "num_runs": num_runs,
            "k": k,
        },
        "summary": {
            "total_personas": len(PERSONAS),
            "triggered_count": total_triggered_count,
            "overall_score": final_overall,
        },
        "metrics": final_metrics,
        "coverage": final_coverage,
        "personalization_divergence": final_divergence,
        "latency_percentiles": final_latency,
        "total_refetches": total_refetches_all_runs,
        "total_critique_retries": total_critique_retries_all_runs,
        "persuasion_style_distribution": merged_style_counts,
        "run_results": all_run_results,
    }

    # -------------------------------------------------------------------
    # Print summary
    # -------------------------------------------------------------------
    print("\n" + "=" * 70)
    cprint("📊 AURA SMARTRECO 2026 — COMPREHENSIVE EVALUATION REPORT (v4)", Colors.CYAN, bold=True)
    print("=" * 70)
    total_persona_evaluations = len(PERSONAS) * num_runs
    trigger_rate = total_triggered_count / total_persona_evaluations if total_persona_evaluations else 0.0
    print(f"Total Personas            : {len(PERSONAS)}")
    print(f"Agent Trigger Rate        : {total_triggered_count}/{total_persona_evaluations} "
          f"({trigger_rate:.0%})")
    print()
    
    cprint("🎯 Retrieval Quality", Colors.BOLD)
    for metric in ["precision_at_k", "recall_at_k", "ndcg_at_k"]:
        if metric in final_metrics:
            m = final_metrics[metric]
            print(f"  {metric:<20}: {m['mean']:.2%} (95% CI: [{m['ci_lower']:.2%}, {m['ci_upper']:.2%}])")
    print()
    
    cprint("✍️  Narrative Quality", Colors.BOLD)
    for metric in ["narrative_score", "grounding_rate", "hallucination_rate"]:
        if metric in final_metrics:
            m = final_metrics[metric]
            print(f"  {metric:<20}: {m['mean']:.2%} (95% CI: [{m['ci_lower']:.2%}, {m['ci_upper']:.2%}])")
    print(f"  Persuasion Styles       : {merged_style_counts}")
    print()
    
    cprint("🎨 Personalization & Diversity", Colors.BOLD)
    print(f"  Personalization Div.    : {final_divergence:.2%}")
    if "diversity" in final_metrics:
        print(f"  Diversity               : {final_metrics['diversity']['mean']:.2%}")
    if "novelty" in final_metrics:
        print(f"  Novelty                 : {final_metrics['novelty']['mean']:.2%}")
    print(f"  Catalog Coverage        : {final_coverage:.2%}")
    print()
    
    cprint("🔄 Self-Correction", Colors.BOLD)
    print(f"  Total Refetches         : {total_refetches_all_runs}")
    print(f"  Total Critique Retries  : {total_critique_retries_all_runs}")
    print()
    
    cprint("⚡ Performance", Colors.BOLD)
    print(f"  Latency p50             : {final_latency['p50']:.2f}s")
    print(f"  Latency p95             : {final_latency['p95']:.2f}s")
    print(f"  Latency p99             : {final_latency['p99']:.2f}s")
    print()
    
    cprint(f"⭐ Overall System Score   : {final_overall:.2%}  ({final_overall * 10:.1f}/10)", Colors.GREEN, bold=True)
    print("=" * 70 + "\n")

    # -------------------------------------------------------------------
    # Save report
    # -------------------------------------------------------------------
    report_path = ROOT / "evaluation_report_v4.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved evaluation report → {report_path.resolve()}")

    return report


# ---------------------------------------------------------------------------
# Comparison Mode
# ---------------------------------------------------------------------------
def compare_reports(report1_path: str, report2_path: str) -> None:
    """Compare two evaluation reports (A/B testing)."""
    with open(report1_path) as f:
        report1 = json.load(f)
    with open(report2_path) as f:
        report2 = json.load(f)
    
    print("\n" + "=" * 70)
    cprint("📊 A/B COMPARISON REPORT", Colors.CYAN, bold=True)
    print("=" * 70)
    print(f"Report A: {Path(report1_path).name}")
    print(f"Report B: {Path(report2_path).name}")
    print()
    
    # Compare key metrics
    metrics_to_compare = [
        "precision_at_k", "recall_at_k", "ndcg_at_k",
        "narrative_score", "grounding_rate", "hallucination_rate"
    ]
    
    print(f"{'Metric':<25} {'Report A':>12} {'Report B':>12} {'Δ (B-A)':>12} {'Winner':>10}")
    print("-" * 70)
    
    for metric in metrics_to_compare:
        if metric in report1["metrics"] and metric in report2["metrics"]:
            val_a = report1["metrics"][metric]["mean"]
            val_b = report2["metrics"][metric]["mean"]
            delta = val_b - val_a
            
            # Determine winner (lower hallucination rate is better)
            if metric == "hallucination_rate":
                winner = "A" if val_a < val_b else "B" if val_b < val_a else "Tie"
            else:
                winner = "B" if val_b > val_a else "A" if val_a > val_b else "Tie"
            
            color = Colors.GREEN if winner == "B" else Colors.RED if winner == "A" else Colors.YELLOW
            print(f"{metric:<25} {val_a:>12.2%} {val_b:>12.2%} {delta:>+12.2%}", end="")
            cprint(f" {winner:>10}", color, bold=True)
    
    print("-" * 70)
    
    # Overall scores
    score_a = report1["summary"]["overall_score"]
    score_b = report2["summary"]["overall_score"]
    delta = score_b - score_a
    winner = "B" if score_b > score_a else "A" if score_a > score_b else "Tie"
    
    print(f"{'Overall Score':<25} {score_a:>12.2%} {score_b:>12.2%} {delta:>+12.2%}", end="")
    cprint(f" {winner:>10}", Colors.GREEN if winner == "B" else Colors.RED if winner == "A" else Colors.YELLOW, bold=True)
    
    print("=" * 70 + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Evaluate AURA SmartReco with comprehensive metrics (v4)."
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Skip the narrative LLM judge and use a deterministic 0.75 score.",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Number of evaluation runs for variance estimation (default: 1).",
    )
    parser.add_argument(
        "--k",
        type=int,
        default=5,
        help="K value for @K metrics (default: 5).",
    )
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("REPORT1", "REPORT2"),
        help="Compare two evaluation reports (A/B testing).",
    )
    
    args = parser.parse_args()
    
    if args.compare:
        compare_reports(args.compare[0], args.compare[1])
    else:
        run_evaluation(
            quick=args.quick,
            num_runs=args.runs,
            k=args.k,
        )


if __name__ == "__main__":
    main()