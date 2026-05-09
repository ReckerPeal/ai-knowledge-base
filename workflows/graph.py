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

from workflows.analyzer import analyze_node
from workflows.collector import collect_node
from workflows.human_flag import human_flag_node
from workflows.model_client import get_cost_guard
from workflows.organizer import organize_node
from workflows.planner import planner_node
from workflows.reviewer import review_node
from workflows.reviser import revise_node
from workflows.saver import save_node
from workflows.state import KBState


LOGGER = logging.getLogger(__name__)


def build_graph() -> Any:
    """Build and compile the LangGraph workflow.

    Returns:
        Compiled LangGraph application.
    """
    graph = StateGraph(KBState)

    graph.add_node("plan", planner_node)
    graph.add_node("collect", collect_node)
    graph.add_node("analyze", analyze_node)
    graph.add_node("organize", organize_node)
    graph.add_node("review", review_node)
    graph.add_node("revise", revise_node)
    graph.add_node("human_flag", human_flag_node)
    graph.add_node("save", save_node)

    graph.add_edge("plan", "collect")
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
    graph.set_entry_point("plan")

    return graph.compile()


def route_after_review(state: KBState) -> str:
    """Route to organize, revise, or human review after automated review.

    Args:
        state: Shared workflow state.

    Returns:
        ``organize`` when review passed, ``revise`` while the retry budget
        remains, otherwise ``human_flag``. The retry budget comes from
        ``state["plan"]["max_iterations"]`` and falls back to ``3``.
    """
    plan = state.get("plan", {}) or {}
    max_iter = int(plan.get("max_iterations", 3))

    if state.get("review_passed"):
        return "organize"
    if int(state.get("iteration") or 0) >= max_iter:
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

    _log_and_save_cost_report()


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


def _log_and_save_cost_report() -> None:
    """Log the final LLM cost report and persist it to disk."""
    guard = get_cost_guard()
    report = guard.get_report()
    total_calls = int(report.get("total_calls") or len(report.get("records") or []))
    total_cost = float(report.get("total_cost_yuan") or 0.0)
    cost_by_node = report["cost_by_node"]

    LOGGER.info("总调用%s次，总成本%.10f", total_calls, total_cost)
    for node_name, node_report in sorted(cost_by_node.items()):
        LOGGER.info(
            "[CostReport] node=%s report=%s",
            node_name,
            json.dumps(node_report, ensure_ascii=False),
        )

    saved_path = guard.save_report()
    LOGGER.info("[CostReport] saved=%s", saved_path)


if __name__ == "__main__":
    main()
