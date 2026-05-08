"""LangGraph assembly for the AI knowledge-base workflow."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langgraph.graph import END, StateGraph

from workflows.human_flag import human_flag_node
from workflows.nodes import (
    analyze_node,
    collect_node,
    organize_node,
    save_node,
)
from workflows.reviewer import review_node
from workflows.reviser import revise_node
from workflows.state import KBState


LOGGER = logging.getLogger(__name__)


def build_graph() -> Any:
    """Build and compile the LangGraph workflow.

    Returns:
        Compiled LangGraph application.
    """
    graph = StateGraph(KBState)

    graph.add_node("collect", collect_node)
    graph.add_node("analyze", analyze_node)
    graph.add_node("organize", organize_node)
    graph.add_node("review", review_node)
    graph.add_node("revise", revise_node)
    graph.add_node("human_flag", human_flag_node)
    graph.add_node("save", save_node)

    graph.add_edge("collect", "analyze")
    graph.add_edge("analyze", "review")
    graph.add_conditional_edges(
        "review",
        route_after_review,
        {
            "organize": "organize",
            "revise": "revise",
            "human_flag": "human_flag",
        },
    )
    graph.add_edge("organize", "save")
    graph.add_edge("revise", "review")
    graph.add_edge("human_flag", END)
    graph.add_edge("save", END)
    graph.set_entry_point("collect")

    return graph.compile()


def route_after_review(state: KBState) -> str:
    """Route to organize, revise, or human review after automated review.

    Args:
        state: Shared workflow state.

    Returns:
        ``organize`` when review passed, ``revise`` while the retry budget
        remains, otherwise ``human_flag``.
    """
    if state.get("review_passed"):
        return "organize"
    if int(state.get("iteration") or 0) >= 3:
        return "human_flag"
    return "revise"


def main() -> None:
    """Stream the compiled workflow and log each node's key output."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = build_graph()
    initial_state: KBState = {
        "plan": {},
        "sources": [],
        "analyses": [],
        "articles": [],
        "review_feedback": "",
        "review_passed": False,
        "needs_human_review": False,
        "pending_review_paths": [],
        "iteration": 0,
        "cost_tracker": {},
    }

    for event in app.stream(initial_state):
        if not isinstance(event, dict):
            LOGGER.info("[Graph] event=%s", event)
            continue

        for node_name, update in event.items():
            LOGGER.info(
                "[Graph] %s output=%s",
                node_name,
                json.dumps(_summarize_update(update), ensure_ascii=False),
            )


def _summarize_update(update: Any) -> dict[str, Any]:
    """Summarize a node update for stream logging.

    Args:
        update: Raw node update emitted by LangGraph.

    Returns:
        Compact dictionary suitable for logs.
    """
    if not isinstance(update, dict):
        return {"value": update}

    summary: dict[str, Any] = {}
    for key, value in update.items():
        if isinstance(value, list):
            summary[key] = {"count": len(value)}
        elif isinstance(value, dict):
            summary[key] = value
        else:
            summary[key] = value
    return summary


if __name__ == "__main__":
    main()
