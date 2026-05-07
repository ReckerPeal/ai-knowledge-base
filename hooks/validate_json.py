#!/usr/bin/env python3
"""Validate knowledge article JSON files."""

from __future__ import annotations

import argparse
import glob
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


LOGGER = logging.getLogger(__name__)

REQUIRED_FIELDS: dict[str, type] = {
    "id": str,
    "title": str,
    "source_url": str,
    "summary": str,
    "tags": list,
    "status": str,
}

ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*-\d{8}-\d{3}$")
VALID_STATUSES = {"draft", "review", "published", "archived"}
VALID_AUDIENCES = {"beginner", "intermediate", "advanced"}
MIN_SUMMARY_LENGTH = 20


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed command-line arguments.
    """
    parser = argparse.ArgumentParser(
        description="Validate knowledge article JSON files.",
        usage="python hooks/validate_json.py <json_file> [json_file2 ...]",
    )
    parser.add_argument(
        "json_files",
        nargs="+",
        help="JSON file paths or glob patterns such as knowledge/articles/*.json.",
    )
    return parser.parse_args()


def expand_input_paths(patterns: list[str]) -> list[Path]:
    """Expand file arguments and glob patterns into a stable path list.

    Args:
        patterns: Raw command-line file paths or glob patterns.

    Returns:
        A de-duplicated list of paths in deterministic order.
    """
    paths: list[Path] = []
    seen: set[Path] = set()

    for pattern in patterns:
        matches = glob.glob(pattern)
        candidates = matches if matches else [pattern]
        for candidate in candidates:
            path = Path(candidate)
            if path not in seen:
                paths.append(path)
                seen.add(path)

    return sorted(paths)


def load_json_file(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    """Load and parse a JSON object from disk.

    Args:
        path: JSON file path.

    Returns:
        A tuple containing the parsed object, if valid, and error messages.
    """
    if not path.exists():
        return None, [f"file does not exist: {path}"]

    if not path.is_file():
        return None, [f"path is not a file: {path}"]

    try:
        with path.open("r", encoding="utf-8") as json_file:
            data = json.load(json_file)
    except json.JSONDecodeError as exc:
        return None, [f"invalid JSON: line {exc.lineno}, column {exc.colno}: {exc.msg}"]
    except OSError as exc:
        LOGGER.exception("Failed to read JSON file %s", path)
        return None, [f"could not read file: {exc}"]

    if not isinstance(data, dict):
        return None, ["top-level JSON value must be an object"]

    return data, []


def validate_required_fields(data: dict[str, Any]) -> list[str]:
    """Validate required field presence and type.

    Args:
        data: Parsed JSON object.

    Returns:
        A list of validation error messages.
    """
    errors: list[str] = []

    for field_name, expected_type in REQUIRED_FIELDS.items():
        if field_name not in data:
            errors.append(f"missing required field: {field_name}")
            continue

        if not isinstance(data[field_name], expected_type):
            errors.append(
                f"field {field_name} must be {expected_type.__name__}, "
                f"got {type(data[field_name]).__name__}"
            )

    return errors


def is_valid_url(value: str) -> bool:
    """Check whether a URL uses HTTP(S) and includes a host.

    Args:
        value: URL string to validate.

    Returns:
        True if the URL has an HTTP or HTTPS scheme and a network location.
    """
    parsed_url = urlparse(value)
    return parsed_url.scheme in {"http", "https"} and bool(parsed_url.netloc)


def validate_article(data: dict[str, Any]) -> list[str]:
    """Validate a knowledge article JSON object.

    Args:
        data: Parsed JSON object.

    Returns:
        A list of validation error messages.
    """
    errors = validate_required_fields(data)

    article_id = data.get("id")
    if isinstance(article_id, str) and not ID_PATTERN.fullmatch(article_id):
        errors.append("id must match {source}-{YYYYMMDD}-{NNN}, e.g. github-20260317-001")

    status = data.get("status")
    if isinstance(status, str) and status not in VALID_STATUSES:
        allowed = ", ".join(sorted(VALID_STATUSES))
        errors.append(f"status must be one of: {allowed}")

    source_url = data.get("source_url")
    if isinstance(source_url, str) and not is_valid_url(source_url):
        errors.append("source_url must be a valid http:// or https:// URL")

    summary = data.get("summary")
    if isinstance(summary, str) and len(summary.strip()) < MIN_SUMMARY_LENGTH:
        errors.append(f"summary must be at least {MIN_SUMMARY_LENGTH} characters")

    tags = data.get("tags")
    if isinstance(tags, list):
        if not tags:
            errors.append("tags must contain at least 1 item")
        elif not all(isinstance(tag, str) for tag in tags):
            errors.append("tags must contain only strings")

    score = data.get("score")
    if score is not None:
        is_number = isinstance(score, (int, float)) and not isinstance(score, bool)
        if not is_number or not 1 <= score <= 10:
            errors.append("score must be a number between 1 and 10")

    audience = data.get("audience")
    if audience is not None and audience not in VALID_AUDIENCES:
        allowed = ", ".join(sorted(VALID_AUDIENCES))
        errors.append(f"audience must be one of: {allowed}")

    return errors


def validate_file(path: Path) -> list[str]:
    """Validate a single JSON file.

    Args:
        path: JSON file path.

    Returns:
        A list of validation error messages.
    """
    data, errors = load_json_file(path)
    if data is None:
        return errors

    return errors + validate_article(data)


def write_line(message: str = "") -> None:
    """Write one line to standard output.

    Args:
        message: Text to write.
    """
    sys.stdout.write(f"{message}\n")


def main() -> int:
    """Run the JSON validator.

    Returns:
        Process exit code. Returns 0 when all files pass, otherwise 1.
    """
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s: %(message)s")

    args = parse_args()
    paths = expand_input_paths(args.json_files)
    total_count = len(paths)
    passed_count = 0
    failed_count = 0
    error_count = 0

    for path in paths:
        errors = validate_file(path)
        if errors:
            failed_count += 1
            error_count += len(errors)
            write_line(f"{path}: FAILED")
            for error in errors:
                write_line(f"  - {error}")
        else:
            passed_count += 1
            write_line(f"{path}: OK")

    write_line(
        "Summary: "
        f"total={total_count}, passed={passed_count}, "
        f"failed={failed_count}, errors={error_count}"
    )

    return 1 if failed_count else 0


if __name__ == "__main__":
    sys.exit(main())
