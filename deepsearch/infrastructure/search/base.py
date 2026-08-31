"""所有搜索适配器必须遵守的统一接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from deepsearch.domain.models import SearchResult


class SearchProvider(ABC):
    """搜索源抽象：失败返回空列表，由研究服务统一决定是否降级。"""

    name = "base"

    @abstractmethod
    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        """返回相互独立的候选结果；失败应表现为空列表而不是终止流程。"""
