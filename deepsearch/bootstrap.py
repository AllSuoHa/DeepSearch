"""应用组合根：在这里把业务用例和具体技术实现装配起来。

应用层描述直接回答、搜索和研究如何进行，并不知道 DuckDuckGo、磁盘缓存或
Markdown 文件的存在。所有具体实现都在本文件集中创建和注入，因此
替换搜索源、模型或存储时，不需要修改各用例主流程。
"""

from __future__ import annotations

from dataclasses import replace

from .application.chat_service import ChatService
from .application.errors import ResearchModelRequiredError
from .application.evaluation import ResearchQualityEvaluator
from .application.intent import IntentClassifier
from .application.planner import ResearchPlanner
from .application.search_service import SearchService
from .application.service import ResearchService
from .application.verification import AnswerValidator, CrossVerifier
from .domain.models import AgentRequest, AgentRunResult, ResearchBrief, ResearchPolicy, ResearchResult, WorkMode
from .domain.ranking import SourceRanker
from .infrastructure.cache import ResearchCache
from .infrastructure.config import Settings
from .infrastructure.fetcher import WebFetcher
from .infrastructure.llm import OpenAICompatibleLLM
from .infrastructure.reporting import MarkdownReporter
from .infrastructure.search import (
    BraveSearch, CrossrefSearch, DuckDuckGoSearch, MockSearch, OpenAlexSearch,
    SearchProvider, WikipediaSearch,
)
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
    providers = search_providers or build_search_providers(settings)
    # 确定性模板只服务显式 Mock 演示；在线研究会在检索前要求可用模型。
    llm = None if settings.mock_llm else OpenAICompatibleLLM(settings.llm)

    # 这是全项目唯一集中实例化基础设施的地方（Composition Root）。
    return ResearchService(
        policy=ResearchPolicy(settings.max_rounds, settings.results_per_query, settings.max_sources, settings.mock_search),
        providers=providers,
        fallback=MockSearch() if settings.mock_search else None,
        fetcher=WebFetcher(settings.request_timeout, settings.fetch_workers),
        planner=ResearchPlanner(),
        verifier=CrossVerifier(),
        validator=AnswerValidator(),
        cache=ResearchCache(settings.cache_dir, settings.cache_ttl_seconds, settings.cache_enabled),
        ranker=SourceRanker(settings.per_domain_limit),
        reporter=MarkdownReporter(llm),
        storage=storage or FileReportStorage(settings.reports_dir),
        quality_evaluator=ResearchQualityEvaluator(),
        academic_providers=[] if settings.mock_search else [
            OpenAlexSearch(settings.request_timeout), CrossrefSearch(settings.request_timeout),
        ],
    )


def build_search_providers(settings: Settings) -> list[SearchProvider]:
    """按用户配置创建搜索源；在线源为空时也绝不注入 Mock。"""

    if settings.mock_search:
        return [MockSearch()]
    available = {
        "brave": lambda: BraveSearch(settings.brave_api_key, settings.request_timeout),
        "duckduckgo": lambda: DuckDuckGoSearch(settings.request_timeout),
        "wikipedia": lambda: WikipediaSearch(settings.request_timeout),
    }
    providers = []
    for name in settings.search_provider_order:
        if name == "brave" and not settings.brave_api_key:
            continue
        factory = available.get(name)
        if factory is not None:
            providers.append(factory())
    return providers or [DuckDuckGoSearch(settings.request_timeout), WikipediaSearch(settings.request_timeout)]


class DeepSearchAgent:
    """稳定的公共门面，隐藏内部服务装配细节。

    CLI、定时任务和外部调用者仍可以使用熟悉的
    ``DeepSearchAgent(settings).research(question)``，同时应用层保持纯净。
    """

    def __init__(self, settings: Settings, search_providers: list[SearchProvider] | None = None, storage: ReportStorage | None = None) -> None:
        self.settings = settings
        self._service = build_research_service(settings, search_providers, storage)
        providers = search_providers or build_search_providers(settings)
        self._search_service = SearchService(
            providers,
            [] if settings.mock_search else [OpenAlexSearch(settings.request_timeout), CrossrefSearch(settings.request_timeout)],
            mock_mode=settings.mock_search,
            content_type=settings.search_content_type,
        )
        # 快速回答模型使用独立配置实例；未配置时由 ChatService 本地降级，
        # 绝不会复用研究模型或消耗研究模型额度。
        chat_model = OpenAICompatibleLLM(settings.effective_chat_llm) if settings.chat_model_available else None
        self._chat_service = ChatService(chat_model)
        self._classifier = IntentClassifier()

    def run(self, request: AgentRequest, progress=None) -> AgentRunResult:
        """统一执行问答、搜索或研究；AUTO 仅在兼容调用中做路由。"""

        requested = request.mode if isinstance(request.mode, WorkMode) else WorkMode(str(request.mode))
        resolved = self._classifier.resolve(request.question, requested)
        notify = progress or (lambda phase, message: None)
        mode_name = {
            WorkMode.CHAT: "问答",
            WorkMode.SEARCH: "搜索",
            WorkMode.RESEARCH: "研究",
        }[resolved]
        notify("route", f"已选择{mode_name}模式")
        if resolved == WorkMode.CHAT:
            chat = self._chat_service.reply(
                request.question,
                request.chat_history,
                notify,
                locale=request.locale,
                region=request.region,
            )
            return AgentRunResult(requested, resolved, chat=chat)
        if resolved == WorkMode.SEARCH:
            notify("search", "正在检索并整理可直接访问的结果…")
            response = self._search_service.search(request.question, locale=request.locale, region=request.region)
            qualifier = "演示" if self.settings.mock_search else "真实"
            notify("saved", f"已整理 {len(response.items)} 条{qualifier}结果")
            return AgentRunResult(requested, resolved, search=response)
        result = self.research(request.question, progress, request.brief)
        return AgentRunResult(requested, resolved, research=result)

    def research(self, question: str, progress=None, brief: ResearchBrief | None = None) -> ResearchResult:
        """执行研究；``brief`` 可约束领域、信息类型和最终报告规格。"""

        if self.settings.runtime_mode != "mock" and not self.settings.llm.api_key:
            raise ResearchModelRequiredError("研究模式需要可用的大模型。请先在设置中配置模型 API Key；普通搜索无需模型。")
        return self._service.research(question, progress, brief)

    def follow_up(self, previous: ResearchResult, question: str, progress=None) -> ResearchResult:
        """继续研究上下文；缺少模型时仍在任何补充检索前终止。"""

        if self.settings.runtime_mode != "mock" and not self.settings.llm.api_key:
            raise ResearchModelRequiredError("研究追问需要可用的大模型。请先在设置中配置模型 API Key。")
        return self._service.follow_up(previous, question, progress)

    def __getattr__(self, name: str):
        # 保留对 cache、planner 等内部对象的兼容访问，方便旧代码平滑升级。
        return getattr(self._service, name)
