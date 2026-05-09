"""Fetch RSS feeds configured in ``rss_sources.yaml`` and emit source dicts.

The legacy ``workflows.pipeline`` module contained a similar implementation
that lived outside the LangGraph collect node. This module is the in-graph
equivalent: it loads the same YAML config, fetches each enabled feed, parses
items with simple regular expressions, and returns dictionaries that follow
the source schema used by ``workflows.trending_collector`` so downstream
nodes (analyzer / organizer / saver) need no special-casing.

Each emitted source carries:

* ``source = "rss"`` — fixed enum value
* ``metadata.feed_name`` — the human-readable feed name from YAML
* ``metadata.category`` — the feed category from YAML, used by the frontend
  to render category badges and grouping rows
"""

from __future__ import annotations

import html
import logging
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable


LOGGER = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RSS_CONFIG_PATH = PROJECT_ROOT / "workflows" / "rss_sources.yaml"
REQUEST_TIMEOUT_SECONDS = 20
REQUEST_MAX_RETRIES = 3
USER_AGENT = (
    "ai-knowledge-base-workflow/1.0 "
    "(+https://github.com/ReckerPeal/ai-knowledge-base)"
)
CHINA_TZ = timezone(timedelta(hours=8))
AI_KEYWORDS: tuple[str, ...] = (
    "ai",
    "llm",
    "agent",
    "rag",
    "model",
    "openai",
    "anthropic",
    "deepseek",
    "qwen",
    "gemini",
    "gpt",
    "claude",
    "transformer",
    "diffusion",
)


def fetch_all_rss(
    *,
    per_source_limit: int = 20,
    config_path: Path | None = None,
    fetcher: Callable[[str], str] | None = None,
    collected_at: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch every enabled RSS feed and return parsed source dictionaries.

    Args:
        per_source_limit: Maximum items to keep from each feed.
        config_path: Override the default ``rss_sources.yaml`` location.
        fetcher: Optional callable for tests; takes a URL and returns text.
        collected_at: Override the timestamp written into each source.

    Returns:
        Aggregated list of source dictionaries across all feeds.
    """
    if per_source_limit <= 0:
        return []

    sources_config = load_rss_sources(config_path or RSS_CONFIG_PATH)
    if not sources_config:
        LOGGER.info("[RSS] no enabled feeds configured")
        return []

    timestamp = collected_at or datetime.now(CHINA_TZ).isoformat(timespec="seconds")
    aggregated: list[dict[str, Any]] = []
    for source in sources_config:
        url = source["url"]
        try:
            feed_text = (fetcher or _http_get)(url)
        except RuntimeError as exc:
            LOGGER.warning(
                "[RSS] fetch failed feed=%s url=%s error=%s",
                source["name"],
                url,
                exc,
            )
            continue
        items = parse_rss_items(
            feed_text,
            feed_name=source["name"],
            category=source["category"],
            limit=per_source_limit,
            collected_at=timestamp,
        )
        ai_filtered = _filter_ai_related(items)
        LOGGER.info(
            "[RSS] feed=%s parsed=%s ai_filtered=%s",
            source["name"],
            len(items),
            len(ai_filtered),
        )
        aggregated.extend(ai_filtered)

    LOGGER.info(
        "[RSS] collected total=%s feeds=%s",
        len(aggregated),
        len(sources_config),
    )
    return aggregated


def load_rss_sources(config_path: Path = RSS_CONFIG_PATH) -> list[dict[str, Any]]:
    """Load enabled RSS source definitions from a simple YAML file.

    Args:
        config_path: YAML file path.

    Returns:
        Enabled source definitions.
    """
    if not config_path.exists():
        LOGGER.warning("[RSS] config file does not exist: %s", config_path)
        return []

    text = config_path.read_text(encoding="utf-8")
    blocks = re.split(r"\n\s*-\s+name:\s*", text)
    sources: list[dict[str, Any]] = []
    for index, block in enumerate(blocks):
        if index == 0:
            continue
        block_text = "name: " + block
        source = {
            "name": _extract_yaml_string(block_text, "name"),
            "url": _extract_yaml_string(block_text, "url"),
            "category": _extract_yaml_string(block_text, "category"),
            "enabled": _extract_yaml_bool(block_text, "enabled", default=True),
        }
        if source["name"] and source["url"] and source["enabled"]:
            sources.append(source)
    return sources


def parse_rss_items(
    rss_text: str,
    *,
    feed_name: str,
    category: str,
    limit: int,
    collected_at: str,
) -> list[dict[str, Any]]:
    """Parse RSS items into source dictionaries.

    Args:
        rss_text: Raw RSS or Atom XML body.
        feed_name: Human-readable feed name.
        category: Category from configuration.
        limit: Maximum items to return.
        collected_at: ISO 8601 timestamp to stamp on every item.

    Returns:
        Parsed source dictionaries (pre-AI-filter).
    """
    items: list[dict[str, Any]] = []
    raw_items = _split_items(rss_text)
    for item_text in raw_items[: max(0, limit)]:
        title = _clean_xml_text(_extract_xml_field(item_text, "title"))
        link = _clean_xml_text(_extract_xml_field(item_text, "link"))
        description = _clean_xml_text(_extract_xml_field(item_text, "description"))
        if not description:
            description = _clean_xml_text(_extract_xml_field(item_text, "summary"))
        published_at = _parse_rss_date(
            _clean_xml_text(_extract_xml_field(item_text, "pubDate"))
            or _clean_xml_text(_extract_xml_field(item_text, "published"))
        )
        if not title or not link:
            continue

        items.append(
            {
                "title": title,
                "source": "rss",
                "source_url": link,
                "summary": description or title,
                "published_at": published_at,
                "collected_at": collected_at,
                "language": _guess_language(f"{title} {description}"),
                "metadata": {
                    "feed_name": feed_name,
                    "category": category,
                    "stars": None,
                    "daily_stars": None,
                    "weekly_stars": None,
                    "monthly_stars": None,
                    "stars_baseline_date": None,
                },
            }
        )
    return items


def _http_get(url: str) -> str:
    """Fetch ``url`` as text with timeout, retry, and exponential backoff."""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml,application/xml;q=0.9,text/xml;q=0.8,*/*;q=0.5",
            "Accept-Language": "en-US,en;q=0.8,zh-CN;q=0.6",
        },
    )
    last_error: Exception | None = None
    for attempt in range(1, REQUEST_MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(
                request, timeout=REQUEST_TIMEOUT_SECONDS
            ) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code < 500 and exc.code != 429:
                raise RuntimeError(f"HTTP {exc.code}: {exc.reason}") from exc
            LOGGER.warning(
                "[RSS] retryable HTTP status=%s url=%s", exc.code, url
            )
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            LOGGER.warning(
                "[RSS] request failed attempt=%s url=%s error=%s",
                attempt,
                url,
                exc,
            )
        if attempt < REQUEST_MAX_RETRIES:
            time.sleep(2 ** (attempt - 1))
    raise RuntimeError(
        f"RSS fetch failed after retries: {last_error}"
    ) from last_error


def _split_items(rss_text: str) -> list[str]:
    """Split a feed body into per-item XML chunks (RSS or Atom)."""
    rss_items = re.findall(r"<item\b[^>]*>(.*?)</item>", rss_text, re.DOTALL)
    if rss_items:
        return rss_items
    atom_entries = re.findall(r"<entry\b[^>]*>(.*?)</entry>", rss_text, re.DOTALL)
    return atom_entries


def _extract_xml_field(item_text: str, field_name: str) -> str:
    """Extract a single XML field body (or first occurrence)."""
    match = re.search(
        rf"<{re.escape(field_name)}\b[^>]*>(.*?)</{re.escape(field_name)}>",
        item_text,
        re.DOTALL | re.IGNORECASE,
    )
    if match:
        return match.group(1)
    # Atom <link href="..."/> self-closing form.
    if field_name == "link":
        atom_match = re.search(
            r"<link\b[^>]*?href=\"([^\"]+)\"[^>]*/?>",
            item_text,
            re.DOTALL | re.IGNORECASE,
        )
        if atom_match:
            return atom_match.group(1)
    return ""


def _clean_xml_text(value: str) -> str:
    """Clean simple XML text content into human-readable text."""
    if not value:
        return ""
    text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", value, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_rss_date(value: str) -> str | None:
    """Parse an RSS or Atom date string into ISO 8601, or ``None``."""
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        parsed = None

    if parsed is None:
        # Try ISO 8601 / Atom-style RFC 3339.
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _guess_language(text: str) -> str:
    """Return ``zh`` when CJK characters are present, else ``en``."""
    return "zh" if re.search(r"[一-鿿]", text) else "en"


def _filter_ai_related(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only items whose title or summary mentions AI-related keywords."""
    filtered: list[dict[str, Any]] = []
    for item in items:
        haystack = f"{item.get('title', '')} {item.get('summary', '')}".lower()
        if any(keyword in haystack for keyword in AI_KEYWORDS):
            filtered.append(item)
    return filtered


def _extract_yaml_string(text: str, key: str) -> str:
    """Extract a quoted/unquoted scalar string from a single YAML block."""
    match = re.search(
        rf"^\s*{re.escape(key)}:\s*(.+?)\s*$", text, re.MULTILINE
    )
    if not match:
        return ""
    return match.group(1).strip().strip('"').strip("'")


def _extract_yaml_bool(text: str, key: str, *, default: bool) -> bool:
    """Extract a boolean scalar from a single YAML block."""
    value = _extract_yaml_string(text, key).lower()
    if value in {"true", "yes", "1"}:
        return True
    if value in {"false", "no", "0"}:
        return False
    return default
