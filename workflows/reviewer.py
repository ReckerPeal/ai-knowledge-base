"""Review node for analysis quality gates."""

from __future__ import annotations

import json
import logging
from typing import Any

from workflows.model_client import accumulate_usage, chat_json
from workflows.state import KBState


LOGGER = logging.getLogger(__name__)

REVIEW_LIMIT = 5
PASS_THRESHOLD = 7.0
REVIEW_TEMPERATURE = 0.1
DEFAULT_MAX_ITERATIONS = 3
SCORE_WEIGHTS: dict[str, float] = {
    "summary_quality": 0.25,
    "technical_depth": 0.25,
    "relevance": 0.20,
    "originality": 0.15,
    "formatting": 0.15,
}


def review_node(state: KBState) -> dict[str, Any]:
    """Review the first five analyses and return a workflow gate decision.

    The LLM provides dimension scores and feedback, but the weighted total and
    pass/fail decision are computed locally to avoid trusting model arithmetic.
    LLM failures auto-pass so review outages do not block the workflow.

    Args:
        state: Shared workflow state containing plan, analyses, iteration, and
            cost_tracker.

    Returns:
        Partial state update with review_passed, review_feedback, iteration, and
        cost_tracker.
    """
    LOGGER.info("[ReviewNode] reviewing analyses")
    iteration = int(state.get("iteration") or 0)
    plan = state.get("plan", {}) or {}
    max_iterations = int(plan.get("max_iterations", DEFAULT_MAX_ITERATIONS))
    cost_tracker = dict(state.get("cost_tracker") or {})
    analyses = list(state.get("analyses") or [])[:REVIEW_LIMIT]
    if iteration >= max_iterations:
        return {
            "review_passed": True,
            "review_feedback": (
                f"iteration >= {max_iterations}，达到最大审核轮次，自动通过。"
            ),
            "iteration": iteration + 1,
            "cost_tracker": cost_tracker,
        }

    system = "你是 AI 技术知识库质量审核专家，输出必须是 JSON 对象。"
    prompt_payload = {
        "plan": state.get("plan") or {},
        "analyses": analyses,
        "rubric": {
            "summary_quality": "摘要质量，1-10 分，权重 25%",
            "technical_depth": "技术深度，1-10 分，权重 25%",
            "relevance": "与 AI/LLM/Agent 知识库的相关性，1-10 分，权重 20%",
            "originality": "原创性与信息增量，1-10 分，权重 15%",
            "formatting": "结构、字段、标签等格式规范，1-10 分，权重 15%",
        },
    }
    prompt = (
        "请审核以下 analyses，只返回 JSON 对象："
        '{"scores": {"summary_quality": number, "technical_depth": number, '
        '"relevance": number, "originality": number, "formatting": number}, '
        '"feedback": string}。\n'
        "每个维度必须为 1-10 分，可包含小数。不要计算总分。\n"
        f"待审核 analyses：{json.dumps(prompt_payload, ensure_ascii=False)}"
    )

    try:
        review, usage = chat_json(
            prompt,
            system=system,
            temperature=REVIEW_TEMPERATURE,
            node_name="reviewer",
        )
    except Exception:
        LOGGER.exception("[ReviewNode] LLM review failed; auto passing")
        return {
            "review_passed": True,
            "review_feedback": "LLM 审核失败，自动通过以避免阻塞流程。",
            "iteration": iteration + 1,
            "cost_tracker": cost_tracker,
        }

    updated_tracker = accumulate_usage(cost_tracker, usage)
    scores = _normalize_scores(review.get("scores"))
    weighted_score = _weighted_score(scores)
    passed = weighted_score >= PASS_THRESHOLD
    feedback = str(review.get("feedback") or "")
    review_feedback = (
        f"weighted_score={weighted_score:.2f}, "
        f"threshold={PASS_THRESHOLD:.2f}, "
        f"scores={json.dumps(scores, ensure_ascii=False)}. "
        f"{feedback}"
    ).strip()

    return {
        "review_passed": passed,
        "review_feedback": review_feedback,
        "iteration": iteration + 1,
        "cost_tracker": updated_tracker,
    }


def _normalize_scores(value: Any) -> dict[str, float]:
    """Normalize reviewer scores to all required dimensions.

    Args:
        value: Raw scores object from the LLM.

    Returns:
        Dimension scores clamped to the 1-10 range. Missing dimensions score 1.
    """
    raw_scores = value if isinstance(value, dict) else {}
    return {
        dimension: _clamp_score(raw_scores.get(dimension))
        for dimension in SCORE_WEIGHTS
    }


def _clamp_score(value: Any) -> float:
    """Convert a raw score to a 1-10 float.

    Args:
        value: Raw score value.

    Returns:
        Score clamped to 1-10.
    """
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 1.0
    return min(max(score, 1.0), 10.0)


def _weighted_score(scores: dict[str, float]) -> float:
    """Compute the weighted review score from local weights.

    Args:
        scores: Normalized dimension scores.

    Returns:
        Weighted score on a 1-10 scale.
    """
    return sum(scores[dimension] * weight for dimension, weight in SCORE_WEIGHTS.items())
