import asyncio

from app.agents.models import Material
from app.agents.researcher import ResearcherAgent


class _OkProvider:
    name = "ok"
    enabled = True

    async def search(self, query: str, limit: int) -> list[Material]:
        return [
            Material(
                id="",
                query=query,
                provider=self.name,
                title="AIGC 内容创作方法",
                url="https://example.com/aigc",
                snippet="AIGC 内容创作方法拆解",
                domain="example.com",
            )
        ]


class _FailProvider:
    name = "fail"
    enabled = True

    async def search(self, query: str, limit: int) -> list[Material]:
        raise RuntimeError("provider unavailable")


def test_researcher_handles_partial_provider_failures(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.agents.researcher.build_providers",
        lambda timeout: [_OkProvider(), _FailProvider()],
    )
    agent = ResearcherAgent(timeout=5)
    report = asyncio.run(agent.run(["AIGC 内容创作"], max_per_keyword=3))

    assert len(report.merged_materials) >= 1
    assert report.provider_stats["ok"].success_count == 1
    assert report.provider_stats["fail"].failure_count == 1
    assert "provider unavailable" in (report.provider_stats["fail"].last_error or "")

