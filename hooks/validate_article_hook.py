#!/usr/bin/env python3
"""Dispatch Codex PostToolUse events to the article JSON validator."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable


LOGGER = logging.getLogger(__name__)

WRITE_TOOLS = {
    "Write",
    "Edit",
    "MultiEdit",
    "ApplyPatch",
    "write",
    "edit",
    "multi_edit",
    "apply_patch",
}
PATH_KEYS = {"file_path", "filePath", "path", "filepath"}
ARTICLES_PARTS = ("knowledge", "articles")


def load_hook_event() -> dict[str, Any]:
    """Load the hook event JSON from standard input.

    Returns:
        Parsed hook event. Invalid or empty input returns an empty event so
        unrelated hook invocations do not block the agent.
    """
    raw_input = sys.stdin.read().strip()
    if not raw_input:
        return {}

    try:
        event = json.loads(raw_input)
    except json.JSONDecodeError:
        LOGGER.exception("Hook input was not valid JSON")
        return {}

    if not isinstance(event, dict):
        return {}
    return event


def get_tool_name(event: dict[str, Any]) -> str:
    """Extract a tool name from a Codex hook event.

    Args:
        event: Parsed hook event payload.

    Returns:
        Tool name or an empty string.
    """
    tool_name = event.get("tool_name") or event.get("tool")
    if isinstance(tool_name, str):
        return tool_name

    tool = event.get("tool")
    if isinstance(tool, dict) and isinstance(tool.get("name"), str):
        return tool["name"]

    return ""


def walk_values(value: Any) -> Iterable[tuple[str, Any]]:
    """Yield all key/value pairs from nested dictionaries and lists.

    Args:
        value: Arbitrary JSON-compatible value.

    Yields:
        Nested key/value pairs.
    """
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key), item
            yield from walk_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk_values(item)


def extract_file_paths(event: dict[str, Any]) -> list[Path]:
    """Extract candidate file paths from a hook event.

    Args:
        event: Parsed hook event payload.

    Returns:
        De-duplicated file paths in event order.
    """
    paths: list[Path] = []
    seen: set[str] = set()

    for key, value in walk_values(event):
        if key not in PATH_KEYS or not isinstance(value, str) or not value.strip():
            continue
        if value in seen:
            continue
        seen.add(value)
        paths.append(Path(value))

    return paths


def contains_article_parts(path: Path) -> bool:
    """Check whether a path is under a knowledge/articles segment.

    Args:
        path: Candidate path.

    Returns:
        True when the path targets a JSON article file.
    """
    parts = path.parts
    for index in range(len(parts) - 1):
        if parts[index : index + 2] == ARTICLES_PARTS:
            return path.suffix == ".json"
    return False


def select_article_paths(paths: list[Path]) -> list[Path]:
    """Filter candidate paths to article JSON files.

    Args:
        paths: Candidate paths from a hook event.

    Returns:
        Article JSON paths.
    """
    return [path for path in paths if contains_article_parts(path)]


def validate_article_path(path: Path) -> int:
    """Run the article JSON validator for one path.

    Args:
        path: Article JSON file path.

    Returns:
        Validator process exit code.
    """
    result = subprocess.run(
        ["python3", "hooks/validate_json.py", str(path)],
        text=True,
        check=False,
    )
    return result.returncode


def main() -> int:
    """Run hook dispatch.

    Returns:
        Zero for irrelevant events or successful validation; non-zero when any
        matching article file fails validation.
    """
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s: %(message)s")
    event = load_hook_event()
    tool_name = get_tool_name(event)
    if tool_name not in WRITE_TOOLS:
        return 0

    article_paths = select_article_paths(extract_file_paths(event))
    if not article_paths:
        return 0

    exit_codes = [validate_article_path(path) for path in article_paths]
    return 1 if any(exit_code != 0 for exit_code in exit_codes) else 0


if __name__ == "__main__":
    sys.exit(main())
