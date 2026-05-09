"""Unified client for calling OpenAI-compatible LLM providers."""

from __future__ import annotations

import logging
import os
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests.cost_guard import CostGuard

try:
    import httpx
except ModuleNotFoundError:
    httpx = None  # type: ignore[assignment]


LOGGER = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE_PATH = PROJECT_ROOT / ".env"
DEFAULT_PROVIDER = "deepseek"
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_TEMPERATURE = 0.3
DEFAULT_MAX_TOKENS = 2048
_cost_guard: CostGuard | None = None

PROVIDER_CONFIGS: dict[str, dict[str, str]] = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
        "model": "deepseek-chat",
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "QWEN_API_KEY",
        "fallback_api_key_env": "DASHSCOPE_API_KEY",
        "model": "qwen-plus",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "model": "gpt-4o-mini",
    },
}

# Approximate public API prices in USD per 1M tokens. Override or extend these
# values when provider pricing changes or project-specific models are used.
MODEL_COSTS_USD_PER_MILLION: dict[str, dict[str, float]] = {
    "deepseek-chat": {"input": 0.27, "output": 1.10},
    "qwen-plus": {"input": 0.40, "output": 1.20},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
}

PROVIDER_COSTS_CNY_PER_MILLION: dict[str, dict[str, float]] = {
    "deepseek": {"input": 1.0, "output": 2.0},
    "qwen": {"input": 4.0, "output": 12.0},
    "openai": {"input": 150.0, "output": 600.0},
}


@dataclass(frozen=True)
class Usage:
    """Token usage statistics for one LLM call.

    Attributes:
        prompt_tokens: Number of input tokens.
        completion_tokens: Number of output tokens.
        total_tokens: Total tokens consumed.
        estimated: Whether token counts were estimated locally.
    """

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated: bool = False


@dataclass(frozen=True)
class LLMResponse:
    """Normalized response returned by all LLM providers.

    Attributes:
        content: Assistant message text.
        usage: Token usage statistics.
        model: Model name used for the request.
        provider: Provider name used for the request.
        cost_usd: Estimated request cost in USD.
    """

    content: str
    usage: Usage
    model: str
    provider: str
    cost_usd: float


@dataclass
class CostSummary:
    """Aggregated token and cost data for one provider.

    Attributes:
        calls: Number of successful LLM calls.
        prompt_tokens: Total input tokens.
        completion_tokens: Total output tokens.
        total_tokens: Total tokens.
    """

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class CostTracker:
    """Track LLM token usage and estimated cost by provider."""

    def __init__(
        self,
        price_table: dict[str, dict[str, float]] | None = None,
    ) -> None:
        """Initialize the cost tracker.

        Args:
            price_table: Optional provider price table in CNY per million tokens.
        """
        self.price_table = price_table or PROVIDER_COSTS_CNY_PER_MILLION
        self._summaries: dict[str, CostSummary] = {}

    def record(self, usage: Usage, provider: str) -> None:
        """Record one successful LLM API call.

        Args:
            usage: Token usage returned by the provider.
            provider: Provider name, such as ``deepseek`` or ``qwen``.
        """
        normalized_provider = provider.lower()
        summary = self._summaries.setdefault(normalized_provider, CostSummary())
        summary.calls += 1
        summary.prompt_tokens += usage.prompt_tokens
        summary.completion_tokens += usage.completion_tokens
        summary.total_tokens += usage.total_tokens

    def estimated_cost(self, provider: str) -> float:
        """Return estimated provider cost in CNY.

        Args:
            provider: Provider name to estimate.

        Returns:
            Estimated cost in yuan. Unknown providers return ``0.0``.
        """
        normalized_provider = provider.lower()
        summary = self._summaries.get(normalized_provider)
        price = self.price_table.get(normalized_provider)
        if summary is None or price is None:
            return 0.0

        input_cost = summary.prompt_tokens * price["input"] / 1_000_000
        output_cost = summary.completion_tokens * price["output"] / 1_000_000
        return round(input_cost + output_cost, 6)

    def report(self, provider: str | None = None) -> None:
        """Log a cost report for one provider or all recorded providers.

        Args:
            provider: Optional provider name. When omitted, reports all providers
                with recorded usage.
        """
        providers = [provider.lower()] if provider else sorted(self._summaries)
        if not providers:
            LOGGER.info("LLM cost report: no successful calls recorded")
            return

        for provider_name in providers:
            summary = self._summaries.get(provider_name, CostSummary())
            LOGGER.info(
                "LLM cost report provider=%s calls=%s input_tokens=%s "
                "output_tokens=%s total_tokens=%s estimated_cost_cny=%.6f",
                provider_name,
                summary.calls,
                summary.prompt_tokens,
                summary.completion_tokens,
                summary.total_tokens,
                self.estimated_cost(provider_name),
            )


tracker = CostTracker()


def get_cost_guard() -> CostGuard:
    """Return the process-wide lazy-loaded cost guard.

    The budget is read from ``BUDGET_YUAN`` on the first call. Subsequent calls
    reuse the same instance so budget accounting spans all chat calls in the
    current process.

    Returns:
        Shared ``CostGuard`` instance.

    Raises:
        ValueError: If ``BUDGET_YUAN`` is set to a non-numeric value.
    """
    global _cost_guard

    if _cost_guard is None:
        load_env_file()
        budget_yuan = float(os.getenv("BUDGET_YUAN", "1.0"))
        _cost_guard = CostGuard(budget_yuan=budget_yuan)
    return _cost_guard


class LLMProvider(ABC):
    """Abstract interface for an LLM provider."""

    @abstractmethod
    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> LLMResponse:
        """Send chat messages to the provider.

        Args:
            messages: OpenAI-compatible chat messages.
            temperature: Sampling temperature.
            max_tokens: Maximum number of completion tokens.

        Returns:
            Normalized LLM response.
        """


class OpenAICompatibleProvider(LLMProvider):
    """LLM provider that calls the OpenAI-compatible chat completions API."""

    def __init__(
        self,
        *,
        provider: str,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        """Initialize an OpenAI-compatible provider.

        Args:
            provider: Provider identifier, such as ``deepseek``.
            base_url: API base URL without the endpoint path.
            api_key: Provider API key.
            model: Model name.
            timeout_seconds: Request timeout in seconds.
        """
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> LLMResponse:
        """Send chat messages to an OpenAI-compatible endpoint.

        Args:
            messages: OpenAI-compatible chat messages.
            temperature: Sampling temperature.
            max_tokens: Maximum number of completion tokens.

        Returns:
            Normalized LLM response.

        Raises:
            httpx.HTTPError: If the API request fails.
            ValueError: If the API response has an unexpected shape.
        """
        if httpx is None:
            raise RuntimeError("httpx is required for LLM API calls")

        validate_messages(messages)
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()

        response_data = response.json()
        content = extract_content(response_data)
        usage = extract_usage(response_data)
        if usage is None:
            usage = estimate_usage(messages, content)
        tracker.record(usage, self.provider)

        return LLMResponse(
            content=content,
            usage=usage,
            model=str(response_data.get("model") or self.model),
            provider=self.provider,
            cost_usd=calculate_cost_usd(self.model, usage),
        )


def validate_messages(messages: list[dict[str, str]]) -> None:
    """Validate OpenAI-compatible chat messages.

    Args:
        messages: Chat messages to validate.

    Raises:
        ValueError: If messages are empty or malformed.
    """
    if not messages:
        raise ValueError("messages must not be empty")

    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role not in {"system", "user", "assistant"}:
            raise ValueError(f"unsupported message role: {role}")
        if not isinstance(content, str):
            raise ValueError("message content must be a string")


def get_provider(provider_name: str | None = None) -> LLMProvider:
    """Create an LLM provider from environment variables.

    Args:
        provider_name: Optional provider override. Defaults to ``LLM_PROVIDER``.

    Returns:
        Configured LLM provider instance.

    Raises:
        ValueError: If the provider is unknown.
        RuntimeError: If the required API key is missing.
    """
    load_env_file()
    provider = (provider_name or os.getenv("LLM_PROVIDER") or DEFAULT_PROVIDER).lower()
    config = PROVIDER_CONFIGS.get(provider)
    if config is None:
        supported = ", ".join(sorted(PROVIDER_CONFIGS))
        raise ValueError(f"unsupported LLM provider: {provider}; supported: {supported}")

    api_key = os.getenv(config["api_key_env"])
    fallback_api_key_env = config.get("fallback_api_key_env")
    if not api_key and fallback_api_key_env:
        api_key = os.getenv(fallback_api_key_env)
    if not api_key:
        raise RuntimeError(f"missing API key environment variable: {config['api_key_env']}")

    model = os.getenv("LLM_MODEL") or config["model"]
    return OpenAICompatibleProvider(
        provider=provider,
        base_url=os.getenv("LLM_BASE_URL") or config["base_url"],
        api_key=api_key,
        model=model,
    )


def load_env_file(env_path: Path | None = None) -> None:
    """Load simple ``KEY=VALUE`` pairs from a local environment file.

    Existing process environment variables take precedence and are not
    overwritten.

    Args:
        env_path: Path to the environment file.
    """
    resolved_path = env_path or ENV_FILE_PATH
    if not resolved_path.exists():
        return

    try:
        lines = resolved_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        LOGGER.exception("Failed to read env file %s", resolved_path)
        return

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        key, value = stripped.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def create_provider(provider_name: str | None = None) -> LLMProvider:
    """Create an LLM provider from environment variables.

    Args:
        provider_name: Optional provider override.

    Returns:
        Configured LLM provider instance.
    """
    return get_provider(provider_name)


def chat_with_retry(
    messages: list[dict[str, str]],
    *,
    provider: LLMProvider | None = None,
    retries: int = DEFAULT_MAX_RETRIES,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> LLMResponse:
    """Call an LLM with exponential-backoff retry.

    Args:
        messages: OpenAI-compatible chat messages.
        provider: Optional provider instance. Defaults to environment config.
        retries: Maximum number of attempts.
        temperature: Sampling temperature.
        max_tokens: Maximum number of completion tokens.

    Returns:
        Normalized LLM response.

    Raises:
        Exception: The final provider exception after all attempts fail.
    """
    llm_provider = provider or get_provider()
    attempts = max(1, retries)
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            return llm_provider.chat(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as exc:
            last_error = exc
            if not should_retry(exc) or attempt >= attempts:
                LOGGER.exception("LLM chat failed on attempt %s/%s", attempt, attempts)
                raise

            delay_seconds = 2 ** (attempt - 1)
            LOGGER.warning(
                "LLM chat failed on attempt %s/%s, retrying in %s seconds: %s",
                attempt,
                attempts,
                delay_seconds,
                exc,
            )
            time.sleep(delay_seconds)

    raise RuntimeError("LLM chat failed without an exception") from last_error


def should_retry(exc: Exception) -> bool:
    """Return whether an exception should trigger a retry.

    Args:
        exc: Exception raised by the provider.

    Returns:
        ``True`` when retrying may succeed.
    """
    if httpx is None:
        return False

    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return status_code == 429 or 500 <= status_code < 600
    return isinstance(exc, (httpx.RequestError, httpx.TimeoutException))


def quick_chat(
    prompt: str,
    *,
    system_prompt: str | None = None,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> str:
    """Send one user prompt and return only the assistant content.

    Args:
        prompt: User prompt text.
        system_prompt: Optional system instruction.
        temperature: Sampling temperature.
        max_tokens: Maximum number of completion tokens.

    Returns:
        Assistant response content.
    """
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    response = chat_with_retry(
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return response.content


def chat(
    prompt: str,
    system: str | None = None,
    *,
    temperature: float = DEFAULT_TEMPERATURE,
    node_name: str = "unknown",
) -> tuple[str, Usage]:
    """Send one prompt and return assistant text plus usage.

    Args:
        prompt: User prompt text.
        system: Optional system instruction.
        temperature: Sampling temperature.
        node_name: Agent or workflow node name for cost audit records.

    Returns:
        Tuple of assistant text and token usage.
    """
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response = chat_with_retry(messages, temperature=temperature)
    cost_guard = get_cost_guard()
    cost_guard.record(
        node_name,
        _usage_to_cost_guard_dict(response.usage),
        response.model,
    )
    cost_guard.check()
    return response.content, response.usage


def chat_json(
    prompt: str,
    system: str | None = None,
    *,
    temperature: float = DEFAULT_TEMPERATURE,
    node_name: str = "unknown",
) -> tuple[dict[str, Any], Usage]:
    """Send one prompt and parse the assistant response as a JSON object.

    Args:
        prompt: User prompt text.
        system: Optional system instruction.
        temperature: Sampling temperature.
        node_name: Agent or workflow node name for cost audit records.

    Returns:
        Tuple of parsed JSON object and token usage.

    Raises:
        ValueError: If the assistant response is not a JSON object.
    """
    text, usage = chat(
        prompt,
        system=system,
        temperature=temperature,
        node_name=node_name,
    )
    cleaned_text = text.strip()
    if cleaned_text.startswith("```"):
        cleaned_text = cleaned_text.strip("`")
        cleaned_text = cleaned_text.removeprefix("json").strip()

    data = json.loads(cleaned_text)
    if not isinstance(data, dict):
        raise ValueError("chat_json response must be a JSON object")
    return data, usage


def accumulate_usage(
    cost_tracker: dict[str, Any] | None,
    usage: Any,
) -> dict[str, Any]:
    """Accumulate token usage into a mutable-friendly summary dictionary.

    Args:
        cost_tracker: Existing token usage summary.
        usage: Usage dataclass or dict containing token counts.

    Returns:
        New token usage summary dictionary.
    """
    updated = dict(cost_tracker or {})

    prompt_tokens = _usage_value(usage, "prompt_tokens")
    completion_tokens = _usage_value(usage, "completion_tokens")
    total_tokens = _usage_value(usage, "total_tokens")
    if total_tokens == 0:
        total_tokens = prompt_tokens + completion_tokens

    updated["prompt_tokens"] = int(updated.get("prompt_tokens") or 0) + prompt_tokens
    updated["completion_tokens"] = (
        int(updated.get("completion_tokens") or 0) + completion_tokens
    )
    updated["total_tokens"] = int(updated.get("total_tokens") or 0) + total_tokens
    updated["calls"] = int(updated.get("calls") or 0) + 1
    return updated


def _usage_to_cost_guard_dict(usage: Usage) -> dict[str, int]:
    """Convert normalized usage to the dictionary shape expected by CostGuard."""
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
    }


def _usage_value(usage: Any, field_name: str) -> int:
    """Read one usage field from a dataclass-like object or dictionary.

    Args:
        usage: Usage dataclass, dictionary, or other object.
        field_name: Token usage field name.

    Returns:
        Integer token count, defaulting to ``0``.
    """
    if isinstance(usage, dict):
        return int(usage.get(field_name) or 0)
    return int(getattr(usage, field_name, 0) or 0)


def extract_content(response_data: dict[str, Any]) -> str:
    """Extract assistant content from an OpenAI-compatible response.

    Args:
        response_data: Parsed response JSON.

    Returns:
        Assistant message content.

    Raises:
        ValueError: If no content is present.
    """
    choices = response_data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("LLM response missing choices")

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise ValueError("LLM response choice must be an object")

    message = first_choice.get("message")
    if not isinstance(message, dict):
        raise ValueError("LLM response choice missing message")

    content = message.get("content")
    if not isinstance(content, str):
        raise ValueError("LLM response message content must be a string")

    return content


def extract_usage(response_data: dict[str, Any]) -> Usage | None:
    """Extract token usage from an OpenAI-compatible response.

    Args:
        response_data: Parsed response JSON.

    Returns:
        Usage object, or ``None`` when usage is absent.
    """
    usage_data = response_data.get("usage")
    if not isinstance(usage_data, dict):
        return None

    prompt_tokens = int(usage_data.get("prompt_tokens") or 0)
    completion_tokens = int(usage_data.get("completion_tokens") or 0)
    total_tokens = int(
        usage_data.get("total_tokens") or prompt_tokens + completion_tokens
    )
    return Usage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        estimated=False,
    )


def estimate_token_count(text: str) -> int:
    """Estimate token count for mixed Chinese and English text.

    Args:
        text: Text to estimate.

    Returns:
        Approximate token count.
    """
    if not text:
        return 0

    cjk_chars = 0
    non_cjk_chars = 0
    for char in text:
        if "\u4e00" <= char <= "\u9fff":
            cjk_chars += 1
        elif not char.isspace():
            non_cjk_chars += 1

    return max(1, cjk_chars + (non_cjk_chars + 3) // 4)


def estimate_usage(messages: list[dict[str, str]], completion: str) -> Usage:
    """Estimate token usage for a chat request and completion.

    Args:
        messages: Chat messages sent to the model.
        completion: Assistant completion content.

    Returns:
        Estimated token usage.
    """
    prompt_tokens = 0
    for message in messages:
        prompt_tokens += 4
        prompt_tokens += estimate_token_count(message.get("role", ""))
        prompt_tokens += estimate_token_count(message.get("content", ""))

    completion_tokens = estimate_token_count(completion)
    return Usage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        estimated=True,
    )


def calculate_cost_usd(model: str, usage: Usage) -> float:
    """Calculate approximate LLM cost in USD.

    Args:
        model: Model name.
        usage: Token usage statistics.

    Returns:
        Estimated cost in USD. Unknown models return ``0.0``.
    """
    price = MODEL_COSTS_USD_PER_MILLION.get(model)
    if price is None:
        LOGGER.warning("No pricing configured for model %s; cost set to 0.0", model)
        return 0.0

    input_cost = usage.prompt_tokens * price["input"] / 1_000_000
    output_cost = usage.completion_tokens * price["output"] / 1_000_000
    return round(input_cost + output_cost, 8)


def main() -> None:
    """Run a minimal manual smoke test from environment configuration."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    prompt = os.getenv("LLM_TEST_PROMPT") or "用一句话说明什么是 RAG。"
    response = chat_with_retry([{"role": "user", "content": prompt}])
    LOGGER.info("Provider: %s", response.provider)
    LOGGER.info("Model: %s", response.model)
    LOGGER.info("Usage: %s", response.usage)
    LOGGER.info("Estimated cost USD: %.8f", response.cost_usd)
    LOGGER.info("Content: %s", response.content)


if __name__ == "__main__":
    main()
