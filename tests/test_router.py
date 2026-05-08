"""Tests for the Router routing pattern."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class RouterTest(unittest.TestCase):
    """Verify intent routing, handler dispatch, and external call boundaries."""

    def test_route_uses_keyword_match_for_github_search(self) -> None:
        """GitHub keywords route directly without LLM classification."""
        from patterns import router

        response_payload = {
            "items": [
                {
                    "full_name": "owner/agent-framework",
                    "html_url": "https://github.com/owner/agent-framework",
                    "description": "Agent workflow framework.",
                    "stargazers_count": 1234,
                    "language": "Python",
                }
            ]
        }

        class FakeResponse:
            """Minimal context manager for urllib responses."""

            def __enter__(self) -> "FakeResponse":
                """Return the fake response."""
                return self

            def __exit__(self, *args: object) -> None:
                """Exit the fake response context."""

            def read(self) -> bytes:
                """Return encoded GitHub search JSON."""
                return json.dumps(response_payload).encode("utf-8")

        with mock.patch.object(router, "_classify_with_llm") as classify_mock:
            with mock.patch("patterns.router.urllib.request.urlopen", return_value=FakeResponse()) as urlopen_mock:
                result = router.route("GitHub 搜索 agent framework")

        classify_mock.assert_not_called()
        request = urlopen_mock.call_args.args[0]
        self.assertIn("GitHub%20%E6%90%9C%E7%B4%A2%20agent%20framework", request.full_url)
        self.assertIn("owner/agent-framework", result)

    def test_route_reads_knowledge_index_for_knowledge_query(self) -> None:
        """Knowledge keywords search the local index file."""
        from patterns import router

        with tempfile.TemporaryDirectory() as temp_dir:
            index_path = Path(temp_dir) / "index.json"
            index_path.write_text(
                json.dumps(
                    [
                        {
                            "title": "OpenClaw Agent Notes",
                            "source_url": "https://example.com/openclaw",
                            "summary": "Local notes about OpenClaw agents.",
                            "tags": ["Agent", "OpenClaw"],
                            "score": 8.5,
                        }
                    ]
                ),
                encoding="utf-8",
            )

            with mock.patch.object(router, "KNOWLEDGE_INDEX_PATH", index_path):
                result = router.route("查询知识库 OpenClaw")

        self.assertIn("OpenClaw Agent Notes", result)
        self.assertIn("https://example.com/openclaw", result)

    def test_route_uses_llm_classifier_for_ambiguous_intent(self) -> None:
        """Ambiguous queries use LLM classification and dispatch to the handler."""
        from patterns import router

        with mock.patch.object(router, "_classify_with_llm", return_value=router.GENERAL_CHAT):
            with mock.patch.object(router, "handle_general_chat", return_value="hello") as chat_mock:
                result = router.route("帮我想一下")

        chat_mock.assert_called_once_with("帮我想一下")
        self.assertEqual("hello", result)


if __name__ == "__main__":
    unittest.main()
