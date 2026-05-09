"""Planning strategy selection for the knowledge-base workflow."""

from __future__ import annotations

import logging
import os
from typing import Any

from workflows.state import KBState


LOGGER = logging.getLogger(__name__)

ENV_TARGET_COUNT = "PLANNER_TARGET_COUNT"
DEFAULT_TARGET_COUNT = 10


def plan_strategy(target_count: Any = None) -> dict[str, Any]:
    """Build a collection strategy from the target article count.

    Args:
        target_count: Desired collection count. When omitted, the value is read
            from ``PLANNER_TARGET_COUNT`` and falls back to ``10``.

    Returns:
        Strategy dictionary with mode, collection limits, quality threshold,
        review-loop budget, target count, and rationale.
    """
    resolved_target = _resolve_target_count(target_count)

    if resolved_target < 10:
        return {
            "mode": "lite",
            "target_count": resolved_target,
            "per_source_limit": 5,
            "relevance_threshold": 0.8,
            "max_iterations": 1,
            "rationale": (
                "目标采集量低于 10，采用 lite 策略以提高相关性门槛、"
                "限制每个来源数量，并减少审核迭代成本。"
            ),
        }

    if resolved_target < 20:
        return {
            "mode": "standard",
            "target_count": resolved_target,
            "per_source_limit": 15,
            "relevance_threshold": 0.8,
            "max_iterations": 2,
            "rationale": (
                "目标采集量在 10 到 19 之间，采用 standard 策略以平衡"
                "覆盖范围、相关性要求和审核迭代成本。"
            ),
        }

    return {
        "mode": "full",
        "target_count": resolved_target,
        "per_source_limit": 20,
        "relevance_threshold": 0.8,
        "max_iterations": 3,
        "rationale": (
            "目标采集量不低于 20，采用 full 策略以扩大每个来源覆盖范围、"
            "适度降低相关性门槛，并保留更多审核迭代空间。"
        ),
    }


def planner_node(state: KBState) -> dict[str, dict[str, Any]]:
    """LangGraph node wrapper that writes the selected plan into state.

    Args:
        state: Shared workflow state.

    Returns:
        Partial state update containing ``plan``.
    """
    del state
    plan = plan_strategy()
    LOGGER.info(
        "[PlannerNode] selected mode=%s target_count=%s",
        plan["mode"],
        plan["target_count"],
    )
    return {"plan": plan}


def _resolve_target_count(target_count: Any) -> int:
    """Resolve and validate the target count input.

    Args:
        target_count: Explicit target count or ``None``.

    Returns:
        Integer target count, falling back to the default for invalid values.
    """
    raw_value = (
        os.getenv(ENV_TARGET_COUNT, str(DEFAULT_TARGET_COUNT))
        if target_count is None
        else target_count
    )
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        LOGGER.warning(
            "[Planner] invalid target count=%r; using default=%s",
            raw_value,
            DEFAULT_TARGET_COUNT,
        )
        return DEFAULT_TARGET_COUNT
