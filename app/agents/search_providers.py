import asyncio
import logging
import os
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import urlparse

import httpx

from app.agents.models import Material

logger = logging.getLogger(__name__)


class SearchProvider(ABC):
    name: str

    @property
    @abstractmethod
    def enabled(self) -> bool:
        pass

    @abstractmethod
    async def search(self, query: str, limit: int) -> list[Material]:
        pass

    @staticmethod
    def _domain(url: str) -> str:
        return urlparse(url).netloc.lower()

    @staticmethod
    def _warn_schema(provider: str, message: str, payload: Any) -> None:
        preview = str(payload)
        if len(preview) > 400:
            preview = preview[:400] + "...(truncated)"
        logger.warning("[%s] %s | payload=%s", provider, message, preview)


class BaiduQianfanProvider(SearchProvider):
    name = "baidu_qianfan"

    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout
        self.api_key = os.getenv("BAIDU_QIANFAN_API_KEY", "").strip()
        self.endpoint = os.getenv(
            "BAIDU_QIANFAN_ENDPOINT",
            "https://qianfan.baidubce.com/v2/ai_search/chat/completions",
        ).strip()
        self.model = os.getenv("BAIDU_QIANFAN_MODEL", "ernie-4.5-turbo-32k")
        self.search_source = os.getenv("BAIDU_QIANFAN_SEARCH_SOURCE", "baidu_search_v1")
        self.app_id = os.getenv("BAIDU_QIANFAN_APP_ID", "").strip()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def search(self, query: str, limit: int) -> list[Material]:
        if not self.enabled:
            return []
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": query}],
            "stream": False,
            "search_source": self.search_source,
            "top_k": min(max(limit, 1), 10),
        }
        if self.app_id:
            payload["app_id"] = self.app_id
        headers = {
            "Content-Type": "application/json",
            "X-Appbuilder-Authorization": f"Bearer {self.api_key}",
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(self.endpoint, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        if isinstance(data, dict) and data.get("code") not in (None, 0, "0", "success", "Success"):
            code = data.get("code")
            message = data.get("message") or data.get("msg") or "Unknown Qianfan error"
            request_id = data.get("request_id") or data.get("requestId")
            raise RuntimeError(f"Baidu Qianfan error code={code}, message={message}, request_id={request_id}")
        return self._extract_materials(query, data, limit)

    def _extract_materials(self, query: str, data: dict[str, Any], limit: int) -> list[Material]:
        out: list[Material] = []
        seen_keys: set[str] = set()
        candidates = []
        choices = data.get("choices", [])
        if isinstance(choices, list) and choices:
            msg = choices[0].get("message", {})
            if isinstance(msg, dict):
                for key in ("references", "search_results", "grounding", "citations"):
                    value = msg.get(key)
                    if isinstance(value, list):
                        candidates.extend(value)
                content = msg.get("content")
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict):
                            for key in ("references", "search_results", "citations"):
                                value = part.get(key)
                                if isinstance(value, list):
                                    candidates.extend(value)
        if not candidates:
            candidates = data.get("references", []) if isinstance(data.get("references"), list) else []
        if not candidates:
            self._warn_schema(
                self.name,
                "No candidate references found in known schema paths.",
                data,
            )

        for item in candidates[:limit]:
            if not isinstance(item, dict):
                continue
            url = (
                item.get("url")
                or item.get("link")
                or item.get("source_url")
                or item.get("source")
                or ""
            )
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                continue
            title = str(item.get("title") or item.get("name") or "Baidu Search Result")[:160]
            snippet = str(item.get("snippet") or item.get("content") or item.get("summary") or "")
            out.append(
                Material(
                    id="",
                    query=query,
                    provider=self.name,
                    title=title,
                    url=url,
                    snippet=snippet,
                    domain=self._domain(url),
                    published_at=item.get("published_at") or item.get("date"),
                    raw=item,
                )
            )
        return out[:limit]


class BochaProvider(SearchProvider):
    name = "bocha"

    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout
        self.api_key = os.getenv("BOCHA_API_KEY", "").strip()
        self.endpoint = os.getenv("BOCHA_WEB_SEARCH_ENDPOINT", "https://api.bochaai.com/v1/web-search")

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def search(self, query: str, limit: int) -> list[Material]:
        if not self.enabled:
            return []
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {"query": query, "count": min(max(limit, 1), 10), "summary": True}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(self.endpoint, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        return self._extract_materials(query, data, limit)

    def _extract_materials(self, query: str, data: dict[str, Any], limit: int) -> list[Material]:
        out: list[Material] = []
        seen_keys: set[str] = set()
        candidates = []
        for key in ("data", "results", "webPages", "items"):
            value = data.get(key)
            if isinstance(value, list):
                candidates.extend(value)
            if isinstance(value, dict):
                for sub in ("value", "results", "items"):
                    if isinstance(value.get(sub), list):
                        candidates.extend(value[sub])
                web_pages = value.get("webPages")
                if isinstance(web_pages, dict):
                    for sub in ("value", "results", "items"):
                        if isinstance(web_pages.get(sub), list):
                            candidates.extend(web_pages[sub])
        data_block = data.get("data")
        if isinstance(data_block, dict):
            web_pages = data_block.get("webPages")
            if isinstance(web_pages, dict):
                for sub in ("value", "results", "items"):
                    if isinstance(web_pages.get(sub), list):
                        candidates.extend(web_pages[sub])
        if not candidates:
            self._warn_schema(
                self.name,
                "No candidate items found in known schema paths.",
                data,
            )
        for item in candidates[:limit]:
            if not isinstance(item, dict):
                continue
            url = item.get("url") or item.get("link") or item.get("source") or ""
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                continue
            title = str(item.get("title") or item.get("name") or "Bocha Search Result")[:160]
            dedupe_key = f"{url}|{title}"
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            out.append(
                Material(
                    id="",
                    query=query,
                    provider=self.name,
                    title=title,
                    url=url,
                    snippet=str(item.get("snippet") or item.get("summary") or item.get("content") or ""),
                    domain=self._domain(url),
                    published_at=item.get("date") or item.get("published_at"),
                    raw=item,
                )
            )
            if len(out) >= limit:
                break
        return out[:limit]


class TianAPIProvider(SearchProvider):
    name = "tianapi"

    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout
        self.api_key = os.getenv("TIANAPI_KEY", "").strip()
        self.endpoint = os.getenv("TIANAPI_NEWS_ENDPOINT", "https://apis.tianapi.com/generalnews/index")

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def search(self, query: str, limit: int) -> list[Material]:
        if not self.enabled:
            return []
        payload = {"key": self.api_key, "word": query, "num": min(max(limit, 1), 10), "page": 1}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(self.endpoint, data=payload)
            resp.raise_for_status()
            data = resp.json()
        return self._extract_materials(query, data, limit)

    def _extract_materials(self, query: str, data: dict[str, Any], limit: int) -> list[Material]:
        result = data.get("result", {})
        news = result.get("newslist", []) if isinstance(result, dict) else []
        if not isinstance(news, list):
            self._warn_schema(
                self.name,
                "Expected result.newslist as list.",
                data,
            )
            return []
        out: list[Material] = []
        for item in news[:limit]:
            if not isinstance(item, dict):
                continue
            url = item.get("url") or ""
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                continue
            out.append(
                Material(
                    id="",
                    query=query,
                    provider=self.name,
                    title=str(item.get("title") or "TianAPI News")[:160],
                    url=url,
                    snippet=str(item.get("description") or item.get("digest") or ""),
                    domain=self._domain(url),
                    published_at=item.get("ctime"),
                    raw=item,
                )
            )
        return out[:limit]


class JuheNewsProvider(SearchProvider):
    name = "juhe"

    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout
        self.api_key = os.getenv("JUHE_API_KEY", "").strip()
        self.endpoint = os.getenv("JUHE_NEWS_ENDPOINT", "https://v.juhe.cn/toutiao/index")
        self.news_type = os.getenv("JUHE_NEWS_TYPE", "top")

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def search(self, query: str, limit: int) -> list[Material]:
        if not self.enabled:
            return []
        params = {
            "key": self.api_key,
            "type": self.news_type,
            "page": 1,
            "page_size": min(max(limit, 1), 20),
            "is_filter": 1,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(self.endpoint, params=params)
            resp.raise_for_status()
            data = resp.json()
        return self._extract_materials(query, data, limit)

    def _extract_materials(self, query: str, data: dict[str, Any], limit: int) -> list[Material]:
        result = data.get("result", {})
        items = result.get("data", []) if isinstance(result, dict) else []
        if not isinstance(items, list):
            self._warn_schema(
                self.name,
                "Expected result.data as list.",
                data,
            )
            return []
        out: list[Material] = []
        lowered_query = query.lower()
        for item in items:
            if not isinstance(item, dict):
                continue
            url = item.get("url") or ""
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                continue
            title = str(item.get("title") or "Juhe News")[:160]
            author = item.get("author_name")
            snippet = str(item.get("category") or "")
            if lowered_query and lowered_query not in f"{title} {snippet}".lower():
                # 聚合新闻是频道流，按 query 做轻过滤
                continue
            out.append(
                Material(
                    id="",
                    query=query,
                    provider=self.name,
                    title=title,
                    url=url,
                    snippet=snippet,
                    domain=self._domain(url),
                    published_at=item.get("date"),
                    author=str(author) if author else None,
                    raw=item,
                )
            )
            if len(out) >= limit:
                break
        return out


def build_providers(timeout: float) -> list[SearchProvider]:
    registry: dict[str, SearchProvider] = {
        "baidu_qianfan": BaiduQianfanProvider(timeout=timeout),
        "bocha": BochaProvider(timeout=timeout),
        "tianapi": TianAPIProvider(timeout=timeout),
        "juhe": JuheNewsProvider(timeout=timeout),
    }
    configured = os.getenv("SEARCH_PROVIDERS", "baidu_qianfan,bocha,tianapi,juhe")
    provider_names = [x.strip().lower() for x in configured.split(",") if x.strip()]

    out: list[SearchProvider] = []
    for name in provider_names:
        provider = registry.get(name)
        if provider and provider.enabled:
            out.append(provider)
    return out


async def gather_provider_results(
    providers: list[SearchProvider],
    query: str,
    limit: int,
) -> list[tuple[str, list[Material] | Exception]]:
    if not providers:
        return []

    async def _runner(provider: SearchProvider) -> tuple[str, list[Material] | Exception]:
        try:
            return provider.name, await provider.search(query, limit)
        except Exception as exc:
            return provider.name, exc

    tasks = [_runner(provider) for provider in providers]
    return await asyncio.gather(*tasks)
