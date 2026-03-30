import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.agents.models import Material, ProviderStat, ResearchReport
from app.schemas import TaskPlan
from app.server import app


class _FakePlanner:
    async def run(self, topic: str, platforms: list[str]) -> TaskPlan:
        return TaskPlan(
            outline=["背景", "方法", "总结"],
            keywords=["AIGC", "内容创作"],
            visual_placeholders=["图1"],
            platform_style={platforms[0]: "测试风格"},
        )


class _FakeResearcher:
    async def run(
        self,
        keywords: list[str],
        max_per_keyword: int | None = None,
        progress_queue=None,
    ) -> ResearchReport:
        if progress_queue is not None:
            await progress_queue.put({"event": "keyword_start", "keyword": keywords[0]})
            await progress_queue.put(
                {"event": "keyword_done", "keyword": keywords[0], "materials": 1, "latency_ms": 5}
            )
            await progress_queue.put({"event": "keyword_progress", "completed": 1, "total": 1})
            await progress_queue.put({"event": "research_done"})
        material = Material(
            id="source123",
            query=keywords[0],
            provider="bocha",
            title="AIGC 实践",
            url="https://example.com/source",
            snippet="实践要点",
            domain="example.com",
            quality_score=88,
            credibility_score=80,
            freshness_score=70,
            relevance_score=90,
        )
        return ResearchReport(
            by_keyword={keywords[0]: [material]},
            merged_materials=[material],
            provider_stats={
                "bocha": ProviderStat(
                    provider="bocha",
                    query_count=1,
                    success_count=1,
                    failure_count=0,
                    result_count=1,
                    latency_ms=100,
                )
            },
            generated_at=datetime.now(UTC),
        )


class _FakeWriter:
    async def stream(
        self,
        topic: str,
        platforms: list[str],
        plan: TaskPlan,
        research: dict[str, list[Material]],
        *,
        enable_llm_stream: bool = False,
    ):
        yield "# 标题\n"
        yield "## 正文\n内容 [S:source123]\n"
        yield "错误引用 [S:bad999]\n"
        yield "## 总结\n结束\n"


def _parse_sse_events(raw_text: str) -> list[dict]:
    events: list[dict] = []
    for line in raw_text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = json.loads(line[6:])
        events.append(payload)
    return events


def test_sse_stream_contains_expected_events(monkeypatch) -> None:
    import app.api.routes as routes

    monkeypatch.setattr(routes, "planner", _FakePlanner())
    monkeypatch.setattr(routes, "researcher", _FakeResearcher())
    monkeypatch.setattr(routes, "writer", _FakeWriter())

    client = TestClient(app)
    resp = client.post(
        "/api/generate/stream",
        json={"topic": "AIGC内容创作", "platform": "zhihu", "enable_llm_stream": False},
    )
    assert resp.status_code == 200

    events = _parse_sse_events(resp.text)
    types = [evt["type"] for evt in events]
    assert "plan" in types
    assert "content" in types
    assert types[-1] == "done"

    content_events = [evt for evt in events if evt["type"] == "content"]
    assert content_events
    assert all(evt["platform"] == "zhihu" for evt in content_events)
    assert any("source123" in evt.get("citations", []) for evt in content_events)
    assert all("[S:bad999]" not in evt["chunk"] for evt in content_events)

    done = events[-1]
    assert done["citation_report"]["zhihu"]["is_consistent"] is True
    assert "quality_guard" in done
    assert "outputs" in done
