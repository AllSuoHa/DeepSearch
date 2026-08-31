"""确定性研究规划器：负责问题分类、拆解、充分性判断和查询调整。"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from ..domain.models import QuestionType, ResearchBrief, SearchPlan, Source, SufficiencyDecision


class ResearchPlanner:
    """不依赖 LLM 的规划下限，确保离线和测试环境也能完整运行。"""

    fact_markers = ("什么时候", "何时", "哪天", "多少", "是谁", "发布于", "what is", "when", "who")
    comparison_markers = ("对比", "比较", "区别", "优劣", "vs", "versus", "哪个更", "成本")
    research_markers = ("漏洞", "安全", "调研", "研究", "证据", "趋势", "现状", "报告")

    def plan(self, question: str, brief: ResearchBrief | None = None) -> SearchPlan:
        """根据问题和研究要求生成可执行的多维搜索计划。"""

        normalized = question.strip()
        brief = brief or ResearchBrief()
        lowered = normalized.lower()
        if any(marker in lowered for marker in self.comparison_markers):
            kind = QuestionType.COMPARISON
        elif any(marker in lowered for marker in self.research_markers):
            kind = QuestionType.RESEARCH
        elif len(normalized) <= 30 and any(marker in lowered for marker in self.fact_markers):
            kind = QuestionType.FACT
        elif len(normalized) <= 24:
            kind = QuestionType.FACT
        else:
            kind = QuestionType.EXPLORATION

        # 不同问题类型需要不同深度：简单事实尽快返回，复杂问题强制多角度覆盖。
        if kind == QuestionType.FACT:
            subquestions, minimum_rounds = [normalized], 1
        elif kind == QuestionType.COMPARISON:
            subquestions, minimum_rounds = [
                f"{normalized}：候选对象与评价标准",
                f"{normalized}：能力、优势与局限",
                f"{normalized}：成本、适用场景与选择建议",
            ], 2
        elif kind == QuestionType.RESEARCH:
            subquestions, minimum_rounds = [
                f"{normalized}：背景与定义",
                f"{normalized}：关键证据与多方观点",
                f"{normalized}：风险、限制与发展趋势",
            ], 2
        else:
            subquestions, minimum_rounds = [
                f"{normalized}：背景与核心概念",
                f"{normalized}：主要影响与实际案例",
                f"{normalized}：争议、限制与未来趋势",
            ], 2
        scope_hint = self._scope_hint(brief)
        queries = [f"{self._compact_query(item)} {scope_hint}".strip() for item in subquestions]
        return SearchPlan(
            normalized,
            kind,
            subquestions,
            queries,
            minimum_rounds,
            f"识别为 {kind.value}；围绕“{brief.domain}”覆盖 {len(subquestions)} 个信息维度，信息范围为 {brief.time_scope}。",
            brief,
        )

    def evaluate(self, plan: SearchPlan, sources: list[Source], round_number: int, newly_added: int, stagnant_rounds: int) -> SufficiencyDecision:
        """判断当前证据是否足够，并给出缺失维度和下一轮查询。"""

        usable = [source for source in sources if source.usable_text]
        if plan.question_type == QuestionType.FACT:
            enough = len(usable) >= 1
            return SufficiencyDecision(enough, "简单事实已有可用来源" if enough else "尚无可用事实来源", plan.subquestions if not enough else [])

        missing = []
        support_counts: dict[str, int] = {}
        base_terms = self.terms(plan.question)
        for subquestion in plan.subquestions:
            # 去掉每个子问题都会重复的原问题词，避免“看似覆盖全部维度”的假阳性。
            focus_terms = self.terms(subquestion) - base_terms
            query_terms = focus_terms or self.terms(subquestion)
            matches = sum(
                1
                for source in usable
                if query_terms & self.terms(f"{source.query} {source.title} {source.usable_text[:800]}")
            )
            support_counts[subquestion] = matches
            if matches < 2:
                missing.append(subquestion)
        # 复杂问题至少经历一次“搜索—反馈—调整”，避免第一轮证据偏差。
        if round_number < plan.minimum_rounds:
            reason = f"复杂问题至少需要 {plan.minimum_rounds} 轮以进行多角度核验"
            return SufficiencyDecision(False, reason, missing or plan.subquestions, self.adjust_queries(plan, missing, round_number))
        # 连续无新增意味着继续搜索的边际收益很低，应及时停止。
        if stagnant_rounds >= 2:
            detail = f"，仍有 {len(missing)} 个维度证据不足" if missing else ""
            return SufficiencyDecision(True, f"连续两轮没有新增有效来源，边际收益过低，停止搜索{detail}", missing)
        minimum_sources = min(6, max(3, len(plan.subquestions) * 2))
        origins = {
            urlsplit(source.url).netloc or source.provider
            for source in usable
        }
        diverse = len(origins) >= min(2, len(usable))
        sufficient = len(usable) >= minimum_sources and not missing and diverse
        if sufficient:
            reason = f"已获得 {len(usable)} 个来源、{len(origins)} 个独立来源域，并且每个信息维度至少有两条证据"
        else:
            reason = (
                f"现有 {len(usable)} 个来源、{len(origins)} 个来源域；"
                f"仍有 {len(missing)} 个维度缺少交叉证据"
            )
        return SufficiencyDecision(sufficient, reason, missing, [] if sufficient else self.adjust_queries(plan, missing, round_number))

    def adjust_queries(self, plan: SearchPlan, missing: list[str], round_number: int) -> list[str]:
        """首轮偏向一手资料，后续轮次主动寻找独立评测、局限和争议。"""

        targets = missing or plan.subquestions
        suffix = "官方文档 原始数据" if round_number == 1 else "独立评测 局限 争议 反方观点"
        scope_hint = self._scope_hint(plan.brief)
        return [f"{self._compact_query(item)} {suffix} {scope_hint}".strip() for item in targets]

    @staticmethod
    def _scope_hint(brief: ResearchBrief) -> str:
        """把用户定义的领域、信息类型和时间范围落实到每个查询。"""

        parts = [brief.domain, " ".join(brief.information_types), brief.time_scope]
        return " ".join(part.strip() for part in parts if part and part not in {"通用", "不限"})

    @staticmethod
    def _compact_query(text: str) -> str:
        return text.replace("：", " ").replace(":", " ").strip()

    @staticmethod
    def terms(text: str) -> set[str]:
        """提取英文词和中文双字片段，供轻量覆盖度与追问相关性判断。"""

        latin = re.findall(r"[a-zA-Z0-9_.+-]{2,}", text.lower())
        chinese = re.findall(r"[\u4e00-\u9fff]{2,}", text)
        chunks: list[str] = []
        for phrase in chinese:
            chunks.extend(phrase[index:index + 2] for index in range(max(1, len(phrase) - 1)))
        return set(latin + chunks)
