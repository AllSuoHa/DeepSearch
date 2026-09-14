"""可配置、自托管的 SearXNG JSON Search API 适配器。"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from deepsearch.domain.models import SearchResult
from .base import SearchProvider

logger = logging.getLogger(__name__)


class SearXNGSearch(SearchProvider):
    name = "searxng"
    capabilities = frozenset({"general", "current", "news", "official"})

    def __init__(self, base_url: str, timeout: float = 8.0) -> None:
        super().__init__()
        self.base_url = self._normalize_base_url(base_url)
        self.timeout = timeout
        self.language = "zh-CN"
        self.time_range = ""

    @staticmethod
    def _normalize_base_url(value: str) -> str:
        clean = value.strip().rstrip("/")
        if not clean:
            return ""
        parsed = urllib.parse.urlsplit(clean)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("SearXNG Base URL 必须是无内嵌凭据的 HTTP(S) 地址")
        return clean

    def set_context(self, *, locale: str = "zh-CN", region: str = "CN", topic: str = "") -> None:
        self.language = locale or "zh-CN"
        self.time_range = "month" if topic == "news" else ""

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        if not self.base_url:
            self.last_status = "not_configured"
            return []
        params = {"q": query[:400], "format": "json", "language": self.language, "safesearch": "1"}
        if self.time_range:
            params["time_range"] = self.time_range
        request = urllib.request.Request(
            f"{self.base_url}/search?{urllib.parse.urlencode(params)}",
            headers={"Accept": "application/json", "User-Agent": "DeepSearch/2.2"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read(2_000_000).decode("utf-8"))
        except Exception as exc:
            code = getattr(exc, "code", 0)
            self.last_status = "rate_limited" if code == 429 else "connection_failed"
            self.last_error = "搜索服务限流" if code == 429 else type(exc).__name__
            logger.warning("SearXNG 搜索失败 error=%s", type(exc).__name__)
            return []
        self.last_status, self.last_error = "available", ""
        retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        results = []
        for item in data.get("results", [])[:limit]:
            title = str(item.get("title", "")).strip()
            url = str(item.get("url", "")).strip()
            if title and url:
                engines = item.get("engines", ())
                provider = self.name + (f"/{','.join(map(str, engines))}" if engines else "")
                results.append(SearchResult(
                    title=title,
                    url=url,
                    snippet=str(item.get("content", "")).strip()[:1200],
                    query=query,
                    provider=provider,
                    published_at=str(item.get("publishedDate", "") or item.get("pubdate", "") or ""),
                    retrieved_at=retrieved_at,
                ))
        return results
