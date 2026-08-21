"""
Time-decayed signal processing.
Converts raw events into a multi-dimensional user profile with exponential decay.
72-hour half-life: recent high-intent actions dominate over old curiosity.
"""
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.models.event import Event

# Event weights: higher = stronger signal of intent
EVENT_WEIGHTS = {
    "course_view": 0.05,
    "search": 0.10,
    "course_click": 0.08,
    "wishlist": 0.30,
    "wishlist_remove": -0.20,
    "syllabus_view": 0.15,
    "faq_expand": 0.03,
    "rec_click": 0.05,
    "rec_dismiss": -0.40,  # Negative signal
    "not_interested": -0.65,  # Strongest negative signal
    "enroll_preview": 0.35,
    "course_impression": 0.01,
    "instructor_view": 0.08,
    "share": 0.12,
    "time_on_page": 0.0,  # Explicit zero: counted as evidence, not scored
}

# Dwell time bonus: ≥15s on a page scales weight by up to 2x
DWELL_BONUS_THRESHOLD_SECONDS = 15
DWEll_BONUS_THRESHOLD_SECONDS = DWELL_BONUS_THRESHOLD_SECONDS  # backward-compat alias (#84)
DWELL_BONUS_MAX_MULTIPLIER = 2.0

# Exponential decay: 72-hour half-life
HALF_LIFE_HOURS = 72.0


def recency_decay(event_timestamp: datetime, now: Optional[datetime] = None) -> float:
    """
    Exponential decay with 72-hour half-life.
    
    Args:
        event_timestamp: When the event occurred (timezone-aware or naive UTC)
        now: Current time (defaults to now())
    
    Returns:
        Decay factor between 0.0 and 1.0
    """
    if now is None:
        now = datetime.now(timezone.utc)
    
    # Make event_timestamp timezone-aware if it isn't
    if event_timestamp.tzinfo is None:
        event_timestamp = event_timestamp.replace(tzinfo=timezone.utc)
    
    age_hours = (now - event_timestamp).total_seconds() / 3600.0
    if age_hours < 0:
        age_hours = 0
    
    # 2^(-age / half_life)
    return math.pow(2.0, -age_hours / HALF_LIFE_HOURS)


def build_user_profile(
    db: Session,
    user_id: int,
    limit: int = 200,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Build a time-decayed behavioral profile for a user.
    
    Args:
        db: Database session
        user_id: User ID
        limit: Max events to process (most recent)
        now: Current time (for decay calculation)
    
    Returns:
        Dict with:
          - category_scores: {category: weighted_score}
          - skill_scores: {skill: weighted_score}
          - difficulty_preference: weighted average difficulty level
          - budget_estimate: inferred budget from price signals
          - excluded_product_ids: set of dismissed/owned product IDs
          - evidence_event_ids: list of event IDs that contributed
          - profile_hash: stable-JSON sha256 of the profile
    """
    if now is None:
        now = datetime.now(timezone.utc)
    
    # Fetch recent events
    events = (
        db.query(Event)
        .filter(Event.user_id == user_id)
        .order_by(desc(Event.created_at))
        .limit(limit)
        .all()
    )
    
    # Aggregation buckets
    category_scores: Dict[str, float] = defaultdict(float)
    skill_scores: Dict[str, float] = defaultdict(float)
    difficulty_weighted_sum: float = 0.0
    difficulty_weight_sum: float = 0.0
    price_signals: List[float] = []
    excluded_product_ids: set = set()
    evidence_event_ids: List[int] = []
    
    for event in events:
        event_type = event.event_type
        if event_type not in EVENT_WEIGHTS:
            continue
        
        weight = EVENT_WEIGHTS[event_type]
        decay = recency_decay(event.created_at, now)
        
        # Dwell time bonus (if payload has dwell_seconds) — defensive parsing (#9)
        payload = event.payload_json if isinstance(event.payload_json, dict) else {}
        raw_dwell = payload.get("dwell_seconds", 0)
        if isinstance(raw_dwell, (int, float)) and not isinstance(raw_dwell, bool):
            dwell_seconds = raw_dwell
        else:
            try:
                dwell_seconds = float(raw_dwell)
            except (TypeError, ValueError):
                dwell_seconds = 0
        if dwell_seconds >= DWELL_BONUS_THRESHOLD_SECONDS and weight > 0:
            # Scale bonus linearly from 1.0 (at threshold) to max (at 60s+)
            bonus = min(DWELL_BONUS_MAX_MULTIPLIER, 1.0 + (dwell_seconds - DWELL_BONUS_THRESHOLD_SECONDS) / 45.0)
            weight *= bonus
        
        # Apply decay to weight
        decayed_weight = weight * decay
        
        # Extract category from payload (if present)
        category = payload.get("category") or payload.get("course_category")
        if category and isinstance(category, str):
            category_scores[category] += decayed_weight
        
        # Extract skills from payload (if present)
        skills = payload.get("skills_taught") or payload.get("skills") or []
        if isinstance(skills, list):
            for skill in skills:
                if isinstance(skill, str):
                    skill_scores[skill] += decayed_weight
        
        # Extract difficulty for preference modeling
        difficulty = payload.get("level") or payload.get("difficulty")
        if difficulty:
            difficulty_map = {"Beginner": 1.0, "Intermediate": 2.0, "Advanced": 3.0}
            diff_val = difficulty_map.get(difficulty)
            if diff_val:
                difficulty_weighted_sum += diff_val * abs(decayed_weight)
                difficulty_weight_sum += abs(decayed_weight)
        
        # Extract price for budget estimation
        price = payload.get("price")
        if price is not None and isinstance(price, (int, float)):
            price_signals.append(float(price))
        
        # Track dismissals as exclusions
        if event_type in ("rec_dismiss", "not_interested"):
            product_id = payload.get("product_id") or payload.get("course_id")
            if product_id is not None:
                try:
                    excluded_product_ids.add(int(product_id))
                except (TypeError, ValueError):
                    pass
        
        # Track evidence
        evidence_event_ids.append(event.id)
    
    # Compute difficulty preference (1=Beginner, 2=Intermediate, 3=Advanced)
    difficulty_preference = (
        difficulty_weighted_sum / difficulty_weight_sum
        if difficulty_weight_sum > 0
        else 1.5  # default to between Beginner and Intermediate
    )
    
    # Compute budget estimate (75th percentile of viewed prices)
    budget_estimate: Optional[float] = None
    if price_signals:
        price_signals.sort()
        idx = int(len(price_signals) * 0.75)
        budget_estimate = price_signals[min(idx, len(price_signals) - 1)]
    
    # Build profile dict
    profile = {
        "user_id": user_id,
        "category_scores": dict(category_scores),
        "skill_scores": dict(skill_scores),
        "difficulty_preference": round(difficulty_preference, 2),
        "budget_estimate": budget_estimate,
        "excluded_product_ids": sorted(excluded_product_ids),
        "evidence_event_ids": evidence_event_ids[:50],  # Cap at 50 for hash stability
    }
    
    # Compute profile hash (stable-JSON sha256)
    profile["profile_hash"] = compute_profile_hash(profile)
    
    return profile


def compute_profile_hash(profile: Dict[str, Any]) -> str:
    """
    Compute a stable-JSON sha256 hash of the profile (excluding the hash itself).
    Used for idempotency: unchanged hash ⇒ skip agent run.
    """
    # Exclude the hash field itself
    hashable = {k: v for k, v in profile.items() if k != "profile_hash"}
    # Stable JSON: sort keys, no whitespace
    stable_json = json.dumps(hashable, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(stable_json.encode("utf-8")).hexdigest()