"""Collection node backed by GitHub Trending HTML scraping.

This node replaces the legacy GitHub Search API call with the public
``github.com/trending`` page. Trending offers two advantages over Search:

1. Items are ranked by short-window star velocity, surfacing freshly hot
   projects instead of perennially-large repos.
2. Each card already exposes the ``X stars today / this week`` delta, so
   ``metadata.daily_stars`` / ``metadata.weekly_stars`` come straight from the
   source instead of being derived via cross-day diffing.

For each (language, window) pair in the active plan we fetch one trending
page, merge results by repository URL (so the same repo can carry both
``daily_stars`` and ``weekly_stars``), and emit a sanitized list of source
dictionaries compatible with the downstream analyzer.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from tests.security import sanitize_input
from workflows.rss_collector import fetch_all_rss
from workflows.state import KBState
from workflows.trending_collector import VALID_WINDOWS, fetch_trending


LOGGER = logging.getLogger(__name__)

DEFAULT_PER_SOURCE_LIMIT = 20
DEFAULT_LANGUAGES: tuple[str, ...] = ("python", "typescript", "rust", "go")
DEFAULT_WINDOWS: tuple[str, ...] = ("daily", "weekly")
DEFAULT_INCLUDE_RSS = True
INTER_REQUEST_DELAY_SECONDS = 1.0
SOURCE_TEXT_FIELDS = ("title", "summary", "description", "language")
SOURCE_METADATA_TEXT_FIELDS = ("author", "feed_name", "category")

ENV_LANGUAGES = "TRENDING_LANGUAGES"
ENV_WINDOWS = "TRENDING_WINDOWS"
ENV_INCLUDE_RSS = "INCLUDE_RSS"


def collect_node(state: KBState) -> dict[str, Any]:
    """Collect trending GitHub repositories for the configured languages.

    Args:
        state: Shared workflow state. ``state["plan"]`` may carry
            ``per_source_limit``, ``languages`` (list[str]), and
            ``windows`` (list[str]).

    Returns:
        Partial state update containing ``sources``.
    """
    plan = state.get("plan", {}) or {}
    limit = max(1, int(plan.get("per_source_limit", DEFAULT_PER_SOURCE_LIMIT)))
    languages = _resolve_languages(plan)
    windows = _resolve_windows(plan)
    include_rss = _resolve_include_rss(plan)

    LOGGER.info(
        "[CollectNode] trending languages=%s windows=%s rss=%s limit=%s",
        languages,
        windows,
        include_rss,
        limit,
    )

    raw_sources: list[dict[str, Any]] = []
    fetch_count = 0
    for window in windows:
        for language in languages:
            if fetch_count > 0:
                time.sleep(INTER_REQUEST_DELAY_SECONDS)
            fetch_count += 1
            try:
                items = fetch_trending(
                    language=language,
                    since=window,
                    limit=limit,
                )
            except RuntimeError as exc:
                LOGGER.warning(
                    "[CollectNode] trending fetch failed lang=%s since=%s: %s",
                    language,
                    window,
                    exc,
                )
                continue
            raw_sources.extend(items)

    if include_rss:
        try:
            rss_items = fetch_all_rss(per_source_limit=limit)
        except RuntimeError as exc:
            LOGGER.warning("[CollectNode] rss fetch failed: %s", exc)
            rss_items = []
        raw_sources.extend(rss_items)
        fetch_count += 1

    merged_sources = _merge_by_url(raw_sources)
    cleaned_sources, total_warnings = _sanitize_sources(merged_sources)
    if total_warnings > 0:
        LOGGER.warning(
            "[Security] collect stage blocked %s suspicious input(s)",
            total_warnings,
        )

    LOGGER.info(
        "[CollectNode] collected %s unique source(s) from %s fetch step(s)",
        len(cleaned_sources),
        fetch_count,
    )
    return {"sources": cleaned_sources}


def _resolve_languages(plan: dict[str, Any]) -> list[str]:
    """Resolve languages from plan / env / defaults."""
    raw = plan.get("languages") or os.environ.get(ENV_LANGUAGES) or ""
    return _normalise_csv(raw, fallback=DEFAULT_LANGUAGES)


def _resolve_windows(plan: dict[str, Any]) -> list[str]:
    """Resolve windows from plan / env / defaults, filtered to known values."""
    raw = plan.get("windows") or os.environ.get(ENV_WINDOWS) or ""
    candidates = _normalise_csv(raw, fallback=DEFAULT_WINDOWS)
    valid = [item for item in candidates if item in VALID_WINDOWS]
    return valid or list(DEFAULT_WINDOWS)


def _resolve_include_rss(plan: dict[str, Any]) -> bool:
    """Resolve whether RSS feeds participate in this collect cycle."""
    if "include_rss" in plan:
        return bool(plan["include_rss"])
    raw_env = os.environ.get(ENV_INCLUDE_RSS)
    if raw_env is None:
        return DEFAULT_INCLUDE_RSS
    return raw_env.strip().lower() in {"1", "true", "yes", "on"}


def _normalise_csv(raw: Any, *, fallback: tuple[str, ...]) -> list[str]:
    """Normalise a list/CSV value into a clean lowercase list."""
    if isinstance(raw, list):
        items = [str(x).strip().lower() for x in raw]
    else:
        items = [chunk.strip().lower() for chunk in str(raw).split(",")]
    items = [item for item in items if item]
    return items or list(fallback)


def _merge_by_url(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge sources keyed by ``source_url``, combining star deltas."""
    merged: dict[str, dict[str, Any]] = {}
    for source in sources:
        url = str(source.get("source_url") or "")
        if not url:
            continue
        existing = merged.get(url)
        if existing is None:
            merged[url] = _clone_source(source)
            continue
        _merge_metadata(existing, source)
    return list(merged.values())


def _clone_source(source: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy with a copied ``metadata`` dict."""
    cloned = dict(source)
    cloned["metadata"] = dict(source.get("metadata") or {})
    return cloned


def _merge_metadata(target: dict[str, Any], extra: dict[str, Any]) -> None:
    """Combine ``extra`` star-delta and counter fields into ``target``."""
    target_meta = target.setdefault("metadata", {})
    extra_meta = extra.get("metadata") or {}

    for key in ("daily_stars", "weekly_stars", "monthly_stars"):
        if target_meta.get(key) is None and extra_meta.get(key) is not None:
            target_meta[key] = extra_meta[key]

    for key in ("stars", "forks", "open_issues"):
        a = target_meta.get(key)
        b = extra_meta.get(key)
        if isinstance(a, int) and isinstance(b, int):
            target_meta[key] = max(a, b)
        elif isinstance(b, int):
            target_meta[key] = b

    for key in ("feed_name", "category"):
        if not target_meta.get(key) and extra_meta.get(key):
            target_meta[key] = extra_meta[key]

    if not target.get("summary") and extra.get("summary"):
        target["summary"] = extra["summary"]
    if (
        target.get("language") in (None, "", "unknown")
        and extra.get("language")
        and extra["language"] != "unknown"
    ):
        target["language"] = extra["language"]


def _sanitize_sources(
    sources: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Sanitize text fields before sources leave the collect node."""
    cleaned_sources: list[dict[str, Any]] = []
    total_warnings = 0
    for source in sources:
        cleaned_source = dict(source)
        total_warnings += _sanitize_text_fields(
            cleaned_source,
            SOURCE_TEXT_FIELDS,
            url=str(cleaned_source.get("source_url") or "?"),
        )
        metadata = cleaned_source.get("metadata")
        if isinstance(metadata, dict):
            cleaned_metadata = dict(metadata)
            total_warnings += _sanitize_text_fields(
                cleaned_metadata,
                SOURCE_METADATA_TEXT_FIELDS,
                url=str(cleaned_source.get("source_url") or "?"),
                prefix="metadata.",
            )
            cleaned_source["metadata"] = cleaned_metadata
        cleaned_sources.append(cleaned_source)
    return cleaned_sources, total_warnings


def _sanitize_text_fields(
    payload: dict[str, Any],
    fields: tuple[str, ...],
    *,
    url: str,
    prefix: str = "",
) -> int:
    """Sanitize selected string fields in a dictionary."""
    total_warnings = 0
    for field in fields:
        value = payload.get(field)
        if not isinstance(value, str):
            continue

        cleaned, warnings = sanitize_input(value)
        payload[field] = cleaned
        total_warnings += len(warnings)
        if warnings:
            LOGGER.warning(
                "[Security] %s %s%s detected suspicious input: %s",
                url,
                prefix,
                field,
                warnings,
            )
    return total_warnings
