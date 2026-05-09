"""Budget guard utilities for multi-agent LLM workflows."""

from __future__ import annotations

import json
import logging
import unittest
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


class BudgetExceededError(Exception):
    """Raised when accumulated LLM cost exceeds the configured budget."""


@dataclass(frozen=True)
class CostRecord:
    """Record one LLM call's token usage and estimated cost.

    Args:
        timestamp: ISO 8601 timestamp for the recorded call.
        node_name: Agent or graph node name that made the call.
        prompt_tokens: Number of input tokens used by the call.
        completion_tokens: Number of output tokens used by the call.
        cost_yuan: Estimated CNY cost for the call.
        model: Model name or identifier, when available.
    """

    timestamp: str
    node_name: str
    prompt_tokens: int
    completion_tokens: int
    cost_yuan: float
    model: str


class CostGuard:
    """Track and guard LLM cost for multi-agent workflows."""

    def __init__(
        self,
        budget_yuan: float = 1.0,
        alert_threshold: float = 0.8,
        input_price_per_million: float = 1.0,
        output_price_per_million: float = 2.0,
    ) -> None:
        """Initialize a cost guard.

        Args:
            budget_yuan: Maximum allowed total spend in CNY.
            alert_threshold: Usage ratio that triggers a warning status.
            input_price_per_million: Prompt token price per million tokens.
            output_price_per_million: Completion token price per million tokens.

        Raises:
            ValueError: If budget, threshold, or prices are invalid.
        """
        if budget_yuan <= 0:
            raise ValueError("budget_yuan must be greater than 0.")
        if not 0 <= alert_threshold <= 1:
            raise ValueError("alert_threshold must be between 0 and 1.")
        if input_price_per_million < 0 or output_price_per_million < 0:
            raise ValueError("token prices must not be negative.")

        self.budget_yuan = float(budget_yuan)
        self.alert_threshold = float(alert_threshold)
        self.input_price_per_million = float(input_price_per_million)
        self.output_price_per_million = float(output_price_per_million)
        self.records: list[CostRecord] = []

    @property
    def total_prompt_tokens(self) -> int:
        """Return accumulated prompt tokens."""
        return sum(record.prompt_tokens for record in self.records)

    @property
    def total_completion_tokens(self) -> int:
        """Return accumulated completion tokens."""
        return sum(record.completion_tokens for record in self.records)

    @property
    def total_cost_yuan(self) -> float:
        """Return accumulated estimated cost in CNY."""
        return round(sum(record.cost_yuan for record in self.records), 10)

    def record(self, node_name: str, usage: dict[str, int], model: str = "") -> CostRecord:
        """Record one LLM call and return the created cost record.

        Args:
            node_name: Agent or graph node name that made the call.
            usage: Token usage shaped as ``{"prompt_tokens": int, "completion_tokens": int}``.
            model: Model name or identifier, when available.

        Returns:
            The created ``CostRecord``.

        Raises:
            ValueError: If node name or usage values are invalid.
        """
        if not node_name:
            raise ValueError("node_name must not be empty.")

        prompt_tokens = self._read_token_count(usage, "prompt_tokens")
        completion_tokens = self._read_token_count(usage, "completion_tokens")
        cost_yuan = self._calculate_cost(prompt_tokens, completion_tokens)

        record = CostRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            node_name=node_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_yuan=cost_yuan,
            model=model,
        )
        self.records.append(record)
        logger.info(
            "Recorded LLM cost for node %s: prompt_tokens=%s completion_tokens=%s cost_yuan=%.10f",
            node_name,
            prompt_tokens,
            completion_tokens,
            cost_yuan,
        )
        return record

    def check(self) -> dict[str, Any]:
        """Check current budget status.

        Returns:
            A budget status dictionary containing status, total_cost, budget,
            usage_ratio, and message.

        Raises:
            BudgetExceededError: If total cost is greater than the configured budget.
        """
        total_cost = self.total_cost_yuan
        usage_ratio = total_cost / self.budget_yuan

        if total_cost > self.budget_yuan:
            message = (
                f"Budget exceeded: total cost {total_cost:.6f} yuan is greater "
                f"than budget {self.budget_yuan:.6f} yuan."
            )
            raise BudgetExceededError(message)

        if usage_ratio >= self.alert_threshold:
            status = "warning"
            message = (
                f"Budget warning: usage ratio {usage_ratio:.2%} reached alert "
                f"threshold {self.alert_threshold:.2%}."
            )
        else:
            status = "ok"
            message = "Budget usage is within the normal range."

        return {
            "status": status,
            "total_cost": total_cost,
            "budget": self.budget_yuan,
            "usage_ratio": usage_ratio,
            "message": message,
        }

    def get_report(self) -> dict[str, Any]:
        """Generate a cost report grouped by node name.

        Returns:
            A JSON-serializable dictionary with total and per-node cost metrics.
        """
        nodes: dict[str, dict[str, Any]] = {}
        for record in self.records:
            node = nodes.setdefault(
                record.node_name,
                {
                    "calls": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "cost_yuan": 0.0,
                    "models": [],
                },
            )
            node["calls"] += 1
            node["prompt_tokens"] += record.prompt_tokens
            node["completion_tokens"] += record.completion_tokens
            node["cost_yuan"] += record.cost_yuan
            if record.model and record.model not in node["models"]:
                node["models"].append(record.model)

        for node in nodes.values():
            node["cost_yuan"] = round(node["cost_yuan"], 10)

        return {
            "budget_yuan": self.budget_yuan,
            "alert_threshold": self.alert_threshold,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_cost_yuan": self.total_cost_yuan,
            "usage_ratio": self.total_cost_yuan / self.budget_yuan,
            "total_calls": len(self.records),
            "cost_by_node": nodes,
            "nodes": nodes,
            "records": [asdict(record) for record in self.records],
        }

    def save_report(self, path: str | Path | None = None) -> Path:
        """Save the current cost report as a JSON file.

        Args:
            path: Output path. Defaults to ``cost_report.json`` in the current
                working directory.

        Returns:
            The path that was written.
        """
        output_path = Path(path) if path is not None else Path("cost_report.json")
        report = self.get_report()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Saved cost report to %s", output_path)
        return output_path

    def _calculate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        """Calculate call cost from token counts."""
        input_cost = prompt_tokens / 1_000_000 * self.input_price_per_million
        output_cost = completion_tokens / 1_000_000 * self.output_price_per_million
        return round(input_cost + output_cost, 10)

    @staticmethod
    def _read_token_count(usage: dict[str, int], key: str) -> int:
        """Read and validate one token count from a usage dictionary."""
        value = usage.get(key)
        if not isinstance(value, int):
            raise ValueError(f"usage['{key}'] must be an integer.")
        if value < 0:
            raise ValueError(f"usage['{key}'] must not be negative.")
        return value


class CostGuardSelfTest(unittest.TestCase):
    """Self-tests for the standalone cost guard script."""

    def test_record_tracks_tokens_and_cost(self) -> None:
        """Recording usage updates total tokens and estimated cost."""
        guard = CostGuard()

        guard.record(
            "collector",
            {"prompt_tokens": 1_000_000, "completion_tokens": 500_000},
            model="test-model",
        )

        self.assertEqual(1_000_000, guard.total_prompt_tokens)
        self.assertEqual(2.0, guard.total_cost_yuan)

    def test_check_raises_when_budget_is_exceeded(self) -> None:
        """Budget checks raise when total cost exceeds budget."""
        guard = CostGuard(budget_yuan=1.0)
        guard.record("analyzer", {"prompt_tokens": 1_000_000, "completion_tokens": 1})

        with self.assertRaises(BudgetExceededError):
            guard.check()

    def test_check_returns_warning_when_threshold_is_reached(self) -> None:
        """Budget checks return warning at or above the alert threshold."""
        guard = CostGuard(budget_yuan=1.0, alert_threshold=0.8)
        guard.record("curator", {"prompt_tokens": 800_000, "completion_tokens": 0})

        status = guard.check()

        self.assertEqual("warning", status["status"])


if __name__ == "__main__":
    unittest.main()
