"""通用、学术与显式演示搜索适配器公共导出。"""

from .base import SearchProvider
from .academic import CrossrefSearch, OpenAlexSearch
from .brave import BraveSearch
from .duckduckgo import DuckDuckGoSearch
from .mock import MockSearch
from .wikipedia import WikipediaSearch

__all__ = [
    "SearchProvider", "BraveSearch", "DuckDuckGoSearch", "MockSearch", "WikipediaSearch",
    "OpenAlexSearch", "CrossrefSearch",
]
