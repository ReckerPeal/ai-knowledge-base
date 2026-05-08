"""Supervisor pattern for iterative LLM worker quality review."""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from pipeline import model_client


LOGGER = logging.getLogger(__name__)

PASSING_SCORE = 7
DEFAULT_MAX_RETRIES = 3
WORKER_ROLE = (
    "你是 Worker Agent，一名严谨的技术分析师。你的职责是基于任务要求"
    "产出结构化分析报告，优先保证事实准确、推理清晰、结论可追溯；"
    "不负责评价自己的输出质量。"
)
SUPERVISOR_ROLE = (
    "你是 Supervisor Agent，一名资深质量审核负责人。你的职责是独立审核"
    "Worker 的分析报告，按照准确性、深度和格式三个维度严格评分，"
    "发现问题时给出可执行的改进反馈。"
)


def supervisor(task: str, max_retries: int = DEFAULT_MAX_RETRIES) -> dict[str, Any]:
    """Run a worker task through a supervisor quality review loop.

    Args:
        task: Task description for the Worker Agent.
        max_retries: Maximum worker attempts before forcing a return.

    Returns:
        A dictionary containing ``output``, ``attempts``, ``final_score``, and
        optionally ``warning`` when the review never passes.

    Raises:
        ValueError: If ``task`` is empty or ``max_retries`` is less than 1.
    """
    normalized_task = task.strip()
    if not normalized_task:
        raise ValueError("task must not be empty")
    if max_retries < 1:
        raise ValueError("max_retries must be at least 1")

    feedback: str | None = None
    last_output: dict[str, Any] = {}
    last_review: dict[str, Any] = {
        "passed": False,
        "score": 0,
        "feedback": "No review completed.",
    }

    for attempt in range(1, max_retries + 1):
        worker_text = _run_worker(normalized_task, feedback)
        last_output = _parse_json_object(worker_text, context="Worker Agent")
        last_review = _run_supervisor_review(normalized_task, last_output)

        final_score = int(last_review["score"])
        if bool(last_review["passed"]) and final_score >= PASSING_SCORE:
            return {
                "output": last_output,
                "attempts": attempt,
                "final_score": final_score,
            }

        feedback = str(last_review.get("feedback") or "请提升分析质量后重做。")
        LOGGER.info(
            "Supervisor review failed attempt=%s score=%s feedback=%s",
            attempt,
            final_score,
            feedback,
        )

    final_score = int(last_review["score"])
    return {
        "output": last_output,
        "attempts": max_retries,
        "final_score": final_score,
        "warning": (
            f"超过最大重试轮次 {max_retries}，强制返回最后一次 Worker 输出。"
        ),
    }


def _run_worker(task: str, feedback: str | None) -> str:
    """Ask the Worker Agent to produce a JSON analysis report.

    Args:
        task: Original task description.
        feedback: Optional Supervisor feedback from the previous attempt.

    Returns:
        Raw Worker Agent response text.
    """
    prompt = (
        f"{WORKER_ROLE}\n"
        "请完成用户任务，并只输出 JSON 格式的分析报告。\n"
        "JSON 可包含 summary、findings、risks、recommendations 等字段；"
        "不要输出 Markdown 代码块或额外解释。\n"
        f"任务：{task}"
    )
    if feedback:
        prompt += f"\n上一轮 Supervisor 反馈：{feedback}\n请根据反馈重做。"

    return _call_chat(prompt)


def _run_supervisor_review(task: str, worker_output: dict[str, Any]) -> dict[str, Any]:
    """Review Worker output for accuracy, depth, and format.

    Args:
        task: Original task description.
        worker_output: Parsed Worker Agent JSON output.

    Returns:
        Validated review JSON with ``passed``, ``score``, and ``feedback``.
    """
    prompt = (
        f"{SUPERVISOR_ROLE}\n"
        "请审核 Worker Agent 的 JSON 分析报告质量。\n"
        "评分维度：准确性(1-10)、深度(1-10)、格式(1-10)。\n"
        "请综合三个维度给出整数总分 score，score >= 7 才能 passed=true。\n"
        "只输出 JSON，格式必须为："
        '{"passed": bool, "score": int, "feedback": str}。\n'
        f"原始任务：{task}\n"
        f"Worker 输出：{json.dumps(worker_output, ensure_ascii=False)}"
    )

    review_text = _call_chat(prompt)
    review = _parse_json_object(review_text, context="Supervisor Agent")
    return _validate_review(review)


def _validate_review(review: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize the Supervisor Agent review JSON.

    Args:
        review: Parsed review object.

    Returns:
        Normalized review containing ``passed``, ``score``, and ``feedback``.

    Raises:
        ValueError: If required fields are missing or invalid.
    """
    passed = review.get("passed")
    score = review.get("score")
    feedback = review.get("feedback")

    if not isinstance(passed, bool):
        raise ValueError("Supervisor review field passed must be bool")
    if not isinstance(score, int) or not 1 <= score <= 10:
        raise ValueError("Supervisor review field score must be int from 1 to 10")
    if not isinstance(feedback, str) or not feedback.strip():
        raise ValueError("Supervisor review field feedback must be a non-empty string")

    normalized_passed = passed and score >= PASSING_SCORE
    return {
        "passed": normalized_passed,
        "score": score,
        "feedback": feedback.strip(),
    }


def _call_chat(prompt: str) -> str:
    """Call ``pipeline.model_client.chat`` and return response text.

    Args:
        prompt: Prompt text.

    Returns:
        Assistant response text.

    Raises:
        RuntimeError: If no compatible chat function is available.
    """
    chat_func = getattr(model_client, "chat", None)
    if callable(chat_func):
        return _extract_text(chat_func(prompt))

    quick_chat = getattr(model_client, "quick_chat", None)
    if callable(quick_chat):
        return _extract_text(quick_chat(prompt))

    raise RuntimeError("pipeline.model_client 缺少 chat() 或 quick_chat()")


def _extract_text(result: Any) -> str:
    """Extract text from supported model client return values.

    Args:
        result: Model client return value.

    Returns:
        Response text.

    Raises:
        RuntimeError: If the return shape is unsupported.
    """
    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, str):
        return result
    content = getattr(result, "content", None)
    if isinstance(content, str):
        return content
    raise RuntimeError(f"chat returned unsupported type: {type(result)}")


def _parse_json_object(text: str, *, context: str) -> dict[str, Any]:
    """Parse a JSON object from LLM output.

    Args:
        text: Raw LLM output.
        context: Agent name used in error messages.

    Returns:
        Parsed JSON object.

    Raises:
        ValueError: If the response is not a JSON object.
    """
    cleaned_text = text.strip()
    if cleaned_text.startswith("```"):
        cleaned_text = cleaned_text.strip("`")
        cleaned_text = cleaned_text.removeprefix("json").strip()

    try:
        data = json.loads(cleaned_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{context} output must be valid JSON") from exc

    if not isinstance(data, dict):
        raise ValueError(f"{context} output must be a JSON object")
    return data


def main() -> None:
    """Run a minimal command-line smoke test for the supervisor pattern."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    task = " ".join(sys.argv[1:]).strip()
    if not task:
        task = input("请输入任务: ").strip()
    result = supervisor(task)
    LOGGER.info("%s", json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
