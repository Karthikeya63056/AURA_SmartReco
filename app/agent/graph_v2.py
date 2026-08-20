"""
LangGraph wiring for the 7-node agent.
Two conditional branches:
  - grade  -> refine (weak & refine_count<1) | generate (strong/exhausted)
  - validate -> generate (fail & retry_count<=1) | persist (pass/exhausted)
"""
import logging
from typing import Dict, Any

from langgraph.graph import StateGraph, START, END

from app.agent.state_v2 import AgentState
from app.agent.nodes_v2 import (
    analyze, retrieve, grade, refine, generate, validate, persist,
)

logger = logging.getLogger(__name__)

MAX_REFINES = 1
MAX_RETRIES = 1


def route_after_grade(state: Dict[str, Any]) -> str:
    """weak & refine_count<1 -> refine; else -> generate."""
    if state.get("retrieval_quality") == "weak" and state.get("refine_count", 0) < MAX_REFINES:
        return "refine"
    return "generate"


def route_after_validate(state: Dict[str, Any]) -> str:
    """fail & retry_count<=1 -> generate (one retry); else -> persist."""
    if not state.get("validation_passed") and state.get("retry_count", 0) <= MAX_RETRIES:
        return "generate"
    return "persist"


def build_graph() -> StateGraph:
    """Construct and compile the 7-node agent graph."""
    builder = StateGraph(AgentState)

    # Add the 7 nodes
    builder.add_node("analyze", analyze)
    builder.add_node("retrieve", retrieve)
    builder.add_node("grade", grade)
    builder.add_node("refine", refine)
    builder.add_node("generate", generate)
    builder.add_node("validate", validate)
    builder.add_node("persist", persist)

    # Fixed edges
    builder.add_edge(START, "analyze")
    builder.add_edge("analyze", "retrieve")
    builder.add_edge("retrieve", "grade")
    builder.add_edge("refine", "retrieve")
    builder.add_edge("generate", "validate")
    builder.add_edge("persist", END)

    # Conditional edges (the 2 genuine decision branches)
    builder.add_conditional_edges(
        "grade", route_after_grade, {"refine": "refine", "generate": "generate"}
    )
    builder.add_conditional_edges(
        "validate", route_after_validate, {"generate": "generate", "persist": "persist"}
    )

    return builder.compile()


# Compiled singleton
graph = build_graph()


def node_count() -> int:
    """Number of user-defined nodes (excludes LangGraph __start__/__end__)."""
    return len([n for n in graph.nodes if n not in ("__start__", "__end__")])


def run_agent(
    run_id: int,
    user_id: int,
    profile_hash: str,
    trigger_reason: str,
) -> Dict[str, Any]:
    """
    Synchronous entry point invoked by the dispatcher via asyncio.to_thread.
    Builds initial state and runs the graph to completion.
    """
    initial_state: AgentState = {
        "user_id": user_id,
        "run_id": run_id,
        "profile_hash": profile_hash,
        "trigger_reason": trigger_reason,
        "profile": None,
        "retrieval_query": None,
        "candidates": None,
        "retrieval_degraded": False,
        "ranked_candidates": None,
        "retrieval_quality": None,
        "refine_count": 0,
        "narrative": None,
        "product_ids": None,
        "product_reasons": None,
        "validation_passed": None,
        "retry_count": 0,
        "critique_feedback": None,
        "recommendation_id": None,
        "quality_score": None,
        "error_message": None,
    }

    result = graph.invoke(initial_state)
    logger.info(
        f"[graph] run {run_id} user {user_id} -> rec {result.get('recommendation_id')} "
        f"quality {result.get('quality_score')} error {result.get('error_message')}"
    )
    return result