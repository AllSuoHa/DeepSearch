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
    sections: tuple[str, ...] = ("摘要", "关键结论", "详细分析", "建议与下一步", "证据局限与争议")
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

    @property
    def usable_text(self) -> str:
        """正文不可用时透明降级到搜索摘要。"""

        return self.content.strip() or self.snippet.strip()


@dataclass(slots=True)
class EvidenceGroup:
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
