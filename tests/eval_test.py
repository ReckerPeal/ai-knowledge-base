"""Evaluation tests for AI knowledge-base analysis behavior."""

from __future__ import annotations

import json
import os
import re
import sys
import warnings
from pathlib import Path
from typing import Any

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    from workflows.model_client import load_env_file

    def load_dotenv(dotenv_path: str | Path | None = None) -> bool:
        """Fallback dotenv loader using the project's built-in env parser."""
        load_env_file(Path(dotenv_path) if dotenv_path is not None else None)
        return True


load_dotenv(PROJECT_ROOT / ".env")
warnings.filterwarnings("ignore", category=pytest.PytestUnknownMarkWarning)


def has_summary(result: dict[str, Any]) -> bool:
    """Return whether a result contains a usable summary."""
    return len(str(result.get("summary") or "").strip()) >= 10


def has_keywords(result: dict[str, Any]) -> bool:
    """Return whether a result contains at least one normalized keyword."""
    return len(result.get("keywords") or []) >= 1


def is_low_relevance(result: dict[str, Any]) -> bool:
    """Return whether a result is filtered or scored as low relevance."""
    return bool(result.get("filtered")) or float(result.get("relevance_score") or 0) <= 0.3


def did_not_crash(result: dict[str, Any]) -> bool:
    """Return whether the evaluator produced a valid result object."""
    return isinstance(result, dict) and result.get("status") in {"ok", "filtered"}


EVAL_CASES: list[dict[str, Any]] = [
    {
        "name": "positive_technical_article",
        "input": (
            "LangGraph introduces a graph-based orchestration runtime for LLM agents. "
            "It supports stateful workflows, tool calling, human review, retries, and "
            "multi-agent coordination for production AI applications."
        ),
        "expected": {
            "checks": [has_summary, has_keywords],
            "min_relevance_score": 0.6,
            "keywords_any": {"AI", "LLM", "Agent", "Workflow"},
        },
    },
    {
        "name": "negative_unrelated_content",
        "input": (
            "This weekend recipe explains how to bake banana bread with cinnamon, "
            "walnuts, ripe bananas, and a simple cream cheese glaze."
        ),
        "expected": {
            "checks": [is_low_relevance],
            "max_relevance_score": 0.3,
        },
    },
    {
        "name": "boundary_tiny_input",
        "input": "AI",
        "expected": {
            "checks": [did_not_crash],
            "min_relevance_score": 0.0,
            "max_relevance_score": 1.0,
        },
    },
]


def local_analyze(text: str) -> dict[str, Any]:
    """Run deterministic local analysis for non-LLM evaluation tests.

    Args:
        text: Source text to evaluate.

    Returns:
        Analysis-like result containing summary, keywords, relevance, and status.
    """
    normalized_text = text.strip()
    lowered = normalized_text.lower()
    keyword_map = {
        "AI": ["ai", "artificial intelligence"],
        "LLM": ["llm", "language model", "gpt"],
        "Agent": ["agent", "multi-agent", "tool calling"],
        "Workflow": ["workflow", "orchestration", "langgraph"],
        "Knowledge Base": ["knowledge base", "retrieval", "rag"],
    }

    keywords = [
        label
        for label, needles in keyword_map.items()
        if any(needle in lowered for needle in needles)
    ]
    relevance_score = min(1.0, len(keywords) / 4)
    filtered = relevance_score < 0.35

    return {
        "status": "filtered" if filtered else "ok",
        "summary": normalized_text[:120] if normalized_text else "",
        "keywords": keywords,
        "relevance_score": relevance_score,
        "filtered": filtered,
    }


def test_eval_cases_structure_is_valid() -> None:
    """Validate EVAL_CASES shape without calling an LLM."""
    assert len(EVAL_CASES) >= 3

    names = {case["name"] for case in EVAL_CASES}
    assert len(names) == len(EVAL_CASES)

    for case in EVAL_CASES:
        assert isinstance(case["name"], str)
        assert len(case["name"]) >= 3
        assert isinstance(case["input"], str)
        assert len(case["input"]) >= 1
        assert isinstance(case["expected"], dict)
        assert "checks" in case["expected"]
        assert len(case["expected"]["checks"]) >= 1
        assert all(callable(check) for check in case["expected"]["checks"])


@pytest.mark.parametrize("case", EVAL_CASES, ids=[case["name"] for case in EVAL_CASES])
def test_local_eval_cases_use_range_assertions(case: dict[str, Any]) -> None:
    """Evaluate local cases using range and membership assertions."""
    result = local_analyze(case["input"])
    expected = case["expected"]

    for check in expected["checks"]:
        assert check(result)

    if "min_relevance_score" in expected:
        assert result["relevance_score"] >= expected["min_relevance_score"]
    if "max_relevance_score" in expected:
        assert result["relevance_score"] <= expected["max_relevance_score"]
    if "keywords_any" in expected:
        assert set(result["keywords"]) & expected["keywords_any"]


@pytest.mark.slow
def test_llm_as_judge_scores_analysis_at_least_five() -> None:
    """Use an LLM judge to score one analysis result from 1 to 10."""
    if os.getenv("RUN_LLM_EVALS") != "1":
        pytest.skip("Set RUN_LLM_EVALS=1 to run slow LLM evaluation tests.")

    api_key = os.getenv("LLM_API_KEY")
    if api_key:
        os.environ.setdefault("DEEPSEEK_API_KEY", api_key)

    if not any(
        os.getenv(name)
        for name in ("DEEPSEEK_API_KEY", "QWEN_API_KEY", "DASHSCOPE_API_KEY", "OPENAI_API_KEY")
    ):
        pytest.skip("LLM_API_KEY or provider-specific API key is required.")

    from workflows.model_client import chat

    case = EVAL_CASES[0]
    analysis = local_analyze(case["input"])
    prompt = (
        "请作为 AI 知识库评测员，对下面的分析结果按 1-10 分打分。"
        "重点看摘要是否覆盖核心技术价值、关键词是否合理、是否适合入库。"
        "只输出 JSON 对象，例如 {\"score\": 7, \"reason\": \"...\"}。\n\n"
        f"原文：{case['input']}\n"
        f"分析结果：{json.dumps(analysis, ensure_ascii=False)}"
    )
    text, _usage = chat(prompt, system="你是严格但公平的 LLM-as-Judge。")
    score = extract_score(text)

    assert score >= 5
    assert score <= 10


def extract_score(text: str) -> float:
    """Extract a 1-10 score from an LLM judge response.

    Args:
        text: Raw LLM response text.

    Returns:
        Parsed score.

    Raises:
        AssertionError: If a valid score cannot be found.
    """
    cleaned_text = text.strip()
    if cleaned_text.startswith("```"):
        cleaned_text = cleaned_text.strip("`")
        cleaned_text = cleaned_text.removeprefix("json").strip()

    try:
        payload = json.loads(cleaned_text)
    except json.JSONDecodeError:
        match = re.search(r"\b(?:score|评分)\D*(10|[1-9](?:\.\d+)?)\b", cleaned_text, re.I)
        assert match is not None, f"Could not parse judge score from: {text}"
        return float(match.group(1))

    assert isinstance(payload, dict)
    score = float(payload.get("score") or payload.get("评分") or 0)
    assert 1 <= score <= 10
    return score
