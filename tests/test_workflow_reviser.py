"""Tests for the analysis revision node."""

from __future__ import annotations

import json
import unittest
from unittest import mock


class WorkflowReviserTest(unittest.TestCase):
    """Verify review-feedback based analysis revision."""

    def test_revise_node_skips_when_analyses_are_empty(self) -> None:
        """Reviser returns no update without analyses."""
        from workflows import reviser

        with mock.patch.object(reviser, "chat_json") as chat_json_mock:
            result = reviser.revise_node(
                {"analyses": [], "review_feedback": "需要改进", "cost_tracker": {}}
            )

        chat_json_mock.assert_not_called()
        self.assertEqual({}, result)

    def test_revise_node_skips_when_feedback_is_empty(self) -> None:
        """Reviser returns no update without review feedback."""
        from workflows import reviser

        with mock.patch.object(reviser, "chat_json") as chat_json_mock:
            result = reviser.revise_node(
                {"analyses": [{"title": "item"}], "review_feedback": "", "cost_tracker": {}}
            )

        chat_json_mock.assert_not_called()
        self.assertEqual({}, result)

    def test_revise_node_injects_feedback_and_updates_usage(self) -> None:
        """Reviser sends feedback in the prompt and returns improved analyses."""
        from workflows import reviser

        analyses = [
            {
                "title": "agent repo",
                "summary": "short",
                "content": "old content",
                "tags": ["AI"],
            }
        ]
        improved = [
            {
                "title": "agent repo",
                "summary": "更完整的摘要",
                "content": "improved content",
                "tags": ["AI", "Agent"],
            }
        ]
        state = {
            "analyses": analyses,
            "review_feedback": "摘要需要更具体，补充技术深度。",
            "cost_tracker": {"total_tokens": 3, "calls": 1},
        }

        with mock.patch.object(
            reviser,
            "chat_json",
            return_value=(
                {"analyses": improved},
                {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
        ) as chat_json_mock:
            result = reviser.revise_node(state)

        prompt = chat_json_mock.call_args.args[0]
        prompt_data = json.loads(prompt.split("修订输入：", 1)[1])
        self.assertEqual("摘要需要更具体，补充技术深度。", prompt_data["review_feedback"])
        self.assertEqual(analyses, prompt_data["analyses"])
        self.assertEqual(0.4, chat_json_mock.call_args.kwargs["temperature"])
        self.assertEqual("reviser", chat_json_mock.call_args.kwargs["node_name"])
        self.assertEqual(improved, result["analyses"])
        self.assertEqual(18, result["cost_tracker"]["total_tokens"])
        self.assertEqual(2, result["cost_tracker"]["calls"])

    def test_revise_node_ignores_non_list_response(self) -> None:
        """Reviser leaves state unchanged when the model omits analyses."""
        from workflows import reviser

        with mock.patch.object(
            reviser,
            "chat_json",
            return_value=({"items": []}, {"total_tokens": 1}),
        ), mock.patch.object(reviser.LOGGER, "warning"):
            result = reviser.revise_node(
                {
                    "analyses": [{"title": "item"}],
                    "review_feedback": "需要改进",
                    "cost_tracker": {},
                }
            )

        self.assertEqual({}, result)

    def test_revise_node_returns_empty_update_when_llm_response_is_invalid(self) -> None:
        """Reviser does not block the workflow on malformed LLM JSON."""
        from workflows import reviser

        with mock.patch.object(
            reviser,
            "chat_json",
            side_effect=ValueError("invalid JSON"),
        ), mock.patch.object(reviser.LOGGER, "warning") as warning_mock:
            result = reviser.revise_node(
                {
                    "analyses": [{"title": "item"}],
                    "review_feedback": "需要改进",
                    "cost_tracker": {"total_tokens": 3},
                }
            )

        self.assertEqual({}, result)
        warning_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
