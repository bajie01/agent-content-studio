import hashlib
import os
import re
import time
import asyncio
from datetime import UTC, datetime
from urllib.parse import urlparse

from app.agents.models import Material, ProviderStat, ResearchReport
from app.agents.search_providers import build_providers, gather_provider_results


class ResearcherAgent:
    def __init__(self, timeout: float | None = None):
        self.timeout = timeout if timeout is not None else float(os.getenv("RESEARCH_TIMEOUT", "60"))
        self.providers = build_providers(timeout=self.timeout)
        self.max_keywords = int(os.getenv("RESEARCH_MAX_KEYWORDS", "4"))
        self.max_per_keyword = int(os.getenv("RESEARCH_MAX_RESULTS_PER_KEYWORD", "3"))
        self.keyword_concurrency = int(os.getenv("RESEARCH_KEYWORD_CONCURRENCY", "3"))
        self.credible_domains = {
            "gov.cn",
            "people.com.cn",
            "xinhuanet.com",
            "csdn.net",
            "wikipedia.org",
            "nature.com",
            "science.org",
            "zhihu.com",
        }

    async def run(
        self,
        keywords: list[str],
        max_per_keyword: int | None = None,
        progress_queue: asyncio.Queue | None = None,
    ) -> ResearchReport:
        selected_keywords = keywords[: self.max_keywords]
        limit = max_per_keyword if max_per_keyword is not None else self.max_per_keyword
        by_keyword: dict[str, list[Material]] = {kw: [] for kw in selected_keywords}
        provider_stats: dict[str, ProviderStat] = {
            p.name: ProviderStat(provider=p.name) for p in self.providers
        }

        semaphore = asyncio.Semaphore(max(1, self.keyword_concurrency))

        async def _worker(keyword: str) -> tuple[str, list[Material], dict[str, ProviderStat], int]:
            async with semaphore:
                if progress_queue is not None:
                    await progress_queue.put({"event": "keyword_start", "keyword": keyword})

                keyword_provider_stats: dict[str, ProviderStat] = {
                    p.name: ProviderStat(provider=p.name) for p in self.providers
                }
                started = time.perf_counter()
                results = await gather_provider_results(self.providers, keyword, limit)
                elapsed_ms = int((time.perf_counter() - started) * 1000)

                combined: list[Material] = []
                for provider_name, item in results:
                    stat = keyword_provider_stats[provider_name]
                    stat.query_count += 1
                    stat.latency_ms += elapsed_ms
                    if isinstance(item, Exception):
                        stat.failure_count += 1
                        stat.last_error = f"{item.__class__.__name__}: {str(item)}"
                        continue
                    stat.success_count += 1
                    stat.result_count += len(item)
                    combined.extend(item)

                normalized = [self._enrich_material(material) for material in combined]
                deduped = self._dedupe(normalized)
                ranked = sorted(deduped, key=lambda x: x.quality_score, reverse=True)[:limit]

                if progress_queue is not None:
                    await progress_queue.put(
                        {
                            "event": "keyword_done",
                            "keyword": keyword,
                            "materials": len(ranked),
                            "latency_ms": elapsed_ms,
                        }
                    )

                return keyword, ranked, keyword_provider_stats, elapsed_ms

        tasks = [asyncio.create_task(_worker(keyword)) for keyword in selected_keywords]
        completed_count = 0
        total_count = len(tasks)
        for fut in asyncio.as_completed(tasks):
            keyword, ranked, keyword_provider_stats, _ = await fut
            completed_count += 1
            by_keyword[keyword] = ranked

            for provider_name, delta in keyword_provider_stats.items():
                base = provider_stats[provider_name]
                base.query_count += delta.query_count
                base.success_count += delta.success_count
                base.failure_count += delta.failure_count
                base.result_count += delta.result_count
                base.latency_ms += delta.latency_ms
                if delta.last_error:
                    base.last_error = delta.last_error

            if progress_queue is not None:
                await progress_queue.put(
                    {
                        "event": "keyword_progress",
                        "completed": completed_count,
                        "total": total_count,
                        "keyword": keyword,
                    }
                )

        if progress_queue is not None:
            await progress_queue.put({"event": "research_done"})

        merged = self._merge_and_rank(by_keyword)
        return ResearchReport(
            by_keyword=by_keyword,
            merged_materials=merged,
            provider_stats=provider_stats,
            generated_at=datetime.now(UTC),
        )

    def _merge_and_rank(self, by_keyword: dict[str, list[Material]], limit: int = 30) -> list[Material]:
        all_items: list[Material] = []
        for items in by_keyword.values():
            all_items.extend(items)
        deduped = self._dedupe(all_items)
        return sorted(deduped, key=lambda x: x.quality_score, reverse=True)[:limit]

    def _dedupe(self, items: list[Material]) -> list[Material]:
        best_by_key: dict[str, Material] = {}
        for item in items:
            key = item.dedupe_key or self._compute_dedupe_key(item.url, item.title)
            item.dedupe_key = key
            existing = best_by_key.get(key)
            if existing is None or item.quality_score > existing.quality_score:
                best_by_key[key] = item
        return list(best_by_key.values())

    def _enrich_material(self, item: Material) -> Material:
        item.url = self._normalize_url(item.url)
        item.domain = urlparse(item.url).netloc.lower()
        item.dedupe_key = self._compute_dedupe_key(item.url, item.title)
        item.id = self._compute_id(item.query, item.provider, item.url, item.title)
        item.relevance_score = self._score_relevance(item.query, item.title, item.snippet)
        item.credibility_score = self._score_credibility(item.domain)
        item.freshness_score = self._score_freshness(item.published_at)
        item.quality_score = (
            0.5 * item.relevance_score
            + 0.3 * item.credibility_score
            + 0.2 * item.freshness_score
        )
        return item

    @staticmethod
    def _normalize_url(url: str) -> str:
        url = url.strip()
        if not url:
            return url
        parsed = urlparse(url)
        scheme = parsed.scheme or "https"
        netloc = parsed.netloc.lower()
        path = parsed.path or "/"
        return f"{scheme}://{netloc}{path}"

    @staticmethod
    def _compute_dedupe_key(url: str, title: str) -> str:
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        normalized_title = re.sub(r"\s+", " ", title.lower()).strip()
        seed = f"{parsed.netloc.lower()}|{path}|{normalized_title}"
        return hashlib.md5(seed.encode("utf-8")).hexdigest()

    @staticmethod
    def _compute_id(query: str, provider: str, url: str, title: str) -> str:
        seed = f"{query}|{provider}|{url}|{title}"
        return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _score_relevance(query: str, title: str, snippet: str) -> float:
        text = f"{title} {snippet}".lower()
        parts = [p.lower() for p in re.split(r"[\s,，、;；]+", query) if p]
        if not parts:
            return 50.0
        hit = sum(1 for part in parts if part in text)
        return min(100.0, 35.0 + 65.0 * (hit / max(1, len(parts))))

    def _score_credibility(self, domain: str) -> float:
        if not domain:
            return 40.0
        if domain in self.credible_domains:
            return 90.0
        if domain.endswith(".gov.cn") or domain.endswith(".edu.cn"):
            return 85.0
        if domain.endswith(".org") or domain.endswith(".edu"):
            return 75.0
        return 55.0

    @staticmethod
    def _score_freshness(published_at: str | None) -> float:
        if not published_at:
            return 50.0
        parsed = ResearcherAgent._parse_datetime(published_at)
        if not parsed:
            return 50.0
        now = datetime.now(UTC)
        delta_days = max(0.0, (now - parsed).total_seconds() / 86400.0)
        if delta_days <= 7:
            return 95.0
        if delta_days <= 30:
            return 85.0
        if delta_days <= 180:
            return 70.0
        if delta_days <= 365:
            return 55.0
        return 40.0

    @staticmethod
    def _parse_datetime(text: str) -> datetime | None:
        text = text.strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                return dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
        except ValueError:
            return None
