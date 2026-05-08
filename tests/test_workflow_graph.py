"""Tests for LangGraph workflow assembly."""

from __future__ import annotations

import importlib
import sys
import types
import unittest
from unittest import mock


class FakeCompiledGraph:
    """Minimal compiled graph returned by the fake StateGraph."""


class FakeStateGraph:
    """Capture LangGraph assembly calls for verification."""

    last_instance: "FakeStateGraph | None" = None

    def __init__(self, state_type: type) -> None:
        """Initialize the fake graph.

        Args:
            state_type: State type passed to StateGraph.
        """
        self.state_type = state_type
        self.nodes: dict[str, object] = {}
        self.edges: list[tuple[str, str]] = []
        self.conditional_edges: list[tuple[str, object, dict[str, str]]] = []
        self.entry_point: str | None = None
        FakeStateGraph.last_instance = self

    def add_node(self, name: str, function: object) -> None:
        """Record a node registration."""
        self.nodes[name] = function

    def add_edge(self, source: str, target: str) -> None:
        """Record a linear edge."""
        self.edges.append((source, target))

    def add_conditional_edges(
        self,
        source: str,
        router: object,
        mapping: dict[str, str],
    ) -> None:
        """Record conditional edge registration."""
        self.conditional_edges.append((source, router, mapping))

    def set_entry_point(self, name: str) -> None:
        """Record entry point."""
        self.entry_point = name

    def compile(self) -> FakeCompiledGraph:
        """Return a fake compiled graph."""
        return FakeCompiledGraph()


class WorkflowGraphTest(unittest.TestCase):
    """Verify workflow graph assembly."""

    def setUp(self) -> None:
        """Install fake langgraph modules before importing workflows.graph."""
        self.original_modules = {
            name: sys.modules.get(name)
            for name in ("langgraph", "langgraph.graph", "workflows.graph")
        }
        fake_langgraph = types.ModuleType("langgraph")
        fake_graph_module = types.ModuleType("langgraph.graph")
        fake_graph_module.StateGraph = FakeStateGraph
        fake_graph_module.END = "__end__"
        sys.modules["langgraph"] = fake_langgraph
        sys.modules["langgraph.graph"] = fake_graph_module
        sys.modules.pop("workflows.graph", None)

    def tearDown(self) -> None:
        """Restore original modules."""
        for name, module in self.original_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def test_build_graph_registers_nodes_edges_and_entry_point(self) -> None:
        """build_graph wires the required LangGraph workflow."""
        graph_module = importlib.import_module("workflows.graph")

        app = graph_module.build_graph()
        fake_graph = FakeStateGraph.last_instance

        self.assertIsInstance(app, FakeCompiledGraph)
        self.assertIsNotNone(fake_graph)
        self.assertEqual("plan", fake_graph.entry_point)
        self.assertEqual(
            {
                "plan",
                "collect",
                "analyze",
                "review",
                "revise",
                "human_flag",
                "organize",
                "save",
            },
            set(fake_graph.nodes),
        )
        self.assertIn(("plan", "collect"), fake_graph.edges)
        self.assertIn(("collect", "analyze"), fake_graph.edges)
        self.assertIn(("analyze", "review"), fake_graph.edges)
        self.assertIn(("revise", "review"), fake_graph.edges)
        self.assertIn(("organize", "save"), fake_graph.edges)
        self.assertIn(("human_flag", "__end__"), fake_graph.edges)
        self.assertIn(("save", "__end__"), fake_graph.edges)
        self.assertEqual(
            (
                "review",
                graph_module.route_after_review,
                {
                    "organize": "organize",
                    "revise": "revise",
                    "human_flag": "human_flag",
                },
            ),
            fake_graph.conditional_edges[0],
        )

    def test_route_after_review_uses_review_passed_flag(self) -> None:
        """Review router maps pass/fail state to branch keys."""
        graph_module = importlib.import_module("workflows.graph")

        self.assertEqual("organize", graph_module.route_after_review({"review_passed": True}))
        self.assertEqual(
            "revise",
            graph_module.route_after_review({"review_passed": False, "iteration": 2}),
        )
        self.assertEqual(
            "human_flag",
            graph_module.route_after_review({"review_passed": False, "iteration": 3}),
        )
        self.assertEqual(
            "human_flag",
            graph_module.route_after_review(
                {"review_passed": False, "iteration": 2, "plan": {"max_iterations": 2}}
            ),
        )

    def test_main_logs_stream_events(self) -> None:
        """CLI smoke path logs key output from stream events."""
        graph_module = importlib.import_module("workflows.graph")

        class FakeApp:
            """Fake app with stream support."""

            def stream(self, state: dict[str, object]) -> list[dict[str, object]]:
                """Return fake stream events."""
                return [
                    {"collect": {"sources": [1, 2]}},
                    {"review": {"review_passed": True}},
                ]

        with mock.patch.object(graph_module, "build_graph", return_value=FakeApp()):
            with mock.patch.object(graph_module.LOGGER, "info") as info_mock:
                graph_module.main()

        self.assertGreaterEqual(info_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
