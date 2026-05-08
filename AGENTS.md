# AGENTS.md

## 项目概述

本项目是一个 AI 知识库助手，自动从 GitHub Trending 和 Hacker News 采集 AI、LLM、Agent 领域的技术动态，经 AI 分析、去重、归类和摘要后，以结构化 JSON 形式沉淀到本地知识库，并支持通过 Telegram、飞书等渠道分发高价值内容。

## 技术栈

- Python 3.12
- OpenCode + 国产大模型
- LangGraph
- OpenClaw

## 编码规范

- 遵循 PEP 8。
- 变量、函数、模块名使用 `snake_case`。
- 类名使用 `PascalCase`。
- 常量使用 `UPPER_SNAKE_CASE`。
- 所有公共函数、类和复杂逻辑必须编写 Google 风格 docstring。
- 禁止裸 `print()`，日志输出统一使用 `logging`。
- 网络请求必须设置超时、重试和明确的异常处理。
- 数据落盘前必须校验 JSON schema 或等价的数据结构约束。
- 不得在代码中硬编码 token、密钥、Webhook URL 或个人凭证。

## 项目结构

```text
.
├── .opencode/
│   ├── agents/          # OpenCode Agent 定义与角色提示词
│   └── skills/          # 可复用技能、工作流和工具封装
├── knowledge/
│   ├── raw/             # 原始采集数据，保留来源快照
│   └── articles/        # AI 分析后的结构化知识条目
└── AGENTS.md            # 项目协作、Agent 和编码规范
```

## 知识条目 JSON 格式

每条知识条目必须是一个独立 JSON 对象，建议按 `knowledge/articles/YYYY-MM-DD/<id>.json` 存储。

```json
{
  "id": "20260507-github-owner-repo",
  "title": "Example AI Agent Framework",
  "source": "github_trending",
  "source_url": "https://github.com/owner/repo",
  "summary": "一句话说明该项目或文章的核心价值。",
  "content": "面向知识库的结构化长摘要，可包含背景、亮点、限制和适用场景。",
  "tags": ["AI", "LLM", "Agent", "Framework"],
  "status": "draft",
  "published_at": "2026-05-07T10:30:00+08:00",
  "collected_at": "2026-05-07T11:00:00+08:00",
  "language": "en",
  "score": 8.6,
  "metadata": {
    "author": "owner",
    "stars": 12345,
    "comments": 42,
    "distribution_channels": ["telegram", "lark"]
  }
}
```

字段约束：

- `id`：全局唯一，稳定可复现，建议由日期、来源和资源标识组合生成。
- `title`：原始标题或经轻度清洗后的标题。
- `source`：来源枚举，例如 `github_trending`、`hacker_news`。
- `source_url`：原始链接，必须可追溯。
- `summary`：短摘要，适合用于消息分发。
- `content`：知识库正文，包含更完整的分析。
- `tags`：主题标签，必须是字符串数组。
- `status`：处理状态，建议取值为 `draft`、`reviewed`、`published`、`archived`。
- `published_at`：原内容发布时间；无法获取时为 `null`。
- `collected_at`：采集时间，必须使用 ISO 8601。
- `score`：AI 评估价值分，范围 `1` 到 `10`。
- `metadata`：来源相关扩展信息。

## Agent 角色概览

| 角色 | 目录建议 | 主要职责 | 输入 | 输出 |
| --- | --- | --- | --- | --- |
| 采集 Agent | `.opencode/agents/collector.md` | 从 GitHub Trending、Hacker News 抓取 AI/LLM/Agent 技术动态，保留原始数据和来源信息 | 来源配置、关键词、时间窗口 | `knowledge/raw/` 下的原始 JSON |
| 分析 Agent | `.opencode/agents/analyzer.md` | 对原始内容进行去重、摘要、打标签、价值评分和结构化转换 | `knowledge/raw/` 原始数据 | 符合规范的知识条目 JSON |
| 整理 Agent | `.opencode/agents/curator.md` | 审核、排序、归档和分发内容，选择适合 Telegram/飞书发布的条目 | 分析后的知识条目 | `knowledge/articles/` 条目与分发任务 |

## 红线

以下操作绝对禁止：

- 禁止提交、记录或输出任何 API Key、Token、Cookie、Webhook URL、私钥或个人凭证。
- 禁止绕过目标站点的访问控制、登录限制、robots.txt 或速率限制。
- 禁止高频抓取 GitHub、Hacker News 或任何第三方站点，必须设置合理间隔、超时和重试上限。
- 禁止伪造来源、篡改 `source_url` 或删除可追溯信息。
- 禁止将未经确认的 AI 推断写成事实；不确定内容必须标记为待核实。
- 禁止直接发布 `draft` 状态的内容到 Telegram、飞书或其他外部渠道。
- 禁止在未校验 JSON 结构的情况下写入 `knowledge/articles/`。
- 禁止覆盖或删除 `knowledge/raw/` 中的原始采集数据，除非有明确的数据保留策略。
- 禁止在异常处理中静默吞错；必须记录上下文并保留可排查信息。
- 禁止引入与项目技术栈无关的大型依赖，除非有清晰收益和维护理由。
