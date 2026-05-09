"""Tests for workflow planning strategy selection."""

from __future__ import annotations

import os
import unittest
from unittest import mock


class WorkflowPlannerTest(unittest.TestCase):
    """Verify planner strategy thresholds and node output."""

    def test_plan_strategy_returns_lite_for_small_target(self) -> None:
        """Targets below 10 use the lite strategy."""
        from workflows.planner import plan_strategy

        plan = plan_strategy(9)

        self.assertEqual("lite", plan["mode"])
        self.assertEqual(5, plan["per_source_limit"])
        self.assertEqual(0.8, plan["relevance_threshold"])
        self.assertEqual(1, plan["max_iterations"])
        self.assertIn("低于 10", plan["rationale"])

    def test_plan_strategy_returns_standard_for_default_target(self) -> None:
        """Default target count comes from the 10-item standard tier."""
        from workflows.planner import plan_strategy

        with mock.patch.dict(os.environ, {}, clear=True):
            plan = plan_strategy()

        self.assertEqual("standard", plan["mode"])
        self.assertEqual(10, plan["target_count"])
        self.assertEqual(15, plan["per_source_limit"])
        self.assertEqual(0.8, plan["relevance_threshold"])
        self.assertEqual(2, plan["max_iterations"])

    def test_plan_strategy_reads_target_from_environment(self) -> None:
        """The planner reads PLANNER_TARGET_COUNT when no target is passed."""
        from workflows.planner import plan_strategy

        with mock.patch.dict(os.environ, {"PLANNER_TARGET_COUNT": "20"}):
            plan = plan_strategy()

        self.assertEqual("full", plan["mode"])
        self.assertEqual(20, plan["target_count"])
        self.assertEqual(20, plan["per_source_limit"])
        self.assertEqual(0.8, plan["relevance_threshold"])
        self.assertEqual(3, plan["max_iterations"])
        self.assertIn("不低于 20", plan["rationale"])

    def test_plan_strategy_falls_back_for_invalid_environment_value(self) -> None:
        """Invalid environment values fall back to the default target count."""
        from workflows.planner import plan_strategy

        with mock.patch.dict(os.environ, {"PLANNER_TARGET_COUNT": "invalid"}):
            plan = plan_strategy()

        self.assertEqual("standard", plan["mode"])
        self.assertEqual(10, plan["target_count"])

    def test_planner_node_returns_plan_update(self) -> None:
        """planner_node wraps the selected strategy for LangGraph state."""
        from workflows.planner import planner_node

        with mock.patch.dict(os.environ, {"PLANNER_TARGET_COUNT": "3"}):
            result = planner_node({})

        self.assertEqual({"plan"}, set(result))
        self.assertEqual("lite", result["plan"]["mode"])


if __name__ == "__main__":
    unittest.main()
