"""Tests for the local knowledge-base MCP server."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any


def write_article(directory: Path, article: dict[str, Any]) -> None:
    """Write a knowledge article fixture to a dated subdirectory."""
    article_dir = directory / "2026-05-08"
    article_dir.mkdir(parents=True, exist_ok=True)
    article_path = article_dir / f"{article['id']}.json"
    article_path.write_text(json.dumps(article, ensure_ascii=False), encoding="utf-8")


class KnowledgeServerTest(unittest.TestCase):
    """Verify MCP tool behavior and JSON-RPC dispatch."""

    def test_search_articles_matches_title_and_summary_with_limit(self) -> None:
        """Search returns matching articles from title and summary fields."""
        from mcp_knowledge_server import KnowledgeBase

        with tempfile.TemporaryDirectory() as temp_dir:
            articles_dir = Path(temp_dir)
            write_article(
                articles_dir,
                {
                    "id": "github-20260326-001",
                    "title": "langgenius/dify",
                    "source": "github",
                    "summary": "Open-source LLM app platform with agent workflows.",
                    "score": 7,
                    "tags": ["agent", "llm"],
                },
            )
            write_article(
                articles_dir,
                {
                    "id": "github-20260326-002",
                    "title": "Vector database notes",
                    "source": "hacker_news",
                    "summary": "Agent memory retrieval patterns.",
                    "score": 8,
                    "tags": ["agent", "rag"],
                },
            )

            knowledge_base = KnowledgeBase(articles_dir)
            results = knowledge_base.search_articles("agent", limit=1)

        self.assertEqual(1, len(results))
        self.assertEqual("github-20260326-001", results[0]["id"])
        self.assertIn("summary", results[0])

    def test_get_article_returns_full_article_by_id(self) -> None:
        """Article lookup returns the complete stored JSON object."""
        from mcp_knowledge_server import KnowledgeBase

        article = {
            "id": "github-20260326-001",
            "title": "langgenius/dify",
            "source": "github",
            "summary": "Open-source LLM app platform.",
            "content": "Full content.",
            "score": 7,
            "tags": ["agent", "llm"],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            articles_dir = Path(temp_dir)
            write_article(articles_dir, article)
            knowledge_base = KnowledgeBase(articles_dir)

            result = knowledge_base.get_article("github-20260326-001")

        self.assertEqual(article, result)

    def test_knowledge_stats_returns_counts_sources_and_tags(self) -> None:
        """Stats include total count, source distribution, and popular tags."""
        from mcp_knowledge_server import KnowledgeBase

        with tempfile.TemporaryDirectory() as temp_dir:
            articles_dir = Path(temp_dir)
            write_article(
                articles_dir,
                {
                    "id": "github-20260326-001",
                    "title": "A",
                    "source": "github",
                    "summary": "A",
                    "score": 7,
                    "tags": ["agent", "llm"],
                },
            )
            write_article(
                articles_dir,
                {
                    "id": "hn-20260326-001",
                    "title": "B",
                    "source": "hacker_news",
                    "summary": "B",
                    "score": 8,
                    "tags": ["agent"],
                },
            )

            stats = KnowledgeBase(articles_dir).knowledge_stats()

        self.assertEqual(2, stats["total_articles"])
        self.assertEqual({"github": 1, "hacker_news": 1}, stats["source_distribution"])
        self.assertEqual({"agent": 2, "llm": 1}, stats["popular_tags"])

    def test_json_rpc_supports_initialize_tools_list_and_tool_call(self) -> None:
        """JSON-RPC dispatcher handles required MCP methods."""
        from mcp_knowledge_server import KnowledgeBase, handle_request

        with tempfile.TemporaryDirectory() as temp_dir:
            articles_dir = Path(temp_dir)
            write_article(
                articles_dir,
                {
                    "id": "github-20260326-001",
                    "title": "langgenius/dify",
                    "source": "github",
                    "summary": "Agent workflow builder.",
                    "score": 7,
                    "tags": ["agent", "llm"],
                },
            )
            knowledge_base = KnowledgeBase(articles_dir)

            init_response = handle_request(
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                knowledge_base,
            )
            list_response = handle_request(
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                knowledge_base,
            )
            call_response = handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "search_articles",
                        "arguments": {"keyword": "agent", "limit": 5},
                    },
                },
                knowledge_base,
            )

        self.assertEqual("2.0", init_response["jsonrpc"])
        self.assertEqual("knowledge-server", init_response["result"]["serverInfo"]["name"])
        tool_names = [tool["name"] for tool in list_response["result"]["tools"]]
        self.assertEqual(["search_articles", "get_article", "knowledge_stats"], tool_names)
        content = call_response["result"]["content"][0]
        self.assertEqual("text", content["type"])
        self.assertIn("github-20260326-001", content["text"])


if __name__ == "__main__":
    unittest.main()
