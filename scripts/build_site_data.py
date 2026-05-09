#!/usr/bin/env python3
"""Generate static site data files under ``docs/data/``.

Outputs:

* ``docs/data/index.json``      — full deduped article list (search/all view).
* ``docs/data/by_date/<YYYY-MM-DD>.json`` — per-day article snapshots.
* ``docs/data/dates.json``      — ``[{date, count}]`` sorted desc, drives the
                                   history-tab dropdown and identifies the
                                   default "today" tab.

The script is intentionally idempotent and safe to run locally for preview.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTICLES_DIR = PROJECT_ROOT / "knowledge" / "articles"
DOCS_DATA_DIR = PROJECT_ROOT / "docs" / "data"
DATE_DIR_PATTERN = "????-??-??"

LOGGER = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Build site data under docs/data/.")
    parser.add_argument(
        "--articles-dir",
        type=Path,
        default=ARTICLES_DIR,
        help="Source articles directory (default: knowledge/articles).",
    )
    parser.add_argument(
        "--docs-data-dir",
        type=Path,
        default=DOCS_DATA_DIR,
        help="Output directory (default: docs/data).",
    )
    return parser.parse_args()


def build(articles_dir: Path, docs_data_dir: Path) -> dict[str, int]:
    """Build all site data files. Returns a small stats dict."""
    if docs_data_dir.exists():
        # Wipe to keep removed dates from lingering.
        shutil.rmtree(docs_data_dir)
    docs_data_dir.mkdir(parents=True, exist_ok=True)

    by_date_dir = docs_data_dir / "by_date"
    by_date_dir.mkdir(parents=True, exist_ok=True)

    dates_summary: list[dict[str, Any]] = []
    by_date_total = 0
    for date_dir in sorted(articles_dir.glob(DATE_DIR_PATTERN), reverse=True):
        if not date_dir.is_dir():
            continue
        items = _load_day(date_dir)
        if not items:
            continue
        items.sort(key=_score_then_stars_desc)
        target = by_date_dir / f"{date_dir.name}.json"
        target.write_text(
            json.dumps(items, ensure_ascii=False), encoding="utf-8"
        )
        dates_summary.append({"date": date_dir.name, "count": len(items)})
        by_date_total += len(items)

    (docs_data_dir / "dates.json").write_text(
        json.dumps(dates_summary, ensure_ascii=False), encoding="utf-8"
    )

    src_index = articles_dir / "index.json"
    dst_index = docs_data_dir / "index.json"
    if src_index.exists():
        dst_index.write_text(src_index.read_text(encoding="utf-8"), encoding="utf-8")
        index_count = _safe_count(dst_index)
    else:
        dst_index.write_text("[]\n", encoding="utf-8")
        index_count = 0

    return {
        "dates": len(dates_summary),
        "by_date_articles": by_date_total,
        "index_articles": index_count,
    }


def _load_day(date_dir: Path) -> list[dict[str, Any]]:
    """Load all article JSON files in a single day directory."""
    items: list[dict[str, Any]] = []
    for file_path in sorted(date_dir.glob("*.json")):
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            LOGGER.warning("[BuildSite] failed to read %s", file_path)
            continue
        if isinstance(data, dict):
            items.append(data)
    return items


def _score_then_stars_desc(article: dict[str, Any]) -> tuple[float, int]:
    """Sort key: highest daily_stars first, then score, then total stars."""
    metadata = article.get("metadata") or {}
    daily = metadata.get("daily_stars")
    daily_val = float(daily) if isinstance(daily, (int, float)) else float("-inf")
    stars = metadata.get("stars")
    stars_val = int(stars) if isinstance(stars, int) else 0
    score = article.get("score")
    score_val = float(score) if isinstance(score, (int, float)) else 0.0
    return (-daily_val, -score_val, -stars_val)


def _safe_count(json_path: Path) -> int:
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    return len(data) if isinstance(data, list) else 0


def main() -> int:
    """Entry point."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = parse_args()
    stats = build(args.articles_dir, args.docs_data_dir)
    LOGGER.info(
        "[BuildSite] dates=%s by_date_articles=%s index_articles=%s",
        stats["dates"],
        stats["by_date_articles"],
        stats["index_articles"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
