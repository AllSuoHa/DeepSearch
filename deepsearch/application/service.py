"""深度研究核心用例。

本模块实现项目最重要的 Agent 反馈循环：规划 → 搜索 → 抓取 →
充分性评估 → 必要时调整策略 → 验证 → 生成 → 校验 → 评分。
它只依赖 ``ports.py`` 声明的抽象协议，不直接创建任何基础设施对象。
"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from ..domain.models import ResearchBrief, ResearchMetrics, ResearchPolicy, ResearchResult, RoundTrace, SearchResult, Source
from .context import ContextResolver
from .errors import ReportQualityError, SearchUnavailableError
from .search_routing import QueryRoute, SearchProviderRegistry, classify_query
from .ports import (
    Planner,
    Progress,
    QualityEvaluatorPort,
    Ranker,
    Reporter,
    ReportStorage,
    ResearchCachePort,
    SearchProvider,
    SourceFetcher,
    Validator,
    Verifier,
)

logger = logging.getLogger(__name__)


class ResearchService:
    """编排一次完整研究，具体技术由组合根通过构造函数注入。"""

    def __init__(
        self,
        policy: ResearchPolicy,
        providers: list[SearchProvider],
        fallback: SearchProvider | None,
        fetcher: SourceFetcher,
        planner: Planner,
        verifier: Verifier,
        validator: Validator,
        cache: ResearchCachePort,
        ranker: Ranker,
        reporter: Reporter,
        storage: ReportStorage,
        quality_evaluator: QualityEvaluatorPort,
        academic_providers: list[SearchProvider] | None = None,
    ) -> None:
        self.policy = policy
        self.providers = providers
        self.academic_providers = academic_providers or []
        self.registry = SearchProviderRegistry([*self.providers, *self.academic_providers])
        self.fallback = fallback
        self.fetcher = fetcher
        self.planner = planner
        self.verifier = verifier
        self.validator = validator
        self.cache = cache
        self.ranker = ranker
        self._used_search_fallback = False
        self.reporter = reporter
        self.storage = storage
        self.quality_evaluator = quality_evaluator
        self._provider_failures: dict[str, str] = {}
        self._provider_failure_counts: dict[str, int] = {}

    def research(
        self,
        question: str,
        progress: Progress | None = None,
        brief: ResearchBrief | None = None,
        *,
        search_query: str = "",
    ) -> ResearchResult:
        """执行研究主流程，并返回报告、证据、轨迹、指标和质量评分。"""

        started = time.perf_counter()
        # 使用差值计算“本次研究”的缓存统计，避免复用 Agent 时累计值失真。
        initial_hits, initial_misses = self.cache.stats.hits, self.cache.stats.misses
        notify = progress or (lambda phase, message: None)

        # 规划阶段必须先于任何网络请求，以便用户立即看到反馈。
        notify("plan", "正在分析问题并制定搜索策略…")
        plan = self.planner.plan(question, brief)
        if search_query.strip() and search_query.strip() != question.strip():
            # 报告仍回答用户当前问题；只有检索词使用去指代后的独立表达。
            rewritten_plan = self.planner.plan(search_query.strip(), brief)
            plan.queries = rewritten_plan.queries
        notify("plan", f"{plan.rationale} 子问题：{len(plan.subquestions)} 个")
        logger.info("规划完成 type=%s subquestions=%d", plan.question_type.value, len(plan.subquestions))

        sources: list[Source] = []
        known_urls: set[str] = set()
        stagnant_rounds = 0
        queries = plan.queries
        route = classify_query(search_query or question)
        ranking_question = f"{question} {search_query}".strip()
        active_providers = self.registry.select(search_query or question)
        for provider in active_providers:
            configure = getattr(provider, "set_context", None)
            if callable(configure):
                configure(topic="news" if route == QueryRoute.CURRENT else "")
        self._provider_failures = {}
        stop_reason = "达到最大搜索轮次"
        completed_rounds = 0
        traces: list[RoundTrace] = []
        total_results = 0

        # Agent 的关键不是固定执行 N 次，而是每轮都根据新证据决定是否继续。
        for round_number in range(1, self.policy.max_rounds + 1):
            round_started = time.perf_counter()
            completed_rounds = round_number
            notify("search", f"正在执行第 {round_number} 轮搜索：{len(queries)} 个查询…")
            search_results = self._search_queries(queries, active_providers)
            retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for item in search_results:
                item.retrieved_at = item.retrieved_at or retrieved_at
                item.freshness_status = (
                    "dated" if item.published_at else "unknown"
                ) if route == QueryRoute.CURRENT else "not_required"
            total_results += len(search_results)
            if self._used_search_fallback:
                notify("search", "演示模式正在使用 Mock 数据；它不会被视为真实来源。")
            candidates = []
            round_urls: set[str] = set()
            by_url: dict[str, SearchResult] = {}
            for result in search_results:
                canonical = self._canonical_url(result.url)
                if canonical in known_urls:
                    continue
                if canonical not in round_urls:
                    round_urls.add(canonical)
                    by_url[canonical] = result
                    candidates.append(result)
                else:
                    # 同一 URL 可能覆盖多个子查询；保留查询来源，供充分性评估使用。
                    existing = by_url[canonical]
                    if result.query and result.query not in existing.query:
                        existing.query = f"{existing.query}；{result.query}".strip("；")
            # 先去重，再按相关性、权威性和域名多样性挑选有限候选。
            slots = max(0, self.policy.max_sources - len(sources))
            fresh_results = self.ranker.rank(candidates, ranking_question, plan.subquestions, slots)
            known_urls.update(self._canonical_url(result.url) for result in fresh_results)
            notify("search", f"第 {round_number} 轮找到 {len(search_results)} 条结果，新增 {len(fresh_results)} 条")
            notify("fetch", f"正在并发抓取 {len(fresh_results)} 个网页正文…")
            new_sources = self._fetch_results(fresh_results)
            for source in new_sources:
                self.ranker.score_source(source, ranking_question)
            relevant = getattr(self.ranker, "is_relevant_source", lambda source: True)
            new_sources = [source for source in new_sources if relevant(source)]
            sources.extend(new_sources)
            stagnant_rounds = stagnant_rounds + 1 if not new_sources else 0
            for index, source in enumerate(sources, 1):
                source.source_id = index
            mock_count = sum(source.provider == "mock" for source in new_sources)
            success = sum(source.fetched and source.provider != "mock" for source in new_sources)
            summary_count = len(new_sources) - success - mock_count
            if mock_count:
                notify("fetch", f"来源读取完成：在线正文 {success} 个，Mock 模拟内容 {mock_count} 个，仅摘要 {summary_count} 个")
            else:
                notify("fetch", f"抓取完成：成功 {success} 个，降级使用摘要 {summary_count} 个")
            logger.info("轮次完成 round=%d new=%d total=%d", round_number, len(new_sources), len(sources))

            decision = self.planner.evaluate(plan, sources, round_number, len(new_sources), stagnant_rounds)
            notify("evaluate", decision.reason)
            traces.append(RoundTrace(
                round_number=round_number,
                queries=list(queries),
                results_found=len(search_results),
                new_sources=len(new_sources),
                fetched_sources=success,
                duration_seconds=round(time.perf_counter() - round_started, 3),
                sufficient=decision.sufficient,
                decision=decision.reason,
                used_fallback=self._used_search_fallback,
                missing_dimensions=list(decision.missing),
                next_queries=list(decision.next_queries),
            ))
            if decision.sufficient:
                stop_reason = decision.reason
                break
            if round_number < self.policy.max_rounds:
                # 下一轮查询围绕“缺失维度”生成，而不是机械重复原问题。
                queries = decision.next_queries or self.planner.adjust_queries(plan, decision.missing, round_number)
                notify("adjust", f"信息不够，正在补充搜索：{'; '.join(queries[:3])}")
            if len(sources) >= self.policy.max_sources:
                stop_reason = "已达到来源数量上限"
                break

        if not self.policy.mock_search:
            sources = [source for source in sources if source.provider != "mock"]
            for index, source in enumerate(sources, 1):
                source.source_id = index
        if not sources:
            raise SearchUnavailableError(
                "没有取得真实来源。请检查网络或搜索 API 配置；在线研究不会使用 Mock 内容代替。"
            )

        # 搜索循环结束后，先组织证据和矛盾，再交给报告器生成内容。
        notify("verify", "正在去重、交叉验证并检查矛盾…")
        evidence, conflicts = self.verifier.organize(sources)
        notify("generate", "正在生成结构化报告…")
        set_progress = getattr(self.reporter, "set_progress", None)
        if callable(set_progress):
            set_progress(notify)
        report = self.reporter.generate(plan, sources, evidence, conflicts, completed_rounds, traces)
        notify("validate", "正在校验引用与问题覆盖度…")
        validation = self.validator.validate(report, sources, plan)
        if not validation.valid:
            # 只允许一次有针对性的模型修订；在线模式不再回退成抽取式伪报告。
            logger.warning("首次报告校验未通过 issues=%s", validation.issues)
            revise = getattr(self.reporter, "revise", None)
            if callable(revise):
                report = revise(report, validation.issues, plan, sources)
            else:
                report = self.validator.repair(report, sources, plan)
            validation = self.validator.validate(report, sources, plan)
        if not validation.valid:
            raise ReportQualityError("报告未通过引用与内容质量门：" + "；".join(validation.issues))
        metrics = ResearchMetrics(
            elapsed_seconds=round(time.perf_counter() - started, 3),
            search_results=total_results,
            fetched_sources=sum(source.fetched and source.provider != "mock" for source in sources),
            failed_sources=sum(not source.fetched for source in sources),
            cache_hits=self.cache.stats.hits - initial_hits,
            cache_misses=self.cache.stats.misses - initial_misses,
        )
        # 评分独立于报告生成，便于未来替换为更严格的评测器或 LLM Judge。
        scorecard = self.quality_evaluator.evaluate(plan, sources, validation, metrics, len(traces))
        notify("score", f"研究质量 {scorecard.overall}/100 · {scorecard.grade}")
        path = self.storage.save(question, report, plan.brief.report.output_format)
        notify("saved", f"报告已保存：{path}")
        review_summary = tuple(getattr(self.reporter, "last_review_summary", ()))
        return ResearchResult(
            question, report, path, sources, completed_rounds, plan, stop_reason, validation,
            traces, metrics, scorecard, self._direct_answer(report), review_summary,
            dict(self._provider_failures),
        )

    def follow_up(self, previous: ResearchResult, question: str, progress: Progress | None = None) -> ResearchResult:
        """优先复用已有证据；只有上下文不足时才发起新的网络研究。"""

        notify = progress or (lambda phase, message: None)
        known_text = previous.report + "\n" + "\n".join(source.usable_text for source in previous.sources)
        known_terms = self.planner.terms(known_text)
        follow_terms = self.planner.terms(question)
        overlap = len(known_terms & follow_terms)
        coverage = overlap / max(1, len(follow_terms))
        freshness_markers = ("最新", "最近", "今天", "当前", "变化", "更新", "新闻", "公告", "数据")
        needs_fresh_evidence = any(marker in question.lower() for marker in freshness_markers)
        direct_context_request = any(marker in question.lower() for marker in (
            "为什么", "展开第一点", "展开第二点", "展开第三点", "上面的来源", "上述来源",
        ))
        # 只有已有证据真正覆盖追问且不要求新时效时才复用；否则重新进入完整反馈循环。
        has_reusable_evidence = bool(previous.sources) and any(source.usable_text for source in previous.sources)
        if has_reusable_evidence and not needs_fresh_evidence and (
            direct_context_request
            or (coverage >= 0.6 and overlap >= min(2, max(1, len(follow_terms))))
        ):
            notify("follow_up", "已有报告包含相关信息，正在基于原来源整理追问答案…")
            plan = self.planner.plan(question, previous.plan.brief)
            evidence, conflicts = self.verifier.organize(previous.sources)
            report = self.reporter.generate(plan, previous.sources, evidence, conflicts, previous.rounds, previous.trace)
            validation = self.validator.validate(report, previous.sources, plan)
            if not validation.valid:
                revise = getattr(self.reporter, "revise", None)
                report = revise(report, validation.issues, plan, previous.sources) if callable(revise) else self.validator.repair(
                    report, previous.sources, plan
                )
                validation = self.validator.validate(report, previous.sources, plan)
            if not validation.valid:
                raise ReportQualityError("追问报告未通过质量门：" + "；".join(validation.issues))
            path = self.storage.save(question, report, plan.brief.report.output_format)
            scorecard = self.quality_evaluator.evaluate(plan, previous.sources, validation, previous.metrics, len(previous.trace))
            notify("saved", f"追问报告已保存：{path}")
            review_summary = tuple(getattr(self.reporter, "last_review_summary", ()))
            return ResearchResult(
                question, report, path, previous.sources, previous.rounds, plan,
                "复用已有研究上下文", validation, previous.trace, previous.metrics, scorecard,
                self._direct_answer(report), review_summary, dict(previous.provider_failures),
            )
        reason = "追问要求最新或补充证据" if needs_fresh_evidence else f"已有证据仅覆盖追问关键词的 {coverage:.0%}"
        notify("follow_up", f"{reason}，自动追加搜索并重新评估…")
        standalone = ContextResolver.standalone_query(previous.question, question)
        return self.research(question, progress, previous.plan.brief, search_query=standalone)

    def _search_queries(self, queries: list[str], providers: list[SearchProvider] | None = None) -> list[SearchResult]:
        """并发执行“搜索源 × 查询”的笛卡尔任务集合。"""

        self._used_search_fallback = False
        tasks = [(provider, query) for provider in (providers or self.providers) for query in queries]
        result_groups: dict[int, list[SearchResult]] = {}
        if tasks:
            with ThreadPoolExecutor(max_workers=min(8, len(tasks))) as executor:
                futures = {
                    executor.submit(self._search_one, provider, query): (index, provider, query)
                    for index, (provider, query) in enumerate(tasks)
                }
                for future in as_completed(futures):
                    try:
                        index, _, _ = futures[future]
                        result_groups[index] = future.result()
                    except Exception as exc:
                        index, provider, query = futures[future]
                        result_groups[index] = []
                        self._provider_failures[provider.name] = type(exc).__name__
                        logger.warning("搜索提供器异常 provider=%s error=%s", provider.name, type(exc).__name__)
        # 按任务创建顺序重组结果，避免线程完成顺序让报告随机漂移。
        results = [result for index in sorted(result_groups) for result in result_groups[index]]
        if not results and self.policy.mock_search and self.fallback is not None:
            logger.warning("演示模式无结果，使用 Mock 数据")
            self._used_search_fallback = True
            for query in queries:
                results.extend(self.fallback.search(query, self.policy.results_per_query))
        return results

    @staticmethod
    def _direct_answer(report: str) -> str:
        match = re.search(r"## (?:结论|摘要)\s+(.+?)(?=\n## |\Z)", report, flags=re.S)
        if not match:
            return ""
        text = re.sub(r"\s+", " ", match.group(1)).strip()
        return text[:800]

    def _search_one(self, provider: SearchProvider, query: str) -> list[SearchResult]:
        """执行一个搜索任务；在线结果优先读写缓存，Mock 不进入缓存。"""

        if provider.name == "mock" and not self.policy.mock_search:
            return []
        if provider.name != "mock":
            cached = self.cache.get_search(provider.name, query, self.policy.results_per_query)
            if cached is not None:
                return cached
        name = provider.name
        if self._provider_failure_counts.get(name, 0) >= 3:
            self._provider_failures[name] = "连续失败，已临时熔断"
            return []
        results: list[SearchResult] = []
        for _ in range(2):
            results = provider.search(query, self.policy.results_per_query)
            if results or not getattr(provider, "last_error", ""):
                break
        if results:
            self._provider_failure_counts[name] = 0
        else:
            self._provider_failure_counts[name] = self._provider_failure_counts.get(name, 0) + 1
            self._provider_failures[name] = str(getattr(provider, "last_error", "") or "未返回结果")
        if provider.name != "mock" and results:
            self.cache.set_search(provider.name, query, self.policy.results_per_query, results)
        return results

    def _fetch_results(self, results: list[SearchResult]) -> list[Source]:
        """混合缓存命中与新抓取结果，同时保持候选来源原有顺序。"""

        if not results:
            return []
        sources: list[Source | None] = [None] * len(results)
        missing, missing_indices = [], []
        for index, result in enumerate(results):
            cached = None if result.provider == "mock" else self.cache.get_source(result.url)
            if cached is None:
                missing.append(result)
                missing_indices.append(index)
            else:
                cached.query = result.query
                sources[index] = cached
        fetched = self.fetcher.fetch_all(missing)
        for index, source in zip(missing_indices, fetched, strict=False):
            sources[index] = source
            self.cache.set_source(source)
        return [source for source in sources if source is not None]

    @staticmethod
    def _canonical_url(url: str) -> str:
        """去掉查询串、片段和尾部斜杠，用于跨搜索源 URL 去重。"""

        if url.startswith("mock://"):
            return url
        parts = urlsplit(url)
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))
