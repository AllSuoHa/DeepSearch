"""Tavily Search REST API 适配器；不引入供应商 SDK。"""

from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime, timezone

from deepsearch.domain.models import SearchResult
from .base import SearchProvider

logger = logging.getLogger(__name__)


class TavilySearch(SearchProvider):
    name = "tavily"
    capabilities = frozenset({"general", "current", "news", "official"})
    endpoint = "https://api.tavily.com/search"

    def __init__(self, api_key: str, timeout: float = 8.0) -> None:
        super().__init__()
        self.api_key = api_key
        self.timeout = timeout
        self.topic = "general"

    def set_context(self, *, locale: str = "zh-CN", region: str = "CN", topic: str = "") -> None:
        self.topic = "news" if topic == "news" else "general"

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        if not self.api_key:
            self.last_status = "not_configured"
            return []
        payload = json.dumps({
            "query": query[:400],
            "search_depth": "basic",
            "topic": self.topic,
            "include_answer": False,
            "include_raw_content": False,
            "max_results": max(1, min(20, limit)),
        }).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "DeepSearch/2.2",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read(2_000_000).decode("utf-8"))
        except Exception as exc:
            code = getattr(exc, "code", 0)
            self.last_status = "rate_limited" if code == 429 else "connection_failed"
            self.last_error = "搜索服务限流" if code == 429 else type(exc).__name__
            logger.warning("Tavily 搜索失败 error=%s", type(exc).__name__)
            return []
        self.last_status, self.last_error = "available", ""
        retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        results = []
        for item in data.get("results", [])[:limit]:
            title = str(item.get("title", "")).strip()
            url = str(item.get("url", "")).strip()
            if title and url:
                results.append(SearchResult(
                    title=title,
                    url=url,
                    snippet=str(item.get("content", "")).strip()[:1200],
                    query=query,
                    provider=self.name,
                    rank_score=float(item.get("score", 0.0) or 0.0),
                    published_at=str(item.get("published_date", "") or ""),
                    retrieved_at=retrieved_at,
                ))
        return results
