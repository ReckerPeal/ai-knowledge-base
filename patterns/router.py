"""Router pattern with keyword-first intent routing and LLM fallback."""

from __future__ import annotations

import json
import logging
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from workflows import model_client


LOGGER = logging.getLogger(__name__)

GITHUB_SEARCH = "github_search"
KNOWLEDGE_QUERY = "knowledge_query"
GENERAL_CHAT = "general_chat"
SUPPORTED_INTENTS = {GITHUB_SEARCH, KNOWLEDGE_QUERY, GENERAL_CHAT}

PROJECT_ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_INDEX_PATH = PROJECT_ROOT / "knowledge" / "articles" / "index.json"

GITHUB_SEARCH_API = "https://api.github.com/search/repositories"
REQUEST_TIMEOUT_SECONDS = 15
REQUEST_MAX_RETRIES = 3
GITHUB_RESULT_LIMIT = 5
KNOWLEDGE_RESULT_LIMIT = 5

KEYWORD_RULES: dict[str, tuple[str, ...]] = {
    GITHUB_SEARCH: (
        "github",
        "repo",
        "repository",
        "仓库",
        "代码库",
        "开源项目",
        "搜索项目",
        "找项目",
    ),
    KNOWLEDGE_QUERY: (
        "knowledge",
        "知识库",
        "本地知识",
        "已收藏",
        "文章",
        "资料库",
        "检索",
        "查询",
    ),
}


def route(query: str) -> str:
    """Route a user query to the matching intent handler.

    Args:
        query: User query text.

    Returns:
        Handler response text.
    """
    normalized_query = query.strip()
    if not normalized_query:
        return "请输入要处理的问题。"

    intent = _classify_by_keyword(normalized_query)
    if intent is None:
        intent = _classify_with_llm(normalized_query)

    handler = {
        GITHUB_SEARCH: handle_github_search,
        KNOWLEDGE_QUERY: handle_knowledge_query,
        GENERAL_CHAT: handle_general_chat,
    }.get(intent, handle_general_chat)
    return handler(normalized_query)


def handle_github_search(query: str) -> str:
    """Search GitHub repositories with the GitHub Search API.

    Args:
        query: Search query. The query is encoded with ``urllib.parse.quote``.

    Returns:
        A formatted repository result list, or an error/empty-state message.
    """
    encoded_query = urllib.parse.quote(query)
    url = (
        f"{GITHUB_SEARCH_API}?q={encoded_query}"
        f"&sort=stars&order=desc&per_page={GITHUB_RESULT_LIMIT}"
    )
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "ai-knowledge-base-router/1.0",
        },
    )

    try:
        data = _request_json(request)
    except RuntimeError as exc:
        LOGGER.exception("GitHub search failed for query=%s", query)
        return f"GitHub 搜索失败：{exc}"

    items = data.get("items")
    if not isinstance(items, list):
        LOGGER.error("GitHub response missing items list: %s", data)
        return "GitHub 搜索结果格式异常。"
    if not items:
        return "未找到匹配的 GitHub 仓库。"

    lines = ["GitHub 搜索结果："]
    for index, item in enumerate(items[:GITHUB_RESULT_LIMIT], start=1):
        if not isinstance(item, dict):
            continue
        full_name = str(item.get("full_name") or "unknown")
        html_url = str(item.get("html_url") or "")
        description = str(item.get("description") or "无描述")
        stars = int(item.get("stargazers_count") or 0)
        language = str(item.get("language") or "Unknown")
        lines.append(
            f"{index}. {full_name} ({stars} stars, {language})\n"
            f"   {description}\n"
            f"   {html_url}"
        )

    return "\n".join(lines)


def handle_knowledge_query(query: str) -> str:
    """Search local knowledge articles from ``knowledge/articles/index.json``.

    Args:
        query: User query text.

    Returns:
        A formatted article result list, or an empty-state message.
    """
    try:
        articles = _load_knowledge_index(KNOWLEDGE_INDEX_PATH)
    except RuntimeError as exc:
        LOGGER.exception("Knowledge index loading failed")
        return f"知识库索引不可用：{exc}"

    scored_articles = [
        (score, article)
        for article in articles
        if (score := _score_article(query, article)) > 0
    ]
    scored_articles.sort(
        key=lambda item: (item[0], float(item[1].get("score") or 0.0)),
        reverse=True,
    )

    if not scored_articles:
        return "知识库中未找到匹配内容。"

    lines = ["知识库检索结果："]
    for index, (_, article) in enumerate(
        scored_articles[:KNOWLEDGE_RESULT_LIMIT],
        start=1,
    ):
        title = str(article.get("title") or "未命名条目")
        summary = str(article.get("summary") or "")
        source_url = str(article.get("source_url") or "")
        score = article.get("score")
        score_text = f"，评分 {score}" if score is not None else ""
        lines.append(f"{index}. {title}{score_text}\n   {summary}\n   {source_url}")

    return "\n".join(lines)


def handle_general_chat(query: str) -> str:
    """Answer a general chat query directly with the LLM.

    Args:
        query: User query text.

    Returns:
        LLM response text.
    """
    system_prompt = "你是一个简洁可靠的 AI 知识库助手。"
    return _call_chat(query, system_prompt=system_prompt)


def _classify_by_keyword(query: str) -> str | None:
    """Classify intent with zero-cost keyword matching.

    Args:
        query: User query text.

    Returns:
        The matched intent, or ``None`` when no keyword rule matches.
    """
    normalized_query = query.lower()
    for intent, keywords in KEYWORD_RULES.items():
        if any(keyword.lower() in normalized_query for keyword in keywords):
            return intent
    return None


def _classify_with_llm(query: str) -> str:
    """Classify ambiguous intent with an LLM JSON response.

    Args:
        query: User query text.

    Returns:
        One of ``github_search``, ``knowledge_query``, or ``general_chat``.
    """
    prompt = (
        "请将用户意图分类为以下三类之一：github_search、knowledge_query、"
        "general_chat。\n"
        "只返回 JSON，例如 {\"intent\": \"general_chat\"}。\n"
        f"用户问题：{query}"
    )

    try:
        data = _call_chat_json(prompt)
    except RuntimeError:
        LOGGER.exception("LLM intent classification failed; fallback to general_chat")
        return GENERAL_CHAT

    intent = data.get("intent")
    if isinstance(intent, str) and intent in SUPPORTED_INTENTS:
        return intent

    LOGGER.warning("Unsupported LLM intent classification: %s", data)
    return GENERAL_CHAT


def _request_json(request: urllib.request.Request) -> dict[str, Any]:
    """Send a JSON HTTP request with timeout, retry, and explicit errors.

    Args:
        request: Prepared urllib request.

    Returns:
        Parsed JSON object.

    Raises:
        RuntimeError: If the request fails or the response is not a JSON object.
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
                raise RuntimeError("response JSON must be an object")
            return data
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code < 500 and exc.code != 429:
                raise RuntimeError(f"HTTP {exc.code}: {exc.reason}") from exc
            LOGGER.warning(
                "Retryable GitHub HTTP error attempt=%s status=%s",
                attempt,
                exc.code,
            )
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            LOGGER.warning("GitHub request failed attempt=%s error=%s", attempt, exc)

        if attempt < REQUEST_MAX_RETRIES:
            time.sleep(2 ** (attempt - 1))

    raise RuntimeError(f"request failed after retries: {last_error}") from last_error


def _load_knowledge_index(index_path: Path) -> list[dict[str, Any]]:
    """Load and validate the local knowledge index.

    Args:
        index_path: Path to ``knowledge/articles/index.json``.

    Returns:
        Article dictionaries from the index.

    Raises:
        RuntimeError: If the index is missing, unreadable, or malformed.
    """
    if not index_path.exists():
        raise RuntimeError(f"未找到索引文件 {index_path}")

    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"索引读取失败：{exc}") from exc

    if isinstance(data, list):
        articles = data
    elif isinstance(data, dict):
        candidates = data.get("articles") or data.get("items") or data.get("results")
        if not isinstance(candidates, list):
            raise RuntimeError("索引 JSON 对象必须包含 articles/items/results 数组")
        articles = candidates
    else:
        raise RuntimeError("索引 JSON 必须是数组或对象")

    validated_articles: list[dict[str, Any]] = []
    for article in articles:
        if isinstance(article, dict):
            validated_articles.append(article)
        else:
            LOGGER.warning("Skipping malformed knowledge article: %s", article)

    return validated_articles


def _score_article(query: str, article: dict[str, Any]) -> int:
    """Score a local article with simple field-weighted keyword matching.

    Args:
        query: User query text.
        article: Knowledge article dictionary.

    Returns:
        Positive match score, or ``0`` when there is no match.
    """
    query_terms = _query_terms(query)
    if not query_terms:
        return 0

    title = str(article.get("title") or "").lower()
    summary = str(article.get("summary") or "").lower()
    content = str(article.get("content") or "").lower()
    tags = " ".join(str(tag) for tag in article.get("tags") or []).lower()

    score = 0
    for term in query_terms:
        if term in title:
            score += 4
        if term in tags:
            score += 3
        if term in summary:
            score += 2
        if term in content:
            score += 1
    return score


def _query_terms(query: str) -> list[str]:
    """Extract searchable terms from a query.

    Args:
        query: User query text.

    Returns:
        Lowercase terms excluding generic routing words.
    """
    stop_words = {
        "github",
        "knowledge",
        "知识库",
        "查询",
        "检索",
        "搜索",
        "帮我",
        "一下",
    }
    normalized = query.lower()
    terms = [
        term.strip()
        for term in normalized.replace("，", " ").replace(",", " ").split()
    ]
    return [term for term in terms if term and term not in stop_words]


def _call_chat(prompt: str, *, system_prompt: str | None = None) -> str:
    """Call ``workflows.model_client`` chat capability and return text.

    Args:
        prompt: User prompt.
        system_prompt: Optional system prompt.

    Returns:
        Assistant text response.

    Raises:
        RuntimeError: If no compatible chat function is available.
    """
    chat_func = getattr(model_client, "chat", None)
    if callable(chat_func):
        try:
            result = chat_func(prompt)
        except TypeError:
            messages = _messages(prompt, system_prompt)
            result = chat_func(messages)
        return _extract_text_result(result)

    quick_chat = getattr(model_client, "quick_chat", None)
    if callable(quick_chat):
        result = quick_chat(prompt, system_prompt=system_prompt)
        return _extract_text_result(result)

    raise RuntimeError("workflows.model_client 缺少 chat() 或 quick_chat()")


def _call_chat_json(prompt: str) -> dict[str, Any]:
    """Call ``workflows.model_client.chat_json`` or parse a chat JSON response.

    Args:
        prompt: JSON-only prompt.

    Returns:
        Parsed JSON object.

    Raises:
        RuntimeError: If the model response cannot be parsed as a JSON object.
    """
    chat_json_func = getattr(model_client, "chat_json", None)
    if callable(chat_json_func):
        try:
            result = chat_json_func(prompt)
        except TypeError:
            result = chat_json_func(_messages(prompt, None))
        if isinstance(result, tuple):
            result = result[0]
        if isinstance(result, dict):
            return result
        if isinstance(result, str):
            return _parse_json_object(result)
        raise RuntimeError(f"chat_json returned unsupported type: {type(result)}")

    return _parse_json_object(_call_chat(prompt))


def _messages(prompt: str, system_prompt: str | None) -> list[dict[str, str]]:
    """Build OpenAI-compatible chat messages.

    Args:
        prompt: User prompt.
        system_prompt: Optional system prompt.

    Returns:
        Chat message list.
    """
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    return messages


def _extract_text_result(result: Any) -> str:
    """Extract text from common model client return shapes.

    Args:
        result: Model client return value.

    Returns:
        Assistant text.
    """
    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, str):
        return result
    content = getattr(result, "content", None)
    if isinstance(content, str):
        return content
    raise RuntimeError(f"chat returned unsupported type: {type(result)}")


def _parse_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from LLM text.

    Args:
        text: LLM response text.

    Returns:
        Parsed JSON object.

    Raises:
        RuntimeError: If parsing fails or the JSON is not an object.
    """
    cleaned_text = text.strip()
    if cleaned_text.startswith("```"):
        cleaned_text = cleaned_text.strip("`")
        cleaned_text = cleaned_text.removeprefix("json").strip()

    try:
        data = json.loads(cleaned_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"LLM did not return valid JSON: {text}") from exc

    if not isinstance(data, dict):
        raise RuntimeError("LLM JSON response must be an object")
    return data


def main() -> None:
    """Run a minimal command-line smoke test for the router."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    query = " ".join(sys.argv[1:]).strip()
    if not query:
        query = input("请输入问题: ").strip()
    LOGGER.info("%s", route(query))


if __name__ == "__main__":
    main()
