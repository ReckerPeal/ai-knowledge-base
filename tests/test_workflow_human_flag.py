"""Tests for the human-review fallback node."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class WorkflowHumanFlagTest(unittest.TestCase):
    """Verify pending-review fallback behavior."""

    def test_human_flag_node_skips_before_iteration_limit(self) -> None:
        """Human flag does nothing while the review loop still has budget."""
        from workflows import human_flag

        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.object(human_flag, "PENDING_REVIEW_DIR", Path(temp_dir)):
                result = human_flag.human_flag_node(
                    {
                        "analyses": [{"title": "item"}],
                        "review_feedback": "需要改进",
                        "iteration": 1,
                        "max_iterations": 3,
                    }
                )

            self.assertEqual({}, result)
            self.assertEqual([], list(Path(temp_dir).glob("*.json")))

    def test_human_flag_node_writes_pending_review_after_limit(self) -> None:
        """Human flag writes problem analyses outside the main knowledge base."""
        from workflows import human_flag

        analyses = [{"title": "bad item", "source_url": "https://example.com/a"}]
        state = {
            "plan": {"topic": "AI"},
            "analyses": analyses,
            "review_feedback": "摘要质量和技术深度不足。",
            "review_passed": False,
            "iteration": 3,
            "max_iterations": 3,
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.object(human_flag, "PENDING_REVIEW_DIR", Path(temp_dir)):
                result = human_flag.human_flag_node(state)
                pending_files = list(Path(temp_dir).glob("*.json"))

            self.assertEqual(1, len(pending_files))
            data = json.loads(pending_files[0].read_text(encoding="utf-8"))

        self.assertTrue(result["needs_human_review"])
        self.assertEqual([str(pending_files[0])], result["pending_review_paths"])
        self.assertEqual(analyses, data["analyses"])
        self.assertEqual("摘要质量和技术深度不足。", data["review_feedback"])
        self.assertEqual(3, data["iteration"])
        self.assertEqual(3, data["max_iterations"])
        self.assertEqual("pending_human_review", data["status"])

    def test_human_flag_node_reads_max_iterations_from_plan(self) -> None:
        """Human flag supports max_iterations configured in plan."""
        from workflows import human_flag

        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.object(human_flag, "PENDING_REVIEW_DIR", Path(temp_dir)):
                result = human_flag.human_flag_node(
                    {
                        "plan": {"max_iterations": 2},
                        "analyses": [{"title": "item"}],
                        "review_feedback": "仍未通过",
                        "review_passed": False,
                        "iteration": 2,
                    }
                )

        self.assertTrue(result["needs_human_review"])


if __name__ == "__main__":
    unittest.main()
