from datetime import UTC, datetime
from typing import AsyncGenerator
import asyncio

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.core.sse import (
    build_quality_guard,
    build_citation_report,
    estimate_word_count,
    extract_citations,
    serialize_research,
    serialize_source_index,
    strip_invalid_citations,
    sse_event,
    summarize_report,
)
from app.schemas import GenerateRequest
from app.services import planner, researcher, writer

router = APIRouter()


def _error_message(exc: Exception) -> str:
    msg = str(exc).strip()
    if not msg:
        msg = repr(exc).strip()
    if not msg:
        msg = "unknown error"
    return f"{exc.__class__.__name__}: {msg}"


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/api/generate/stream")
async def generate_stream(req: GenerateRequest) -> StreamingResponse:
    async def event_generator() -> AsyncGenerator[str, None]:
        started_at = datetime.now(UTC).isoformat()
        target_platform = req.platform
        try:
            yield sse_event(
                "status",
                {
                    "stage": "PLANNING",
                    "message": f"收到主题：{req.topic}，正在生成任务规划...",
                    "timestamp": started_at,
                },
            )

            plan = await planner.run(req.topic, req.platforms)
            yield sse_event(
                "plan",
                {
                    "stage": "PLANNING",
                    "plan": plan.model_dump(),
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )

            yield sse_event(
                "status",
                {
                    "stage": "RESEARCHING",
                    "message": f"正在检索关键词：{', '.join(plan.keywords[:6])}",
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )
            progress_queue: asyncio.Queue = asyncio.Queue()
            research_task = asyncio.create_task(
                researcher.run(plan.keywords, progress_queue=progress_queue)
            )
            while True:
                if research_task.done() and progress_queue.empty():
                    break
                try:
                    progress = await asyncio.wait_for(progress_queue.get(), timeout=0.5)
                except TimeoutError:
                    continue

                event = progress.get("event")
                if event == "keyword_start":
                    msg = f"正在检索关键词：{progress.get('keyword')}"
                elif event == "keyword_done":
                    msg = (
                        f"关键词检索完成：{progress.get('keyword')}，"
                        f"命中 {progress.get('materials', 0)} 条，耗时 {progress.get('latency_ms', 0)}ms"
                    )
                elif event == "keyword_progress":
                    msg = (
                        f"研究进度 {progress.get('completed', 0)}/{progress.get('total', 0)}，"
                        f"最近完成：{progress.get('keyword')}"
                    )
                else:
                    continue

                yield sse_event(
                    "status",
                    {
                        "stage": "RESEARCHING",
                        "message": msg,
                        "timestamp": datetime.now(UTC).isoformat(),
                    },
                )

            research_result = await research_task
            summary = summarize_report(research_result)
            no_provider_enabled = len(summary.get("provider_stats", {})) == 0
            yield sse_event(
                "status",
                {
                    "stage": "RESEARCHING",
                    "message": (
                        "未配置可用搜索源，已跳过外部检索并进入写作阶段。"
                        if no_provider_enabled
                        else "检索完成，进入写作阶段。"
                    ),
                    "research": serialize_research(research_result.by_keyword),
                    "research_summary": summary,
                    "source_index": serialize_source_index(research_result.merged_materials),
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )

            yield sse_event(
                "status",
                {
                    "stage": "WRITING",
                    "message": (
                        f"Writer 正在为平台 {target_platform} 融合素材并开始创作..."
                        + ("（LLM 实时流式）" if req.enable_llm_stream else "（服务端分片流式）")
                    ),
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )
            citation_tail = ""
            emitted_citations: set[str] = set()
            full_text_parts: list[str] = []
            chunk_count = 0
            available_ids = {item.id for item in research_result.merged_materials}
            async for chunk in writer.stream(
                req.topic,
                [target_platform],
                plan,
                research_result.by_keyword,
                enable_llm_stream=req.enable_llm_stream,
            ):
                cleaned_chunk = strip_invalid_citations(chunk, available_ids)
                full_text_parts.append(cleaned_chunk)
                chunk_count += 1
                combined = citation_tail + cleaned_chunk
                citations = []
                for cid in extract_citations(combined):
                    if cid not in available_ids:
                        continue
                    if cid in emitted_citations:
                        continue
                    emitted_citations.add(cid)
                    citations.append(cid)
                citation_tail = combined[-80:]
                yield sse_event(
                    "content",
                    {
                        "stage": "WRITING",
                        "platform": target_platform,
                        "chunk": cleaned_chunk,
                        "citations": citations,
                        "timestamp": datetime.now(UTC).isoformat(),
                    },
                )

            full_text = "".join(full_text_parts)
            all_cited = extract_citations(full_text)
            citation_report = build_citation_report(available_ids, all_cited)
            word_count = estimate_word_count(full_text)
            quality_guard = build_quality_guard(target_platform, full_text, citation_report)
            output_meta = {
                "chunk_count": chunk_count,
                "char_count": len(full_text),
                "word_count": word_count,
                "citation_consistency": citation_report["is_consistent"],
                "quality_score": quality_guard["score"],
                "quality_passed": quality_guard["is_passed"],
            }
            yield sse_event(
                "done",
                {
                    "stage": "DONE",
                    "message": "生成完成",
                    "citation_report": {target_platform: citation_report},
                    "outputs": {target_platform: output_meta},
                    "quality_guard": {target_platform: quality_guard},
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )
        except Exception as exc:
            yield sse_event(
                "error",
                {
                    "stage": "ERROR",
                    "message": _error_message(exc),
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)
