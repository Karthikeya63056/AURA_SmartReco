import logging
from typing import Dict, Any
from langgraph.graph import StateGraph, END
from app.agent.state import AgentState
from app.agent.nodes import (
    analyze_behavior_node,
    retrieve_candidates_node,
    evaluate_and_rerank_node,
    generate_narrative_node,
    store_node
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


async def refetch_node(state: AgentState) -> Dict[str, Any]:
    """Intermediate node that increments refetch count and broadens search query."""
    current_count = state.get("refetch_count", 0) + 1
    query = state.get("search_query", "AI course")
    # Broaden query phrase
    broad_query = f"{query} fundamentals advanced machine learning python"
    logger.info(f"[Refetch Loop] Refetch #{current_count} triggered. Broadening query to: '{broad_query}'")

    return {
        "refetch_count": current_count,
        "search_query": broad_query
    }


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


@traceable(name="SmartRecoAgent", run_type="chain")
def build_recommendation_graph() -> StateGraph:
    """Construct and compile the recommendation StateGraph with LangSmith tracing."""
    workflow = StateGraph(AgentState)

    # Add Nodes
    workflow.add_node("analyze", analyze_behavior_node)
    workflow.add_node("retrieve", retrieve_candidates_node)
    workflow.add_node("evaluate", evaluate_and_rerank_node)
    workflow.add_node("refetch_broaden", refetch_node)
    workflow.add_node("generate", generate_narrative_node)
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
    workflow.add_edge("generate", "store")
    workflow.add_edge("store", END)

    return workflow.compile()


# Compiled singleton graph app
recommendation_agent = build_recommendation_graph()
