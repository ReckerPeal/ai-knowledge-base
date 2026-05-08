"""Four-step automation pipeline for the local AI knowledge base."""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha1
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

try:
    from model_client import chat_with_retry, create_provider, tracker
except ModuleNotFoundError:
    from pipeline.model_client import chat_with_retry, create_provider, tracker


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "knowledge" / "raw"
ARTICLES_DIR = PROJECT_ROOT / "knowledge" / "articles"
RSS_CONFIG_PATH = Path(__file__).resolve().parent / "rss_sources.yaml"

LOGGER = logging.getLogger(__name__)
REQUEST_TIMEOUT_SECONDS = 30.0
HTTP_MAX_RETRIES = 3
AI_KEYWORDS = ("ai", "llm", "agent", "rag", "model", "openai", "deepseek", "qwen")
VALID_STATUSES = {"draft", "reviewed", "published", "archived"}


@dataclass(frozen=True)
class CollectedItem:
    """Raw content item collected from an external source.

    Attributes:
        title: Source title.
        source: Source identifier.
        source_url: Traceable original URL.
        summary: Source-provided short description.
        published_at: Original publication timestamp, if known.
        collected_at: Collection timestamp in ISO 8601 format.
        language: Best-effort language code.
        metadata: Source-specific metadata.
    """

    title: str
    source: str
    source_url: str
    summary: str
    published_at: str | None
    collected_at: str
    language: str
    metadata: dict[str, Any]


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments.

    Returns:
        Parsed command-line arguments.
    """
    parser = argparse.ArgumentParser(
        description="Run the AI knowledge-base collection and analysis pipeline.",
    )
    parser.add_argument(
        "--sources",
        default="github,rss",
        help="Comma-separated source list: github,rss.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum items to collect per selected source.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the pipeline without writing raw data or articles.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable detailed logs.",
    )
    return parser.parse_args()


def configure_logging(verbose: bool) -> None:
    """Configure process logging.

    Args:
        verbose: Whether to enable debug logs.
    """
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def load_env_file(env_path: Path = PROJECT_ROOT / ".env") -> None:
    """Load simple KEY=VALUE pairs from a local env file.

    Existing environment variables are not overwritten.

    Args:
        env_path: Environment file path.
    """
    if not env_path.exists():
        return

    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        LOGGER.exception("Failed to read env file %s", env_path)
        return

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        key, value = stripped.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def selected_sources(raw_sources: str) -> list[str]:
    """Parse and validate selected source names.

    Args:
        raw_sources: Comma-separated source names.

    Returns:
        Normalized source names.

    Raises:
        ValueError: If an unsupported source is requested.
    """
    sources = [source.strip().lower() for source in raw_sources.split(",")]
    normalized = [source for source in sources if source]
    unsupported = sorted(set(normalized) - {"github", "rss"})
    if unsupported:
        raise ValueError(f"unsupported sources: {', '.join(unsupported)}")
    return normalized or ["github", "rss"]


def utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO 8601 format.

    Returns:
        Current UTC timestamp.
    """
    return datetime.now(timezone.utc).isoformat()


def fetch_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Fetch JSON over HTTP with timeout and explicit error handling.

    Args:
        url: Request URL.
        params: Optional query parameters.
        headers: Optional request headers.

    Returns:
        Parsed JSON object.

    Raises:
        httpx.HTTPError: If the HTTP request fails.
        ValueError: If the response is not a JSON object.
    """
    response = fetch_with_retry(url, params=params, headers=headers)

    data = response.json()
    if not isinstance(data, dict):
        raise ValueError(f"JSON response from {url} must be an object")
    return data


def fetch_text(url: str) -> str:
    """Fetch text over HTTP with timeout and explicit error handling.

    Args:
        url: Request URL.

    Returns:
        Response text.

    Raises:
        httpx.HTTPError: If the HTTP request fails.
    """
    response = fetch_with_retry(url)
    return response.text


def fetch_with_retry(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    retries: int = HTTP_MAX_RETRIES,
) -> httpx.Response:
    """Fetch a URL with timeout, retry, and explicit error logging.

    Args:
        url: Request URL.
        params: Optional query parameters.
        headers: Optional request headers.
        retries: Maximum number of attempts.

    Returns:
        HTTP response with successful status.

    Raises:
        httpx.HTTPError: Final HTTP error after all attempts fail.
    """
    attempts = max(1, retries)
    for attempt in range(1, attempts + 1):
        try:
            response = httpx.get(
                url,
                params=params,
                headers=headers,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            if not is_retryable_status(exc.response.status_code) or attempt >= attempts:
                LOGGER.exception("HTTP request failed for %s", url)
                raise
            delay_seconds = 2 ** (attempt - 1)
            LOGGER.warning(
                "HTTP request failed for %s on attempt %s/%s, retrying in %s seconds",
                url,
                attempt,
                attempts,
                delay_seconds,
            )
            time.sleep(delay_seconds)
        except httpx.RequestError:
            if attempt >= attempts:
                LOGGER.exception("HTTP request failed for %s", url)
                raise
            delay_seconds = 2 ** (attempt - 1)
            LOGGER.warning(
                "HTTP request failed for %s on attempt %s/%s, retrying in %s seconds",
                url,
                attempt,
                attempts,
                delay_seconds,
            )
            time.sleep(delay_seconds)

    raise RuntimeError("HTTP request failed without an exception")


def is_retryable_status(status_code: int) -> bool:
    """Return whether an HTTP status code should be retried.

    Args:
        status_code: HTTP response status code.

    Returns:
        True when retrying may succeed.
    """
    return status_code == 429 or 500 <= status_code < 600


def collect_github(limit: int) -> list[CollectedItem]:
    """Collect AI-related repositories from GitHub Search API.

    Args:
        limit: Maximum repositories to collect.

    Returns:
        Collected repository items.
    """
    per_page = max(1, min(limit, 100))
    query = "AI OR LLM OR Agent in:name,description,readme"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ai-knowledge-base-pipeline",
    }
    github_token = os.getenv("GITHUB_TOKEN")
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    data = fetch_json(
        "https://api.github.com/search/repositories",
        params={"q": query, "sort": "stars", "order": "desc", "per_page": per_page},
        headers=headers,
    )
    items = data.get("items")
    if not isinstance(items, list):
        raise ValueError("GitHub search response missing items list")

    collected_at = utc_now_iso()
    collected: list[CollectedItem] = []
    for item in items[:per_page]:
        if not isinstance(item, dict):
            continue

        title = str(item.get("full_name") or item.get("name") or "").strip()
        source_url = str(item.get("html_url") or "").strip()
        if not title or not source_url:
            continue

        description = str(item.get("description") or "").strip()
        collected.append(
            CollectedItem(
                title=title,
                source="github_search",
                source_url=source_url,
                summary=description or title,
                published_at=item.get("created_at")
                if isinstance(item.get("created_at"), str)
                else None,
                collected_at=collected_at,
                language=normalize_language(item.get("language")),
                metadata={
                    "author": str(item.get("owner", {}).get("login", "")),
                    "stars": int(item.get("stargazers_count") or 0),
                    "forks": int(item.get("forks_count") or 0),
                    "open_issues": int(item.get("open_issues_count") or 0),
                    "updated_at": item.get("updated_at"),
                },
            )
        )

    LOGGER.info("Collected %s GitHub items", len(collected))
    return collected


def load_rss_sources(config_path: Path = RSS_CONFIG_PATH) -> list[dict[str, Any]]:
    """Load enabled RSS source definitions from a simple YAML file.

    Args:
        config_path: RSS source YAML path.

    Returns:
        Enabled source definitions.
    """
    if not config_path.exists():
        LOGGER.warning("RSS config file does not exist: %s", config_path)
        return []

    text = config_path.read_text(encoding="utf-8")
    blocks = re.split(r"\n\s*-\s+name:\s*", text)
    sources: list[dict[str, Any]] = []
    for index, block in enumerate(blocks):
        if index == 0:
            continue
        source_text = "name: " + block
        source = {
            "name": extract_yaml_string(source_text, "name"),
            "url": extract_yaml_string(source_text, "url"),
            "category": extract_yaml_string(source_text, "category"),
            "enabled": extract_yaml_bool(source_text, "enabled", default=True),
        }
        if source["name"] and source["url"] and source["enabled"]:
            sources.append(source)

    return sources


def extract_yaml_string(text: str, key: str) -> str:
    """Extract a quoted or unquoted scalar string from simple YAML text.

    Args:
        text: YAML block text.
        key: Field name.

    Returns:
        Extracted value, or an empty string.
    """
    match = re.search(rf"^\s*{re.escape(key)}:\s*(.+?)\s*$", text, re.MULTILINE)
    if not match:
        return ""
    return match.group(1).strip().strip('"').strip("'")


def extract_yaml_bool(text: str, key: str, *, default: bool) -> bool:
    """Extract a boolean scalar from simple YAML text.

    Args:
        text: YAML block text.
        key: Field name.
        default: Default value when the key is absent.

    Returns:
        Parsed boolean value.
    """
    value = extract_yaml_string(text, key).lower()
    if value in {"true", "yes", "1"}:
        return True
    if value in {"false", "no", "0"}:
        return False
    return default


def collect_rss(limit: int) -> list[CollectedItem]:
    """Collect AI-related content from configured RSS feeds.

    Args:
        limit: Maximum RSS items to collect across enabled feeds.

    Returns:
        Collected RSS items.
    """
    collected: list[CollectedItem] = []
    for source in load_rss_sources():
        if len(collected) >= limit:
            break

        remaining = limit - len(collected)
        try:
            feed_text = fetch_text(str(source["url"]))
        except httpx.HTTPError:
            continue

        feed_items = parse_rss_items(
            feed_text,
            source_name=str(source["name"]),
            category=str(source.get("category") or ""),
            limit=remaining,
        )
        collected.extend(filter_ai_related(feed_items))

    LOGGER.info("Collected %s RSS items", len(collected[:limit]))
    return collected[:limit]


def parse_rss_items(
    rss_text: str,
    *,
    source_name: str,
    limit: int,
    category: str = "",
) -> list[CollectedItem]:
    """Parse RSS item entries with simple regular expressions.

    Args:
        rss_text: RSS XML text.
        source_name: Feed display name.
        limit: Maximum items to return.
        category: Source category from configuration.

    Returns:
        Parsed RSS items.
    """
    collected_at = utc_now_iso()
    items: list[CollectedItem] = []
    item_matches = re.findall(r"<item\b[^>]*>(.*?)</item>", rss_text, re.DOTALL)
    for item_text in item_matches[: max(0, limit)]:
        title = clean_xml_text(extract_xml_field(item_text, "title"))
        source_url = clean_xml_text(extract_xml_field(item_text, "link"))
        description = clean_xml_text(extract_xml_field(item_text, "description"))
        published_at = parse_rss_date(clean_xml_text(extract_xml_field(item_text, "pubDate")))
        if not title or not source_url:
            continue

        items.append(
            CollectedItem(
                title=title,
                source="rss",
                source_url=source_url,
                summary=description or title,
                published_at=published_at,
                collected_at=collected_at,
                language=guess_language(f"{title} {description}"),
                metadata={"feed_name": source_name, "category": category},
            )
        )

    return items


def extract_xml_field(item_text: str, field_name: str) -> str:
    """Extract one XML field body from RSS item text.

    Args:
        item_text: RSS item XML.
        field_name: XML field name.

    Returns:
        Field content, or an empty string.
    """
    match = re.search(
        rf"<{re.escape(field_name)}\b[^>]*>(.*?)</{re.escape(field_name)}>",
        item_text,
        re.DOTALL | re.IGNORECASE,
    )
    return match.group(1) if match else ""


def clean_xml_text(value: str) -> str:
    """Clean simple XML text content.

    Args:
        value: Raw XML field content.

    Returns:
        Human-readable text.
    """
    text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", value, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_rss_date(value: str) -> str | None:
    """Parse an RSS date into ISO 8601 format.

    Args:
        value: RSS date string.

    Returns:
        ISO 8601 timestamp, or ``None`` when parsing fails.
    """
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def filter_ai_related(items: list[CollectedItem]) -> list[CollectedItem]:
    """Keep RSS items that appear related to AI topics.

    Args:
        items: Candidate RSS items.

    Returns:
        Filtered items.
    """
    filtered = []
    for item in items:
        haystack = f"{item.title} {item.summary}".lower()
        if any(keyword in haystack for keyword in AI_KEYWORDS):
            filtered.append(item)
    return filtered


def save_raw_items(items: list[CollectedItem], raw_dir: Path, *, dry_run: bool) -> Path | None:
    """Save collected raw items to the raw knowledge directory.

    Args:
        items: Collected items.
        raw_dir: Raw output directory.
        dry_run: Whether to skip writes.

    Returns:
        Written raw file path, or ``None`` in dry-run mode.
    """
    if dry_run:
        LOGGER.info("Dry run: skipped writing %s raw items", len(items))
        return None

    raw_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = raw_dir / f"{timestamp}-collected.json"
    payload = [asdict(item) for item in items]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    LOGGER.info("Saved raw collection snapshot to %s", path)
    return path


def analyze_items(items: list[CollectedItem]) -> list[tuple[CollectedItem, dict[str, Any]]]:
    """Analyze collected items with the configured LLM.

    Args:
        items: Collected source items.

    Returns:
        Tuples of source item and LLM analysis data.
    """
    create_provider()
    analyzed: list[tuple[CollectedItem, dict[str, Any]]] = []
    for item in items:
        prompt = build_analysis_prompt(item)
        response = chat_with_retry(
            [{"role": "user", "content": prompt}],
            max_tokens=900,
        )
        analysis = parse_llm_json(response.content)
        analyzed.append((item, analysis))
    LOGGER.info("Analyzed %s items with LLM", len(analyzed))
    return analyzed


def build_analysis_prompt(item: CollectedItem) -> str:
    """Build an LLM prompt for one collected item.

    Args:
        item: Collected source item.

    Returns:
        Prompt text asking for strict JSON.
    """
    return (
        "你是 AI 技术知识库分析 Agent。请基于以下来源内容输出严格 JSON，"
        "不要使用 Markdown 代码块。JSON 字段必须包含 summary, content, tags, score。"
        "summary 至少 20 个字符，content 说明背景、亮点、限制和适用场景，"
        "tags 是字符串数组，score 是 1 到 10 的数字。\n\n"
        f"标题: {item.title}\n"
        f"来源: {item.source}\n"
        f"URL: {item.source_url}\n"
        f"来源摘要: {item.summary}\n"
        f"元数据: {json.dumps(item.metadata, ensure_ascii=False)}"
    )


def parse_llm_json(content: str) -> dict[str, Any]:
    """Parse JSON returned by the LLM.

    Args:
        content: LLM response content.

    Returns:
        Parsed JSON object.

    Raises:
        ValueError: If the LLM output is not a JSON object.
    """
    cleaned = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()

    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("LLM analysis response must be a JSON object")
    return data


def organize_articles(
    analyzed_items: list[tuple[CollectedItem, dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Deduplicate, normalize, and validate analyzed article data.

    Args:
        analyzed_items: Collected items paired with analysis data.

    Returns:
        Valid knowledge article objects.
    """
    articles: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for item, analysis in analyzed_items:
        normalized_url = item.source_url.strip()
        if normalized_url in seen_urls:
            continue
        seen_urls.add(normalized_url)

        article = build_article(item, analysis)
        errors = validate_article_object(article)
        if errors:
            LOGGER.warning("Skipping invalid article %s: %s", item.source_url, errors)
            continue
        articles.append(article)

    LOGGER.info("Organized %s valid articles", len(articles))
    return articles


def build_article(item: CollectedItem, analysis: dict[str, Any]) -> dict[str, Any]:
    """Build a normalized knowledge article from analysis data.

    Args:
        item: Collected source item.
        analysis: LLM analysis data.

    Returns:
        Normalized article object.
    """
    tags = analysis.get("tags")
    if not isinstance(tags, list):
        tags = ["AI"]
    normalized_tags = [str(tag).strip() for tag in tags if str(tag).strip()]
    if not normalized_tags:
        normalized_tags = ["AI"]

    summary = str(analysis.get("summary") or item.summary).strip()
    content = str(analysis.get("content") or summary).strip()
    score = normalize_score(analysis.get("score"))

    return {
        "id": make_article_id(item),
        "title": item.title.strip(),
        "source": item.source,
        "source_url": item.source_url.strip(),
        "summary": summary,
        "content": content,
        "tags": normalized_tags,
        "status": "draft",
        "published_at": item.published_at,
        "collected_at": item.collected_at,
        "language": item.language or "unknown",
        "score": score,
        "metadata": item.metadata,
    }


def validate_article_object(article: dict[str, Any]) -> list[str]:
    """Validate an article with local structural constraints.

    Args:
        article: Article object.

    Returns:
        List of validation errors.
    """
    errors: list[str] = []
    required_fields = {
        "id": str,
        "title": str,
        "source": str,
        "source_url": str,
        "summary": str,
        "content": str,
        "tags": list,
        "status": str,
        "published_at": (str, type(None)),
        "collected_at": str,
        "language": str,
        "score": (int, float),
        "metadata": dict,
    }
    for field_name, expected_type in required_fields.items():
        value = article.get(field_name)
        if not isinstance(value, expected_type):
            errors.append(f"{field_name} has invalid type")

    if not re.fullmatch(r"\d{8}-[a-z][a-z0-9_]*-[a-z0-9][a-z0-9_-]*", article["id"]):
        errors.append("id has invalid format")
    if article["status"] not in VALID_STATUSES:
        errors.append("status is invalid")
    if len(article["summary"].strip()) < 20:
        errors.append("summary is too short")
    if not 1 <= float(article["score"]) <= 10:
        errors.append("score is out of range")
    if not article["tags"] or not all(isinstance(tag, str) and tag for tag in article["tags"]):
        errors.append("tags are invalid")
    return errors


def make_article_id(item: CollectedItem) -> str:
    """Create a stable article ID from source date and URL.

    Args:
        item: Collected source item.

    Returns:
        Stable article identifier.
    """
    date_prefix = item.collected_at[:10].replace("-", "")
    resource = resource_slug(item)
    return f"{date_prefix}-{item.source}-{resource}"


def resource_slug(item: CollectedItem) -> str:
    """Create a resource slug for an article ID.

    Args:
        item: Collected source item.

    Returns:
        Lowercase URL-derived slug.
    """
    parsed_url = urlparse(item.source_url)
    if item.source == "github_search":
        parts = [part for part in parsed_url.path.split("/") if part]
        if len(parts) >= 2:
            return slugify(f"{parts[0]}-{parts[1]}")

    base = f"{parsed_url.netloc}-{parsed_url.path}".strip("-")
    slug = slugify(base)[:48].strip("-")
    digest = sha1(item.source_url.encode("utf-8")).hexdigest()[:8]
    return f"{slug or 'item'}-{digest}"


def slugify(value: str) -> str:
    """Convert text into an ID-safe lowercase slug.

    Args:
        value: Raw text.

    Returns:
        Slug containing lowercase letters, numbers, and hyphens.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower())
    return slug.strip("-") or "item"


def normalize_score(value: Any) -> float:
    """Normalize a score to the range 1 to 10.

    Args:
        value: Raw score value.

    Returns:
        Bounded score.
    """
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 5.0
    return min(10.0, max(1.0, round(score, 2)))


def normalize_language(value: Any) -> str:
    """Normalize source language values.

    Args:
        value: Raw language value.

    Returns:
        Language code or descriptive value.
    """
    if not isinstance(value, str) or not value.strip():
        return "unknown"
    return value.strip().lower()


def guess_language(text: str) -> str:
    """Guess whether text is Chinese or English.

    Args:
        text: Text to inspect.

    Returns:
        ``zh`` when CJK characters are present, otherwise ``en``.
    """
    return "zh" if re.search(r"[\u4e00-\u9fff]", text) else "en"


def save_articles(
    articles: list[dict[str, Any]],
    articles_dir: Path = ARTICLES_DIR,
    *,
    dry_run: bool,
) -> list[Path]:
    """Save articles as individual JSON files under dated directories.

    Args:
        articles: Valid article objects.
        articles_dir: Base article output directory.
        dry_run: Whether to skip writes.

    Returns:
        Paths that would be or were written.
    """
    paths: list[Path] = []
    for article in articles:
        errors = validate_article_object(article)
        if errors:
            raise ValueError(f"article {article.get('id')} failed validation: {errors}")

        date_dir = article["collected_at"][:10]
        path = articles_dir / date_dir / f"{article['id']}.json"
        paths.append(path)
        if dry_run:
            LOGGER.info("Dry run: would save article to %s", path)
            continue

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(article, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        LOGGER.info("Saved article to %s", path)

    return paths


def collect_sources(sources: list[str], limit: int) -> list[CollectedItem]:
    """Collect items from selected sources.

    Args:
        sources: Selected source names.
        limit: Maximum items per source.

    Returns:
        Combined collected items.
    """
    collected: list[CollectedItem] = []
    if "github" in sources:
        collected.extend(collect_github(limit))
    if "rss" in sources:
        collected.extend(collect_rss(limit))
    return collected


def run_pipeline(args: argparse.Namespace) -> list[Path]:
    """Run the four-step knowledge automation pipeline.

    Args:
        args: Parsed command-line arguments.

    Returns:
        Article paths written or planned.
    """
    load_env_file()
    sources = selected_sources(args.sources)
    limit = max(1, args.limit)

    LOGGER.info("Step 1 Collect: sources=%s limit=%s", ",".join(sources), limit)
    items = collect_sources(sources, limit)
    save_raw_items(items, RAW_DIR, dry_run=args.dry_run)
    if not items:
        LOGGER.info("No items collected")
        return []

    LOGGER.info("Step 2 Analyze: items=%s", len(items))
    analyzed_items = analyze_items(items)

    LOGGER.info("Step 3 Organize: items=%s", len(analyzed_items))
    articles = organize_articles(analyzed_items)

    LOGGER.info("Step 4 Save: articles=%s", len(articles))
    return save_articles(articles, ARTICLES_DIR, dry_run=args.dry_run)


def main() -> int:
    """CLI entry point.

    Returns:
        Process exit code.
    """
    args = parse_args()
    configure_logging(args.verbose)
    try:
        paths = run_pipeline(args)
    except Exception:
        LOGGER.exception("Pipeline failed")
        tracker.report()
        return 1

    LOGGER.info("Pipeline completed, article paths=%s", len(paths))
    tracker.report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
