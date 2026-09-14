"""按查询意图选择搜索能力，避免所有来源无差别执行。"""

from __future__ import annotations

from enum import Enum
from typing import Iterable

from .ports import SearchProvider
from ..domain.models import SearchContentType


class QueryRoute(str, Enum):
    GENERAL = "general"
    CURRENT = "current"
    ACADEMIC = "academic"
    BACKGROUND = "background"
    OFFICIAL = "official"


_CURRENT_MARKERS = (
    "今天", "今日", "现在", "当前", "最新", "最近", "实时", "新闻", "价格",
    "天气", "库存", "汇率", "股价", "比分", "政策变化", "更新",
)
_ACADEMIC_MARKERS = ("论文", "期刊", "文献", "学术", "研究综述", "paper", "journal", "doi")
_BACKGROUND_MARKERS = ("是什么", "定义", "百科", "历史", "起源", "人物", "概念", "简介")
_OFFICIAL_MARKERS = ("官网", "官方", "政策", "公告", "通知", "标准", "法规", "招标")


def classify_query(query: str, content_type: str = "") -> QueryRoute:
    """以显式内容类型优先，再用少量可解释信号判断搜索能力。"""

    text = query.casefold()
    if content_type == SearchContentType.ACADEMIC.value or any(item in text for item in _ACADEMIC_MARKERS):
        return QueryRoute.ACADEMIC
    if content_type == SearchContentType.ANNOUNCEMENT.value or any(item in text for item in _OFFICIAL_MARKERS):
        return QueryRoute.OFFICIAL
    if content_type == SearchContentType.NEWS.value or any(item in text for item in _CURRENT_MARKERS):
        return QueryRoute.CURRENT
    if content_type == SearchContentType.KNOWLEDGE.value or any(item in text for item in _BACKGROUND_MARKERS):
        return QueryRoute.BACKGROUND
    return QueryRoute.GENERAL


class SearchProviderRegistry:
    """保存有序 Provider，并按能力选择当前查询真正需要的来源。"""

    def __init__(self, providers: Iterable[SearchProvider]) -> None:
        self.providers = list(providers)

    def select(self, query: str, content_type: str = "") -> list[SearchProvider]:
        route = classify_query(query, content_type)
        selected: list[SearchProvider] = []
        for provider in self.providers:
            capabilities = frozenset(getattr(provider, "capabilities", {"general"}))
            # 测试替身和第三方旧适配器没有能力声明时，按通用网页源兼容。
            if route == QueryRoute.ACADEMIC:
                allowed = bool(capabilities & {"general", "academic"})
            elif route == QueryRoute.BACKGROUND:
                allowed = bool(capabilities & {"general", "background"})
            elif route == QueryRoute.OFFICIAL:
                allowed = bool(capabilities & {"general", "official"})
            elif route == QueryRoute.CURRENT:
                allowed = bool(capabilities & {"current", "news"})
            else:
                allowed = "general" in capabilities
            if allowed:
                selected.append(provider)
        # 自定义 provider 至少仍有机会执行；知识/学术专用源不会因此进入实时查询。
        return selected or [
            provider for provider in self.providers
            if "general" in frozenset(getattr(provider, "capabilities", {"general"}))
        ]
