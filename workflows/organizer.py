"""Organization node for analyzed knowledge articles."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from hashlib import sha1
from typing import Any

from workflows.state import KBState


LOGGER = logging.getLogger(__name__)

MIN_ARTICLE_SCORE = 6.0
CHINA_TZ = timezone(timedelta(hours=8))


def organize_node(state: KBState) -> dict[str, Any]:
    """Filter, deduplicate, and format analyzed articles.

    Args:
        state: Shared workflow state containing ``analyses`` and review fields.

    Returns:
        Partial state update containing ``articles`` and ``cost_tracker``.
    """
    LOGGER.info("[OrganizeNode] organizing analyzed articles")
    cost_tracker = dict(state.get("cost_tracker") or {})
    analyses = list(state.get("analyses", []))

    articles: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for analysis in analyses:
        score = _as_float(analysis.get("score"))
        if score < MIN_ARTICLE_SCORE:
            continue

        source_url = str(analysis.get("source_url") or "")
        if not source_url or source_url in seen_urls:
            continue
        seen_urls.add(source_url)

        articles.append(_format_article(analysis, score))

    return {"articles": articles, "cost_tracker": cost_tracker}


def _format_article(analysis: dict[str, Any], score: float) -> dict[str, Any]:
    """Format one analysis as a knowledge article.

    Args:
        analysis: Normalized analysis dictionary.
        score: Validated article quality score.

    Returns:
        Knowledge article dictionary.
    """
    collected_at = str(analysis.get("collected_at") or _now_iso())
    source_url = str(analysis.get("source_url") or "")
    return {
        "id": str(analysis.get("id") or _article_id(collected_at, source_url)),
        "title": str(analysis.get("title") or "未命名条目"),
        "source": str(analysis.get("source") or "github_search"),
        "source_url": source_url,
        "summary": str(analysis.get("summary") or ""),
        "content": str(analysis.get("content") or analysis.get("summary") or ""),
        "tags": _normalize_tags(analysis.get("tags")),
        "status": str(analysis.get("status") or "draft"),
        "published_at": analysis.get("published_at"),
        "collected_at": collected_at,
        "language": str(analysis.get("language") or "unknown"),
        "score": score,
        "metadata": dict(analysis.get("metadata") or {}),
    }


def _normalize_tags(value: Any) -> list[str]:
    """Normalize an arbitrary tag value to a string list.

    Args:
        value: Raw tag value.

    Returns:
        String tag list.
    """
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


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


def _article_id(collected_at: str, source_url: str) -> str:
    """Create a stable article id from collection date and source URL.

    Args:
        collected_at: ISO collection timestamp.
        source_url: Traceable source URL.

    Returns:
        Stable article id.
    """
    date_part = collected_at[:10].replace("-", "") or _now_iso()[:10].replace("-", "")
    digest = sha1(source_url.encode("utf-8")).hexdigest()[:10]
    return f"{date_part}-github-{digest}"


def _now_iso() -> str:
    """Return the current timestamp in ISO 8601 with China timezone.

    Returns:
        Current timestamp string.
    """
    return datetime.now(CHINA_TZ).isoformat(timespec="seconds")
