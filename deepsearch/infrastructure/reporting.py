"""把研究证据综合为结论优先的 Markdown 报告。"""

from __future__ import annotations

import json
import re
from datetime import datetime

from .llm import LanguageModel
from ..application.errors import ReportQualityError
from ..application.prompts import (
    DRAFT_SYSTEM,
    DRAFT_USER,
    EVIDENCE_SYSTEM,
    EVIDENCE_USER,
    REPAIR_SYSTEM,
    REPAIR_USER,
    REVIEW_SYSTEM,
    REVIEW_USER,
)
from ..application.verification import canonicalize_source_section, source_table
from ..domain.models import Confidence, EvidenceGroup, QuestionType, RoundTrace, SearchPlan, Source


class MarkdownReporter:
    """先综合结论，再给证据与来源；确定性模板仅供显式演示模式使用。"""

    def __init__(self, llm: LanguageModel | None = None) -> None:
        self.llm = llm
        self.last_review_summary: tuple[str, ...] = ()
        self._progress = lambda phase, message: None

    def set_progress(self, callback) -> None:
        self._progress = callback

    def generate(
        self,
        plan: SearchPlan,
        sources: list[Source],
        evidence: list[EvidenceGroup],
        conflicts: list[str],
        rounds: int,
        trace: list[RoundTrace] | None = None,
    ) -> str:
        """按研究要求生成报告；模型失败时明确中止，不生成伪报告。"""

        trace = trace or []
        self.last_review_summary = ()
        if self.llm is not None:
            # 每个来源最多注入 4000 字符，避免单页吞噬全部模型上下文。
            source_context = "\n\n".join(
                f"[{source.source_id}] {source.title}\nURL: {source.url}\n{source.usable_text[:4000]}"
                for source in sources
            )
            specification = plan.brief.report
            stage = "准备模型输入"
            try:
                stage = "证据整理"
                self._progress("evidence_map", "正在把来源整理为结论、引用、冲突与证据缺口…")
                evidence_raw = self._generate_json(
                    EVIDENCE_SYSTEM,
                    EVIDENCE_USER.format(
                        question=plan.question,
                        objective=plan.brief.objective,
                        subquestions="；".join(plan.subquestions),
                        sources=source_context,
                    ),
                )
                evidence_map = self._parse_json(evidence_raw, "证据整理")
                evidence_text = json.dumps(evidence_map, ensure_ascii=False, indent=2)
                stage = "初稿生成"
                self._progress("draft", "正在按结论优先的结构生成初稿…")
                draft = self.llm.generate(
                    DRAFT_SYSTEM,
                    DRAFT_USER.format(
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
                        evidence_map=evidence_text,
                        sources=source_context,
                    ),
                )
                stage = "独立审校"
                self._progress("review", "正在独立检查直接性、重复、引用和排版并重写…")
                reviewed_raw = self._generate_json(
                    REVIEW_SYSTEM,
                    REVIEW_USER.format(
                        question=plan.question,
                        evidence_map=evidence_text,
                        sources=source_context,
                        draft=draft,
                    ),
                )
                reviewed = self._parse_json(reviewed_raw, "独立审校")
                body = str(reviewed.get("final_report", "")).strip()
                if not body:
                    raise ReportQualityError("独立审校没有返回终稿")
                self.last_review_summary = tuple(
                    str(item) for item in reviewed.get("issues", []) if str(item).strip()
                )
                return self._ensure_header(body, plan, sources, rounds)
            except ReportQualityError:
                raise
            except Exception as exc:
                detail = str(exc).strip() or type(exc).__name__
                raise ReportQualityError(f"三阶段报告生成失败（{stage}）：{detail}") from exc
        return self._deterministic_report(plan, sources, evidence, conflicts, rounds, trace)

    def revise(self, report: str, issues: list[str], plan: SearchPlan, sources: list[Source]) -> str:
        """确定性校验失败时执行唯一一次有针对性的模型修订。"""

        if self.llm is None:
            return report
        source_context = "\n".join(
            f"[{source.source_id}] {source.title} | {source.url} | {self._excerpt(source.usable_text, 800)}"
            for source in sources
        )
        repaired = self.llm.generate(
            REPAIR_SYSTEM,
            REPAIR_USER.format(
                question=plan.question,
                issues="；".join(issues),
                sources=source_context,
                report=report,
            ),
        ).strip()
        if not repaired:
            raise ReportQualityError("模型没有返回修订后的报告")
        self.last_review_summary = (*self.last_review_summary, "已根据确定性校验结果完成一次针对性修订")
        # 来源索引来自系统已经掌握的 Source，而不是模型推断；即使模型在
        # 修订时误删来源表，也应由确定性代码恢复可追溯清单。
        return self._ensure_sources(repaired, sources)

    @staticmethod
    def _parse_json(value: str, stage: str) -> dict:
        clean = value.strip().lstrip("\ufeff")
        # 小型思考模型偶尔会在合法 JSON 前后附加 <think> 或说明文字。
        # 优先直接解析；失败后只提取第一个完整 JSON 对象，不尝试猜改字段。
        try:
            parsed = json.loads(clean)
        except json.JSONDecodeError as direct_error:
            without_thinking = re.sub(r"<think>.*?</think>", "", clean, flags=re.I | re.S).strip()
            start = without_thinking.find("{")
            if start < 0:
                raise ReportQualityError(f"{stage}没有返回有效 JSON") from direct_error
            try:
                parsed, _ = json.JSONDecoder().raw_decode(without_thinking[start:])
            except json.JSONDecodeError as exc:
                raise ReportQualityError(f"{stage}没有返回有效 JSON") from exc
        if not isinstance(parsed, dict):
            raise ReportQualityError(f"{stage}返回格式不是 JSON 对象")
        return parsed

    def _generate_json(self, system: str, user: str) -> str:
        """在适配器支持时启用结构化输出，否则保持通用文本协议。"""

        generate_json = getattr(self.llm, "generate_json", None)
        if callable(generate_json):
            return generate_json(system, user)
        return self.llm.generate(system, user)

    @staticmethod
    def _ensure_header(body: str, plan: SearchPlan, sources: list[Source], rounds: int) -> str:
        if body.lstrip().startswith("# "):
            report = body.strip()
        else:
            timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
            report = (
                f"# {plan.question}\n\n> 生成时间：{timestamp}\n"
                f"> 搜索轮次：{rounds} 轮 | 有效来源：{len(sources)} 个\n\n{body.strip()}"
            )
        return MarkdownReporter._ensure_sources(report, sources)

    @staticmethod
    def _ensure_sources(report: str, sources: list[Source]) -> str:
        """用已知来源重建清单，避免模型漏项或输出冗长裸链接。"""

        return canonicalize_source_section(report, sources, heading="参考来源")

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
            "## 结论",
            "",
        ]

        if claims:
            if plan.question_type == QuestionType.FACT:
                # 事实题第一句话必须直接给答案；后续句子只补必要背景。
                direct = claims[: min(2, len(claims))]
                lines.append("综合现有证据，" + " ".join(self._claim_text(*item[:2]) for item in direct))
            else:
                lines.append(f"围绕“{plan.question}”，演示证据支持以下结论：")
                lines.append("")
                for index, (statement, ids, _) in enumerate(claims[:4], 1):
                    lines.append(f"- **发现 {index}**：{statement} {self._citations(ids)}")
        else:
            lines.append("当前没有获得可用证据，不能形成可靠总结。请恢复网络、调整搜索源或扩大研究预算后重试。")

        lines.extend(["", "## 关键发现", ""])
        if claims:
            for index, (statement, ids, confidence) in enumerate(claims[:5], 1):
                lines.append(
                    f"{index}. **{self._conclusion_label(plan, index)}**：{statement} "
                    f"{self._citations(ids)}  _{confidence.value}_"
                )
        else:
            lines.append("1. 证据不足，当前不能给出负责任的事实结论。")

        lines.extend(["", "## 分析", ""])
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
                independent = {item.url for item in selected if item.provider != "mock"}
                confidence = Confidence.VERIFIED if len(independent) >= 2 else Confidence.SINGLE
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

        lines.extend(["", "## 局限", ""])
        if mocked:
            lines.append("- 本报告包含 Mock 模拟来源，只能验证 Agent 工作流与报告结构，不能替代实时事实调研。")
        if any(not source.fetched and source.provider != "mock" for source in sources):
            lines.append("- 部分页面只取得搜索摘要，关键决策前应打开原文核对上下文。")
        lines.extend([f"- {item}" for item in conflicts] or ["- 当前来源中未自动识别出明确数字冲突；这不代表不存在时间或统计口径差异。"])

        known_sections = {"结论", "关键发现", "分析", "局限", "参考来源", "全部来源"}
        for section in specification.sections:
            if section.strip() and section.strip() not in known_sections:
                lines.extend(["", f"## {section.strip()}", ""])
                if claims:
                    statement, ids, _ = claims[0]
                    lines.append(f"根据当前证据，该章节最相关的发现是：{statement} {self._citations(ids)}")
                else:
                    lines.append("当前没有足够证据填充此自定义章节。")

        lines.extend(["", source_table(sources, heading="参考来源"), ""])
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
