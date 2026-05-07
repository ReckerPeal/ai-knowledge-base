---
name: tech-summary
description: 当需要对采集的技术内容进行深度分析总结时使用此技能
allowed-tools: Read, Grep, Glob, WebFetch
---

# Tech Summary

## 使用场景

当需要对 `knowledge/raw/` 中已采集的技术内容进行深度分析、摘要提炼、价值评分、标签建议和趋势发现时使用此技能。适用于 GitHub Trending、Hacker News 或其他技术来源的原始采集结果分析。

## 执行步骤

1. 读取 `knowledge/raw/` 最新采集文件：优先选择最近日期的 JSON 文件，确认 `source`、`collected_at` 和 `items` 可解析。
2. 逐条深度分析：为每条内容写不超过 50 字的中文摘要，提炼 2-3 个基于事实的技术亮点，给出 1-10 分评分和中文理由，并建议主题标签。
3. 趋势发现：归纳本批内容中的共同主题、新概念、新工具形态或值得持续跟踪的技术方向。
4. 输出分析结果 JSON：返回结构化 JSON，不输出额外解释性文本。

## 评分标准

- 9-10：改变格局，可能显著影响 AI/LLM/Agent 工程实践、开源生态、基础设施范式或产品方向。
- 7-8：直接有帮助，对开发者、研究者或产品团队有明确实用价值。
- 5-6：值得了解，有一定技术信息量或趋势参考价值，但短期行动价值有限。
- 1-4：可略过，相关性弱、重复度高、信息不足、营销色彩强或缺少可验证技术价值。

## 注意事项

- 15 个项目中 9-10 分不超过 2 个；不足 15 个项目时仍应保持高分克制。
- 摘要必须不超过 50 字，避免营销化表达。
- 技术亮点必须基于原始采集内容或公开页面事实，不写未经验证的推断。
- 评分理由必须能支撑分数，遇到信息不足、重复或来源不清时应降分。
- 标签建议应简短、可归类，避免过度细分。
- 不修改 `knowledge/raw/` 原始采集文件。
- 不输出或保存任何 API Key、Token、Cookie、Webhook URL、私钥或个人凭证。

## 输出格式

JSON 结构：

```json
{
  "source_raw_file": "knowledge/raw/github-trending-YYYY-MM-DD.json",
  "skill": "tech-summary",
  "analyzed_at": "2026-05-07T23:45:00+08:00",
  "items": [
    {
      "name": "owner/repo",
      "url": "https://github.com/owner/repo",
      "summary": "项目名做什么，以及为什么值得关注。",
      "highlights": [
        "基于事实的技术亮点一。",
        "基于事实的技术亮点二。"
      ],
      "score": 8,
      "score_reason": "该项目能直接帮助开发者构建 Agent 工作流，且热度和场景明确。",
      "suggested_tags": [
        "AI",
        "LLM",
        "Agent"
      ]
    }
  ],
  "trends": {
    "common_themes": [
      "共同主题一",
      "共同主题二"
    ],
    "new_concepts": [
      "新概念一",
      "新概念二"
    ]
  }
}
```
