"""Persistence node for knowledge articles."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from workflows.state import KBState


LOGGER = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTICLES_DIR = PROJECT_ROOT / "knowledge" / "articles"


def save_node(state: KBState) -> dict[str, Any]:
    """Write articles to disk and update ``index.json``.

    Args:
        state: Shared workflow state containing ``articles``.

    Returns:
        Partial state update containing ``saved_paths``.
    """
    LOGGER.info("[SaveNode] saving articles and index")
    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)

    saved_paths: list[str] = []
    articles = list(state.get("articles", []))
    for article in articles:
        _validate_article(article)
        date_dir = str(article["collected_at"])[:10]
        article_path = ARTICLES_DIR / date_dir / f"{article['id']}.json"
        article_path.parent.mkdir(parents=True, exist_ok=True)
        article_path.write_text(
            json.dumps(article, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        saved_paths.append(str(article_path))

    index_path = ARTICLES_DIR / "index.json"
    index_articles = _merge_index(index_path, articles)
    index_path.write_text(
        json.dumps(index_articles, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return {"saved_paths": saved_paths}


def _merge_index(index_path: Path, articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge saved articles into the local index by ``source_url``.

    Args:
        index_path: Index file path.
        articles: Newly saved articles.

    Returns:
        Merged article index.
    """
    existing: list[dict[str, Any]] = []
    if index_path.exists():
        try:
            loaded = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            LOGGER.exception("[SaveNode] failed to read existing index")
            loaded = []
        if isinstance(loaded, list):
            existing = [item for item in loaded if isinstance(item, dict)]
        elif isinstance(loaded, dict) and isinstance(loaded.get("articles"), list):
            existing = [item for item in loaded["articles"] if isinstance(item, dict)]

    merged: dict[str, dict[str, Any]] = {}
    for article in existing + articles:
        source_url = str(article.get("source_url") or article.get("id") or "")
        if source_url:
            merged[source_url] = article
    return list(merged.values())


def _validate_article(article: dict[str, Any]) -> None:
    """Validate required article fields before writing JSON.

    Args:
        article: Knowledge article dictionary.

    Raises:
        ValueError: If a required field is missing.
    """
    required_fields = {
        "id",
        "title",
        "source",
        "source_url",
        "summary",
        "content",
        "tags",
        "status",
        "collected_at",
        "score",
        "metadata",
    }
    missing_fields = sorted(field for field in required_fields if field not in article)
    if missing_fields:
        raise ValueError(f"article missing required fields: {missing_fields}")
    if not isinstance(article["tags"], list):
        raise ValueError("article tags must be a list")
    if not 1 <= _as_float(article["score"]) <= 10:
        raise ValueError("article score must be between 1 and 10")


def _as_float(value: Any) -> float:
    """Convert a value to float with a safe default.

    Args:
        value: Raw numeric value.

    Returns:
        Float value, defaulting to ``0.0``.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
