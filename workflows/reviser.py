"""Revision node for improving analyses from reviewer feedback."""

from __future__ import annotations

import json
import logging
from typing import Any

from workflows.model_client import accumulate_usage, chat_json
from workflows.state import KBState


LOGGER = logging.getLogger(__name__)

REVISE_TEMPERATURE = 0.4


def revise_node(state: KBState) -> dict[str, Any]:
    """Revise analyses according to review feedback.

    Args:
        state: Shared workflow state containing analyses, review_feedback, and
            cost_tracker.

    Returns:
        Empty update when there is nothing to revise, otherwise updated analyses
        and accumulated cost tracking data.
    """
    analyses = list(state.get("analyses") or [])
    review_feedback = str(state.get("review_feedback") or "").strip()
    if not analyses or not review_feedback:
        return {}

    system = "你是 AI 技术知识库编辑，输出必须是 JSON 对象。"
    prompt_payload = {
        "review_feedback": review_feedback,
        "analyses": analyses,
    }
    prompt = (
        "请根据审核反馈改写 analyses，保留 source_url、published_at、"
        "collected_at、metadata 等可追溯字段，不要虚构来源。\n"
        "只返回 JSON 对象：{\"analyses\": [ ... ]}。\n"
        f"修订输入：{json.dumps(prompt_payload, ensure_ascii=False)}"
    )

    try:
        revised, usage = chat_json(
            prompt,
            system=system,
            temperature=REVISE_TEMPERATURE,
        )
    except Exception as exc:
        LOGGER.warning("[ReviseNode] LLM revision failed; keeping current analyses: %s", exc)
        return {}

    improved = revised.get("analyses")
    if not isinstance(improved, list):
        LOGGER.warning("[ReviseNode] revision response missing analyses list")
        return {}

    return {
        "analyses": [item for item in improved if isinstance(item, dict)],
        "cost_tracker": accumulate_usage(state.get("cost_tracker") or {}, usage),
    }
