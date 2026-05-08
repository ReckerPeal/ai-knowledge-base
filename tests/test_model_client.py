"""Tests for LLM model client cost tracking."""

from __future__ import annotations

import unittest


class CostTrackerTest(unittest.TestCase):
    """Verify token aggregation and cost estimation."""

    def test_record_and_estimated_cost_use_provider_price_table(self) -> None:
        """Cost tracker aggregates usage and estimates CNY cost."""
        from pipeline.model_client import CostTracker, Usage

        tracker = CostTracker()
        tracker.record(
            Usage(
                prompt_tokens=1_000_000,
                completion_tokens=500_000,
                total_tokens=1_500_000,
            ),
            "deepseek",
        )

        self.assertEqual(2.0, tracker.estimated_cost("deepseek"))

    def test_chat_success_records_usage(self) -> None:
        """Provider chat records token usage after a successful response."""
        from pipeline import model_client

        cost_tracker = model_client.CostTracker()
        original_tracker = model_client.tracker
        original_httpx = model_client.httpx

        class FakeResponse:
            """Minimal response compatible with provider chat parsing."""

            def raise_for_status(self) -> None:
                """Pretend the request succeeded."""

            def json(self) -> dict[str, object]:
                """Return an OpenAI-compatible chat response."""
                return {
                    "model": "deepseek-chat",
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {
                        "prompt_tokens": 1000,
                        "completion_tokens": 500,
                        "total_tokens": 1500,
                    },
                }

        class FakeClient:
            """Minimal context-manager HTTP client."""

            def __init__(self, timeout: float) -> None:
                """Initialize the fake client.

                Args:
                    timeout: Request timeout supplied by the provider.
                """
                self.timeout = timeout

            def __enter__(self) -> "FakeClient":
                """Return the fake client."""
                return self

            def __exit__(self, *args: object) -> None:
                """Exit the fake client context."""

            def post(self, *args: object, **kwargs: object) -> FakeResponse:
                """Return a successful fake response."""
                return FakeResponse()

        class FakeHttpx:
            """Minimal httpx module replacement."""

            Client = FakeClient

        try:
            model_client.tracker = cost_tracker
            model_client.httpx = FakeHttpx()
            provider = model_client.OpenAICompatibleProvider(
                provider="deepseek",
                base_url="https://example.com/v1",
                api_key="test-key",
                model="deepseek-chat",
            )

            response = provider.chat([{"role": "user", "content": "hello"}])

            self.assertEqual("ok", response.content)
            self.assertEqual(0.002, cost_tracker.estimated_cost("deepseek"))
        finally:
            model_client.tracker = original_tracker
            model_client.httpx = original_httpx


if __name__ == "__main__":
    unittest.main()
