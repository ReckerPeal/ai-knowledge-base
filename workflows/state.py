"""Shared LangGraph workflow state for the knowledge base pipeline."""

from __future__ import annotations

from typing import TypedDict


class KBState(TypedDict):
    """LangGraph 工作流共享状态。

    字段遵循报告式通信原则：只保存节点间传递所需的结构化摘要、
    评估结果和计数信息，不保存无法追溯、未压缩的原始全文数据。
    """

    # 采集节点输出的来源摘要列表；每项为 dict，包含 source、source_url、
    # title、summary、published_at、metadata 等可追溯字段，不保存原始全文快照。
    sources: list[dict]

    # 分析节点输出的结构化摘要列表；每项为 dict，包含 summary、content、
    # tags、score、language、metadata 等 LLM 分析结果。
    analyses: list[dict]

    # 整理节点输出的知识条目列表；每项为 dict，符合 knowledge/articles
    # 条目 schema，并已经完成格式化、去重和状态标记。
    articles: list[dict]

    # 审核节点给出的结构化反馈文本；用于下一轮改写或人工复核，
    # 只记录问题摘要和改进建议，不记录完整对话。
    review_feedback: str

    # 审核节点的布尔结论；True 表示当前 articles 已达到发布或归档质量门槛。
    review_passed: bool

    # 当前审核循环次数；从 0 或 1 开始由工作流维护，最多执行 3 次。
    iteration: int

    # Token 与成本追踪摘要；dict 可包含 provider、calls、prompt_tokens、
    # completion_tokens、total_tokens、estimated_cost 等聚合字段。
    cost_tracker: dict
