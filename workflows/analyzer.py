"""Analysis node for source summaries."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from workflows import model_client
from workflows.state import KBState


LOGGER = logging.getLogger(__name__)

CHINA_TZ = timezone(timedelta(hours=8))


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


def _now_iso() -> str:
    """Return the current timestamp in ISO 8601 with China timezone.

    Returns:
        Current timestamp string.
    """
    return datetime.now(CHINA_TZ).isoformat(timespec="seconds")
