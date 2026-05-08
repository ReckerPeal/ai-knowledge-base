"""Human-review fallback node for exhausted review loops."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from hashlib import sha1
from pathlib import Path
from typing import Any

from workflows.state import KBState


LOGGER = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PENDING_REVIEW_DIR = PROJECT_ROOT / "pending_review"
DEFAULT_MAX_ITERATIONS = 3
CHINA_TZ = timezone(timedelta(hours=8))


def human_flag_node(state: KBState) -> dict[str, Any]:
    """Write unresolved analyses to ``pending_review/`` for human judgment.

    Args:
        state: Shared workflow state containing analyses, review status,
            review_feedback, iteration, and optional max_iterations.

    Returns:
        Empty update before the iteration limit. Once the limit is exhausted,
        returns a human-review flag and the written pending-review file path.
    """
    iteration = int(state.get("iteration") or 0)
    max_iterations = _max_iterations(state)
    if state.get("review_passed") or iteration < max_iterations:
        return {}

    analyses = [item for item in list(state.get("analyses") or []) if isinstance(item, dict)]
    review_feedback = str(state.get("review_feedback") or "")
    payload = {
        "status": "pending_human_review",
        "created_at": _now_iso(),
        "iteration": iteration,
        "max_iterations": max_iterations,
        "review_feedback": review_feedback,
        "plan": state.get("plan") or {},
        "analyses": analyses,
    }

    PENDING_REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    pending_path = PENDING_REVIEW_DIR / f"{_pending_id(payload)}.json"
    pending_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    LOGGER.info("[HumanFlagNode] wrote pending review file path=%s", pending_path)

    return {
        "needs_human_review": True,
        "pending_review_paths": [str(pending_path)],
    }


def _max_iterations(state: KBState) -> int:
    """Read the review loop limit from state or plan.

    Args:
        state: Shared workflow state.

    Returns:
        Positive max iteration count.
    """
    plan = state.get("plan") if isinstance(state.get("plan"), dict) else {}
    value = state.get("max_iterations") or plan.get("max_iterations")
    try:
        max_iterations = int(value)
    except (TypeError, ValueError):
        max_iterations = DEFAULT_MAX_ITERATIONS
    return max(max_iterations, 1)


def _pending_id(payload: dict[str, Any]) -> str:
    """Create a stable-ish pending-review file id for this payload.

    Args:
        payload: Pending-review payload.

    Returns:
        Filename stem with timestamp and content digest.
    """
    date_part = str(payload["created_at"])[:19].replace("-", "").replace(":", "")
    digest = sha1(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"),
    ).hexdigest()[:10]
    return f"{date_part}-pending-review-{digest}"


def _now_iso() -> str:
    """Return current timestamp in ISO 8601 with China timezone."""
    return datetime.now(CHINA_TZ).isoformat(timespec="seconds")
