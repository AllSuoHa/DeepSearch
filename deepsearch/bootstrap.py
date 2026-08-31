"""应用组合根：在这里把业务用例和具体技术实现装配起来。

应用层只描述“研究如何进行”，并不知道 DuckDuckGo、磁盘缓存或
Markdown 文件的存在。所有具体实现都在本文件集中创建和注入，因此
替换搜索源、模型或存储时，不需要修改研究主循环。
"""

from __future__ import annotations

from dataclasses import replace

from .application.evaluation import ResearchQualityEvaluator
from .application.planner import ResearchPlanner
from .application.service import ResearchService
from .application.verification import AnswerValidator, CrossVerifier
from .domain.models import ResearchBrief, ResearchPolicy, ResearchResult
from .domain.ranking import SourceRanker
from .infrastructure.cache import ResearchCache
from .infrastructure.config import Settings
from .infrastructure.fetcher import WebFetcher
from .infrastructure.llm import OpenAICompatibleLLM
from .infrastructure.reporting import MarkdownReporter
from .infrastructure.search import DuckDuckGoSearch, MockSearch, SearchProvider, WikipediaSearch
from .infrastructure.storage import FileReportStorage, ReportStorage

PROFILE_OVERRIDES = {
    "快速": {"max_rounds": 1, "max_sources": 8, "results_per_query": 3},
    "均衡": {},
    "深度": {"max_rounds": 4, "max_sources": 28, "results_per_query": 6},
}


def apply_profile(settings: Settings, profile: str = "均衡") -> Settings:
    """生成单次研究配置副本，不污染用户保存的全局配置。

    例如“深度”模式只在当前请求中扩大轮次和来源数；研究结束后，
    ``config.json`` 中的默认值仍保持不变。
    """

    return replace(settings, **PROFILE_OVERRIDES.get(profile, {}))


def build_research_service(
    settings: Settings,
    search_providers: list[SearchProvider] | None = None,
    storage: ReportStorage | None = None,
) -> ResearchService:
    """根据配置创建一个依赖完整、可立即运行的研究服务。"""

    # Mock 模式完全离线；在线模式同时注入两个搜索源以提高覆盖和容错。
    providers = search_providers or (
        [MockSearch()]
        if settings.mock_search
        else [DuckDuckGoSearch(settings.request_timeout), WikipediaSearch(settings.request_timeout)]
    )
    # 没有密钥时不创建 LLM 客户端，报告器会自动使用确定性模板。
    llm = None if settings.mock_llm else OpenAICompatibleLLM(settings.llm)

    # 这是全项目唯一集中实例化基础设施的地方（Composition Root）。
    return ResearchService(
        policy=ResearchPolicy(settings.max_rounds, settings.results_per_query, settings.max_sources, settings.mock_search),
        providers=providers,
        fallback=MockSearch(),
        fetcher=WebFetcher(settings.request_timeout, settings.fetch_workers),
        planner=ResearchPlanner(),
        verifier=CrossVerifier(),
        validator=AnswerValidator(),
        cache=ResearchCache(settings.cache_dir, settings.cache_ttl_seconds, settings.cache_enabled),
        ranker=SourceRanker(settings.per_domain_limit),
        reporter=MarkdownReporter(llm),
        storage=storage or FileReportStorage(settings.reports_dir),
        quality_evaluator=ResearchQualityEvaluator(),
    )


class DeepSearchAgent:
    """稳定的公共门面，隐藏内部服务装配细节。

    CLI、定时任务和外部调用者仍可以使用熟悉的
    ``DeepSearchAgent(settings).research(question)``，同时应用层保持纯净。
    """

    def __init__(self, settings: Settings, search_providers: list[SearchProvider] | None = None, storage: ReportStorage | None = None) -> None:
        self._service = build_research_service(settings, search_providers, storage)

    def research(self, question: str, progress=None, brief: ResearchBrief | None = None) -> ResearchResult:
        """执行研究；``brief`` 可约束领域、信息类型和最终报告规格。"""

        return self._service.research(question, progress, brief)

    def follow_up(self, previous: ResearchResult, question: str, progress=None) -> ResearchResult:
        return self._service.follow_up(previous, question, progress)

    def __getattr__(self, name: str):
        # 保留对 cache、planner 等内部对象的兼容访问，方便旧代码平滑升级。
        return getattr(self._service, name)
