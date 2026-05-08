"""LangGraph node functions for the knowledge-base workflow."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from hashlib import sha1
from pathlib import Path
from typing import Any

from workflows import model_client
from workflows.state import KBState


LOGGER = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTICLES_DIR = PROJECT_ROOT / "knowledge" / "articles"
GITHUB_SEARCH_API = "https://api.github.com/search/repositories"
GITHUB_QUERY = "AI LLM agent language:python"
GITHUB_RESULT_LIMIT = 10
REQUEST_TIMEOUT_SECONDS = 20
REQUEST_MAX_RETRIES = 3
MIN_ARTICLE_SCORE = 6.0
FORCE_PASS_ITERATION = 2
CHINA_TZ = timezone(timedelta(hours=8))


def collect_node(state: KBState) -> dict[str, Any]:
    """Collect AI-related GitHub repositories as structured source summaries.

    Args:
        state: Shared workflow state.

    Returns:
        Partial state update containing ``sources``.
    """
    LOGGER.info("[CollectNode] collecting GitHub repositories")
    del state

    encoded_query = urllib.parse.quote(GITHUB_QUERY)
    url = (
        f"{GITHUB_SEARCH_API}?q={encoded_query}"
        f"&sort=stars&order=desc&per_page={GITHUB_RESULT_LIMIT}"
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


def analyze_node(state: KBState) -> dict[str, Any]:
    """Analyze collected sources with an LLM.

    Args:
        state: Shared workflow state containing ``sources``.

    Returns:
        Partial state update containing ``analyses`` and ``cost_tracker``.
    """
    LOGGER.info("[AnalyzeNode] analyzing collected sources")
    cost_tracker = dict(state.get("cost_tracker") or {})
    analyses: list[dict[str, Any]] = []
    system = "你是 AI 技术知识库分析员，输出必须是 JSON 对象。"

    for source in state.get("sources", []):
        prompt = (
            "请基于以下 GitHub 仓库摘要生成中文结构化分析。\n"
            "输出 JSON 字段：summary(str)、content(str)、tags(list[str])、"
            "score(float, 1-10)、language(str)。\n"
            f"来源摘要：{json.dumps(source, ensure_ascii=False)}"
        )
        analysis, usage = model_client.chat_json(prompt, system=system)
        cost_tracker = model_client.accumulate_usage(cost_tracker, usage)
        analyses.append(_normalize_analysis(source, analysis))

    return {"analyses": analyses, "cost_tracker": cost_tracker}


def organize_node(state: KBState) -> dict[str, Any]:
    """Filter, deduplicate, and optionally revise analyzed articles.

    Args:
        state: Shared workflow state containing ``analyses`` and review fields.

    Returns:
        Partial state update containing ``articles`` and ``cost_tracker``.
    """
    LOGGER.info("[OrganizeNode] organizing analyzed articles")
    cost_tracker = dict(state.get("cost_tracker") or {})
    analyses = list(state.get("analyses", []))

    if state.get("iteration", 0) > 0 and state.get("review_feedback"):
        analyses, cost_tracker = _revise_with_feedback(
            analyses,
            str(state.get("review_feedback") or ""),
            cost_tracker,
        )

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


def review_node(state: KBState) -> dict[str, Any]:
    """Review organized articles with four quality dimensions.

    Args:
        state: Shared workflow state containing ``articles`` and ``iteration``.

    Returns:
        Partial state update containing review status, feedback, iteration, and
        ``cost_tracker``.
    """
    LOGGER.info("[ReviewNode] reviewing organized articles")
    iteration = int(state.get("iteration") or 0)
    if iteration >= FORCE_PASS_ITERATION:
        return {
            "review_passed": True,
            "review_feedback": "iteration >= 2，达到最大审核轮次，强制通过。",
            "iteration": iteration + 1,
        }

    system = "你是知识库质量审核专家，输出必须是 JSON 对象。"
    prompt = (
        "请审核以下知识库条目，按四个维度评分：摘要质量、标签准确、"
        "分类合理、一致性。输出 JSON："
        '{"passed": bool, "overall_score": float, "feedback": str, "scores": {...}}。\n'
        "overall_score 范围 0-1，>= 0.75 且无严重问题时 passed=true。\n"
        f"条目：{json.dumps(state.get('articles', []), ensure_ascii=False)}"
    )
    review, usage = model_client.chat_json(prompt, system=system)
    cost_tracker = model_client.accumulate_usage(state.get("cost_tracker") or {}, usage)
    overall_score = _as_float(review.get("overall_score"))
    passed = bool(review.get("passed")) and overall_score >= 0.75

    return {
        "review_passed": passed,
        "review_feedback": str(review.get("feedback") or ""),
        "iteration": iteration + 1,
        "cost_tracker": cost_tracker,
    }


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


def _normalize_analysis(
    source: dict[str, Any],
    analysis: dict[str, Any],
) -> dict[str, Any]:
    """Merge one source summary with its LLM analysis.

    Args:
        source: Structured source summary.
        analysis: LLM analysis JSON.

    Returns:
        Normalized analysis dictionary.
    """
    return {
        "title": str(source.get("title") or analysis.get("title") or ""),
        "source": str(source.get("source") or "github_search"),
        "source_url": str(source.get("source_url") or ""),
        "summary": str(analysis.get("summary") or source.get("summary") or ""),
        "content": str(analysis.get("content") or analysis.get("summary") or ""),
        "tags": _normalize_tags(analysis.get("tags")),
        "score": _normalize_score(analysis.get("score")),
        "published_at": source.get("published_at"),
        "collected_at": source.get("collected_at") or _now_iso(),
        "language": str(analysis.get("language") or source.get("language") or "unknown"),
        "metadata": dict(source.get("metadata") or {}),
    }


def _revise_with_feedback(
    analyses: list[dict[str, Any]],
    feedback: str,
    cost_tracker: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Revise analyses according to review feedback.

    Args:
        analyses: Current analysis list.
        feedback: Review feedback text.
        cost_tracker: Existing token usage summary.

    Returns:
        Revised analyses and updated token usage summary.
    """
    system = "你是知识库编辑，按审核反馈定向修改条目，输出必须是 JSON 对象。"
    prompt = (
        "请根据审核反馈修正以下知识条目，保持 source_url 不变。\n"
        "输出 JSON：{\"articles\": [ ... ]}，score 必须为 1-10。\n"
        f"审核反馈：{feedback}\n"
        f"条目：{json.dumps(analyses, ensure_ascii=False)}"
    )
    revised, usage = model_client.chat_json(prompt, system=system)
    updated_tracker = model_client.accumulate_usage(cost_tracker, usage)
    articles = revised.get("articles")
    if not isinstance(articles, list):
        LOGGER.warning("[OrganizeNode] revision response missing articles list")
        return analyses, updated_tracker

    revised_analyses = [item for item in articles if isinstance(item, dict)]
    return revised_analyses, updated_tracker


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


def _normalize_score(value: Any) -> float:
    """Normalize model scores to the article schema's 1-10 scale.

    Args:
        value: Raw model score. Legacy 0-1 scores are accepted.

    Returns:
        Score clamped to the 1-10 range.
    """
    score = _as_float(value)
    if 0 < score <= 1:
        score *= 10
    return min(max(score, 1.0), 10.0)


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
