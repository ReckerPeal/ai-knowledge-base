#!/usr/bin/env python3
"""Score knowledge article JSON files across five quality dimensions."""

from __future__ import annotations

import argparse
import glob
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


LOGGER = logging.getLogger(__name__)

TECH_KEYWORDS = {
    "agent",
    "ai",
    "api",
    "benchmark",
    "dataset",
    "embedding",
    "evaluation",
    "framework",
    "inference",
    "langchain",
    "langgraph",
    "llm",
    "mcp",
    "memory",
    "model",
    "multimodal",
    "rag",
    "reasoning",
    "retrieval",
    "transformer",
    "workflow",
    "代理",
    "大模型",
    "工作流",
    "推理",
    "检索",
    "模型",
    "评测",
}
STANDARD_TAGS = {
    "AI",
    "LLM",
    "Agent",
    "Framework",
    "RAG",
    "MCP",
    "Tooling",
    "Evaluation",
    "Benchmark",
    "Dataset",
    "Research",
    "Memory",
    "Multimodal",
    "Automation",
    "Security",
    "Coding",
    "Python",
    "GitHub",
    "HackerNews",
}
VALID_STATUSES = {"draft", "review", "reviewed", "published", "archived"}
CHINESE_HOLLOW_WORDS = {
    "赋能",
    "抓手",
    "闭环",
    "打通",
    "全链路",
    "底层逻辑",
    "颗粒度",
    "对齐",
    "拉通",
    "沉淀",
    "强大的",
    "革命性的",
}
ENGLISH_HOLLOW_WORDS = {
    "groundbreaking",
    "revolutionary",
    "game-changing",
    "cutting-edge",
    "empower",
    "synergy",
    "seamless",
    "transformative",
}


@dataclass(frozen=True)
class DimensionScore:
    """A single quality dimension score.

    Attributes:
        name: Human-readable dimension name.
        score: Points awarded for this dimension.
        max_score: Maximum points available for this dimension.
        notes: Short scoring explanation.
    """

    name: str
    score: float
    max_score: int
    notes: str


@dataclass(frozen=True)
class QualityReport:
    """Quality scoring result for one knowledge article.

    Attributes:
        path: Path to the scored article file.
        total_score: Weighted total score out of 100.
        grade: Letter grade for the total score.
        dimension_scores: Per-dimension scoring details.
        errors: File loading or parsing errors.
    """

    path: Path
    total_score: float
    grade: str
    dimension_scores: list[DimensionScore]
    errors: list[str]


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed command-line arguments.
    """
    parser = argparse.ArgumentParser(
        description="Score knowledge article JSON quality.",
        usage="python hooks/check_quality.py <json_file|glob> [more ...]",
    )
    parser.add_argument(
        "json_files",
        nargs="+",
        help="JSON file paths or glob patterns such as knowledge/articles/*.json.",
    )
    return parser.parse_args()


def expand_input_paths(patterns: list[str]) -> list[Path]:
    """Expand file arguments and glob patterns into deterministic paths.

    Args:
        patterns: Raw file paths or glob patterns.

    Returns:
        De-duplicated paths sorted by string value.
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
    """Load a JSON object from disk.

    Args:
        path: JSON file path.

    Returns:
        Parsed object and a list of loading errors.
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


def grade_for_score(total_score: float) -> str:
    """Convert a numeric score to a grade.

    Args:
        total_score: Weighted total score.

    Returns:
        A, B, or C grade.
    """
    if total_score >= 80:
        return "A"
    if total_score >= 60:
        return "B"
    return "C"


def score_summary(data: dict[str, Any]) -> DimensionScore:
    """Score summary length and technical keyword coverage.

    Args:
        data: Knowledge article object.

    Returns:
        Summary quality dimension score.
    """
    summary = data.get("summary")
    if not isinstance(summary, str):
        return DimensionScore("摘要质量", 0.0, 25, "summary 缺失或不是字符串")

    clean_summary = summary.strip()
    summary_length = len(clean_summary)
    lowered_summary = clean_summary.lower()
    keyword_hits = sorted(
        keyword
        for keyword in TECH_KEYWORDS
        if keyword.lower() in lowered_summary
    )

    if summary_length >= 50:
        length_score = 25.0
    elif summary_length >= 20:
        length_score = 15.0
    else:
        length_score = min(10.0, summary_length / 20 * 10)

    keyword_bonus = min(5.0, len(keyword_hits) * 2.5)
    score = min(25.0, length_score + keyword_bonus)
    notes = f"{summary_length} 字符"
    if keyword_hits:
        notes += f"，关键词: {', '.join(keyword_hits[:5])}"
    else:
        notes += "，未命中技术关键词"
    return DimensionScore("摘要质量", score, 25, notes)


def score_technical_depth(data: dict[str, Any]) -> DimensionScore:
    """Score technical depth from the article score field.

    Args:
        data: Knowledge article object.

    Returns:
        Technical depth dimension score.
    """
    raw_score = data.get("score")
    if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
        return DimensionScore("技术深度", 0.0, 25, "score 缺失或不是数字")

    clamped_score = max(0.0, min(10.0, float(raw_score)))
    score = clamped_score / 10 * 25
    return DimensionScore("技术深度", score, 25, f"score={raw_score}，按 1-10 映射")


def is_valid_url(value: Any) -> bool:
    """Check whether a value is a valid HTTP(S) URL.

    Args:
        value: URL candidate.

    Returns:
        True when the value is an HTTP(S) URL with host.
    """
    if not isinstance(value, str):
        return False
    parsed_url = urlparse(value)
    return parsed_url.scheme in {"http", "https"} and bool(parsed_url.netloc)


def is_valid_timestamp(value: Any) -> bool:
    """Check whether a value is an ISO 8601 timestamp.

    Args:
        value: Timestamp candidate.

    Returns:
        True when the value is null or a valid ISO 8601 string.
    """
    if value is None:
        return True
    if not isinstance(value, str) or not value.strip():
        return False

    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def score_format(data: dict[str, Any]) -> DimensionScore:
    """Score required format fields.

    Args:
        data: Knowledge article object.

    Returns:
        Format compliance dimension score.
    """
    checks = {
        "id": isinstance(data.get("id"), str) and bool(data["id"].strip()),
        "title": isinstance(data.get("title"), str) and bool(data["title"].strip()),
        "source_url": is_valid_url(data.get("source_url")),
        "status": data.get("status") in VALID_STATUSES,
        "时间戳": (
            is_valid_timestamp(data.get("collected_at"))
            and is_valid_timestamp(data.get("published_at"))
            and data.get("collected_at") is not None
        ),
    }
    score = sum(4 for passed in checks.values() if passed)
    failed = [name for name, passed in checks.items() if not passed]
    notes = "五项格式完整" if not failed else f"需修正: {', '.join(failed)}"
    return DimensionScore("格式规范", float(score), 20, notes)


def score_tags(data: dict[str, Any]) -> DimensionScore:
    """Score tag count and membership in the standard tag list.

    Args:
        data: Knowledge article object.

    Returns:
        Tag precision dimension score.
    """
    tags = data.get("tags")
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        return DimensionScore("标签精度", 0.0, 15, "tags 缺失或不是字符串数组")

    unique_tags = list(dict.fromkeys(tag.strip() for tag in tags if tag.strip()))
    legal_tags = [tag for tag in unique_tags if tag in STANDARD_TAGS]
    illegal_tags = [tag for tag in unique_tags if tag not in STANDARD_TAGS]

    count_score = 7.0 if 1 <= len(unique_tags) <= 3 else 3.0 if unique_tags else 0.0
    legal_score = 8.0 * len(legal_tags) / len(unique_tags) if unique_tags else 0.0
    score = min(15.0, count_score + legal_score)
    if illegal_tags:
        notes = f"非法标签: {', '.join(illegal_tags)}"
    else:
        notes = f"{len(unique_tags)} 个合法标签"
    return DimensionScore("标签精度", score, 15, notes)


def score_hollow_words(data: dict[str, Any]) -> DimensionScore:
    """Detect hollow words in summary, content, and title fields.

    Args:
        data: Knowledge article object.

    Returns:
        Hollow-word detection dimension score.
    """
    text_parts = [
        str(data.get("title", "")),
        str(data.get("summary", "")),
        str(data.get("content", "")),
    ]
    text = "\n".join(text_parts)
    lowered_text = text.lower()
    chinese_hits = sorted(word for word in CHINESE_HOLLOW_WORDS if word in text)
    english_hits = sorted(word for word in ENGLISH_HOLLOW_WORDS if word in lowered_text)
    hits = chinese_hits + english_hits
    penalty = min(15.0, len(hits) * 3.0)
    score = 15.0 - penalty
    notes = "未发现空洞词" if not hits else f"命中: {', '.join(hits)}"
    return DimensionScore("空洞词检测", score, 15, notes)


def score_article(path: Path, data: dict[str, Any]) -> QualityReport:
    """Score one parsed knowledge article.

    Args:
        path: Article path used in report output.
        data: Parsed JSON object.

    Returns:
        Complete quality report.
    """
    dimension_scores = [
        score_summary(data),
        score_technical_depth(data),
        score_format(data),
        score_tags(data),
        score_hollow_words(data),
    ]
    total_score = sum(score.score for score in dimension_scores)
    grade = grade_for_score(total_score)
    return QualityReport(path, total_score, grade, dimension_scores, [])


def score_file(path: Path) -> QualityReport:
    """Load and score a single file.

    Args:
        path: JSON file path.

    Returns:
        Complete quality report. Invalid files receive grade C.
    """
    data, errors = load_json_file(path)
    if data is None:
        return QualityReport(path, 0.0, "C", [], errors)

    return score_article(path, data)


def progress_bar(current: int, total: int, width: int = 24) -> str:
    """Build a text progress bar.

    Args:
        current: Current completed item count.
        total: Total item count.
        width: Number of cells in the bar.

    Returns:
        Rendered progress bar string.
    """
    if total <= 0:
        return "[" + "-" * width + "]"
    filled = round(width * current / total)
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def format_points(value: float) -> str:
    """Format score values compactly.

    Args:
        value: Score value.

    Returns:
        Formatted score string.
    """
    if value.is_integer():
        return str(int(value))
    return f"{value:.1f}"


def write_line(message: str = "") -> None:
    """Write one line to standard output.

    Args:
        message: Text to write.
    """
    sys.stdout.write(f"{message}\n")


def print_report(report: QualityReport, current: int, total: int) -> None:
    """Print one quality report.

    Args:
        report: Quality report to render.
        current: Current report index.
        total: Total reports count.
    """
    write_line(f"{progress_bar(current, total)} {current}/{total} {report.path}")
    if report.errors:
        write_line("  等级: C，总分: 0/100")
        for error in report.errors:
            write_line(f"  - 错误: {error}")
        return

    write_line(f"  等级: {report.grade}，总分: {format_points(report.total_score)}/100")
    for dimension in report.dimension_scores:
        score = format_points(dimension.score)
        write_line(
            f"  - {dimension.name}: {score}/{dimension.max_score} "
            f"({dimension.notes})"
        )


def main() -> int:
    """Run the quality checker.

    Returns:
        Process exit code. Returns 1 when any article is grade C.
    """
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s: %(message)s")
    args = parse_args()
    paths = expand_input_paths(args.json_files)
    reports = [score_file(path) for path in paths]

    for index, report in enumerate(reports, start=1):
        print_report(report, index, len(reports))

    grade_counts = {"A": 0, "B": 0, "C": 0}
    for report in reports:
        grade_counts[report.grade] += 1

    write_line(
        "Summary: "
        f"total={len(reports)}, A={grade_counts['A']}, "
        f"B={grade_counts['B']}, C={grade_counts['C']}"
    )
    return 1 if grade_counts["C"] else 0


if __name__ == "__main__":
    sys.exit(main())
