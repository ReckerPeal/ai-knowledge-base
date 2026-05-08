"""Tests for shared LangGraph workflow state."""

from __future__ import annotations

import unittest
from typing import get_type_hints


class WorkflowStateTest(unittest.TestCase):
    """Verify KBState fields and annotations."""

    def test_kb_state_defines_required_fields(self) -> None:
        """KBState exposes the shared structured workflow fields."""
        from workflows.state import KBState

        hints = get_type_hints(KBState)

        self.assertEqual(
            {
                "sources",
                "plan",
                "analyses",
                "articles",
                "review_feedback",
                "review_passed",
                "needs_human_review",
                "pending_review_paths",
                "iteration",
                "cost_tracker",
            },
            set(hints),
        )
        self.assertEqual(list[dict], hints["sources"])
        self.assertEqual(dict, hints["plan"])
        self.assertEqual(list[dict], hints["analyses"])
        self.assertEqual(list[dict], hints["articles"])
        self.assertEqual(str, hints["review_feedback"])
        self.assertEqual(bool, hints["review_passed"])
        self.assertEqual(bool, hints["needs_human_review"])
        self.assertEqual(list[str], hints["pending_review_paths"])
        self.assertEqual(int, hints["iteration"])
        self.assertEqual(dict, hints["cost_tracker"])


if __name__ == "__main__":
    unittest.main()
