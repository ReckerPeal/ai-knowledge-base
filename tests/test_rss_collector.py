"""Tests for ``workflows.rss_collector``."""

from __future__ import annotations

from pathlib import Path

import pytest

from workflows import rss_collector


SAMPLE_RSS = """<?xml version="1.0"?>
<rss version="2.0">
<channel>
  <title>Sample AI Feed</title>
  <item>
    <title>OpenAI launches new agent SDK</title>
    <link>https://example.com/agent-sdk</link>
    <description><![CDATA[A new SDK for building AI agents with LLM tool calls.]]></description>
    <pubDate>Mon, 09 May 2026 02:30:00 GMT</pubDate>
  </item>
  <item>
    <title>Off-topic gardening post</title>
    <link>https://example.com/garden</link>
    <description>How to grow tomatoes.</description>
    <pubDate>Mon, 09 May 2026 03:00:00 GMT</pubDate>
  </item>
  <item>
    <title>Anthropic publishes Claude benchmark</title>
    <link>https://example.com/claude-bench</link>
    <description>New benchmark numbers for Claude on agent tasks.</description>
  </item>
</channel>
</rss>
"""


SAMPLE_ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Sample Atom Feed</title>
  <entry>
    <title>Transformer scaling laws revisited</title>
    <link href="https://example.com/atom-1"/>
    <summary>New paper on transformer scaling laws.</summary>
    <published>2026-05-09T04:00:00Z</published>
  </entry>
  <entry>
    <title>Garden Atom Post</title>
    <link href="https://example.com/atom-2"/>
    <summary>Off-topic content.</summary>
    <published>2026-05-09T05:00:00Z</published>
  </entry>
</feed>
"""


SAMPLE_YAML = """sources:
  - name: "Test AI Feed"
    url: "https://example.com/feed.xml"
    category: "公司博客"
    enabled: true

  - name: "Disabled Source"
    url: "https://example.com/disabled.xml"
    category: "综合技术"
    enabled: false

  - name: "Atom AI Feed"
    url: "https://example.com/atom.xml"
    category: "AI 研究"
    enabled: true
"""


def _write_yaml(tmp_path: Path) -> Path:
    config = tmp_path / "rss_sources.yaml"
    config.write_text(SAMPLE_YAML, encoding="utf-8")
    return config


def test_load_rss_sources_filters_disabled(tmp_path: Path) -> None:
    config = _write_yaml(tmp_path)
    sources = rss_collector.load_rss_sources(config)
    names = [src["name"] for src in sources]
    assert names == ["Test AI Feed", "Atom AI Feed"]


def test_parse_rss_items_extracts_fields_and_metadata() -> None:
    items = rss_collector.parse_rss_items(
        SAMPLE_RSS,
        feed_name="Test AI Feed",
        category="公司博客",
        limit=10,
        collected_at="2026-05-09T18:00:00+08:00",
    )
    assert len(items) == 3

    first = items[0]
    assert first["title"] == "OpenAI launches new agent SDK"
    assert first["source"] == "rss"
    assert first["source_url"] == "https://example.com/agent-sdk"
    assert "agent" in first["summary"].lower()
    assert first["language"] == "en"
    assert first["metadata"]["feed_name"] == "Test AI Feed"
    assert first["metadata"]["category"] == "公司博客"
    assert first["metadata"]["stars"] is None
    assert first["published_at"] is not None and first["published_at"].startswith("2026")


def test_parse_atom_entries() -> None:
    items = rss_collector.parse_rss_items(
        SAMPLE_ATOM,
        feed_name="Atom AI Feed",
        category="AI 研究",
        limit=10,
        collected_at="2026-05-09T18:00:00+08:00",
    )
    assert len(items) == 2
    titles = [it["title"] for it in items]
    assert "Transformer scaling laws revisited" in titles


def test_fetch_all_rss_pipeline_filters_off_topic(tmp_path: Path) -> None:
    config = _write_yaml(tmp_path)

    feed_payloads = {
        "https://example.com/feed.xml": SAMPLE_RSS,
        "https://example.com/atom.xml": SAMPLE_ATOM,
    }

    def fake_fetch(url: str) -> str:
        return feed_payloads[url]

    items = rss_collector.fetch_all_rss(
        per_source_limit=10,
        config_path=config,
        fetcher=fake_fetch,
        collected_at="2026-05-09T18:00:00+08:00",
    )

    titles = sorted(it["title"] for it in items)
    # Off-topic gardening posts must be filtered out by AI keyword guard.
    assert "Off-topic gardening post" not in titles
    assert "Garden Atom Post" not in titles
    # Both AI-related ones must be retained.
    assert "OpenAI launches new agent SDK" in titles
    assert "Anthropic publishes Claude benchmark" in titles
    assert "Transformer scaling laws revisited" in titles


def test_fetch_all_rss_returns_empty_when_limit_zero(tmp_path: Path) -> None:
    config = _write_yaml(tmp_path)
    items = rss_collector.fetch_all_rss(
        per_source_limit=0,
        config_path=config,
        fetcher=lambda _u: SAMPLE_RSS,
    )
    assert items == []


def test_fetch_all_rss_recovers_from_one_feed_failure(tmp_path: Path) -> None:
    config = _write_yaml(tmp_path)

    def fake_fetch(url: str) -> str:
        if "atom.xml" in url:
            raise RuntimeError("simulated network failure")
        return SAMPLE_RSS

    items = rss_collector.fetch_all_rss(
        per_source_limit=10,
        config_path=config,
        fetcher=fake_fetch,
    )
    # Only the working RSS feed should contribute.
    assert all(it["metadata"]["feed_name"] == "Test AI Feed" for it in items)
    assert len(items) >= 2  # 2 of 3 are AI-related


def test_guess_language_chinese_text() -> None:
    assert rss_collector._guess_language("人工智能 大语言模型") == "zh"
    assert rss_collector._guess_language("Mixed AI 模型 content") == "zh"
    assert rss_collector._guess_language("pure english text only") == "en"


def test_extract_yaml_helpers_handle_quotes() -> None:
    block = 'name: "OpenAI Blog"\nurl: \'https://x.com\'\nenabled: true'
    assert rss_collector._extract_yaml_string(block, "name") == "OpenAI Blog"
    assert rss_collector._extract_yaml_string(block, "url") == "https://x.com"
    assert rss_collector._extract_yaml_bool(block, "enabled", default=False) is True
    assert rss_collector._extract_yaml_bool(block, "missing", default=True) is True


@pytest.mark.parametrize(
    "date_str,expected_prefix",
    [
        ("Mon, 09 May 2026 02:30:00 GMT", "2026-05-09"),
        ("2026-05-09T04:00:00Z", "2026-05-09"),
        ("", None),
        ("not-a-date", None),
    ],
)
def test_parse_rss_date(date_str: str, expected_prefix: str | None) -> None:
    result = rss_collector._parse_rss_date(date_str)
    if expected_prefix is None:
        assert result is None
    else:
        assert result is not None
        assert result.startswith(expected_prefix)
