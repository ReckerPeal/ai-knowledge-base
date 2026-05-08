"""Tests for the Supervisor pattern."""

from __future__ import annotations

import json
import unittest
from unittest import mock


class SupervisorTest(unittest.TestCase):
    """Verify worker/supervisor review loops."""

    def test_supervisor_returns_output_when_first_review_passes(self) -> None:
        """A passing first attempt returns the worker JSON output."""
        from patterns import supervisor as supervisor_module

        responses = [
            (json.dumps({"summary": "Accurate analysis", "findings": []}), {}),
            (
                json.dumps(
                    {
                        "passed": True,
                        "score": 8,
                        "feedback": "Good enough.",
                    }
                ),
                {},
            ),
        ]

        with mock.patch.object(
            supervisor_module.model_client,
            "chat",
            side_effect=responses,
            create=True,
        ):
            result = supervisor_module.supervisor("Analyze a repo")

        self.assertEqual({"summary": "Accurate analysis", "findings": []}, result["output"])
        self.assertEqual(1, result["attempts"])
        self.assertEqual(8, result["final_score"])
        self.assertNotIn("warning", result)

    def test_supervisor_retries_with_feedback_until_passed(self) -> None:
        """A failed review is fed into the next worker attempt."""
        from patterns import supervisor as supervisor_module

        responses = [
            (json.dumps({"summary": "Too shallow"}), {}),
            (
                json.dumps(
                    {
                        "passed": False,
                        "score": 5,
                        "feedback": "Add more depth.",
                    }
                ),
                {},
            ),
            (json.dumps({"summary": "Deeper analysis", "details": ["More depth"]}), {}),
            (
                json.dumps(
                    {
                        "passed": True,
                        "score": 7,
                        "feedback": "Passed.",
                    }
                ),
                {},
            ),
        ]

        with mock.patch.object(
            supervisor_module.model_client,
            "chat",
            side_effect=responses,
            create=True,
        ) as chat_mock:
            result = supervisor_module.supervisor("Analyze an AI framework")

        second_worker_prompt = chat_mock.call_args_list[2].args[0]
        self.assertIn("Add more depth.", second_worker_prompt)
        self.assertEqual({"summary": "Deeper analysis", "details": ["More depth"]}, result["output"])
        self.assertEqual(2, result["attempts"])
        self.assertEqual(7, result["final_score"])

    def test_supervisor_forces_return_after_max_retries(self) -> None:
        """Repeated failed reviews return the final worker output with a warning."""
        from patterns import supervisor as supervisor_module

        responses = []
        for index in range(3):
            responses.append((json.dumps({"summary": f"Attempt {index + 1}"}), {}))
            responses.append(
                (
                    json.dumps(
                        {
                            "passed": False,
                            "score": 4,
                            "feedback": "Still insufficient.",
                        }
                    ),
                    {},
                )
            )

        with mock.patch.object(
            supervisor_module.model_client,
            "chat",
            side_effect=responses,
            create=True,
        ):
            result = supervisor_module.supervisor("Analyze weak evidence", max_retries=3)

        self.assertEqual({"summary": "Attempt 3"}, result["output"])
        self.assertEqual(3, result["attempts"])
        self.assertEqual(4, result["final_score"])
        self.assertIn("warning", result)


if __name__ == "__main__":
    unittest.main()
