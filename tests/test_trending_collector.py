"""Tests for ``workflows.trending_collector``."""

from __future__ import annotations

from pathlib import Path

import pytest

from workflows import trending_collector
from workflows.trending_collector import build_url, fetch_trending


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _read_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def test_build_url_with_language_and_window() -> None:
    assert (
        build_url("python", "daily")
        == "https://github.com/trending/python?since=daily"
    )


def test_build_url_handles_uppercase_and_whitespace() -> None:
    assert (
        build_url("  TypeScript ", "weekly")
        == "https://github.com/trending/typescript?since=weekly"
    )


def test_build_url_without_language() -> None:
    assert build_url("", "daily") == "https://github.com/trending?since=daily"


def test_build_url_treats_all_as_no_filter() -> None:
    assert build_url("all", "daily") == "https://github.com/trending?since=daily"


def test_fetch_rejects_invalid_window() -> None:
    with pytest.raises(ValueError):
        fetch_trending("python", since="hourly")


def test_fetch_returns_empty_when_limit_zero() -> None:
    items = fetch_trending(
        "python",
        since="daily",
        limit=0,
        fetcher=lambda _u: _read_fixture("trending_python_daily.html"),
    )
    assert items == []


def test_fetch_parses_full_article() -> None:
    items = fetch_trending(
        "python",
        since="daily",
        fetcher=lambda _u: _read_fixture("trending_python_daily.html"),
        collected_at="2026-05-09T18:00:00+08:00",
    )
    assert len(items) == 3

    first = items[0]
    assert first["title"] == "owner1/repo-one"
    assert first["source"] == "github_trending"
    assert first["source_url"] == "https://github.com/owner1/repo-one"
    assert first["summary"] == "First repo description."
    assert first["language"] == "Python"
    assert first["collected_at"] == "2026-05-09T18:00:00+08:00"

    metadata = first["metadata"]
    assert metadata["author"] == "owner1"
    assert metadata["stars"] == 12345
    assert metadata["forks"] == 678
    assert metadata["daily_stars"] == 100
    assert metadata["weekly_stars"] is None
    assert metadata["monthly_stars"] is None
    assert metadata["stars_baseline_date"] is None


def test_fetch_handles_missing_description_and_language() -> None:
    items = fetch_trending(
        "python",
        since="daily",
        fetcher=lambda _u: _read_fixture("trending_python_daily.html"),
    )
    second = items[1]
    assert second["title"] == "owner2/repo-two"
    assert second["summary"] == ""
    assert second["language"] == "unknown"
    assert second["metadata"]["forks"] == 0
    assert second["metadata"]["daily_stars"] == 50


def test_fetch_weekly_window_populates_weekly_stars() -> None:
    items = fetch_trending(
        "python",
        since="weekly",
        fetcher=lambda _u: _read_fixture("trending_python_weekly.html"),
    )
    assert len(items) == 1
    metadata = items[0]["metadata"]
    assert metadata["weekly_stars"] == 2500
    assert metadata["daily_stars"] is None
    assert metadata["monthly_stars"] is None


def test_fetch_respects_limit() -> None:
    items = fetch_trending(
        "python",
        since="daily",
        limit=2,
        fetcher=lambda _u: _read_fixture("trending_python_daily.html"),
    )
    assert len(items) == 2


def test_fetch_returns_empty_for_unknown_html() -> None:
    items = fetch_trending(
        "python",
        since="daily",
        fetcher=lambda _u: "<html><body><p>nothing here</p></body></html>",
    )
    assert items == []


def test_fetch_uses_china_timezone_when_collected_at_omitted(monkeypatch) -> None:
    items = fetch_trending(
        "python",
        since="daily",
        fetcher=lambda _u: _read_fixture("trending_python_daily.html"),
    )
    # Should produce an ISO 8601 timestamp ending with +08:00.
    assert items, "fixture should yield at least one item"
    assert items[0]["collected_at"].endswith("+08:00")


def test_internal_int_parser_handles_commas_and_garbage() -> None:
    assert trending_collector._parse_int("12,345") == 12345
    assert trending_collector._parse_int("100 stars") == 100
    assert trending_collector._parse_int("") == 0
    assert trending_collector._parse_int("no digits") == 0
