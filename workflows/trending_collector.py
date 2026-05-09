"""Scrape GitHub Trending pages and return source-compatible dictionaries.

The official site has no JSON endpoint, so we parse the public HTML at
``https://github.com/trending`` (and per-language sub-pages such as
``/trending/python``). The page exposes both the cumulative star count and a
"X stars today / this week / this month" badge that we map onto
``metadata.daily_stars`` / ``metadata.weekly_stars``.

Each call hits a single trending page; multi-language / multi-window
aggregation is handled by ``workflows.collector``.
"""

from __future__ import annotations

import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from bs4 import BeautifulSoup, Tag


LOGGER = logging.getLogger(__name__)

TRENDING_BASE_URL = "https://github.com/trending"
REQUEST_TIMEOUT_SECONDS = 20
REQUEST_MAX_RETRIES = 3
USER_AGENT = (
    "ai-knowledge-base-workflow/1.0 "
    "(+https://github.com/ReckerPeal/ai-knowledge-base)"
)
CHINA_TZ = timezone(timedelta(hours=8))

VALID_WINDOWS: tuple[str, ...] = ("daily", "weekly", "monthly")
DEFAULT_LIMIT = 25

_DELTA_PATTERN = re.compile(
    r"([\d,]+)\s+stars?\s+(today|this\s+week|this\s+month)",
    re.IGNORECASE,
)


def fetch_trending(
    language: str = "",
    *,
    since: str = "daily",
    limit: int = DEFAULT_LIMIT,
    fetcher: Callable[[str], str] | None = None,
    collected_at: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch one trending page and return parsed sources.

    Args:
        language: GitHub trending language slug (``python``, ``typescript``,
            ``rust``, ``go``, ...). Empty string fetches the language-agnostic
            page.
        since: Time window. Must be one of ``VALID_WINDOWS``.
        limit: Maximum number of articles to return.
        fetcher: Optional callable that takes a URL and returns HTML, used in
            tests to bypass the network.
        collected_at: Override timestamp for ``collected_at`` (testing aid).

    Returns:
        A list of source dictionaries with the same shape as the legacy
        GitHub Search collector. ``metadata.daily_stars`` is filled when
        ``since == "daily"``; ``metadata.weekly_stars`` is filled when
        ``since == "weekly"``.

    Raises:
        ValueError: If ``since`` is not a recognised window.
        RuntimeError: If all retry attempts fail.
    """
    if since not in VALID_WINDOWS:
        raise ValueError(f"since must be one of {VALID_WINDOWS}, got {since!r}")
    if limit <= 0:
        return []

    url = build_url(language, since)
    LOGGER.info("[Trending] fetching url=%s", url)
    html = (fetcher or _http_get)(url)
    items = _parse_html(html, since=since, limit=limit, collected_at=collected_at)
    LOGGER.info(
        "[Trending] parsed url=%s items=%s", url, len(items),
    )
    return items


def build_url(language: str, since: str) -> str:
    """Build the trending URL for ``language`` and ``since``.

    Args:
        language: Language slug; empty means the language-agnostic page.
        since: Time window.

    Returns:
        Fully qualified URL.
    """
    base = TRENDING_BASE_URL
    slug = (language or "").strip().lower()
    if slug and slug != "all":
        base = f"{base}/{urllib.parse.quote(slug)}"
    return f"{base}?since={since}"


def _http_get(url: str) -> str:
    """Fetch ``url`` as HTML text with timeout, retry, and backoff.

    Args:
        url: Trending page URL.

    Returns:
        Decoded HTML body.

    Raises:
        RuntimeError: If all retry attempts fail.
    """
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.8",
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
                "[Trending] retryable HTTP status=%s url=%s", exc.code, url
            )
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            LOGGER.warning(
                "[Trending] request failed attempt=%s url=%s error=%s",
                attempt,
                url,
                exc,
            )
        if attempt < REQUEST_MAX_RETRIES:
            time.sleep(2 ** (attempt - 1))
    raise RuntimeError(
        f"trending fetch failed after retries: {last_error}"
    ) from last_error


def _parse_html(
    html: str,
    *,
    since: str,
    limit: int,
    collected_at: str | None,
) -> list[dict[str, Any]]:
    """Parse a trending page HTML body into source dictionaries."""
    soup = BeautifulSoup(html, "html.parser")
    articles = soup.select("article.Box-row")
    if not articles:
        LOGGER.warning("[Trending] no articles found in HTML; selector drift?")
        return []

    timestamp = collected_at or datetime.now(CHINA_TZ).isoformat(timespec="seconds")
    items: list[dict[str, Any]] = []
    for article in articles[:limit]:
        item = _parse_article(article, since=since, collected_at=timestamp)
        if item is not None:
            items.append(item)
    return items


def _parse_article(
    article: Tag,
    *,
    since: str,
    collected_at: str,
) -> dict[str, Any] | None:
    """Convert one ``<article class="Box-row">`` element into a source dict."""
    heading = article.find("h2")
    if not isinstance(heading, Tag):
        return None
    link = heading.find("a")
    if not isinstance(link, Tag):
        return None
    href = (link.get("href") or "").strip()
    if not href.startswith("/"):
        return None
    full_name = href.lstrip("/")
    if "/" not in full_name:
        return None
    owner, _ = full_name.split("/", 1)
    repo_url = f"https://github.com{href}"

    description_el = article.find("p", class_="col-9")
    description = (
        description_el.get_text(strip=True) if isinstance(description_el, Tag) else ""
    )

    language_el = article.find("span", attrs={"itemprop": "programmingLanguage"})
    language = (
        language_el.get_text(strip=True) if isinstance(language_el, Tag) else "unknown"
    )

    stars = _link_count(article, f"{href}/stargazers")
    forks = _link_count(article, f"{href}/forks")
    delta = _extract_delta(article)

    metadata: dict[str, Any] = {
        "author": owner,
        "stars": stars,
        "forks": forks,
        "daily_stars": delta if since == "daily" else None,
        "weekly_stars": delta if since == "weekly" else None,
        "monthly_stars": delta if since == "monthly" else None,
        "stars_baseline_date": None,
    }

    return {
        "title": full_name,
        "source": "github_trending",
        "source_url": repo_url,
        "summary": description,
        "published_at": None,
        "collected_at": collected_at,
        "language": language,
        "metadata": metadata,
    }


def _link_count(article: Tag, href_suffix: str) -> int:
    """Find an ``<a href="...{href_suffix}">`` and parse its integer text."""
    link = article.find(
        "a",
        href=lambda value: isinstance(value, str) and value.endswith(href_suffix),
    )
    if not isinstance(link, Tag):
        return 0
    return _parse_int(link.get_text(strip=True))


def _extract_delta(article: Tag) -> int | None:
    """Find the ``X stars today / this week / this month`` badge."""
    for span in article.find_all("span", class_="d-inline-block"):
        if not isinstance(span, Tag):
            continue
        text = span.get_text(" ", strip=True)
        match = _DELTA_PATTERN.search(text)
        if match:
            return _parse_int(match.group(1))
    return None


def _parse_int(text: str) -> int:
    """Parse an integer from human-formatted text such as ``"12,345"``."""
    if not text:
        return 0
    cleaned = re.sub(r"[^\d]", "", text)
    return int(cleaned) if cleaned else 0
