"""跨层共享的领域模型。

这些 dataclass 是研究流程的“公共语言”：应用层负责创建和更新它们，
基础设施层负责填充来源内容，CLI 与 Web 前端只读取并展示它们。
模型本身不依赖 Streamlit、HTTP、文件系统或具体大模型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class QuestionType(str, Enum):
    """问题类型决定拆解方式、最低研究轮次和报告结构。"""

    FACT = "simple_fact"
    COMPARISON = "comparison"
    EXPLORATION = "open_exploration"
    RESEARCH = "deep_research"


class WorkMode(str, Enum):
    """工作模式；AUTO 是旧入口的兼容路由值，Web 显式使用其余三项。"""

    AUTO = "auto"
    CHAT = "chat"
    SEARCH = "search"
    RESEARCH = "research"


class ContextPolicy(str, Enum):
    """一次执行如何使用既有会话内容；重跑必须显式使用 ``FRESH``。"""

    FRESH = "fresh"
    CONVERSATION = "conversation"
    FOLLOW_UP = "follow_up"


class SearchContentType(str, Enum):
    """搜索内容偏好；用于查询聚焦，不改变搜索源或启用大模型。"""

    GENERAL = "综合"
    NEWS = "新闻"
    KNOWLEDGE = "知识"
    ANNOUNCEMENT = "公告"
    ACADEMIC = "学术"


# 研究高级选项中的内置信息维度。自定义项由配置层追加，展示层负责去重。
RESEARCH_INFORMATION_TYPES = ("新闻", "知识", "公告", "研究", "数据", "政策")


class ResourceType(str, Enum):
    """搜索结果展示分组；值直接作为简体中文界面标签。"""

    WEB = "网页"
    OFFICIAL = "官方信息"
    WATCH = "播放平台"
    COMMUNITY = "社区与聚合"
    ACADEMIC = "学术文献"


class RiskLevel(str, Enum):
    """链接访问风险，不等同于对页面内容真实性的事实背书。"""

    TRUSTED = "可信来源"
    NORMAL = "普通网页"
    UNVERIFIED = "未验证"
    CAUTION = "谨慎访问"


class Confidence(str, Enum):
    """证据组的可读可信度标签。"""

    VERIFIED = "✅ 多源验证"
    SINGLE = "⚠️ 单一来源"
    CONFLICT = "❌ 来源矛盾"


@dataclass(frozen=True, slots=True)
class ReportSpecification:
    """用户对最终研究资产的可执行要求。

    报告始终先生成并校验一份规范 Markdown，再由存储适配器按
    ``output_format`` 导出，避免不同文件格式绕过引用质量门。
    """

    output_format: str = "markdown"
    target_words: int = 1200
    audience: str = "通用读者"
    language: str = "中文"
    sections: tuple[str, ...] = ("结论", "关键发现", "分析", "局限", "参考来源")
    custom_instructions: str = ""


@dataclass(frozen=True, slots=True)
class ResearchBrief:
    """一次研究的目标、信息边界和交付标准。"""

    domain: str = "通用"
    objective: str = "形成可验证、可执行的总结报告"
    information_types: tuple[str, ...] = ("知识", "新闻", "公告")
    time_scope: str = "不限"
    report: ReportSpecification = field(default_factory=ReportSpecification)


@dataclass(slots=True)
class SearchPlan:
    """规划器输出：问题拆解、查询、最低深度和最终交付要求。"""

    question: str
    question_type: QuestionType
    subquestions: list[str]
    queries: list[str]
    minimum_rounds: int = 1
    rationale: str = ""
    brief: ResearchBrief = field(default_factory=ResearchBrief)


@dataclass(slots=True)
class SearchResult:
    """搜索提供器返回的轻量候选，尚未完成正文抓取。"""

    title: str
    url: str
    snippet: str = ""
    query: str = ""
    provider: str = ""
    rank_score: float = 0.0
    resource_type: str = ResourceType.WEB.value
    risk_level: str = RiskLevel.UNVERIFIED.value
    risk_reasons: tuple[str, ...] = ()
    published_at: str = ""
    authors: tuple[str, ...] = ()
    doi: str = ""
    retrieved_at: str = ""
    freshness_status: str = "unknown"


@dataclass(slots=True)
class Source:
    """进入报告的完整来源，包含正文、状态和两个独立评分。"""

    title: str
    url: str
    content: str
    snippet: str = ""
    query: str = ""
    provider: str = ""
    fetched: bool = True
    error: str | None = None
    source_id: int = 0
    relevance_score: float = 0.0
    quality_score: float = 0.0
    resource_type: str = ResourceType.WEB.value
    risk_level: str = RiskLevel.UNVERIFIED.value
    risk_reasons: tuple[str, ...] = ()
    published_at: str = ""
    authors: tuple[str, ...] = ()
    doi: str = ""
    retrieved_at: str = ""
    freshness_status: str = "unknown"

    @property
    def usable_text(self) -> str:
        """正文不可用时透明降级到搜索摘要。"""

        return self.content.strip() or self.snippet.strip()


@dataclass(slots=True)
class EvidenceGroup:
    """一条归纳主张及支持它的来源编号和保守可信度。"""

    statement: str
    source_ids: list[int]
    confidence: Confidence


@dataclass(slots=True)
class SufficiencyDecision:
    """每轮结束时的反馈：是否停止、缺什么、下一步搜什么。"""

    sufficient: bool
    reason: str
    missing: list[str] = field(default_factory=list)
    next_queries: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ValidationResult:
    """确定性质量门的结果；issues 可直接驱动唯一一次修订。"""

    valid: bool
    issues: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RoundTrace:
    """单轮结构化轨迹，供调试、测试和证据页面展示。"""

    round_number: int
    queries: list[str]
    results_found: int
    new_sources: int
    fetched_sources: int
    duration_seconds: float
    sufficient: bool
    decision: str
    used_fallback: bool = False
    missing_dimensions: list[str] = field(default_factory=list)
    next_queries: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ResearchMetrics:
    """一次研究的效率与健康度统计。"""

    elapsed_seconds: float = 0.0
    search_results: int = 0
    fetched_sources: int = 0
    failed_sources: int = 0
    cache_hits: int = 0
    cache_misses: int = 0


@dataclass(frozen=True, slots=True)
class ResearchPolicy:
    """应用用例拥有的运行预算，与配置文件和 UI 表单解耦。"""

    max_rounds: int = 3
    results_per_query: int = 4
    max_sources: int = 16
    mock_search: bool = False


@dataclass(frozen=True, slots=True)
class QualityDimension:
    """一个可解释的 0–100 工程质量维度。"""

    name: str
    score: int
    explanation: str


@dataclass(frozen=True, slots=True)
class ResearchScorecard:
    """研究质量总分、等级、分项解释和改进建议。"""

    overall: int = 0
    grade: str = "待评估"
    dimensions: tuple[QualityDimension, ...] = ()
    recommendations: tuple[str, ...] = ()


@dataclass(slots=True)
class ResearchResult:
    """一次研究的完整交付对象，也是 CLI 与 Web 的统一数据源。"""

    question: str
    report: str
    report_path: Path
    sources: list[Source]
    rounds: int
    plan: SearchPlan
    stop_reason: str
    validation: ValidationResult
    trace: list[RoundTrace] = field(default_factory=list)
    metrics: ResearchMetrics = field(default_factory=ResearchMetrics)
    scorecard: ResearchScorecard = field(default_factory=ResearchScorecard)
    direct_answer: str = ""
    review_summary: tuple[str, ...] = ()
    provider_failures: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ChatTurn:
    """可安全发送给快速回答模型的一条精简会话消息。"""

    role: str
    content: str


@dataclass(frozen=True, slots=True)
class AgentRequest:
    """统一运行入口的请求对象。"""

    question: str
    mode: WorkMode = WorkMode.AUTO
    brief: ResearchBrief = field(default_factory=ResearchBrief)
    locale: str = "zh-CN"
    region: str = "CN"
    conversation_id: str = ""
    chat_history: tuple[ChatTurn, ...] = ()
    context_policy: ContextPolicy = ContextPolicy.CONVERSATION
    previous_research: ResearchResult | None = None


@dataclass(slots=True)
class ChatResult:
    """直接回答交付；不包含来源、报告或可入库资产。"""

    question: str
    answer: str
    used_model: bool = False
    used_fallback: bool = False
    elapsed_seconds: float = 0.0
    model_name: str = ""


@dataclass(slots=True)
class SearchResponse:
    """搜索模式交付：直接链接优先，不伪装成研究报告。"""

    query: str
    answer: str
    items: list[SearchResult]
    queries: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    artifact_path: Path | None = None
    provider_failures: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RunAudit:
    """可安全展示和持久化的实际执行信息，不包含密钥或请求正文。"""

    requested_mode: WorkMode
    actual_mode: WorkMode
    model_name: str = ""
    used_search: bool = False
    search_providers: tuple[str, ...] = ()
    context_policy: ContextPolicy = ContextPolicy.FRESH


@dataclass(slots=True)
class AgentRunResult:
    """统一入口的互斥结果：一次运行只包含直接回答、搜索或研究之一。"""

    requested_mode: WorkMode
    resolved_mode: WorkMode
    search: SearchResponse | None = None
    research: ResearchResult | None = None
    chat: ChatResult | None = None
    audit: RunAudit | None = None

    @property
    def content(self) -> str:
        """返回适合对话列表显示的主要文本。"""

        if self.chat is not None:
            return self.chat.answer
        if self.search is not None:
            return self.search.answer
        return self.research.report if self.research is not None else ""

    @property
    def artifact_path(self) -> Path | None:
        """返回本次搜索快照或研究报告的本地文件路径。"""

        if self.chat is not None:
            return None
        if self.search is not None:
            return self.search.artifact_path
        return self.research.report_path if self.research is not None else None
