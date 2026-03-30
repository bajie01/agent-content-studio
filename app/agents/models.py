from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class Material:
    id: str
    query: str
    provider: str
    title: str
    url: str
    snippet: str
    domain: str
    published_at: str | None = None
    author: str | None = None
    language: str = "zh"
    content_type: str = "article"
    fetch_status: str = "ok"
    quality_score: float = 0.0
    credibility_score: float = 0.0
    freshness_score: float = 0.0
    relevance_score: float = 0.0
    dedupe_key: str = ""
    raw: dict[str, Any] | None = None


@dataclass
class ProviderStat:
    provider: str
    query_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    result_count: int = 0
    latency_ms: int = 0
    last_error: str | None = None


@dataclass
class ResearchReport:
    by_keyword: dict[str, list[Material]]
    merged_materials: list[Material]
    provider_stats: dict[str, ProviderStat]
    generated_at: datetime
