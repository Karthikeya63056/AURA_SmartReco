from typing import TypedDict, List, Dict, Any, Optional


class AgentState(TypedDict):
    user_id: int
    trigger_reason: str
    events_summary: str
    user_profile: Dict[str, Any]  # {"interests": [...], "skill_level": "...", "intent": "..."}
    search_query: str
    candidates: List[Dict[str, Any]]
    quality_score: int
    refetch_count: int
    final_narrative: str
    recommended_product_ids: List[int]
    metadata: Dict[str, Any]  # {"needs_refetch": bool, "eval_reasoning": str, ...}
