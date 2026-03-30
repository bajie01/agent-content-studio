# Backend API Handoff (v0.1)

## Base

- Local: `http://127.0.0.1:8000`
- Content stream endpoint: `POST /api/generate/stream`
- Health endpoint: `GET /health`

## Request

### Generate (SSE)

`POST /api/generate/stream`

Headers:

- `Content-Type: application/json`
- `Accept: text/event-stream`

Body:

```json
{
  "topic": "AIGC 对内容创作行业的影响",
  "platform": "zhihu",
  "enable_llm_stream": true
}
```

Field rules:

- `topic`: required, 2-200 chars
- `platform`: required, single value only, enum:
  - `zhihu`
  - `xiaohongshu`
  - `baijiahao`
- `enable_llm_stream`: optional, default `false`

Backward compatibility:

- Old field `platforms: ["zhihu"]` is still accepted.
- If both `platform` and `platforms` are provided, they must match.

## SSE Event Protocol

Every frame is a single line:

```text
data: {json}
```

Top-level common fields:

- `type`: `status | plan | content | done | error`
- `stage`: `PLANNING | RESEARCHING | WRITING | DONE | ERROR`
- `timestamp`: ISO time

### 1) `status`

Examples:

- Planning status
- Research progress status
- Writing start status

Possible payload fields:

- `message: string`
- `research: Record<string, Material[]>` (on research finish)
- `research_summary: ResearchSummary` (on research finish)
- `source_index: SourceIndexItem[]` (on research finish)

### 2) `plan`

Payload:

- `plan.outline: string[]`
- `plan.keywords: string[]`
- `plan.visual_placeholders: string[]`
- `plan.platform_style: Record<string, string>`

### 3) `content`

Payload:

- `platform: string`
- `chunk: string` (incremental markdown text)
- `citations: string[]` (newly detected source ids in this chunk)

### 4) `done`

Payload:

- `message: "生成完成"`
- `citation_report: Record<platform, CitationReport>`
- `outputs: Record<platform, OutputMeta>`
- `quality_guard: Record<platform, QualityGuard>`

### 5) `error`

Payload:

- `message: string` (always includes exception class and detail)

## Data Shapes

### Material (in `research`)

- `id`
- `provider`
- `title`
- `url`
- `domain`
- `snippet`
- `published_at`
- `scores.quality`
- `scores.credibility`
- `scores.freshness`
- `scores.relevance`

### ResearchSummary

- `keyword_count`
- `material_count`
- `provider_stats`:
  - `query_count`
  - `success_count`
  - `failure_count`
  - `result_count`
  - `latency_ms`
  - `last_error`

### SourceIndexItem

- `id`
- `title`
- `url`
- `provider`
- `domain`
- `scores.*`

### CitationReport

- `total_available_sources`
- `total_cited_sources`
- `resolved_citations`
- `missing_citations`
- `unused_sources`
- `is_consistent`

### OutputMeta

- `chunk_count`
- `char_count`
- `word_count`
- `citation_consistency`
- `quality_score`
- `quality_passed`

### QualityGuard

- `platform`
- `score`
- `is_passed`
- `estimated_word_count`
- `checks[]`:
  - `name`
  - `ok`
  - `detail`

## Frontend Integration Notes

- Render `content.chunk` as markdown incrementally.
- Keep an append-only log for `status.message`.
- Use `source_index` + `citations` to create clickable source badges.
- Show `done.quality_guard[platform]` as final quality card.
- If `error` arrives, stop stream and preserve existing text.

## Curl Example

```bash
curl -N -X POST "http://127.0.0.1:8000/api/generate/stream" \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{
    "topic":"AIGC 对内容创作行业的影响",
    "platform":"zhihu",
    "enable_llm_stream":true
  }'
```

