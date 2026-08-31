"""把研究证据综合为结论优先的 Markdown 报告。"""

from __future__ import annotations

import re
from datetime import datetime

from .llm import LanguageModel
from ..application.prompts import REPORT_SYSTEM, REPORT_USER
from ..application.verification import source_table
from ..domain.models import Confidence, EvidenceGroup, QuestionType, RoundTrace, SearchPlan, Source


class MarkdownReporter:
    """先综合结论，再给证据与来源；LLM 不可用时仍输出完整总结。"""

    def __init__(self, llm: LanguageModel | None = None) -> None:
        self.llm = llm

    def generate(
        self,
        plan: SearchPlan,
        sources: list[Source],
        evidence: list[EvidenceGroup],
        conflicts: list[str],
        rounds: int,
        trace: list[RoundTrace] | None = None,
    ) -> str:
        """按研究要求生成报告；模型失败时回退到证据驱动的确定性综合。"""

        trace = trace or []
        if self.llm is not None:
            # 每个来源最多注入 4000 字符，避免单页吞噬全部模型上下文。
            source_context = "\n\n".join(
                f"[{source.source_id}] {source.title}\nURL: {source.url}\n{source.usable_text[:4000]}"
                for source in sources
            )
            trace_context = "\n".join(
                f"第 {item.round_number} 轮：查询={'; '.join(item.queries)}；判断={item.decision}；下一步={'; '.join(item.next_queries) or '停止'}"
                for item in trace
            ) or "无结构化轨迹"
            specification = plan.brief.report
            try:
                body = self.llm.generate(
                    REPORT_SYSTEM,
                    REPORT_USER.format(
                        question=plan.question,
                        objective=plan.brief.objective,
                        domain=plan.brief.domain,
                        information_types="、".join(plan.brief.information_types),
                        time_scope=plan.brief.time_scope,
                        audience=specification.audience,
                        language=specification.language,
                        target_words=specification.target_words,
                        sections="、".join(specification.sections),
                        custom_instructions=specification.custom_instructions or "无",
                        subquestions="；".join(plan.subquestions),
                        rounds=rounds,
                        trace=trace_context,
                        sources=source_context,
                    ),
                )
                return self._ensure_header(body, plan, sources, rounds)
            except Exception:
                # 模型异常不能浪费已完成的检索、阅读和交叉验证。
                pass
        return self._deterministic_report(plan, sources, evidence, conflicts, rounds, trace)

    @staticmethod
    def _ensure_header(body: str, plan: SearchPlan, sources: list[Source], rounds: int) -> str:
        if body.lstrip().startswith("# "):
            return body.strip() + "\n"
        timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
        return (
            f"# {plan.question}\n\n> 生成时间：{timestamp}\n"
            f"> 搜索轮次：{rounds} 轮 | 有效来源：{len(sources)} 个\n\n{body.strip()}\n"
        )

    def _deterministic_report(
        self,
        plan: SearchPlan,
        sources: list[Source],
        evidence: list[EvidenceGroup],
        conflicts: list[str],
        rounds: int,
        trace: list[RoundTrace],
    ) -> str:
        """不用模型也要直接回答问题，而不是只输出链接和检索统计。"""

        timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
        fetched = sum(source.fetched and source.provider != "mock" for source in sources)
        mocked = sum(source.provider == "mock" for source in sources)
        specification = plan.brief.report
        target_words = max(300, min(5000, specification.target_words))
        detail_limit = 2 if target_words < 800 else 3 if target_words < 1800 else 5
        claims = self._claims(sources, evidence)
        mock_note = f" | Mock 来源：{mocked} 个" if mocked else ""

        lines = [
            f"# {plan.question}",
            "",
            f"> 生成时间：{timestamp}",
            f"> 搜索轮次：{rounds} 轮 | 有效来源：{len(sources)} 个 | 在线正文：{fetched} 个{mock_note}",
            f"> 研究领域：{plan.brief.domain} | 信息类型：{'、'.join(plan.brief.information_types)} | 时间范围：{plan.brief.time_scope}",
            f"> 交付要求：{specification.output_format} · 约 {target_words} 字 · 面向{specification.audience}",
            "",
            "## 摘要",
            "",
        ]

        if claims:
            if plan.question_type == QuestionType.FACT:
                # 事实题第一句话必须直接给答案；后续句子只补必要背景。
                direct = claims[: min(2, len(claims))]
                lines.append("综合现有证据，" + " ".join(self._claim_text(*item[:2]) for item in direct))
            else:
                lines.append(f"围绕“{plan.question}”，现有证据支持以下综合结论：")
                lines.append("")
                for index, (statement, ids, _) in enumerate(claims[:4], 1):
                    lines.append(f"- **发现 {index}**：{statement} {self._citations(ids)}")
        else:
            lines.append("当前没有获得可用证据，不能形成可靠总结。请恢复网络、调整搜索源或扩大研究预算后重试。")

        lines.extend(["", "## 关键结论", ""])
        if claims:
            for index, (statement, ids, confidence) in enumerate(claims[:5], 1):
                lines.append(
                    f"{index}. **{self._conclusion_label(plan, index)}**：{statement} "
                    f"{self._citations(ids)}  _{confidence.value}_"
                )
        else:
            lines.append("1. 证据不足，当前不能给出负责任的事实结论。")

        lines.extend(["", "## 详细分析", ""])
        for index, subquestion in enumerate(plan.subquestions, 1):
            # 子问题都含原问题前缀；移除公共部分，避免“成本”等主问题词污染所有章节的证据排序。
            focus = subquestion.replace(plan.question, "", 1).strip("：: ")
            selected = (self._related_sources(focus or subquestion, sources) or sources)[:detail_limit]
            lines.extend([f"### {index}. {subquestion}", ""])
            if selected:
                first = selected[0]
                lines.append(f"**综合判断**：{self._excerpt(first.usable_text, 260)} [{first.source_id}]")
                lines.append("")
                lines.append("**证据依据**：")
                lines.append("")
                for source in selected:
                    lines.append(f"- **{source.title}**：{self._excerpt(source.usable_text)} [{source.source_id}]")
                confidence = Confidence.VERIFIED if len({item.url for item in selected}) >= 2 else Confidence.SINGLE
                lines.extend(["", f"**本节可信度**：{confidence.value}（{len(selected)} 条可追溯证据）", ""])
            else:
                lines.extend(["尚无充分证据，不能可靠作答。", "", f"**本节可信度**：{Confidence.SINGLE.value}（0 条证据）", ""])

        if plan.question_type == QuestionType.COMPARISON:
            lines.extend(["### 对比表", "", "| 观察对象/来源 | 可验证发现 | 证据状态 |", "|---|---|---|"])
            for source in sources[: min(8, detail_limit * 2)]:
                status = "✅ 正文/可追溯" if source.fetched and source.provider != "mock" else "⚠️ 摘要或 Mock"
                lines.append(
                    f"| {source.title.replace('|', '/')} [{source.source_id}] | "
                    f"{self._excerpt(source.usable_text, 110).replace('|', '/')} | {status} |"
                )
            lines.append("")

        lines.extend(["## 建议与下一步", ""])
        recommendations = self._recommendations(plan, sources, conflicts, trace)
        lines.extend(f"- {item}" for item in recommendations)

        lines.extend(["", "## 证据局限与存在争议的信息", ""])
        if mocked:
            lines.append("- 本报告包含 Mock 模拟来源，只能验证 Agent 工作流与报告结构，不能替代实时事实调研。")
        if any(not source.fetched and source.provider != "mock" for source in sources):
            lines.append("- 部分页面只取得搜索摘要，关键决策前应打开原文核对上下文。")
        lines.extend([f"- {item}" for item in conflicts] or ["- 当前来源中未自动识别出明确数字冲突；这不代表不存在时间或统计口径差异。"])

        lines.extend(["", "## 研究过程", ""])
        if trace:
            lines.extend(["| 轮次 | 查询策略 | 新增来源 | 充分性评估 | 后续动作 |", "|---:|---|---:|---|---|"])
            for item in trace:
                queries = "；".join(item.queries[:3]).replace("|", "/")
                decision = item.decision.replace("|", "/")
                action = ("停止并生成报告" if item.sufficient else "；".join(item.next_queries[:2]) or "达到预算后停止").replace("|", "/")
                lines.append(f"| {item.round_number} | {queries} | {item.new_sources} | {decision} | {action} |")
        else:
            lines.append(f"完成 {rounds} 轮研究；当前结果未包含结构化轮次轨迹。")

        known_sections = {"摘要", "关键结论", "详细分析", "建议与下一步", "证据局限与争议", "证据局限与存在争议的信息", "研究过程", "全部来源"}
        for section in specification.sections:
            if section.strip() and section.strip() not in known_sections:
                lines.extend(["", f"## {section.strip()}", ""])
                if claims:
                    statement, ids, _ = claims[0]
                    lines.append(f"根据当前证据，该章节最相关的发现是：{statement} {self._citations(ids)}")
                else:
                    lines.append("当前没有足够证据填充此自定义章节。")

        if specification.custom_instructions:
            lines.extend([
                "",
                "## 报告要求执行情况",
                "",
                f"- 自定义要求：{specification.custom_instructions}",
                f"- 本报告以约 {target_words} 字为篇幅目标；确定性降级模式优先保证引用完整和章节覆盖。",
            ])

        lines.extend(["", source_table(sources), ""])
        return "\n".join(lines)

    def _claims(
        self,
        sources: list[Source],
        evidence: list[EvidenceGroup],
    ) -> list[tuple[str, list[int], Confidence]]:
        """从交叉验证结果和来源首句构造去重的可引用结论。"""

        claims: list[tuple[str, list[int], Confidence]] = []
        seen: set[str] = set()
        for item in evidence:
            statement = self._excerpt(item.statement, 240)
            if self._is_metadata_statement(statement):
                continue
            key = re.sub(r"\W+", "", statement.lower())[:120]
            if key and key not in seen:
                seen.add(key)
                claims.append((statement, item.source_ids, item.confidence))
        for source in sources:
            statement = self._excerpt(source.usable_text, 240)
            if self._is_metadata_statement(statement):
                continue
            key = re.sub(r"\W+", "", statement.lower())[:120]
            if key and key not in seen:
                seen.add(key)
                claims.append((statement, [source.source_id], Confidence.SINGLE))
        return claims

    @staticmethod
    def _claim_text(statement: str, ids: list[int]) -> str:
        return f"{statement} {MarkdownReporter._citations(ids)}"

    @staticmethod
    def _citations(ids: list[int]) -> str:
        return "".join(f"[{source_id}]" for source_id in sorted(set(ids)))

    @staticmethod
    def _conclusion_label(plan: SearchPlan, index: int) -> str:
        labels = {
            QuestionType.FACT: "直接答案",
            QuestionType.COMPARISON: "比较发现",
            QuestionType.EXPLORATION: "核心洞察",
            QuestionType.RESEARCH: "研究发现",
        }
        return f"{labels[plan.question_type]} {index}"

    @staticmethod
    def _recommendations(
        plan: SearchPlan,
        sources: list[Source],
        conflicts: list[str],
        trace: list[RoundTrace],
    ) -> list[str]:
        recommendations = []
        missing = trace[-1].missing_dimensions if trace else []
        if missing:
            recommendations.append(f"优先补充以下证据缺口：{'；'.join(missing[:3])}。")
        if conflicts:
            recommendations.append("对存在数值差异的来源逐条核对发布日期、统计口径和原始数据。")
        if any(source.provider == "mock" for source in sources):
            recommendations.append("在用于真实决策前切换到在线模式，并用正式来源重新运行同一研究计划。")
        if len({source.url for source in sources}) < 2:
            recommendations.append("增加至少一个独立来源，避免单一证据决定最终结论。")
        if not recommendations:
            recommendations.append("按报告中的关键结论行动前，优先复核高影响主张对应的原始来源。")
        recommendations.append(f"后续追问应围绕“{plan.question}”的具体结论提出；系统会判断复用证据还是追加检索。")
        return recommendations

    @staticmethod
    def _related_sources(subquestion: str, sources: list[Source]) -> list[Source]:
        terms = MarkdownReporter._semantic_terms(subquestion)
        scored = []
        for source in sources:
            source_terms = MarkdownReporter._terms(f"{source.title} {source.usable_text[:800]}")
            score = len(terms & source_terms)
            if score:
                scored.append((score, source.quality_score, -source.source_id, source))
        scored.sort(reverse=True, key=lambda item: item[:3])
        return [item[3] for item in scored]

    @staticmethod
    def _semantic_terms(text: str) -> set[str]:
        """为常见研究维度补少量中英概念别名，改进无模型模式的证据分配。"""

        terms = MarkdownReporter._terms(text)
        lowered = text.lower()
        aliases = {
            ("成本", "价格", "费用"): {"price", "pricing", "cost", "token", "cache", "batch"},
            ("能力", "推理", "性能", "评测", "优势", "局限"): {"benchmark", "reasoning", "performance", "capabilities", "quality", "latency", "swe", "livebench", "arena"},
            ("候选", "标准", "评价", "比较"): {"model", "pricing", "benchmark", "index", "compare"},
            ("发布", "日期", "什么时候", "何时"): {"release", "released", "date", "october"},
            ("新闻", "动态", "公告", "变化"): {"news", "announcement", "release", "update"},
            ("风险", "限制", "安全", "争议"): {"risk", "limitation", "security", "controversy"},
        }
        for markers, additions in aliases.items():
            if any(marker in lowered for marker in markers):
                terms.update(additions)
        return terms

    @staticmethod
    def _terms(text: str) -> set[str]:
        terms = set(re.findall(r"[a-zA-Z0-9_.+-]{2,}", text.lower()))
        for phrase in re.findall(r"[\u4e00-\u9fff]{2,}", text):
            terms.update(phrase[index:index + 2] for index in range(max(1, len(phrase) - 1)))
        return terms

    @staticmethod
    def _is_metadata_statement(statement: str) -> bool:
        lowered = statement.lower()
        return any(
            marker in lowered
            for marker in ("mock 搜索源", "验证完整工作流", "来源正文不可用")
        )

    @staticmethod
    def _excerpt(text: str, limit: int = 180) -> str:
        printable = "".join(character for character in text if character in "\n\t" or ord(character) >= 32)
        clean = re.sub(r"\s+", " ", printable).strip()
        if not clean:
            return "来源正文不可用，仅保留搜索摘要"
        sentence = re.split(r"(?<=[。！？.!?])\s+", clean, maxsplit=1)[0]
        return sentence[:limit].rstrip() + ("…" if len(sentence) > limit else "")
