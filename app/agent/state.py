from typing import TypedDict, List, Dict, Any, Optional


class AgentState(TypedDict):
    user_id: int
    trigger_reason: str
    current_behavior_hash: str
    events_summary: str
    recurring_patterns: str
    user_profile: Dict[str, Any]  # {"interests": [...], "skill_level": "...", "intent": "..."}
    user_skills: List[str]
    persuasion_style: str  # 'analytical' | 'social' | 'motivational' | 'practical' | 'hybrid'
    search_query: str
    candidates: List[Dict[str, Any]]
    quality_score: int
    refetch_count: int
    final_narrative: str
    recommended_product_ids: List[int]
    product_reasons: List[str]
    metadata: Dict[str, Any]  # {"needs_refetch": bool, "eval_reasoning": str, ...}
    critique_retry_count: int
    critique_feedback: str
    validation_passed: bool
