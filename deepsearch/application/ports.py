"""应用层端口定义。

Protocol 只约束研究服务需要哪些能力，不规定能力由哪个库或服务实现。
测试可注入内存实现，生产环境则由 ``bootstrap.py`` 注入真实适配器。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from ..domain.models import (
    EvidenceGroup,
    ResearchBrief,
    ResearchMetrics,
    ResearchScorecard,
    RoundTrace,
    SearchPlan,
    SearchResult,
    Source,
    SufficiencyDecision,
    ValidationResult,
)

Progress = Callable[[str, str], None]


class SearchProvider(Protocol):
    """统一不同搜索服务的最小接口。"""

    name: str

    def search(self, query: str, limit: int = 5) -> list[SearchResult]: ...


class SourceFetcher(Protocol):
    """把搜索候选转换为带正文或摘要的来源。"""

    def fetch_all(self, results: list[SearchResult]) -> list[Source]: ...


class Planner(Protocol):
    """负责计划、充分性判断和查询调整。"""

    def plan(self, question: str, brief: ResearchBrief | None = None) -> SearchPlan: ...

    def evaluate(self, plan: SearchPlan, sources: list[Source], round_number: int, newly_added: int, stagnant_rounds: int) -> SufficiencyDecision: ...

    def adjust_queries(self, plan: SearchPlan, missing: list[str], round_number: int) -> list[str]: ...

    def terms(self, text: str) -> set[str]: ...


class Ranker(Protocol):
    """对候选和完整来源进行相关性、质量与多样性排序。"""

    def rank(self, results: list[SearchResult], question: str, subquestions: list[str], limit: int | None = None) -> list[SearchResult]: ...

    def score_source(self, source: Source, question: str) -> None: ...


class Verifier(Protocol):
    """把来源组织为可引用证据组并暴露冲突。"""

    def organize(self, sources: list[Source]) -> tuple[list[EvidenceGroup], list[str]]: ...


class Validator(Protocol):
    """在落盘前执行确定性报告检查与单次修复。"""

    def validate(self, report: str, sources: list[Source], plan: SearchPlan) -> ValidationResult: ...

    def repair(self, report: str, sources: list[Source], plan: SearchPlan) -> str: ...


class Reporter(Protocol):
    """根据计划和证据生成结论优先报告。"""

    def generate(self, plan: SearchPlan, sources: list[Source], evidence: list[EvidenceGroup], conflicts: list[str], rounds: int, trace: list[RoundTrace] | None = None) -> str: ...


class ReportStorage(Protocol):
    """保存已通过质量门的报告并列出历史资产。"""

    def save(self, question: str, report: str, output_format: str = "markdown") -> Path: ...

    def list(self, query: str = "") -> list[Path]: ...


class CacheStats(Protocol):
    """当前进程可观测的缓存命中统计。"""

    hits: int
    misses: int


class ResearchCachePort(Protocol):
    """搜索结果与正文的两级缓存接口。"""

    stats: CacheStats

    def get_search(self, provider: str, query: str, limit: int) -> list[SearchResult] | None: ...

    def set_search(self, provider: str, query: str, limit: int, results: list[SearchResult]) -> None: ...

    def get_source(self, url: str) -> Source | None: ...

    def set_source(self, source: Source) -> None: ...


class QualityEvaluatorPort(Protocol):
    """把研究过程和证据转换为可解释质量评分。"""

    def evaluate(self, plan: SearchPlan, sources: list[Source], validation: ValidationResult, metrics: ResearchMetrics, trace_count: int) -> ResearchScorecard: ...
