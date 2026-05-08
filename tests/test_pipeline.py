"""Tests for the knowledge automation pipeline."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


class PipelineTest(unittest.TestCase):
    """Verify pipeline parsing, organization, and save behavior."""

    def test_parse_rss_items_extracts_basic_fields(self) -> None:
        """RSS parser extracts item title, link, date, and summary."""
        from workflows.pipeline import parse_rss_items

        rss_xml = """
        <rss><channel>
          <item>
            <title>Agent Framework Launch</title>
            <link>https://example.com/agent-framework</link>
            <description>New LLM agent framework for automation.</description>
            <pubDate>Fri, 08 May 2026 10:00:00 GMT</pubDate>
          </item>
        </channel></rss>
        """

        items = parse_rss_items(rss_xml, source_name="Example Feed", limit=5)

        self.assertEqual(1, len(items))
        self.assertEqual("Agent Framework Launch", items[0].title)
        self.assertEqual("https://example.com/agent-framework", items[0].source_url)
        self.assertEqual("Example Feed", items[0].metadata["feed_name"])

    def test_organize_articles_deduplicates_and_validates(self) -> None:
        """Organizer removes duplicate URLs and returns valid article objects."""
        from workflows.pipeline import CollectedItem, organize_articles

        item = CollectedItem(
            title="Example AI Agent Framework",
            source="github_search",
            source_url="https://github.com/example/agent-framework",
            summary="Repository about agent automation.",
            published_at=None,
            collected_at="2026-05-08T12:00:00+08:00",
            language="en",
            metadata={"author": "example", "stars": 42},
        )
        analyzed = {
            "summary": "A practical AI agent framework for workflow automation.",
            "content": "This project provides agent orchestration patterns and tools.",
            "tags": ["AI", "LLM", "Agent"],
            "score": 8.2,
        }

        articles = organize_articles([(item, analyzed), (item, analyzed)])

        self.assertEqual(1, len(articles))
        self.assertEqual("draft", articles[0]["status"])
        self.assertEqual("github_search", articles[0]["source"])
        self.assertTrue(articles[0]["id"].startswith("20260508-github_search-"))

    def test_save_articles_writes_dated_json_files(self) -> None:
        """Saving writes each article to knowledge/articles/YYYY-MM-DD."""
        from workflows.pipeline import save_articles

        article = {
            "id": "20260508-rss-example",
            "title": "Example AI Agent Framework",
            "source": "rss",
            "source_url": "https://example.com/agent",
            "summary": "A practical AI agent framework for workflow automation.",
            "content": "This article explains a practical AI agent framework.",
            "tags": ["AI", "Agent"],
            "status": "draft",
            "published_at": None,
            "collected_at": "2026-05-08T12:00:00+08:00",
            "language": "en",
            "score": 8.0,
            "metadata": {"feed_name": "Example Feed"},
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = save_articles([article], Path(temp_dir), dry_run=False)

            self.assertEqual(1, len(paths))
            saved_data = json.loads(paths[0].read_text(encoding="utf-8"))

        self.assertEqual(article["id"], saved_data["id"])
        self.assertIn("2026-05-08", str(paths[0]))


if __name__ == "__main__":
    unittest.main()
