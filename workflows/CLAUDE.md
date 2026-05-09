# workflows/CLAUDE.md

> 本目录是 LangGraph 工作流的核心代码区。本文件继承根目录 [../CLAUDE.md](../CLAUDE.md) 与 [../AGENTS.md](../AGENTS.md) 的全部规则；以下为 **本目录专属约束**。

## 模块职责

| 文件 | 角色 | 输入 | 输出（state 字段） |
| --- | --- | --- | --- |
| `state.py` | `KBState` TypedDict 定义 | — | 全部共享字段 |
| `planner.py` | 制定采集策略（lite/standard/deep） | `target_count`（环境变量 `PLANNER_TARGET_COUNT`） | `plan` |
| `collector.py` | 从 GitHub Search API 采集仓库摘要 | `plan` | `sources` |
| `analyzer.py` | LLM 把 sources → 结构化分析 | `sources` | `analyses`、`cost_tracker` |
| `reviewer.py` | 给前 5 条 analyses 打分（加权本地汇总） | `analyses`、`iteration` | `review_passed`、`review_feedback`、`iteration` |
| `reviser.py` | 按 `review_feedback` 改写 analyses | `analyses`、`review_feedback` | `analyses`、`cost_tracker` |
| `organizer.py` | 过滤 / 去重 / 格式化为 article schema | `analyses` | `articles` |
| `human_flag.py` | 审核循环耗尽时写入 `pending_review/` | `analyses`、`iteration` | `needs_human_review`、`pending_review_paths` |
| `saver.py` | 校验后写入 `knowledge/articles/YYYY-MM-DD/` | `articles` | `saved_paths` |
| `model_client.py` | OpenAI 兼容 LLM 统一客户端（DeepSeek/Qwen 等）+ 成本追踪 | — | — |
| `graph.py` | 组装并编译 LangGraph 图 | — | 编译后的 app |
| `pipeline.py` | 旧版 4 步流水线（采集/分析/整理/落盘） | — | — |
| `rss_sources.yaml` | RSS 来源清单（被 `pipeline.py` 使用） | — | — |

## 图拓扑（`graph.py`）

```
plan → collect → analyze → review ─┬─ pass ──→ organize → save → END
                                   ├─ retry ─→ revise ──→ review (循环)
                                   └─ over  ─→ human_flag ───────→ END
```

- 路由函数：`route_after_review`，重试预算来自 `state["plan"]["max_iterations"]`，默认 `3`。
- 入口节点：`plan`；终止节点：`save`、`human_flag`。

## 节点契约（写新/改老节点都必须遵守）

1. **签名固定**：`def xxx_node(state: KBState) -> dict[str, Any]`，**只返回需要更新的字段**，禁止返回完整 state。
2. **不修改 `state` 实参**：用 `dict(state.get(...) or {})` / `list(state.get(...) or [])` 浅拷贝再写。
3. **不返回未压缩的原始全文**：`KBState` 只承载结构化摘要、评分和计数（参见 `state.py` 注释）。
4. **`cost_tracker` 增量累加**：调用 LLM 后用 `model_client.accumulate_usage` 合并，不要直接覆盖。
5. **LLM 失败策略**：审核类节点（reviewer）失败应**自动放行**避免阻塞；分析/改写类节点失败应记录并跳过该条而非整轮中断。
6. **日志统一**：模块级 `LOGGER = logging.getLogger(__name__)`，节点首行 `LOGGER.info("[NodeName] ...")`。
7. **时间字段**：统一使用 `CHINA_TZ = timezone(timedelta(hours=8))`，输出 ISO 8601。

## LLM 调用规范（`model_client.py`）

- 统一入口：`chat_json(...)` / `chat_with_retry(...)`；**不要直接** `httpx.post`。
- 必须传 `system` 提示词；要求模型输出 JSON 时显式声明。
- 默认 `timeout=60s`、`max_retries=3`、`temperature=0.3`、`max_tokens=2048`，特殊节点可在调用处覆盖（如 reviewer 用 `0.1`、reviser 用 `0.4`）。
- API Key 从 `.env` 读取（`DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY` 等），**绝不硬编码**。
- 每次调用必须经过 `CostGuard`，调用结束后由 `graph.main()` 写出成本报告。

## 落盘规则（`saver.py` / `human_flag.py`）

- `saver.py` 只能写 `knowledge/articles/YYYY-MM-DD/<id>.json`，写入前必须 `_validate_article`。
- `human_flag.py` 只能写 `pending_review/`，文件名建议含 ISO 时间戳 + sha1 短哈希。
- **禁止**直接读写 `knowledge/raw/`（采集快照只读，由 `pipeline.py` 维护）。
- 写入路径必须经过 `Path(...).resolve()` 防御目录穿越。

## 安全与抓取

- `collector.py` 调 GitHub Search API：必须 `timeout=20s`、最多 `3` 次重试、UA 不伪装成浏览器。
- 来源文本字段（title / summary / description / language / metadata.author）必须经过 `tests.security.sanitize_input`。
- `organizer.py` 输出前必须经过 `tests.security.filter_output`。
- 抓取频率不得突破 AGENTS.md 红线；新增来源前评估 robots.txt 与速率限制。

## 修改 checklist

改这个目录下任何文件前，至少回答：

- [ ] 是否新增/修改 `KBState` 字段？是 → 同步更新 `state.py` 注释、`graph.py` 初始 state、相关节点和测试。
- [ ] 是否新增 LangGraph 节点？是 → 在 `graph.py` `add_node` + `add_edge` + 路由 + 初始 state，并加 `tests/`。
- [ ] 是否调用 LLM？是 → 走 `model_client`、写测试、确认 `cost_tracker` 累加。
- [ ] 是否落盘？是 → 走 `saver`/`human_flag`，不要在节点里裸写文件。
- [ ] 是否新增依赖？是 → 写入 `requirements.txt` 并说明必要性（AGENTS.md 红线）。
- [ ] `pytest tests/` 是否通过？

## 调试

```bash
# 直接跑整张图（开启 INFO 日志）
python -m workflows.graph

# 单跑老的 4 步流水线
python -m workflows.pipeline

# 限定采集量
PLANNER_TARGET_COUNT=5 python -m workflows.graph
```
