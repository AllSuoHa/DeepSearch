"""DuckDuckGo HTML 搜索适配器。"""

from __future__ import annotations

import html
import logging
import re
import urllib.parse
import urllib.request

from deepsearch.domain.models import SearchResult
from .base import SearchProvider

logger = logging.getLogger(__name__)


class DuckDuckGoSearch(SearchProvider):
    """无需 API Key 和第三方包的免费搜索实现。"""

    name = "duckduckgo"
    endpoint = "https://html.duckduckgo.com/html/"

    def __init__(self, timeout: float = 8.0) -> None:
        self.timeout = timeout

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        """提交查询并把 HTML 结果解析为统一 ``SearchResult``。"""

        request = urllib.request.Request(
            self.endpoint,
            data=urllib.parse.urlencode({"q": query, "kp": "-1"}).encode(),
            headers={"User-Agent": "Mozilla/5.0 DeepSearch/1.0", "Accept": "text/html"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read(1_500_000).decode("utf-8", errors="replace")
        except Exception as exc:  # network failures are an expected degraded path
            logger.warning("搜索失败 query=%r error=%s", query, exc)
            return []

        # HTML 结构变化被隔离在本适配器内，不会影响应用层研究逻辑。
        links = re.findall(
            r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            body,
            flags=re.I | re.S,
        )
        snippets = re.findall(
            r'<(?:a|div)[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</(?:a|div)>',
            body,
            flags=re.I | re.S,
        )
        results: list[SearchResult] = []
        for index, (url, title) in enumerate(links[:limit]):
            decoded = html.unescape(url)
            parsed = urllib.parse.urlparse(decoded)
            # DuckDuckGo 常返回跳转链接，uddg 参数才是真实目标 URL。
            target = urllib.parse.parse_qs(parsed.query).get("uddg", [decoded])[0]
            clean_title = _clean_html(title)
            snippet = _clean_html(snippets[index]) if index < len(snippets) else ""
            if target.startswith("http"):
                results.append(SearchResult(clean_title, target, snippet, query, self.name))
        logger.info("搜索完成 provider=%s query=%r results=%d", self.name, query, len(results))
        return results


def _clean_html(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()
