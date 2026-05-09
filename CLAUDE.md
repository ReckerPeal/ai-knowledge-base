# CLAUDE.md

> 本文件是 Claude Code 在本仓库工作的最高优先级指令。完整协作规范、Agent 角色、JSON Schema、红线请以 [AGENTS.md](AGENTS.md) 为准；本文件只补充 Claude Code 专属信息并提取关键约束。

## 项目概述

本项目是一个 **AI 知识库助手**：自动从 GitHub Trending、Hacker News（及 RSS 源）采集 AI / LLM / Agent 领域的技术动态，经 AI 分析、去重、归类、摘要、价值评分后，以结构化 JSON 形式沉淀到本地知识库 `knowledge/articles/`，并可通过 Telegram、飞书等渠道分发高价值内容。

## 技术栈

- Python 3.12（虚拟环境 `.venv`）
- LangGraph / LangChain
- OpenCode + 国产大模型
- MCP Server（`mcp_knowledge_server.py`）
- pytest（测试）

## 仓库结构（实际）

```text
.
├── AGENTS.md                  # 协作规范、Agent 角色、JSON Schema、红线（权威来源）
├── CLAUDE.md                  # 本文件
├── mcp_knowledge_server.py    # 对外暴露知识库的 MCP Server
├── requirements.txt
├── workflows/                 # LangGraph 工作流：collector / analyzer / reviewer / reviser / organizer / saver / planner / pipeline / graph / star_history
├── scripts/                   # 一次性 / 周期性脚本：backfill_daily_stars、build_site_data
├── docs/                      # 静态站点（GitHub Pages）：列表 + 详情 + 全部搜索
├── patterns/                  # router、supervisor 等编排模式
├── hooks/                     # 写盘/质量校验钩子（validate_article_hook、validate_json、check_quality）
├── knowledge/
│   ├── raw/                   # 原始采集快照（禁止覆盖/删除）
│   └── articles/YYYY-MM-DD/   # 结构化知识条目 JSON
├── pending_review/            # 待人工核验内容
├── tests/                     # pytest 测试
└── .github/                   # CI 工作流
```

## 常用命令

```bash
# 激活虚拟环境
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 运行测试
pytest                         # 全量
pytest tests/test_xxx.py -q    # 单文件

# 运行 MCP Server
python mcp_knowledge_server.py

# 触发某个工作流（示例，按实际入口调整）
python -m workflows.pipeline
```

环境变量见 `.env.example`，本地放在 `.env`，**禁止提交**。

## 编码规范（与 AGENTS.md 对齐）

- 遵循 PEP 8；变量/函数/模块用 `snake_case`，类用 `PascalCase`，常量用 `UPPER_SNAKE_CASE`。
- 公共函数 / 类 / 复杂逻辑必须写 Google 风格 docstring。
- 禁止裸 `print()`，统一用 `logging`。
- 网络请求必须设置 **timeout、retry、明确异常处理**。
- 数据落盘前必须校验 JSON schema（参考 `hooks/validate_json.py`、`hooks/validate_article_hook.py`）。
- 异常不得静默吞掉，必须记录上下文。
- 不引入与项目技术栈无关的大型依赖。

## 知识条目写入要求

- 路径：`knowledge/articles/YYYY-MM-DD/<id>.json`，每文件一个 JSON 对象。
- 必备字段、枚举、时间格式等以 [AGENTS.md](AGENTS.md#知识条目-json-格式) 为准。
- `id` 全局唯一、可复现；`source_url` 必须可追溯；`collected_at` 用 ISO 8601。
- 写入前必须经过 `hooks/validate_article_hook.py` 等价校验。

## Claude Code 专属规则

- **始终优先 Edit/Read 等专用工具**，仅在 shell 操作时用 Bash。
- 修改 `workflows/`、`patterns/`、`hooks/` 后，相关 `tests/` 必须通过；新增功能配套写测试。
- 涉及 LLM 调用、抓取频率、外部 webhook 的改动必须显式说明对**红线**的影响。
- 不主动创建文档 / README / 总结文件，除非用户明确要求。
- 遇到 `knowledge/raw/` 下的文件：**只读不删不改**。

## 红线（绝对禁止，摘自 AGENTS.md）

- ❌ 提交或输出任何 API Key、Token、Cookie、Webhook URL、私钥、个人凭证。
- ❌ 绕过 robots.txt、登录限制、速率限制；高频抓取 GitHub / HN / 第三方站点。
- ❌ 伪造来源、篡改 `source_url`、删除可追溯信息。
- ❌ 把未确认的 AI 推断写成事实（不确定内容标记为待核实）。
- ❌ 直接发布 `status=draft` 的内容到 Telegram / 飞书 / 其他渠道。
- ❌ 未通过 JSON 校验就写入 `knowledge/articles/`。
- ❌ 覆盖或删除 `knowledge/raw/` 原始数据。
- ❌ 异常处理中静默吞错。
- ❌ 引入与技术栈无关的大型依赖。

## 参考

- 规范权威来源：[AGENTS.md](AGENTS.md)
- 子 Agent 角色定义：见 AGENTS.md「Agent 角色概览」章节
- CI：`.github/workflows/`
