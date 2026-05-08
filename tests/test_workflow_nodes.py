"""Tests for LangGraph workflow nodes."""

from __future__ import annotations

import json
import tempfile
import urllib.parse
import unittest
from pathlib import Path
from unittest import mock


class WorkflowNodesTest(unittest.TestCase):
    """Verify workflow node partial state updates."""

    def test_collect_node_searches_github_and_returns_sources(self) -> None:
        """Collect node maps GitHub repository data to source summaries."""
        from workflows import collector

        response_payload = {
            "items": [
                {
                    "full_name": "owner/ai-agent",
                    "html_url": "https://github.com/owner/ai-agent",
                    "description": "AI agent framework.",
                    "stargazers_count": 99,
                    "language": "Python",
                    "updated_at": "2026-05-08T00:00:00Z",
                    "owner": {"login": "owner"},
                }
            ]
        }

        class FakeResponse:
            """Minimal urllib response context manager."""

            def __enter__(self) -> "FakeResponse":
                """Return this fake response."""
                return self

            def __exit__(self, *args: object) -> None:
                """Exit the fake response context."""

            def read(self) -> bytes:
                """Return encoded JSON payload."""
                return json.dumps(response_payload).encode("utf-8")

        with mock.patch(
            "workflows.collector.urllib.request.urlopen",
            return_value=FakeResponse(),
        ) as urlopen_mock:
            result = collector.collect_node({"plan": {"per_source_limit": 3}})

        self.assertEqual(1, len(result["sources"]))
        self.assertEqual("owner/ai-agent", result["sources"][0]["title"])
        self.assertEqual("github_search", result["sources"][0]["source"])
        request = urlopen_mock.call_args.args[0]
        query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
        self.assertEqual(["3"], query["per_page"])

    def test_analyze_node_uses_llm_and_accumulates_usage(self) -> None:
        """Analyze node creates structured Chinese analyses from sources."""
        from workflows import analyzer

        state = {
            "sources": [
                {
                    "title": "owner/ai-agent",
                    "source_url": "https://github.com/owner/ai-agent",
                    "summary": "AI agent framework.",
                }
            ],
            "cost_tracker": {},
        }

        with mock.patch.object(
            analyzer.model_client,
            "chat_json",
            return_value=(
                {
                    "summary": "一个 AI Agent 框架。",
                    "tags": ["AI", "Agent"],
                    "score": 0.8,
                },
                {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
        ):
            result = analyzer.analyze_node(state)

        self.assertEqual(1, len(result["analyses"]))
        self.assertEqual(8.0, result["analyses"][0]["score"])
        self.assertEqual(15, result["cost_tracker"]["total_tokens"])

    def test_organize_node_filters_and_deduplicates_without_llm_revision(self) -> None:
        """Organize node filters low scores and deduplicates URLs without LLM calls."""
        from workflows import organizer

        state = {
            "analyses": [
                {
                    "title": "A",
                    "source": "github_search",
                    "source_url": "https://example.com/a",
                    "summary": "old",
                    "content": "old content",
                    "tags": ["AI"],
                    "score": 9.0,
                },
                {
                    "title": "A duplicate",
                    "source": "github_search",
                    "source_url": "https://example.com/a",
                    "summary": "duplicate",
                    "tags": ["AI"],
                    "score": 8.0,
                },
                {
                    "title": "Low",
                    "source": "github_search",
                    "source_url": "https://example.com/low",
                    "summary": "low",
                    "tags": ["AI"],
                    "score": 5.0,
                },
            ],
            "plan": {"relevance_threshold": 0.7},
            "iteration": 1,
            "review_feedback": "摘要太短",
            "cost_tracker": {},
        }

        result = organizer.organize_node(state)

        self.assertEqual(1, len(result["articles"]))
        self.assertEqual("old", result["articles"][0]["summary"])
        self.assertEqual({}, result["cost_tracker"])

    def test_save_node_writes_articles_and_index(self) -> None:
        """Save node writes dated article files and updates index.json."""
        from workflows import saver

        article = {
            "id": "20260508-github-owner-ai-agent",
            "title": "owner/ai-agent",
            "source": "github_search",
            "source_url": "https://github.com/owner/ai-agent",
            "summary": "摘要",
            "content": "正文",
            "tags": ["AI"],
            "status": "draft",
            "published_at": None,
            "collected_at": "2026-05-08T12:00:00+08:00",
            "language": "en",
            "score": 8.0,
            "metadata": {},
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.object(saver, "ARTICLES_DIR", Path(temp_dir)):
                result = saver.save_node({"articles": [article]})
                saved_paths = result["saved_paths"]

            self.assertEqual(1, len(saved_paths))
            self.assertTrue(Path(saved_paths[0]).exists())
            self.assertTrue((Path(temp_dir) / "index.json").exists())


if __name__ == "__main__":
    unittest.main()
