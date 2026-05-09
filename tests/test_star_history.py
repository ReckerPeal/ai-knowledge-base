"""Tests for ``workflows.star_history``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workflows.star_history import enrich_with_daily_stars, find_baseline_stars


def _write_article(
    base_dir: Path,
    date: str,
    article_id: str,
    source_url: str,
    stars: int,
) -> None:
    """Write a minimal article JSON file under ``base_dir/<date>``."""
    day_dir = base_dir / date
    day_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": article_id,
        "source_url": source_url,
        "metadata": {"stars": stars},
    }
    (day_dir / f"{article_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def test_find_baseline_picks_yesterday(tmp_path: Path) -> None:
    _write_article(tmp_path, "2026-05-08", "a1", "https://x/y", 100)

    stars, baseline = find_baseline_stars(
        "https://x/y",
        "2026-05-09",
        articles_dir=tmp_path,
    )

    assert stars == 100
    assert baseline == "2026-05-08"


def test_find_baseline_skips_today_and_walks_back(tmp_path: Path) -> None:
    # Today's record exists but must be ignored.
    _write_article(tmp_path, "2026-05-09", "a-today", "https://x/y", 999)
    # Yesterday is missing entirely.
    _write_article(tmp_path, "2026-05-07", "a-old", "https://x/y", 50)

    stars, baseline = find_baseline_stars(
        "https://x/y",
        "2026-05-09",
        articles_dir=tmp_path,
    )

    assert stars == 50
    assert baseline == "2026-05-07"


def test_find_baseline_respects_lookback_window(tmp_path: Path) -> None:
    _write_article(tmp_path, "2026-05-04", "a-old", "https://x/y", 10)

    stars, baseline = find_baseline_stars(
        "https://x/y",
        "2026-05-09",
        articles_dir=tmp_path,
        max_lookback_days=3,
    )

    assert stars is None
    assert baseline is None


def test_find_baseline_returns_none_when_url_not_seen(tmp_path: Path) -> None:
    _write_article(tmp_path, "2026-05-08", "a1", "https://other/repo", 100)

    stars, baseline = find_baseline_stars(
        "https://x/y",
        "2026-05-09",
        articles_dir=tmp_path,
    )

    assert stars is None
    assert baseline is None


@pytest.mark.parametrize(
    "today_date",
    ["", "not-a-date", "2026/05/09"],
)
def test_find_baseline_handles_invalid_today_date(
    tmp_path: Path, today_date: str
) -> None:
    stars, baseline = find_baseline_stars(
        "https://x/y",
        today_date,
        articles_dir=tmp_path,
    )

    assert stars is None
    assert baseline is None


def test_find_baseline_returns_none_for_blank_url(tmp_path: Path) -> None:
    _write_article(tmp_path, "2026-05-08", "a1", "https://x/y", 100)

    stars, baseline = find_baseline_stars(
        "",
        "2026-05-09",
        articles_dir=tmp_path,
    )

    assert stars is None
    assert baseline is None


def test_enrich_computes_delta(tmp_path: Path) -> None:
    _write_article(tmp_path, "2026-05-08", "a1", "https://x/y", 100)
    source = {
        "source_url": "https://x/y",
        "metadata": {"stars": 130, "author": "owner"},
    }

    enriched = enrich_with_daily_stars(
        source,
        today_date="2026-05-09",
        articles_dir=tmp_path,
    )

    assert enriched["metadata"]["stars"] == 130
    assert enriched["metadata"]["daily_stars"] == 30
    assert enriched["metadata"]["stars_baseline_date"] == "2026-05-08"
    assert enriched["metadata"]["author"] == "owner"


def test_enrich_handles_missing_baseline(tmp_path: Path) -> None:
    source = {
        "source_url": "https://x/y",
        "metadata": {"stars": 130},
    }

    enriched = enrich_with_daily_stars(
        source,
        today_date="2026-05-09",
        articles_dir=tmp_path,
    )

    assert enriched["metadata"]["daily_stars"] is None
    assert enriched["metadata"]["stars_baseline_date"] is None


def test_enrich_does_not_mutate_input(tmp_path: Path) -> None:
    source = {
        "source_url": "https://x/y",
        "metadata": {"stars": 130},
    }

    enrich_with_daily_stars(
        source,
        today_date="2026-05-09",
        articles_dir=tmp_path,
    )

    assert "daily_stars" not in source["metadata"]


def test_enrich_handles_negative_delta(tmp_path: Path) -> None:
    """Stars can decrease (deletion / un-star); represent as negative."""
    _write_article(tmp_path, "2026-05-08", "a1", "https://x/y", 200)
    source = {
        "source_url": "https://x/y",
        "metadata": {"stars": 180},
    }

    enriched = enrich_with_daily_stars(
        source,
        today_date="2026-05-09",
        articles_dir=tmp_path,
    )

    assert enriched["metadata"]["daily_stars"] == -20
