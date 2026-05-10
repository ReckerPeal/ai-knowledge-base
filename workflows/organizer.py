"""Organization node for analyzed knowledge articles."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from hashlib import sha1
from typing import Any

from tests.security import filter_output
from workflows.state import KBState


LOGGER = logging.getLogger(__name__)

DEFAULT_RELEVANCE_THRESHOLD = 0.5
CHINA_TZ = timezone(timedelta(hours=8))
ARTICLE_TEXT_FIELDS = ("title", "source", "summary", "content", "language")
MIN_SUMMARY_LENGTH = 20
MAX_PADDED_SUMMARY_LENGTH = 200


def organize_node(state: KBState) -> dict[str, Any]:
    """Filter, deduplicate, and format analyzed articles.

    Args:
        state: Shared workflow state containing ``analyses`` and review fields.

    Returns:
        Partial state update containing ``articles`` and ``cost_tracker``.
    """
    LOGGER.info("[OrganizeNode] organizing analyzed articles")
    plan = state.get("plan", {}) or {}
    threshold = float(plan.get("relevance_threshold", DEFAULT_RELEVANCE_THRESHOLD))
    cost_tracker = dict(state.get("cost_tracker") or {})
    analyses = list(state.get("analyses", []))

    articles: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    dropped_short = 0
    for analysis in analyses:
        score = _as_float(analysis.get("score"))
        if _relevance_score(score) < threshold:
            continue

        source_url = str(analysis.get("source_url") or "")
        if not source_url or source_url in seen_urls:
            continue

        article = _format_article(analysis, score)
        if len(article["summary"]) < MIN_SUMMARY_LENGTH:
            dropped_short += 1
            LOGGER.warning(
                "[OrganizeNode] dropped article with short summary (%s chars) url=%s",
                len(article["summary"]),
                source_url,
            )
            continue

        seen_urls.add(source_url)
        articles.append(_filter_article_output(article))

    if dropped_short:
        LOGGER.warning(
            "[OrganizeNode] dropped %s analyses for short summary",
            dropped_short,
        )
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
    raw_summary = str(analysis.get("summary") or "").strip()
    content = str(analysis.get("content") or raw_summary).strip()
    summary = _ensure_summary_length(raw_summary, content)
    return {
        "id": str(analysis.get("id") or _article_id(collected_at, source_url)),
        "title": str(analysis.get("title") or "未命名条目"),
        "source": str(analysis.get("source") or "github_search"),
        "source_url": source_url,
        "summary": summary,
        "content": content,
        "tags": _normalize_tags(analysis.get("tags")),
        "status": str(analysis.get("status") or "draft"),
        "published_at": analysis.get("published_at"),
        "collected_at": collected_at,
        "language": str(analysis.get("language") or "unknown"),
        "score": score,
        "metadata": dict(analysis.get("metadata") or {}),
    }


def _ensure_summary_length(summary: str, content: str) -> str:
    """Pad a too-short summary using the article's longer ``content``.

    If the LLM returns a summary below ``MIN_SUMMARY_LENGTH`` characters
    (after stripping), prefer the first sentence/snippet of ``content`` to
    avoid downstream JSON-validator failures while still preserving the
    LLM's structured output. Returns the original summary when it is long
    enough, or an empty string when no usable text is available; the
    organize node will drop empty results.

    Args:
        summary: Raw summary from the analyzer.
        content: Longer analysis content from the analyzer.

    Returns:
        Summary text guaranteed to be >= ``MIN_SUMMARY_LENGTH`` characters
        when content is available, otherwise the original (possibly empty)
        summary that callers can drop.
    """
    if len(summary) >= MIN_SUMMARY_LENGTH:
        return summary
    if not content:
        return summary

    snippet = content.replace("\n", " ").strip()
    if len(snippet) > MAX_PADDED_SUMMARY_LENGTH:
        snippet = snippet[:MAX_PADDED_SUMMARY_LENGTH].rstrip() + "…"

    if not summary:
        return snippet if len(snippet) >= MIN_SUMMARY_LENGTH else summary

    if summary in snippet:
        return snippet if len(snippet) >= MIN_SUMMARY_LENGTH else summary

    combined = f"{summary} — {snippet}"
    if len(combined) > MAX_PADDED_SUMMARY_LENGTH:
        combined = combined[:MAX_PADDED_SUMMARY_LENGTH].rstrip() + "…"
    return combined if len(combined) >= MIN_SUMMARY_LENGTH else summary


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


def _filter_article_output(article: dict[str, Any]) -> dict[str, Any]:
    """Mask PII in article output fields.

    Args:
        article: Sanitized article dictionary.

    Returns:
        Article dictionary with PII masked in text fields and tags.
    """
    total_detections = _filter_text_fields(
        article,
        ARTICLE_TEXT_FIELDS,
        url=str(article.get("source_url") or "?"),
    )
    total_detections += _filter_tag_list(article)
    if total_detections > 0:
        LOGGER.warning(
            "[Security] organize stage masked %s PII occurrence(s)",
            total_detections,
        )
    return article


def _filter_tag_list(article: dict[str, Any]) -> int:
    """Mask PII in string tags.

    Args:
        article: Article dictionary containing optional tags.

    Returns:
        Number of PII detections.
    """
    tags = article.get("tags")
    if not isinstance(tags, list):
        return 0

    filtered_tags: list[str] = []
    total_detections = 0
    for tag in tags:
        if not isinstance(tag, str):
            continue
        filtered, detections = filter_output(tag, mask=True)
        filtered_tags.append(filtered)
        total_detections += len(detections)
        if detections:
            LOGGER.warning(
                "[Security] %s tags masked PII types: %s",
                article.get("source_url") or "?",
                sorted({item["type"] for item in detections}),
            )
    article["tags"] = filtered_tags
    return total_detections


def _filter_text_fields(
    payload: dict[str, Any],
    fields: tuple[str, ...],
    *,
    url: str,
) -> int:
    """Mask PII in selected string fields.

    Args:
        payload: Dictionary containing article output fields.
        fields: Field names to filter when present.
        url: Source URL used in security logs.

    Returns:
        Number of PII detections.
    """
    total_detections = 0
    for field in fields:
        value = payload.get(field)
        if not isinstance(value, str):
            continue
        filtered, detections = filter_output(value, mask=True)
        payload[field] = filtered
        total_detections += len(detections)
        if detections:
            LOGGER.warning(
                "[Security] %s %s masked PII types: %s",
                url,
                field,
                sorted({item["type"] for item in detections}),
            )
    return total_detections


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


def _relevance_score(score: float) -> float:
    """Normalize an article score to the plan threshold's 0-1 scale.

    Args:
        score: Article score, usually on the knowledge schema's 1-10 scale.

    Returns:
        Relevance score normalized to 0-1.
    """
    if score > 1:
        return score / 10
    return score


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
