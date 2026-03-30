import json
import os
import re
from typing import Any, Literal

from app.agents.models import Material, ProviderStat, ResearchReport

SSEType = Literal["status", "plan", "content", "done", "error"]


def sse_event(event_type: SSEType, payload: dict[str, Any]) -> str:
    data = {"type": event_type, **payload}
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def serialize_research(result: dict[str, list[Material]]) -> dict[str, list[dict[str, Any]]]:
    return {
        key: [
            {
                "id": item.id,
                "provider": item.provider,
                "title": item.title,
                "url": item.url,
                "domain": item.domain,
                "snippet": item.snippet,
                "published_at": item.published_at,
                "scores": {
                    "quality": round(item.quality_score, 2),
                    "credibility": round(item.credibility_score, 2),
                    "freshness": round(item.freshness_score, 2),
                    "relevance": round(item.relevance_score, 2),
                },
            }
            for item in items
        ]
        for key, items in result.items()
    }


def serialize_provider_stats(stats: dict[str, ProviderStat]) -> dict[str, dict[str, int]]:
    return {
        name: {
            "query_count": stat.query_count,
            "success_count": stat.success_count,
            "failure_count": stat.failure_count,
            "result_count": stat.result_count,
            "latency_ms": stat.latency_ms,
            "last_error": stat.last_error,
        }
        for name, stat in stats.items()
    }


def summarize_report(report: ResearchReport) -> dict[str, Any]:
    return {
        "keyword_count": len(report.by_keyword),
        "material_count": len(report.merged_materials),
        "provider_stats": serialize_provider_stats(report.provider_stats),
        "generated_at": report.generated_at.isoformat(),
    }


def serialize_source_index(materials: list[Material], limit: int = 50) -> list[dict[str, Any]]:
    return [
        {
            "id": item.id,
            "title": item.title,
            "url": item.url,
            "provider": item.provider,
            "domain": item.domain,
            "scores": {
                "quality": round(item.quality_score, 2),
                "credibility": round(item.credibility_score, 2),
                "freshness": round(item.freshness_score, 2),
                "relevance": round(item.relevance_score, 2),
            },
        }
        for item in materials[:limit]
    ]


def extract_citations(text: str) -> list[str]:
    found = re.findall(r"\[S:([A-Za-z0-9_-]{6,40})\]", text)
    seen: set[str] = set()
    ordered: list[str] = []
    for citation in found:
        if citation in seen:
            continue
        seen.add(citation)
        ordered.append(citation)
    return ordered


def strip_invalid_citations(text: str, available_source_ids: set[str]) -> str:
    def _repl(match: re.Match[str]) -> str:
        cid = match.group(1)
        return match.group(0) if cid in available_source_ids else ""

    return re.sub(r"\[S:([A-Za-z0-9_-]{6,40})\]", _repl, text)


def build_citation_report(
    available_source_ids: set[str],
    cited_source_ids: list[str],
) -> dict[str, Any]:
    unique_cited = []
    seen: set[str] = set()
    for cid in cited_source_ids:
        if cid in seen:
            continue
        seen.add(cid)
        unique_cited.append(cid)

    missing = [cid for cid in unique_cited if cid not in available_source_ids]
    resolved = [cid for cid in unique_cited if cid in available_source_ids]
    unused = sorted(list(available_source_ids - set(resolved)))

    return {
        "total_available_sources": len(available_source_ids),
        "total_cited_sources": len(unique_cited),
        "resolved_citations": resolved,
        "missing_citations": missing,
        "unused_sources": unused,
        "is_consistent": len(missing) == 0,
    }


def estimate_word_count(text: str) -> int:
    english_tokens = re.findall(r"[A-Za-z0-9_]+", text)
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", text)
    return len(english_tokens) + len(cjk_chars)


def build_quality_guard(platform: str, text: str, citation_report: dict[str, Any]) -> dict[str, Any]:
    platform = platform.lower()
    word_count = estimate_word_count(text)
    title_ok = bool(re.search(r"^# .+", text, flags=re.MULTILINE))
    heading_count = len(re.findall(r"^##\s+", text, flags=re.MULTILINE))
    has_conclusion = any(x in text for x in ("总结", "结论", "最后", "收尾"))
    has_list = bool(re.search(r"^\s*[-*]\s+", text, flags=re.MULTILINE))
    has_steps = bool(re.search(r"^\s*\d+\.\s+", text, flags=re.MULTILINE))
    citation_ok = (
        citation_report.get("total_available_sources", 0) == 0
        or citation_report.get("total_cited_sources", 0) > 0
    )

    ranges = {
        "zhihu": (
            int(os.getenv("QUALITY_ZHIHU_MIN_WORDS", "1800")),
            int(os.getenv("QUALITY_ZHIHU_MAX_WORDS", "2600")),
        ),
        "xiaohongshu": (
            int(os.getenv("QUALITY_XHS_MIN_WORDS", "800")),
            int(os.getenv("QUALITY_XHS_MAX_WORDS", "1400")),
        ),
        "baijiahao": (
            int(os.getenv("QUALITY_BJH_MIN_WORDS", "1200")),
            int(os.getenv("QUALITY_BJH_MAX_WORDS", "2000")),
        ),
    }
    min_words, max_words = ranges.get(platform, (1000, 2000))
    length_ok = min_words <= word_count <= max_words

    zhihu_min_h2 = int(os.getenv("QUALITY_ZHIHU_MIN_H2", "4"))
    xhs_require_steps = os.getenv("QUALITY_XHS_REQUIRE_LIST_OR_STEPS", "1") != "0"
    bjh_min_h2 = int(os.getenv("QUALITY_BJH_MIN_H2", "3"))

    platform_signal_checks = {
        "zhihu": {
            "name": "论证结构",
            "ok": heading_count >= zhihu_min_h2 and has_conclusion,
            "detail": f"期望至少{zhihu_min_h2}个二级标题且有总结/结论段。",
        },
        "xiaohongshu": {
            "name": "清单与步骤感",
            "ok": (has_list or has_steps) if xhs_require_steps else True,
            "detail": "期望包含清单或步骤编号。",
        },
        "baijiahao": {
            "name": "事实导向结构",
            "ok": heading_count >= bjh_min_h2 and has_conclusion,
            "detail": f"期望至少{bjh_min_h2}个二级标题并有结论收尾。",
        },
    }
    signal = platform_signal_checks.get(
        platform,
        {"name": "结构完整性", "ok": heading_count >= 3, "detail": "期望存在多个分节。"},
    )

    checks = [
        {"name": "标题存在", "ok": title_ok, "detail": "需要一级标题。"},
        {
            "name": "长度区间",
            "ok": length_ok,
            "detail": f"当前估算字数 {word_count}，期望 {min_words}-{max_words}。",
        },
        {"name": "引用覆盖", "ok": citation_ok, "detail": "有外部素材时应至少引用一个来源。"},
        {"name": signal["name"], "ok": signal["ok"], "detail": signal["detail"]},
    ]
    score = int(round(100 * sum(1 for c in checks if c["ok"]) / max(1, len(checks))))

    return {
        "platform": platform,
        "score": score,
        "is_passed": all(c["ok"] for c in checks),
        "estimated_word_count": word_count,
        "checks": checks,
    }
