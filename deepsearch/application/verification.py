"""证据交叉验证与报告引用校验。"""

from __future__ import annotations

import re
from collections import defaultdict

from ..domain.models import Confidence, EvidenceGroup, SearchPlan, Source, ValidationResult


class CrossVerifier:
    """按主题聚合相似证据，并保守标记多来源数值分歧。"""

    sentence_pattern = re.compile(r"(?<=[。！？.!?])\s+")

    def organize(self, sources: list[Source]) -> tuple[list[EvidenceGroup], list[str]]:
        """返回证据组和可能冲突；不尝试凭空裁决哪个数字正确。"""

        groups: dict[str, list[tuple[int, str]]] = defaultdict(list)
        for source in sources:
            for sentence in self.sentence_pattern.split(source.usable_text[:2000])[:8]:
                clean = sentence.strip()
                key = self._topic_key(clean)
                if len(clean) >= 20 and key:
                    groups[key].append((source.source_id, clean))
        evidence, conflicts = [], []
        for items in groups.values():
            ids = sorted({item[0] for item in items})
            numbers = {number for _, sentence in items for number in re.findall(r"\b\d+(?:\.\d+)?%?\b", sentence)}
            # 同一主题来自多个来源且数字不同，只提示口径冲突，不武断选边。
            if len(ids) > 1 and len(numbers) > 1:
                conflicts.append(f"来源 {', '.join(f'[{item}]' for item in ids)} 对相关数值给出不同表述，需结合原文口径核对。")
                confidence = Confidence.CONFLICT
            else:
                confidence = Confidence.VERIFIED if len(ids) > 1 else Confidence.SINGLE
            evidence.append(EvidenceGroup(items[0][1], ids, confidence))
        return evidence[:20], conflicts[:10]

    @staticmethod
    def _topic_key(sentence: str) -> str:
        words = re.findall(r"[a-zA-Z0-9]{3,}|[\u4e00-\u9fff]{2,}", sentence.lower())
        return "|".join(sorted(set(words))[:3])


class AnswerValidator:
    """在交付前检查报告结构、引用编号和论点—来源文本对应关系。"""

    citation_pattern = re.compile(r"\[(\d+)]")

    def validate(self, report: str, sources: list[Source], plan: SearchPlan) -> ValidationResult:
        """执行确定性校验，返回全部问题而不是遇到首错就停止。"""

        issues = []
        valid_ids = {source.source_id for source in sources}
        cited = {int(value) for value in self.citation_pattern.findall(report)}
        invalid = sorted(cited - valid_ids)
        if invalid:
            issues.append(f"存在无效引用编号: {invalid}")
        if sources and not cited:
            issues.append("报告没有引用任何来源")
        if "## 全部来源" not in report:
            issues.append("缺少全部来源清单")
        if "## 摘要" not in report or "## 详细分析" not in report:
            issues.append("报告结构不完整")
        for index, subquestion in enumerate(plan.subquestions, 1):
            if f"### {index}." not in report:
                issues.append(f"未显式覆盖第 {index} 个子问题：{subquestion}")
        unsupported = self._unsupported_detail_claims(report, sources)
        if unsupported:
            issues.append(f"有 {len(unsupported)} 条详细论点与所引来源缺少明显文本对应")
        return ValidationResult(not issues, issues)

    def _unsupported_detail_claims(self, report: str, sources: list[Source]) -> list[str]:
        """拦截“引用存在，但引用正文与论点明显无关”的简单幻觉。"""

        by_id = {source.source_id: source for source in sources}
        section = ""
        unsupported = []
        for line in report.splitlines():
            if line.startswith("## "):
                section = line
            if section != "## 详细分析" or not line.lstrip().startswith("- "):
                continue
            ids = [int(value) for value in self.citation_pattern.findall(line)]
            if not ids or any(source_id not in by_id for source_id in ids):
                continue
            claim_terms = self._terms(self.citation_pattern.sub("", line))
            source_terms = set().union(*(self._terms(by_id[source_id].usable_text) for source_id in ids))
            if claim_terms and len(claim_terms & source_terms) / len(claim_terms) < 0.12:
                unsupported.append(line[:160])
        return unsupported

    @staticmethod
    def _terms(text: str) -> set[str]:
        terms = set(re.findall(r"[a-zA-Z0-9_.+-]{2,}", text.lower()))
        for phrase in re.findall(r"[\u4e00-\u9fff]{2,}", text):
            terms.update(phrase[index:index + 2] for index in range(len(phrase) - 1))
        return terms

    def repair(self, report: str, sources: list[Source], plan: SearchPlan) -> str:
        """移除不存在的引用，并在缺失时补上完整来源表。"""

        valid_ids = {source.source_id for source in sources}
        report = self.citation_pattern.sub(lambda match: match.group(0) if int(match.group(1)) in valid_ids else "", report)
        if "## 全部来源" not in report:
            report += "\n\n" + source_table(sources)
        return report.strip() + "\n"


def source_table(sources: list[Source]) -> str:
    lines = ["## 全部来源", "", "| 编号 | 标题 | 链接 | 质量分 | 状态 |", "|---|---|---|---:|---|"]
    for source in sources:
        if source.provider == "mock":
            status = "🧪 Mock 模拟内容"
        else:
            status = "✅ 正文抓取成功" if source.fetched else "⚠️ 仅使用搜索摘要"
        score = round(source.quality_score * 100)
        lines.append(f"| [{source.source_id}] | {source.title.replace('|', '/')} | {source.url.replace('|', '%7C')} | {score} | {status} |")
    return "\n".join(lines)
