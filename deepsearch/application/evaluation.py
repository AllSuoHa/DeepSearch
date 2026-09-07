"""研究结果质量评估器。

评分是可解释的工程信号，用来暴露证据风险；它不宣称能够自动证明
事实绝对正确，也不能替代专业领域的人工审核。
"""

from __future__ import annotations

from urllib.parse import urlsplit

from ..domain.models import QualityDimension, ResearchMetrics, ResearchScorecard, SearchPlan, Source, ValidationResult


class ResearchQualityEvaluator:
    """用稳定的五维规则为每份报告生成可解释 scorecard。"""

    def evaluate(
        self,
        plan: SearchPlan,
        sources: list[Source],
        validation: ValidationResult,
        metrics: ResearchMetrics,
        trace_count: int,
    ) -> ResearchScorecard:
        """计算综合分、等级、各维度说明以及低分项改进建议。"""

        real_sources = [source for source in sources if source.provider != "mock"]
        total = max(1, len(real_sources))
        usable = sum(bool(source.usable_text) for source in real_sources)
        domains = {self._domain(source.url) for source in real_sources}
        source_quality = round(sum(source.quality_score for source in real_sources) / total * 100)
        grounding = (100 if validation.valid else max(0, 100 - 18 * len(validation.issues))) if real_sources else 0
        coverage = (
            100 if validation.valid else min(
                90,
                round(30 + 16 * len(plan.subquestions) + (10 if trace_count >= plan.minimum_rounds else 0)),
            )
        ) if real_sources else 0
        diversity_target = max(2, min(5, len(real_sources)))
        diversity = min(100, round(len(domains) / diversity_target * 100)) if real_sources else 0
        retrieval = round(usable / total * 100) if real_sources else 0

        # 维度必须能由现有结构化数据计算，保证离线测试可重复。
        dimensions = (
            QualityDimension("引用完整性", grounding, "检查无效引用、遗漏来源和未被证据支持的详细论点。"),
            QualityDimension("问题覆盖度", coverage, f"覆盖 {len(plan.subquestions)} 个研究子问题，并执行 {trace_count} 轮充分性判断。"),
            QualityDimension("来源多样性", diversity, f"{len(real_sources)} 个真实来源来自 {len(domains)} 个独立域名；Mock 不计入验证。"),
            QualityDimension("来源质量", source_quality, "综合权威域名、正文完整度、可抓取性与内容相关性。"),
            QualityDimension("检索健康度", retrieval, f"{usable}/{len(real_sources)} 个真实来源具有可用正文或摘要。"),
        )
        # 引用与覆盖直接影响报告可信度，因此权重高于检索健康度。
        weights = (0.30, 0.22, 0.18, 0.20, 0.10)
        overall = round(sum(item.score * weight for item, weight in zip(dimensions, weights, strict=True)))
        grade = "卓越" if overall >= 90 else "可靠" if overall >= 78 else "可用" if overall >= 65 else "需复核"
        # 只展示真正需要改进的维度，避免给高质量报告制造无效噪声。
        recommendations = tuple(
            text
            for score, text in (
                (grounding, "修复引用或补充能直接支持结论的证据。"),
                (diversity, "增加不同机构与独立域名，降低单一来源偏差。"),
                (source_quality, "优先检索官方文档、论文、标准与一手数据。"),
                (retrieval, "提高正文抓取成功率，减少仅依赖搜索摘要。"),
            )
            if score < 75
        )
        return ResearchScorecard(overall, grade, dimensions, recommendations)

    @staticmethod
    def _domain(url: str) -> str:
        if url.startswith("mock://"):
            return urlsplit(url).netloc or "mock"
        return urlsplit(url).netloc.lower().removeprefix("www.")
