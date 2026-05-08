"""Tests for the analysis review node."""

from __future__ import annotations

import json
import unittest
from unittest import mock


class WorkflowReviewerTest(unittest.TestCase):
    """Verify analysis review behavior."""

    def test_review_node_scores_first_five_analyses_with_weighted_total(self) -> None:
        """Reviewer limits input, uses low temperature, and recomputes pass score."""
        from workflows import reviewer

        analyses = [
            {"title": f"item-{index}", "summary": "AI analysis", "content": "details"}
            for index in range(7)
        ]
        state = {
            "plan": {"topic": "AI agents"},
            "analyses": analyses,
            "iteration": 2,
            "cost_tracker": {"total_tokens": 3, "calls": 1},
        }
        review_payload = {
            "feedback": "前 5 条整体可用。",
            "scores": {
                "summary_quality": 8,
                "technical_depth": 6,
                "relevance": 7,
                "originality": 9,
                "formatting": 8,
            },
        }

        with mock.patch.object(
            reviewer,
            "chat_json",
            return_value=(
                review_payload,
                {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
        ) as chat_json_mock:
            result = reviewer.review_node(state)

        prompt = chat_json_mock.call_args.args[0]
        prompt_data = json.loads(prompt.split("待审核 analyses：", 1)[1])
        self.assertEqual(5, len(prompt_data["analyses"]))
        self.assertEqual("item-4", prompt_data["analyses"][-1]["title"])
        self.assertEqual(0.1, chat_json_mock.call_args.kwargs["temperature"])
        self.assertTrue(result["review_passed"])
        self.assertIn("7.45", result["review_feedback"])
        self.assertEqual(3, result["iteration"])
        self.assertEqual(18, result["cost_tracker"]["total_tokens"])
        self.assertEqual(2, result["cost_tracker"]["calls"])

    def test_review_node_rejects_when_weighted_score_is_below_threshold(self) -> None:
        """Reviewer ignores model arithmetic and applies local threshold."""
        from workflows import reviewer

        with mock.patch.object(
            reviewer,
            "chat_json",
            return_value=(
                {
                    "feedback": "深度不足。",
                    "overall_score": 10,
                    "scores": {
                        "summary_quality": 6,
                        "technical_depth": 6,
                        "relevance": 6,
                        "originality": 6,
                        "formatting": 6,
                    },
                },
                {"total_tokens": 1},
            ),
        ):
            result = reviewer.review_node(
                {"plan": {}, "analyses": [{"title": "x"}], "iteration": 0}
            )

        self.assertFalse(result["review_passed"])
        self.assertIn("6.00", result["review_feedback"])

    def test_review_node_auto_passes_when_llm_fails(self) -> None:
        """Reviewer does not block the workflow when LLM review fails."""
        from workflows import reviewer

        with mock.patch.object(
            reviewer,
            "chat_json",
            side_effect=RuntimeError("provider unavailable"),
        ), mock.patch.object(reviewer.LOGGER, "exception"):
            result = reviewer.review_node(
                {"plan": {}, "analyses": [{"title": "x"}], "iteration": 4}
            )

        self.assertTrue(result["review_passed"])
        self.assertIn("LLM 审核失败", result["review_feedback"])
        self.assertEqual(5, result["iteration"])
        self.assertEqual({}, result["cost_tracker"])


if __name__ == "__main__":
    unittest.main()
