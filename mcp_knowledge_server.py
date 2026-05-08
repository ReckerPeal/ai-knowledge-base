#!/usr/bin/env python3
"""MCP server for searching the local AI knowledge base.

The server implements JSON-RPC 2.0 over stdio and supports the MCP
``initialize``, ``tools/list``, and ``tools/call`` methods without third-party
dependencies.
"""

from __future__ import annotations

import json
import logging
import sys
from collections import Counter
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger(__name__)
SERVER_NAME = "knowledge-server"
SERVER_VERSION = "0.1.0"
DEFAULT_ARTICLES_DIR = Path(__file__).resolve().parent / "knowledge" / "articles"

JSONRPC_VERSION = "2.0"
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class KnowledgeBase:
    """Read and query local knowledge article JSON files."""

    def __init__(self, articles_dir: Path) -> None:
        """Initialize the knowledge base.

        Args:
            articles_dir: Directory containing article JSON files, recursively.
        """
        self.articles_dir = articles_dir

    def load_articles(self) -> list[dict[str, Any]]:
        """Load all valid JSON object articles from the articles directory.

        Returns:
            A deterministic list of article dictionaries sorted by file path.
        """
        if not self.articles_dir.exists():
            return []

        articles: list[dict[str, Any]] = []
        for article_path in sorted(self.articles_dir.rglob("*.json")):
            if not article_path.is_file():
                continue

            try:
                with article_path.open("r", encoding="utf-8") as article_file:
                    article = json.load(article_file)
            except json.JSONDecodeError as exc:
                LOGGER.warning(
                    "Skipping invalid article JSON %s: line %s column %s: %s",
                    article_path,
                    exc.lineno,
                    exc.colno,
                    exc.msg,
                )
                continue
            except OSError:
                LOGGER.exception("Failed to read article file %s", article_path)
                continue

            if isinstance(article, dict):
                articles.append(article)
            else:
                LOGGER.warning("Skipping non-object article JSON %s", article_path)

        return articles

    def search_articles(self, keyword: str, limit: int = 5) -> list[dict[str, Any]]:
        """Search articles by keyword in title and summary.

        Args:
            keyword: Case-insensitive search keyword.
            limit: Maximum number of matches to return.

        Returns:
            Matching article summaries containing common distribution fields.
        """
        normalized_keyword = keyword.strip().lower()
        normalized_limit = max(0, min(int(limit), 50))
        if not normalized_keyword or normalized_limit == 0:
            return []

        results: list[dict[str, Any]] = []
        for article in self.load_articles():
            title = str(article.get("title", ""))
            summary = str(article.get("summary", ""))
            haystack = f"{title}\n{summary}".lower()
            if normalized_keyword not in haystack:
                continue

            results.append(
                {
                    "id": article.get("id"),
                    "title": title,
                    "source": article.get("source"),
                    "summary": summary,
                    "score": article.get("score"),
                    "tags": article.get("tags", []),
                }
            )
            if len(results) >= normalized_limit:
                break

        return results

    def get_article(self, article_id: str) -> dict[str, Any] | None:
        """Return one complete article by ID.

        Args:
            article_id: Stable article identifier.

        Returns:
            The matching article object, or ``None`` when not found.
        """
        normalized_id = article_id.strip()
        if not normalized_id:
            return None

        for article in self.load_articles():
            if article.get("id") == normalized_id:
                return article

        return None

    def knowledge_stats(self) -> dict[str, Any]:
        """Build aggregate statistics for the local knowledge base.

        Returns:
            Article count, source distribution, and popular tag counts.
        """
        articles = self.load_articles()
        source_counter: Counter[str] = Counter()
        tag_counter: Counter[str] = Counter()

        for article in articles:
            source = article.get("source")
            if isinstance(source, str) and source:
                source_counter[source] += 1

            tags = article.get("tags", [])
            if isinstance(tags, list):
                for tag in tags:
                    if isinstance(tag, str) and tag:
                        tag_counter[tag] += 1

        return {
            "total_articles": len(articles),
            "source_distribution": dict(source_counter.most_common()),
            "popular_tags": dict(tag_counter.most_common(20)),
        }


def tool_definitions() -> list[dict[str, Any]]:
    """Return MCP tool definitions exposed by this server.

    Returns:
        A list of JSON-schema backed tool descriptors.
    """
    return [
        {
            "name": "search_articles",
            "description": "Search knowledge articles by keyword in title and summary.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "Keyword to search for.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results to return.",
                        "default": 5,
                        "minimum": 0,
                        "maximum": 50,
                    },
                },
                "required": ["keyword"],
                "additionalProperties": False,
            },
        },
        {
            "name": "get_article",
            "description": "Get a complete knowledge article by ID.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "article_id": {
                        "type": "string",
                        "description": "Article ID to retrieve.",
                    }
                },
                "required": ["article_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "knowledge_stats",
            "description": "Return knowledge-base article counts, sources, and tags.",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    ]


def success_response(request_id: Any, result: Any) -> dict[str, Any]:
    """Build a JSON-RPC success response.

    Args:
        request_id: JSON-RPC request ID.
        result: Response result object.

    Returns:
        JSON-RPC 2.0 success response.
    """
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def error_response(request_id: Any, code: int, message: str) -> dict[str, Any]:
    """Build a JSON-RPC error response.

    Args:
        request_id: JSON-RPC request ID, or ``None`` if unknown.
        code: JSON-RPC error code.
        message: Human-readable error message.

    Returns:
        JSON-RPC 2.0 error response.
    """
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def as_tool_content(data: Any) -> dict[str, Any]:
    """Format a Python object as MCP text tool content.

    Args:
        data: Tool result data.

    Returns:
        MCP ``tools/call`` result payload.
    """
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(data, ensure_ascii=False, indent=2),
            }
        ]
    }


def initialize_result() -> dict[str, Any]:
    """Return MCP initialize metadata.

    Returns:
        Server capabilities and identity.
    """
    return {
        "protocolVersion": "2024-11-05",
        "capabilities": {"tools": {}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
    }


def call_tool(
    tool_name: str,
    arguments: dict[str, Any],
    knowledge_base: KnowledgeBase,
) -> dict[str, Any]:
    """Dispatch a MCP tool call to the local implementation.

    Args:
        tool_name: Name from ``tools/call`` params.
        arguments: Tool arguments object.
        knowledge_base: Knowledge base instance to query.

    Returns:
        MCP tool result payload.

    Raises:
        ValueError: If parameters are invalid or the tool does not exist.
    """
    if tool_name == "search_articles":
        keyword = arguments.get("keyword")
        if not isinstance(keyword, str):
            raise ValueError("search_articles requires string argument: keyword")

        limit = arguments.get("limit", 5)
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("search_articles argument limit must be an integer")

        return as_tool_content(knowledge_base.search_articles(keyword, limit))

    if tool_name == "get_article":
        article_id = arguments.get("article_id")
        if not isinstance(article_id, str):
            raise ValueError("get_article requires string argument: article_id")

        article = knowledge_base.get_article(article_id)
        if article is None:
            article = {"error": "article not found", "article_id": article_id}
        return as_tool_content(article)

    if tool_name == "knowledge_stats":
        return as_tool_content(knowledge_base.knowledge_stats())

    raise ValueError(f"unknown tool: {tool_name}")


def handle_request(
    request: dict[str, Any],
    knowledge_base: KnowledgeBase,
) -> dict[str, Any] | None:
    """Handle one JSON-RPC request object.

    Args:
        request: Decoded JSON-RPC request object.
        knowledge_base: Knowledge base instance to query.

    Returns:
        A JSON-RPC response, or ``None`` for notifications.
    """
    request_id = request.get("id")
    if request.get("jsonrpc") != JSONRPC_VERSION:
        return error_response(request_id, INVALID_REQUEST, "invalid JSON-RPC version")

    method = request.get("method")
    if not isinstance(method, str):
        return error_response(request_id, INVALID_REQUEST, "method must be a string")

    if "id" not in request:
        if method == "notifications/initialized":
            return None
        return None

    if method == "initialize":
        return success_response(request_id, initialize_result())

    if method == "tools/list":
        return success_response(request_id, {"tools": tool_definitions()})

    if method == "tools/call":
        params = request.get("params", {})
        if not isinstance(params, dict):
            return error_response(request_id, INVALID_PARAMS, "params must be an object")

        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(tool_name, str):
            return error_response(request_id, INVALID_PARAMS, "tool name must be a string")
        if not isinstance(arguments, dict):
            return error_response(request_id, INVALID_PARAMS, "arguments must be an object")

        try:
            result = call_tool(tool_name, arguments, knowledge_base)
        except ValueError as exc:
            return error_response(request_id, INVALID_PARAMS, str(exc))
        except Exception:
            LOGGER.exception("Tool call failed: %s", tool_name)
            return error_response(request_id, INTERNAL_ERROR, "internal server error")

        return success_response(request_id, result)

    return error_response(request_id, METHOD_NOT_FOUND, f"method not found: {method}")


def handle_json_line(line: str, knowledge_base: KnowledgeBase) -> dict[str, Any] | None:
    """Decode and handle one stdio JSON-RPC line.

    Args:
        line: Raw line received from stdin.
        knowledge_base: Knowledge base instance to query.

    Returns:
        JSON-RPC response, or ``None`` for blank lines and notifications.
    """
    stripped_line = line.strip()
    if not stripped_line:
        return None

    try:
        request = json.loads(stripped_line)
    except json.JSONDecodeError:
        return error_response(None, PARSE_ERROR, "parse error")

    if not isinstance(request, dict):
        return error_response(None, INVALID_REQUEST, "request must be an object")

    return handle_request(request, knowledge_base)


def run_stdio_server(knowledge_base: KnowledgeBase) -> None:
    """Run the JSON-RPC stdio event loop.

    Args:
        knowledge_base: Knowledge base instance to query.
    """
    for line in sys.stdin:
        response = handle_json_line(line, knowledge_base)
        if response is None:
            continue

        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def configure_logging() -> None:
    """Configure stderr logging for diagnostics."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def main() -> int:
    """Run the MCP knowledge server.

    Returns:
        Process exit code.
    """
    configure_logging()
    run_stdio_server(KnowledgeBase(DEFAULT_ARTICLES_DIR))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
