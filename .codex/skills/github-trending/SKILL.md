---
name: github-trending
description: 当需要采集 GitHub 热门开源项目时使用此技能
allowed-tools: Read, Grep, Glob, WebFetch
---

# GitHub Trending

## 使用场景

当需要从 GitHub 热门仓库中采集 AI、LLM、Agent 相关开源项目，并沉淀为 `knowledge/raw/` 下的原始 JSON 快照时使用此技能。适用于每日、每周或指定时间窗口的热门项目采集任务。

## 执行步骤

1. 搜索热门仓库：优先使用 GitHub API 查询热门或近期高增长仓库，按 stars、created、pushed、topics、关键词等条件组合筛选。
2. 提取信息：为每个候选仓库提取 `name`、`url`、`description`、`stars`、`language`、`topics` 等公开可见信息。
3. 过滤候选：只纳入 AI、LLM、Agent 相关项目；排除 Awesome 列表、纯资源导航、无明确项目实现的集合类仓库。
4. 去重：按仓库 URL、owner/name、项目名和主题相似度去重，保留来源最清晰、热度最高的一条。
5. 撰写中文摘要：按“项目名 + 做什么 + 为什么值得关注”的公式生成摘要，避免编造未验证能力。
6. 排序取 Top15：按热度、增长趋势、AI/LLM/Agent 相关性和工程价值综合排序，保留 Top15。
7. 输出 JSON：将结果输出到 `knowledge/raw/github-trending-YYYY-MM-DD.json`，日期使用采集当天日期。

## 注意事项

- 只访问公开页面或公开 API，不登录、不使用个人 token、不绕过访问控制。
- 网络请求必须低频，设置合理超时和重试上限。
- 不采集、输出或保存任何 API Key、Token、Cookie、Webhook URL、私钥或个人凭证。
- 摘要必须基于仓库公开信息，不能把推测写成事实。
- `topics` 缺失时使用空数组，不要编造标签。
- 输出前确认 JSON 可解析，并确保 `items` 是数组。
- 写入 `knowledge/raw/` 前不得覆盖同名文件，除非用户明确要求更新当天快照。

## 输出格式

输出文件路径：

```text
knowledge/raw/github-trending-YYYY-MM-DD.json
```

JSON 结构：

```json
{
  "source": "github_trending",
  "skill": "github-trending",
  "collected_at": "2026-05-07T23:30:00+08:00",
  "items": [
    {
      "name": "owner/repo",
      "url": "https://github.com/owner/repo",
      "summary": "项目名用于构建某类 AI 工具或框架，值得关注是因为它在近期获得较高热度并解决了明确工程问题。",
      "stars": 12345,
      "language": "Python",
      "topics": [
        "ai",
        "llm",
        "agent"
      ]
    }
  ]
}
```
