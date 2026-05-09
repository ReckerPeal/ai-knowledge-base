"""Lookup historical star counts to compute daily deltas."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTICLES_DIR = PROJECT_ROOT / "knowledge" / "articles"
CHINA_TZ = timezone(timedelta(hours=8))
DEFAULT_LOOKBACK_DAYS = 3


def find_baseline_stars(
    source_url: str,
    today_date: str,
    *,
    articles_dir: Path | None = None,
    max_lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> tuple[int | None, str | None]:
    """Find the most recent prior-day stars count for a source URL.

    Args:
        source_url: Canonical source URL to match.
        today_date: Today's date in ``YYYY-MM-DD``; lookback excludes this day.
        articles_dir: Directory containing per-day article folders.
        max_lookback_days: Maximum days to look back for a baseline.

    Returns:
        ``(stars, baseline_date)`` from the most recent matching snapshot,
        or ``(None, None)`` when no prior snapshot is found within the lookback
        window.
    """
    if not source_url:
        return None, None
    base_dir = articles_dir or ARTICLES_DIR
    try:
        today = datetime.strptime(today_date, "%Y-%m-%d").date()
    except ValueError:
        LOGGER.warning("[StarHistory] invalid today_date=%s", today_date)
        return None, None

    for offset in range(1, max_lookback_days + 1):
        target_date = (today - timedelta(days=offset)).isoformat()
        date_dir = base_dir / target_date
        if not date_dir.is_dir():
            continue
        stars = _scan_day_for_url(date_dir, source_url)
        if stars is not None:
            return stars, target_date
    return None, None


def enrich_with_daily_stars(
    source: dict[str, Any],
    *,
    today_date: str | None = None,
    articles_dir: Path | None = None,
    max_lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> dict[str, Any]:
    """Return a copy of ``source`` with daily-star fields added to metadata.

    Args:
        source: A source dictionary produced by the collector.
        today_date: Override today's date (mainly for tests).
        articles_dir: Override articles directory (mainly for tests).
        max_lookback_days: Maximum days to look back for a baseline.

    Returns:
        New source dict with ``metadata.daily_stars`` and
        ``metadata.stars_baseline_date`` set. The original is not mutated.
        Both fields are ``None`` when no baseline is found within the
        lookback window or current stars is missing.
    """
    enriched = dict(source)
    metadata = dict(enriched.get("metadata") or {})

    current_stars = metadata.get("stars")
    source_url = str(enriched.get("source_url") or "")
    today = today_date or datetime.now(CHINA_TZ).date().isoformat()

    baseline_stars, baseline_date = find_baseline_stars(
        source_url,
        today,
        articles_dir=articles_dir,
        max_lookback_days=max_lookback_days,
    )

    if isinstance(current_stars, int) and isinstance(baseline_stars, int):
        metadata["daily_stars"] = current_stars - baseline_stars
        metadata["stars_baseline_date"] = baseline_date
    else:
        metadata["daily_stars"] = None
        metadata["stars_baseline_date"] = baseline_date

    enriched["metadata"] = metadata
    return enriched


def _scan_day_for_url(date_dir: Path, source_url: str) -> int | None:
    """Find the stars count for ``source_url`` in a single date directory.

    Args:
        date_dir: Directory containing a day's article JSON files.
        source_url: Canonical source URL to match.

    Returns:
        Stars count from the matching article, or ``None`` if not found.
    """
    for file_path in sorted(date_dir.glob("*.json")):
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            LOGGER.warning("[StarHistory] failed to read %s", file_path)
            continue
        if not isinstance(data, dict):
            continue
        if str(data.get("source_url") or "") != source_url:
            continue
        metadata = data.get("metadata") or {}
        if not isinstance(metadata, dict):
            continue
        stars = metadata.get("stars")
        if isinstance(stars, bool):
            continue
        if isinstance(stars, int):
            return stars
        if isinstance(stars, (str, float)):
            try:
                return int(stars)
            except (TypeError, ValueError):
                continue
    return None
