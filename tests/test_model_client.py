"""Tests for LLM model client cost tracking."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class CostTrackerTest(unittest.TestCase):
    """Verify token aggregation and cost estimation."""

    def test_record_and_estimated_cost_use_provider_price_table(self) -> None:
        """Cost tracker aggregates usage and estimates CNY cost."""
        from workflows.model_client import CostTracker, Usage

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
        from workflows import model_client

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

    def test_accumulate_usage_supports_dict_usage(self) -> None:
        """Usage summaries can be accumulated from dict-shaped usage data."""
        from workflows.model_client import accumulate_usage

        result = accumulate_usage(
            {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5, "calls": 1},
            {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10},
        )

        self.assertEqual(6, result["prompt_tokens"])
        self.assertEqual(9, result["completion_tokens"])
        self.assertEqual(15, result["total_tokens"])
        self.assertEqual(2, result["calls"])

    def test_chat_json_parses_object_response(self) -> None:
        """chat_json parses a JSON object from chat text."""
        from workflows import model_client

        original_chat = model_client.chat

        try:
            model_client.chat = lambda prompt, system=None: ('{"ok": true}', {})

            data, usage = model_client.chat_json("Return JSON")

            self.assertEqual({"ok": True}, data)
            self.assertEqual({}, usage)
        finally:
            model_client.chat = original_chat

    def test_get_provider_loads_local_env_file(self) -> None:
        """Provider config loads API keys from a local env file."""
        from workflows import model_client

        original_env_path = model_client.ENV_FILE_PATH
        original_api_key = model_client.os.environ.pop("DEEPSEEK_API_KEY", None)

        try:
            with TemporaryDirectory() as temp_dir:
                env_path = Path(temp_dir) / ".env"
                env_path.write_text("DEEPSEEK_API_KEY=local-test-key\n", encoding="utf-8")
                model_client.ENV_FILE_PATH = env_path

                provider = model_client.get_provider("deepseek")

            self.assertIsInstance(provider, model_client.OpenAICompatibleProvider)
            self.assertTrue(provider.api_key == "local-test-key")
        finally:
            model_client.ENV_FILE_PATH = original_env_path
            model_client.os.environ.pop("DEEPSEEK_API_KEY", None)
            if original_api_key is not None:
                model_client.os.environ["DEEPSEEK_API_KEY"] = original_api_key


if __name__ == "__main__":
    unittest.main()
