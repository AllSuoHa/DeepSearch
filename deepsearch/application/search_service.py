"""链接优先的单轮搜索用例，与深度研究报告流程完全分离。"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlsplit

from .errors import SearchUnavailableError
from ..domain.models import ResourceType, RiskLevel, SearchResponse, SearchResult


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

    def __init__(self, providers, academic_providers=(), mock_mode: bool = False) -> None:
        self.providers = list(providers)
        self.academic_providers = list(academic_providers)
        self.mock_mode = mock_mode

    def search(self, query: str, limit: int = 12, locale: str = "zh-CN", region: str = "CN") -> SearchResponse:
        """改写查询、并发检索、去重分类，并返回可直接展示的结果。"""

        started = time.perf_counter()
        normalized = query.strip()
        if not normalized:
            raise ValueError("搜索内容不能为空")
        is_media = self._contains(normalized, self.media_markers)
        is_academic = self._contains(normalized, self.academic_markers)
        queries = [normalized]
        if is_media:
            queries.append(f'{normalized} {region} 官方 播放平台 在线观看')
        if re.search(r"[\u4e00-\u9fff]", normalized):
            if is_media:
                queries.append(f"{normalized} official streaming platform where to watch")
            elif is_academic:
                queries.append(f"{normalized} paper literature review")
            elif self._contains(normalized, self.technical_markers):
                queries.append(f"{normalized} official documentation GitHub")
        queries = list(dict.fromkeys(queries))
        providers = [*self.providers, *(self.academic_providers if is_academic else [])]
        for provider in providers:
            configure = getattr(provider, "set_context", None)
            if callable(configure):
                configure(locale=locale, region=region)
        raw = self._search_all(providers, queries, max(3, min(8, limit)))
        items = self._clean_and_classify(raw, is_media)
        items = self._deduplicate(items)[:limit]
        if not items:
            raise SearchUnavailableError(
                "没有取得真实搜索结果。请检查网络或配置 Brave Search API；在线模式不会再使用 Mock 结果代替。"
            )
        warnings = []
        if self.mock_mode:
            warnings.append("当前是显式演示模式：Mock 结果只用于验证界面和流程，不代表真实来源。")
        elif not any(item.provider == "brave" for item in items):
            warnings.append("当前结果来自免费回退搜索源；配置 Brave Search API 可提升稳定性与覆盖率。")
        return SearchResponse(
            normalized, self._answer(normalized, items, is_media), items, queries, warnings,
            round(time.perf_counter() - started, 3),
        )

    @staticmethod
    def _contains(text: str, markers: tuple[str, ...]) -> bool:
        lowered = text.lower()
        return any(marker in lowered for marker in markers)

    @staticmethod
    def _search_all(providers, queries: list[str], limit: int) -> list[SearchResult]:
        """并发执行搜索源与查询的笛卡尔积，并恢复稳定任务顺序。"""

        tasks = [(provider, query) for provider in providers for query in queries]
        if not tasks:
            return []
        groups: dict[int, list[SearchResult]] = {}
        with ThreadPoolExecutor(max_workers=min(8, len(tasks))) as executor:
            futures = {executor.submit(provider.search, query, limit): index for index, (provider, query) in enumerate(tasks)}
            for future in as_completed(futures):
                try:
                    groups[futures[future]] = future.result()
                except Exception:
                    groups[futures[future]] = []
        # future 的完成顺序不稳定；按创建序号重组可让结果和快照可重复。
        return [item for index in sorted(groups) for item in groups[index]]

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
        return sorted(cleaned, key=lambda item: (priority.get(item.resource_type, 4), -item.rank_score, item.title))

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
        metadata = " · ".join(part for part in (item.resource_type, item.risk_level, item.published_at) if part)
        lines.extend([f"### {index}. [{item.title}]({item.url})", "", metadata, "", item.snippet or "无摘要", ""])
    if response.warnings:
        lines.extend(["## 提示", "", *(f"- {warning}" for warning in response.warnings), ""])
    return "\n".join(lines).strip() + "\n"
