"""Wikipedia / MediaWiki 公共 API 搜索适配器。"""

from __future__ import annotations

import html
import json
import logging
import re
import urllib.parse
import urllib.request

from deepsearch.domain.models import SearchResult
from .base import SearchProvider

logger = logging.getLogger(__name__)


class WikipediaSearch(SearchProvider):
    """作为第二条免费检索路径，补充百科类背景和定义信息。"""

    name = "wikipedia"
    capabilities = frozenset({"background"})

    def __init__(self, timeout: float = 8.0) -> None:
        super().__init__()
        self.timeout = timeout

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        # 中文问题优先查询中文站，其余问题使用英文站提高命中率。
        language = "zh" if len(re.findall(r"[\u4e00-\u9fff]", query)) >= 2 else "en"
        params = urllib.parse.urlencode({
            "action": "query", "list": "search", "srsearch": query,
            "srlimit": max(1, limit), "format": "json", "utf8": 1,
        })
        endpoint = f"https://{language}.wikipedia.org/w/api.php?{params}"
        request = urllib.request.Request(endpoint, headers={"User-Agent": "DeepSearch/1.1 (personal research tool)"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            self.last_status = "rate_limited" if getattr(exc, "code", 0) == 429 else "connection_failed"
            self.last_error = "搜索服务限流" if self.last_status == "rate_limited" else type(exc).__name__
            logger.warning("Wikipedia 搜索失败 error=%s", type(exc).__name__)
            return []
        self.last_status, self.last_error = "available", ""
        results = []
        for item in payload.get("query", {}).get("search", [])[:limit]:
            title = str(item.get("title", "")).strip()
            if not title:
                continue
            url_title = urllib.parse.quote(title.replace(" ", "_"))
            snippet = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", str(item.get("snippet", ""))))).strip()
            results.append(SearchResult(title, f"https://{language}.wikipedia.org/wiki/{url_title}", snippet, query, self.name))
        return results
