# Multi-Agent Content Backend

后端基于 FastAPI + SSE，实现 `Planner -> Researcher -> Writer` 的多智能体流水线，前后端完全分离。

## 目录结构

```text
app/
  api/routes.py          # 路由层
  agents/                # 多智能体实现
  core/sse.py            # SSE 封装与序列化
  schemas.py             # Pydantic 数据模型
  server.py              # FastAPI app + CORS
main.py                  # 启动入口（仅导出 app）
```

## 运行

```bash
uv sync
uv run uvicorn main:app --reload --port 8000
```

可选环境变量：

- `KIMI_API_KEY` 或 `MOONSHOT_API_KEY`：配置后启用 Kimi 生成 Planner/Writer 高质量内容。
- `KIMI_BASE_URL`：默认 `https://api.moonshot.cn/v1`
- `KIMI_MODEL`：默认 `kimi-k2.5`
- `KIMI_TEMPERATURE`：默认 `0.7`（`kimi-k2.5` 会自动忽略该参数）
- `KIMI_CONNECT_TIMEOUT`：默认 `15`
- `KIMI_READ_TIMEOUT`：默认 `180`
- `KIMI_WRITE_TIMEOUT`：默认 `30`
- `KIMI_POOL_TIMEOUT`：默认 `30`
- `KIMI_MAX_RETRIES`：默认 `2`（超时自动重试）
- `RESEARCH_TIMEOUT`：默认 `60`（搜索 provider 请求超时秒数）
- `RESEARCH_MAX_KEYWORDS`：默认 `4`（每次研究最多处理关键词数）
- `RESEARCH_MAX_RESULTS_PER_KEYWORD`：默认 `3`（每个关键词最多保留素材数）
- `RESEARCH_KEYWORD_CONCURRENCY`：默认 `3`（关键词并发度）
- `SEARCH_PROVIDERS`：默认 `baidu_qianfan,bocha,tianapi,juhe`（大陆优先）
- `BAIDU_QIANFAN_API_KEY`：启用百度千帆智能搜索
- `BAIDU_QIANFAN_ENDPOINT`：默认 `https://qianfan.baidubce.com/v2/ai_search/chat/completions`
- `BAIDU_QIANFAN_MODEL`：默认 `ernie-4.5-turbo-32k`
- `BAIDU_QIANFAN_SEARCH_SOURCE`：默认 `baidu_search_v1`
- `BAIDU_QIANFAN_APP_ID`：可选（多数场景不需要）；仅当接口返回 `invalid_appId` 时再配置
- `BOCHA_API_KEY`：启用 Bocha Web Search
- `BOCHA_WEB_SEARCH_ENDPOINT`：默认 `https://api.bochaai.com/v1/web-search`
- `TIANAPI_KEY`：启用 TianAPI 新闻检索
- `TIANAPI_NEWS_ENDPOINT`：默认 `https://apis.tianapi.com/generalnews/index`
- `JUHE_API_KEY`：启用聚合数据新闻检索
- `JUHE_NEWS_ENDPOINT`：默认 `https://v.juhe.cn/toutiao/index`
- `JUHE_NEWS_TYPE`：默认 `top`
- `QUALITY_ZHIHU_MIN_WORDS/QUALITY_ZHIHU_MAX_WORDS`：知乎字数阈值（默认 `1800/2600`）
- `QUALITY_XHS_MIN_WORDS/QUALITY_XHS_MAX_WORDS`：小红书字数阈值（默认 `800/1400`）
- `QUALITY_BJH_MIN_WORDS/QUALITY_BJH_MAX_WORDS`：百家号字数阈值（默认 `1200/2000`）
- `QUALITY_ZHIHU_MIN_H2`：知乎最小二级标题数（默认 `4`）
- `QUALITY_BJH_MIN_H2`：百家号最小二级标题数（默认 `3`）
- `QUALITY_XHS_REQUIRE_LIST_OR_STEPS`：小红书是否强制清单/步骤（默认 `1`）

未配置 `KIMI_API_KEY` 时，系统自动使用内置降级策略，仍可完整演示流程。

## 接口

前端交接文档：`API_HANDOFF.md`

### 健康检查

`GET /health`

### 流式生成

`POST /api/generate/stream`

请求体：

```json
{
  "topic": "AIGC 对内容行业的影响",
  "platform": "zhihu",
  "enable_llm_stream": true
}
```

`platform` 说明：
- 当前版本每次请求仅允许 1 个平台（例如 `"zhihu"`）。
- 仅支持：`zhihu`、`xiaohongshu`、`baijiahao`（大小写不敏感，后端会规范化为小写）。
- 兼容旧字段 `platforms: ["zhihu"]`，但建议尽快迁移到 `platform`。
- Writer 会按平台模板自动约束标题风格、结构、段落节奏和结尾动作。

`enable_llm_stream` 说明：
- `false`（默认）：先拿到完整 LLM 文本，再由后端切片通过 SSE 返回。
- `true`：Writer 使用 Kimi `stream=true` 真实流式输出，边生成边推送。

返回 `text/event-stream`，`data` 字段统一为：

```json
{
  "type": "status | plan | content | done | error",
  "...": "对应字段"
}
```

SSE 类型说明：

- `status`: 阶段日志，研究阶段会附带：
  - 实时进度日志（关键词开始/完成/整体进度）
  - `research`: 标准化素材摘要（id/provider/title/url/domain/scores）
  - `research_summary`: provider 统计与素材总数
  - `source_index`: 写作可引用来源索引（`S:source_id` -> URL）
- `plan`: Planner 规划结果，字段含 `outline/keywords/visual_placeholders/platform_style`。
- `content`: Writer 流式文本分片，字段含 `platform/chunk/citations`（当前 chunk 新出现的 `source_id`）。
- `done`: 正常结束，并附：
  - `citation_report`: 按平台输出的引用一致性报告
  - `outputs`: 按平台输出的产物元数据（chunk/char/word/citation_consistency/quality_score）
  - `quality_guard`: 平台质量守卫结果（是否达标、评分、检查项明细）
- `error`: 异常信息。

## 测试

```bash
uv run pytest -q
```
