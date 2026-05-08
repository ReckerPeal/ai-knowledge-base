"""Collection node for GitHub source summaries."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from workflows.state import KBState


LOGGER = logging.getLogger(__name__)

GITHUB_SEARCH_API = "https://api.github.com/search/repositories"
GITHUB_QUERY = "AI LLM agent language:python"
DEFAULT_PER_SOURCE_LIMIT = 10
REQUEST_TIMEOUT_SECONDS = 20
REQUEST_MAX_RETRIES = 3
CHINA_TZ = timezone(timedelta(hours=8))


def collect_node(state: KBState) -> dict[str, Any]:
    """Collect AI-related GitHub repositories as structured source summaries.

    Args:
        state: Shared workflow state.

    Returns:
        Partial state update containing ``sources``.
    """
    LOGGER.info("[CollectNode] collecting GitHub repositories")
    plan = state.get("plan", {}) or {}
    limit = int(plan.get("per_source_limit", DEFAULT_PER_SOURCE_LIMIT))

    encoded_query = urllib.parse.quote(GITHUB_QUERY)
    url = (
        f"{GITHUB_SEARCH_API}?q={encoded_query}"
        f"&sort=stars&order=desc&per_page={limit}"
    )
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "ai-knowledge-base-workflow/1.0",
        },
    )
    data = _request_json(request)
    items = data.get("items")
    if not isinstance(items, list):
        raise ValueError("GitHub Search API response missing items list")

    collected_at = _now_iso()
    sources: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        owner = item.get("owner") if isinstance(item.get("owner"), dict) else {}
        sources.append(
            {
                "title": str(item.get("full_name") or item.get("name") or ""),
                "source": "github_search",
                "source_url": str(item.get("html_url") or ""),
                "summary": str(item.get("description") or ""),
                "published_at": item.get("created_at"),
                "collected_at": collected_at,
                "language": str(item.get("language") or "unknown"),
                "metadata": {
                    "author": str(owner.get("login") or ""),
                    "stars": int(item.get("stargazers_count") or 0),
                    "forks": int(item.get("forks_count") or 0),
                    "open_issues": int(item.get("open_issues_count") or 0),
                    "updated_at": item.get("updated_at"),
                },
            }
        )

    return {"sources": sources}


def _request_json(request: urllib.request.Request) -> dict[str, Any]:
    """Execute an HTTP request with timeout, retry, and JSON parsing.

    Args:
        request: Prepared urllib request.

    Returns:
        Parsed JSON object.

    Raises:
        RuntimeError: If all retry attempts fail.
        ValueError: If the response is not a JSON object.
    """
    last_error: Exception | None = None
    for attempt in range(1, REQUEST_MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(
                request,
                timeout=REQUEST_TIMEOUT_SECONDS,
            ) as response:
                data = json.loads(response.read().decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("response JSON must be an object")
            return data
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code < 500 and exc.code != 429:
                raise RuntimeError(f"HTTP {exc.code}: {exc.reason}") from exc
            LOGGER.warning("[CollectNode] retryable HTTP error status=%s", exc.code)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            LOGGER.warning("[CollectNode] request failed attempt=%s error=%s", attempt, exc)

        if attempt < REQUEST_MAX_RETRIES:
            time.sleep(2 ** (attempt - 1))

    raise RuntimeError(f"GitHub request failed after retries: {last_error}") from last_error


def _now_iso() -> str:
    """Return the current timestamp in ISO 8601 with China timezone.

    Returns:
        Current timestamp string.
    """
    return datetime.now(CHINA_TZ).isoformat(timespec="seconds")
