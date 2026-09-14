"""Brave Web Search API 适配器。"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

from deepsearch.domain.models import SearchResult
from .base import SearchProvider

logger = logging.getLogger(__name__)


class BraveSearch(SearchProvider):
    """带地区、语言与中等安全过滤的 Brave Web Search 适配器。"""

    name = "brave"
    capabilities = frozenset({"general", "current", "news", "official"})
    endpoint = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str, timeout: float = 8.0, country: str = "CN", language: str = "zh") -> None:
        super().__init__()
        self.api_key = api_key
        self.timeout = timeout
        self.country = country
        self.language = language

    def set_context(
        self, *, locale: str = "zh-CN", region: str = "CN", topic: str = "",
    ) -> None:
        """为当前单次搜索设置语言和地区；安全级别始终保持 moderate。"""

        self.country = (region or "CN").upper()[:2]
        self.language = (locale or "zh-CN").split("-", 1)[0].lower()

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        """查询 Brave；凭据缺失或网络异常时返回空列表供上层回退。"""

        if not self.api_key:
            self.last_status = "not_configured"
            return []
        params = urllib.parse.urlencode({
            "q": query[:400],
            "count": max(1, min(20, limit)),
            "country": self.country,
            "search_lang": self.language,
            "safesearch": "moderate",
            "extra_snippets": "true",
        })
        request = urllib.request.Request(
            f"{self.endpoint}?{params}",
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": self.api_key,
                "User-Agent": "DeepSearch/2.2",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            self.last_status = "rate_limited" if getattr(exc, "code", 0) == 429 else "connection_failed"
            self.last_error = "搜索服务限流" if self.last_status == "rate_limited" else type(exc).__name__
            logger.warning("Brave 搜索失败 error=%s", type(exc).__name__)
            return []
        self.last_status, self.last_error = "available", ""
        results = []
        for item in payload.get("web", {}).get("results", [])[:limit]:
            title, url = str(item.get("title", "")).strip(), str(item.get("url", "")).strip()
            if not title or not url:
                continue
            snippets = [str(item.get("description", "")).strip(), *item.get("extra_snippets", [])]
            snippet = " ".join(part for part in snippets if part)[:1200]
            results.append(SearchResult(title, url, snippet, query, self.name))
        return results
