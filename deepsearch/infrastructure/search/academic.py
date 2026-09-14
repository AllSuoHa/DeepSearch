"""OpenAlex 与 Crossref 公共学术元数据搜索。"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

from deepsearch.domain.models import ResourceType, RiskLevel, SearchResult
from .base import SearchProvider

logger = logging.getLogger(__name__)


def _request_json(url: str, timeout: float, agent: str) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": agent})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _openalex_abstract(index: dict | None) -> str:
    if not index:
        return ""
    positions = [(position, word) for word, values in index.items() for position in values]
    return " ".join(word for _, word in sorted(positions))[:1600]


class OpenAlexSearch(SearchProvider):
    """读取 OpenAlex 作品元数据、作者和可用摘要。"""

    name = "openalex"
    capabilities = frozenset({"academic"})

    def __init__(self, timeout: float = 8.0) -> None:
        super().__init__()
        self.timeout = timeout

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        """按关键词搜索作品并映射为统一学术结果模型。"""

        params = urllib.parse.urlencode({"search": query, "per-page": max(1, min(20, limit))})
        try:
            payload = _request_json(f"https://api.openalex.org/works?{params}", self.timeout, "DeepSearch/2.2")
        except Exception as exc:
            self.last_status = "rate_limited" if getattr(exc, "code", 0) == 429 else "connection_failed"
            self.last_error = "搜索服务限流" if self.last_status == "rate_limited" else type(exc).__name__
            logger.warning("OpenAlex 搜索失败 error=%s", type(exc).__name__)
            return []
        self.last_status, self.last_error = "available", ""
        results = []
        for item in payload.get("results", [])[:limit]:
            title = str(item.get("display_name", "")).strip()
            location = item.get("primary_location") or {}
            url = str(location.get("landing_page_url") or item.get("doi") or item.get("id") or "").strip()
            if not title or not url.startswith("http"):
                continue
            authors = tuple(
                str(entry.get("author", {}).get("display_name", "")).strip()
                for entry in item.get("authorships", [])[:8]
                if entry.get("author", {}).get("display_name")
            )
            results.append(SearchResult(
                title, url, _openalex_abstract(item.get("abstract_inverted_index")), query, self.name,
                resource_type=ResourceType.ACADEMIC.value, risk_level=RiskLevel.TRUSTED.value,
                published_at=str(item.get("publication_date") or ""), authors=authors,
                doi=str(item.get("doi") or ""),
            ))
        return results


class CrossrefSearch(SearchProvider):
    """使用 Crossref 补充 DOI、出版日期和文献落地页。"""

    name = "crossref"
    capabilities = frozenset({"academic"})

    def __init__(self, timeout: float = 8.0) -> None:
        super().__init__()
        self.timeout = timeout

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        """查询 Crossref；单源失败由并发搜索层隔离。"""

        params = urllib.parse.urlencode({"query": query, "rows": max(1, min(20, limit)), "select": "title,URL,abstract,author,published,DOI"})
        try:
            payload = _request_json(f"https://api.crossref.org/works?{params}", self.timeout, "DeepSearch/2.2")
        except Exception as exc:
            self.last_status = "rate_limited" if getattr(exc, "code", 0) == 429 else "connection_failed"
            self.last_error = "搜索服务限流" if self.last_status == "rate_limited" else type(exc).__name__
            logger.warning("Crossref 搜索失败 error=%s", type(exc).__name__)
            return []
        self.last_status, self.last_error = "available", ""
        results = []
        for item in payload.get("message", {}).get("items", [])[:limit]:
            titles = item.get("title") or []
            title, url = (str(titles[0]).strip() if titles else ""), str(item.get("URL") or "").strip()
            if not title or not url.startswith("http"):
                continue
            authors = tuple(
                " ".join(filter(None, (str(author.get("given", "")).strip(), str(author.get("family", "")).strip())))
                for author in item.get("author", [])[:8]
            )
            date_parts = (item.get("published") or {}).get("date-parts") or []
            published = "-".join(str(value) for value in date_parts[0]) if date_parts else ""
            results.append(SearchResult(
                title, url, str(item.get("abstract") or "")[:1600], query, self.name,
                resource_type=ResourceType.ACADEMIC.value, risk_level=RiskLevel.TRUSTED.value,
                published_at=published, authors=tuple(item for item in authors if item), doi=str(item.get("DOI") or ""),
            ))
        return results
