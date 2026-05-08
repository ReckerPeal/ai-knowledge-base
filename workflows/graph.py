"""LangGraph assembly for the AI knowledge-base workflow."""

from __future__ import annotations

import json
import logging
from typing import Any

from langgraph.graph import END, StateGraph

from workflows.nodes import (
    analyze_node,
    collect_node,
    organize_node,
    review_node,
    save_node,
)
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
    graph.add_node("save", save_node)

    graph.add_edge("collect", "analyze")
    graph.add_edge("analyze", "organize")
    graph.add_edge("organize", "review")
    graph.add_conditional_edges(
        "review",
        _route_after_review,
        {
            "save": "save",
            "organize": "organize",
        },
    )
    graph.add_edge("save", END)
    graph.set_entry_point("collect")

    return graph.compile()


def _route_after_review(state: KBState) -> str:
    """Route after the review node based on ``review_passed``.

    Args:
        state: Shared workflow state.

    Returns:
        ``save`` when review passed, otherwise ``organize``.
    """
    if state.get("review_passed"):
        return "save"
    return "organize"


def main() -> None:
    """Stream the compiled workflow and log each node's key output."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = build_graph()
    initial_state: KBState = {
        "sources": [],
        "analyses": [],
        "articles": [],
        "review_feedback": "",
        "review_passed": False,
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
