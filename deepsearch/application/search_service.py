"""链接优先的单轮搜索用例，与深度研究报告流程完全分离。"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .errors import SearchUnavailableError
from .search_routing import QueryRoute, SearchProviderRegistry, classify_query
from ..domain.models import ResourceType, RiskLevel, SearchContentType, SearchResponse, SearchResult
from ..domain.ranking import SourceRanker


class SearchService:
    """执行链接优先的单轮搜索，不进入研究报告生成与质量评分流程。"""

    media_markers = ("影视", "电影", "电视剧", "剧集", "哪里看", "在线观看", "播放平台", "season", "movie")
    academic_markers = ("论文", "期刊", "文献", "学术", "paper", "journal", "doi")
    technical_markers = (
        "技术", "架构", "模型", "开源", "代码", "文档", "api", "agent", "rag", "python",
        "github", "framework", "documentation",
    )
    blocked_markers = (
        "磁力", "torrent", "种子下载", "盗版", "破解", "网盘资源", "无码", "免安装破解",
        ".exe download", "crack download", "keygen",
    )
    caution_markers = ("免费下载", "高速下载", "立即安装", "网盘", "短链", "跳转下载")
    watch_hosts = {
        "netflix.com", "disneyplus.com", "primevideo.com", "amazon.com", "max.com", "hulu.com",
        "youku.com", "iqiyi.com", "v.qq.com", "mgtv.com", "bilibili.com", "tv.apple.com",
    }
    official_hosts = {"imdb.com", "themoviedb.org", "wikipedia.org", "douban.com"}
    community_hosts = {"reddit.com", "zhihu.com", "douban.com", "tieba.baidu.com"}
    content_hints = {
        SearchContentType.NEWS.value: "最新 新闻",
        SearchContentType.KNOWLEDGE.value: "百科 资料",
        SearchContentType.ANNOUNCEMENT.value: "官方 公告",
        SearchContentType.ACADEMIC.value: "论文 文献",
    }

    def __init__(
        self,
        providers,
        academic_providers=(),
        mock_mode: bool = False,
        content_type: SearchContentType | str = SearchContentType.GENERAL,
    ) -> None:
        self.providers = list(providers)
        self.academic_providers = list(academic_providers)
        self.mock_mode = mock_mode
        self.content_type = (
            content_type.value if isinstance(content_type, SearchContentType) else str(content_type).strip()
        ) or SearchContentType.GENERAL.value
        self.registry = SearchProviderRegistry([*self.providers, *self.academic_providers])
        self.ranker = SourceRanker(per_domain_limit=2)
        self._provider_failures: dict[str, str] = {}
        self._failure_counts: dict[str, int] = {}

    def search(self, query: str, limit: int = 12, locale: str = "zh-CN", region: str = "CN") -> SearchResponse:
        """改写查询、并发检索、去重分类，并返回可直接展示的结果。"""

        started = time.perf_counter()
        normalized = query.strip()
        if not normalized:
            raise ValueError("搜索内容不能为空")
        is_media = self._contains(normalized, self.media_markers)
        is_academic = (
            self.content_type == SearchContentType.ACADEMIC.value
            or self._contains(normalized, self.academic_markers)
        )
        # 内容类型只给搜索词增加可解释的聚焦提示，不更换搜索供应商，也不
        # 调用大模型；“综合”保持原始查询，兼容已有行为与历史结果。
        # 自定义类型本身就是查询聚焦词，例如“专利”“财报”或“访谈”。
        content_hint = self.content_hints.get(
            self.content_type,
            "" if self.content_type == SearchContentType.GENERAL.value else self.content_type,
        )
        if not content_hint and any(marker in normalized.casefold() for marker in ("官网", "官方", "政策", "公告", "通知", "法规")):
            content_hint = "官方"
        scoped_query = f"{normalized} {content_hint}".strip()
        queries = [scoped_query]
        if is_media:
            queries.append(f'{scoped_query} {region} 官方 播放平台 在线观看')
        if re.search(r"[\u4e00-\u9fff]", normalized):
            if is_media:
                queries.append(f"{scoped_query} official streaming platform where to watch")
            elif is_academic:
                queries.append(f"{scoped_query} paper literature review")
            elif self._contains(normalized, self.technical_markers):
                queries.append(f"{scoped_query} official documentation GitHub")
        queries = list(dict.fromkeys(queries))
        route = classify_query(normalized, self.content_type)
        providers = self.registry.select(normalized, self.content_type)
        for provider in providers:
            configure = getattr(provider, "set_context", None)
            if callable(configure):
                configure(
                    locale=locale,
                    region=region,
                    topic="news" if route == QueryRoute.CURRENT else "",
                )
        raw = self._search_all(providers, queries, max(3, min(8, limit)))
        retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for item in raw:
            item.retrieved_at = item.retrieved_at or retrieved_at
            item.freshness_status = (
                "dated" if item.published_at else "unknown"
            ) if route == QueryRoute.CURRENT else "not_required"
        items = self._clean_and_classify(raw, is_media)
        items = self.ranker.rank(items, normalized, queries, limit=max(limit * 2, limit))
        items = self._deduplicate(items)[:limit]
        if not items:
            provider_labels = {
                "brave": "Brave",
                "duckduckgo": "DuckDuckGo",
                "wikipedia": "Wikipedia",
                "tavily": "Tavily",
                "searxng": "SearXNG",
                "openalex": "OpenAlex",
                "crossref": "Crossref",
            }
            attempted = "、".join(dict.fromkeys(
                provider_labels.get(
                    str(getattr(provider, "name", "")).lower(),
                    str(getattr(provider, "name", "")),
                )
                for provider in providers
                if str(getattr(provider, "name", "")).strip()
            )) or "当前搜索源"
            raise SearchUnavailableError(
                f"没有取得可用的真实搜索结果。已尝试 {attempted}，但本次均未返回可用内容；"
                "常见原因是网络超时、访问限制或搜索服务临时异常。请检查网络，或配置 Tavily/Brave/SearXNG "
                "提升稳定性；在线模式不会使用 Mock 结果代替。"
            )
        warnings = []
        if self.mock_mode:
            warnings.append("当前是显式演示模式：Mock 结果只用于验证界面和流程，不代表真实来源。")
        if self._provider_failures:
            warnings.append("部分搜索源已降级：" + "；".join(
                f"{name}（{reason}）" for name, reason in self._provider_failures.items()
            ))
        if not any(item.provider.split("/", 1)[0] in {"brave", "tavily"} for item in items):
            warnings.append("当前结果来自免费或自托管搜索源，稳定性和额度取决于对应服务。")
        return SearchResponse(
            normalized, self._answer(normalized, items, is_media), items, queries, warnings,
            round(time.perf_counter() - started, 3),
            provider_failures=dict(self._provider_failures),
        )

    @staticmethod
    def _contains(text: str, markers: tuple[str, ...]) -> bool:
        lowered = text.lower()
        return any(marker in lowered for marker in markers)

    def _search_all(self, providers, queries: list[str], limit: int) -> list[SearchResult]:
        """并发执行搜索源与查询的笛卡尔积，并恢复稳定任务顺序。"""

        tasks = [(provider, query) for provider in providers for query in queries]
        if not tasks:
            return []
        self._provider_failures = {}
        groups: dict[int, list[SearchResult]] = {}
        with ThreadPoolExecutor(max_workers=min(8, len(tasks))) as executor:
            futures = {
                executor.submit(self._search_one, provider, query, limit): (index, provider)
                for index, (provider, query) in enumerate(tasks)
            }
            for future in as_completed(futures):
                try:
                    index, _ = futures[future]
                    groups[index] = future.result()
                except Exception as exc:
                    index, provider = futures[future]
                    groups[index] = []
                    self._provider_failures[provider.name] = type(exc).__name__
        # future 的完成顺序不稳定；按创建序号重组可让结果和快照可重复。
        return [item for index in sorted(groups) for item in groups[index]]

    def _search_one(self, provider, query: str, limit: int) -> list[SearchResult]:
        """单源有限重试；连续失败三次后本次服务实例内熔断。"""

        name = str(getattr(provider, "name", "unknown"))
        if self._failure_counts.get(name, 0) >= 3:
            self._provider_failures[name] = "连续失败，已临时熔断"
            return []
        results: list[SearchResult] = []
        for _ in range(2):
            results = provider.search(query, limit)
            if results or not getattr(provider, "last_error", ""):
                break
        if results:
            self._failure_counts[name] = 0
            return results
        reason = str(getattr(provider, "last_error", "") or "未返回结果")
        self._failure_counts[name] = self._failure_counts.get(name, 0) + 1
        self._provider_failures[name] = reason
        return []

    def _clean_and_classify(self, results: list[SearchResult], is_media: bool) -> list[SearchResult]:
        cleaned = []
        for item in results:
            parsed = urlsplit(item.url)
            if item.provider == "mock" and not self.mock_mode:
                continue
            if item.provider != "mock" and (parsed.scheme not in {"http", "https"} or not parsed.netloc):
                continue
            combined = f"{item.title} {item.url} {item.snippet}".lower()
            if not self.mock_mode and any(marker in combined for marker in self.blocked_markers):
                continue
            host = parsed.netloc.lower().removeprefix("www.")
            if item.resource_type == ResourceType.ACADEMIC.value:
                pass
            elif is_media and self._host_matches(host, self.watch_hosts):
                item.resource_type, item.risk_level = ResourceType.WATCH.value, RiskLevel.TRUSTED.value
            elif self._host_matches(host, self.official_hosts):
                item.resource_type, item.risk_level = ResourceType.OFFICIAL.value, RiskLevel.TRUSTED.value
            elif self._host_matches(host, self.community_hosts):
                item.resource_type, item.risk_level = ResourceType.COMMUNITY.value, RiskLevel.NORMAL.value
            elif item.provider == "mock":
                item.risk_level = RiskLevel.UNVERIFIED.value
            elif any(marker in combined for marker in self.caution_markers):
                item.risk_level = RiskLevel.CAUTION.value
                item.risk_reasons = ("页面包含下载或跳转提示，请确认域名与文件类型",)
            else:
                item.risk_level = RiskLevel.UNVERIFIED.value
            item.snippet = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", item.snippet)).strip()[:600]
            cleaned.append(item)
        priority = {
            ResourceType.WATCH.value: 0, ResourceType.OFFICIAL.value: 1,
            ResourceType.ACADEMIC.value: 1, ResourceType.COMMUNITY.value: 2, ResourceType.WEB.value: 3,
        }
        return sorted(cleaned, key=lambda item: (-item.rank_score, priority.get(item.resource_type, 4), item.title))

    @staticmethod
    def _host_matches(host: str, choices: set[str]) -> bool:
        return any(host == choice or host.endswith("." + choice) for choice in choices)

    @staticmethod
    def _deduplicate(items: list[SearchResult]) -> list[SearchResult]:
        unique, seen = [], set()
        for item in items:
            parsed = urlsplit(item.url)
            key = (parsed.netloc.lower(), parsed.path.rstrip("/"))
            if key not in seen:
                seen.add(key)
                unique.append(item)
        return unique

    @staticmethod
    def _answer(query: str, items: list[SearchResult], is_media: bool) -> str:
        if is_media:
            platforms = [item for item in items if item.resource_type == ResourceType.WATCH.value]
            if platforms:
                return f"找到 {len(platforms)} 个可能的正规播放入口和 {len(items) - len(platforms)} 条补充结果。请以平台页面显示的地区版权和订阅状态为准。"
            return "暂未找到可确认的正规播放入口，下面保留了官方信息与其他网页结果；未验证来源请谨慎访问。"
        return f"已为“{query}”整理 {len(items)} 条直接结果，并按来源类型和访问风险排序。"


def search_response_markdown(response: SearchResponse) -> str:
    """把结构化搜索结果序列化为可下载、可投递的 Markdown 快照。"""

    lines = [f"# {response.query}", "", response.answer, "", "## 搜索结果", ""]
    for index, item in enumerate(response.items, 1):
        metadata = " · ".join(part for part in (
            item.resource_type,
            item.risk_level,
            f"Provider: {item.provider}" if item.provider else "",
            f"Published: {item.published_at}" if item.published_at else "Published: unknown",
            f"Retrieved: {item.retrieved_at}" if item.retrieved_at else "Retrieved: unknown",
            f"Freshness: {item.freshness_status}",
        ) if part)
        lines.extend([
            f"### {index}. [{item.title}]({item.url})", "", metadata,
            f"Query: {item.query or response.query}", "", item.snippet or "无摘要", "",
        ])
    if response.warnings:
        lines.extend(["## 提示", "", *(f"- {warning}" for warning in response.warnings), ""])
    return "\n".join(lines).strip() + "\n"
