"""
TypedDict state for the 7-node agent graph.
"""
from typing import TypedDict, List, Dict, Any, Optional


class AgentState(TypedDict):
    """State passed through the agent graph."""
    # Inputs
    user_id: int
    run_id: int
    profile_hash: str
    trigger_reason: str
    
    # Profile (from analyze node)
    profile: Optional[Dict[str, Any]]
    
    # Retrieval (from retrieve node)
    retrieval_query: Optional[str]
    candidates: Optional[List[Dict[str, Any]]]
    retrieval_degraded: bool
    
    # Ranking (from retrieve node)
    ranked_candidates: Optional[List[Dict[str, Any]]]
    
    # Quality control (from grade node)
    retrieval_quality: Optional[str]  # "strong" | "weak" | "exhausted"
    refine_count: int
    
    # Generation (from generate node)
    narrative: Optional[str]
    product_ids: Optional[List[int]]
    product_reasons: Optional[List[str]]
    llm_cost_usd: Optional[float]
    llm_tokens: Optional[int]
    llm_latency_ms: Optional[int]
    
    # Validation (from validate node)
    validation_passed: Optional[bool]
    retry_count: int
    critique_feedback: Optional[str]
    
    # Output (from persist node)
    recommendation_id: Optional[int]
    quality_score: Optional[int]
    error_message: Optional[str]