import logging
from typing import Dict, Any
from langgraph.graph import StateGraph, END
from app.agent.state import AgentState
from app.agent.nodes import (
    analyze_behavior_node,
    retrieve_candidates_node,
    evaluate_and_rerank_node,
    generate_narrative_node,
    critique_narrative_node,
    store_node,
    refetch_broaden_node,
    refetch_node
)

try:
    from langsmith import traceable
except ImportError:
    # No-op decorator fallback if langsmith is not installed
    def traceable(name: str = "", run_type: str = "chain"):
        def decorator(func):
            return func
        return decorator

logger = logging.getLogger(__name__)


def should_refetch(state: AgentState) -> str:
    """Conditional edge evaluating quality score and max retries."""
    quality_score = state.get("quality_score", 100)
    refetch_count = state.get("refetch_count", 0)
    metadata = state.get("metadata", {})
    needs_refetch = metadata.get("needs_refetch", False)

    if (quality_score < 60 or needs_refetch) and refetch_count < 2:
        logger.info(f"Refetch condition met! Quality score {quality_score} < 60 (refetch_count={refetch_count})")
        return "refetch"
    
    return "proceed"


def should_retry_or_store(state: AgentState) -> str:
    """Conditional edge evaluating narrative critique validation and retry limit."""
    validation_passed = state.get("validation_passed", False)
    retry_count = state.get("critique_retry_count", 0)

    if validation_passed:
        logger.info("[Critique Edge] Validation passed. Proceeding to store.")
        return "store"

    if retry_count <= 1:
        logger.info(f"[Critique Edge] Validation failed. Retrying generate_narrative (retry #{retry_count}).")
        return "generate"

    logger.warning(f"[Critique Edge] Validation failed and retry limit reached ({retry_count}). Proceeding to store.")
    return "store"


@traceable(name="SmartRecoAgent", run_type="chain")
def build_recommendation_graph() -> StateGraph:
    """Construct and compile the recommendation StateGraph with LangSmith tracing."""
    workflow = StateGraph(AgentState)

    # Add Nodes
    workflow.add_node("analyze", analyze_behavior_node)
    workflow.add_node("retrieve", retrieve_candidates_node)
    workflow.add_node("evaluate", evaluate_and_rerank_node)
    workflow.add_node("refetch_broaden", refetch_broaden_node)
    workflow.add_node("generate", generate_narrative_node)
    workflow.add_node("critique", critique_narrative_node)
    workflow.add_node("store", store_node)

    # Set Entry Point
    workflow.set_entry_point("analyze")

    # Wire Standard Edges
    workflow.add_edge("analyze", "retrieve")
    workflow.add_edge("retrieve", "evaluate")

    # Add Conditional Refetch Edge
    workflow.add_conditional_edges(
        "evaluate",
        should_refetch,
        {
            "refetch": "refetch_broaden",
            "proceed": "generate"
        }
    )

    workflow.add_edge("refetch_broaden", "retrieve")

    # Wire Generate → Critique
    workflow.add_edge("generate", "critique")

    # Add Conditional Critique Edge
    workflow.add_conditional_edges(
        "critique",
        should_retry_or_store,
        {
            "generate": "generate",
            "store": "store"
        }
    )

    workflow.add_edge("store", END)

    return workflow.compile()



# Compiled singleton graph app
recommendation_agent = build_recommendation_graph()
