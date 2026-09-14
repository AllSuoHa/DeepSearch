"""证据交叉验证与报告引用校验。"""

from __future__ import annotations

import re
from urllib.parse import urlsplit
from collections import defaultdict

from ..domain.models import Confidence, EvidenceGroup, QuestionType, SearchPlan, Source, ValidationResult


class CrossVerifier:
    """按主题聚合相似证据，并保守标记多来源数值分歧。"""

    sentence_pattern = re.compile(r"(?<=[。！？.!?])\s+")

    def organize(self, sources: list[Source]) -> tuple[list[EvidenceGroup], list[str]]:
        """返回证据组和可能冲突；不尝试凭空裁决哪个数字正确。"""

        groups: dict[str, list[tuple[int, str]]] = defaultdict(list)
        real_ids = {source.source_id for source in sources if source.provider != "mock"}
        for source in sources:
            for sentence in self.sentence_pattern.split(source.usable_text[:2000])[:8]:
                clean = sentence.strip()
                key = self._topic_key(clean)
                if len(clean) >= 20 and key:
                    groups[key].append((source.source_id, clean))
        evidence, conflicts = [], []
        for items in groups.values():
            ids = sorted({item[0] for item in items})
            independent_ids = [source_id for source_id in ids if source_id in real_ids]
            numbers = {number for _, sentence in items for number in re.findall(r"\b\d+(?:\.\d+)?%?\b", sentence)}
            # 同一主题来自多个来源且数字不同，只提示口径冲突，不武断选边。
            if len(independent_ids) > 1 and len(numbers) > 1:
                conflicts.append(f"来源 {', '.join(f'[{item}]' for item in independent_ids)} 对相关数值给出不同表述，需结合原文口径核对。")
                confidence = Confidence.CONFLICT
            else:
                confidence = Confidence.VERIFIED if len(independent_ids) > 1 else Confidence.SINGLE
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
        if "## 参考来源" not in report and "## 全部来源" not in report:
            issues.append("缺少参考来源清单")
        if "## 结论" not in report:
            issues.append("缺少必需章节：## 结论")
        # 简单事实题只需直接答案、引用和来源清单；强制扩写“分析”会让
        # 本应简洁的回答因形式问题失败。复杂研究仍保留分析章节约束。
        if plan.question_type != QuestionType.FACT and "## 分析" not in report:
            issues.append("缺少必需章节：## 分析")
        report_terms = self._terms(report)
        question_terms = self._terms(plan.question)
        for index, subquestion in enumerate(plan.subquestions, 1):
            focus_terms = self._terms(subquestion) - question_terms
            if focus_terms and not (focus_terms & report_terms):
                issues.append(f"未覆盖第 {index} 个子问题：{subquestion}")
        unsupported = self._unsupported_detail_claims(report, sources)
        if unsupported:
            issues.append(f"有 {len(unsupported)} 条详细论点与所引来源缺少明显文本对应")
        if any(source.provider != "mock" for source in sources) and self._has_repeated_content(report):
            issues.append("报告存在大段重复内容")
        if self._contains_template_noise(report):
            issues.append("报告混入网页菜单、语言切换或模板噪声")
        return ValidationResult(not issues, issues)

    def _unsupported_detail_claims(self, report: str, sources: list[Source]) -> list[str]:
        """拦截“引用存在，但引用正文与论点明显无关”的简单幻觉。"""

        by_id = {source.source_id: source for source in sources}
        section = ""
        checked_sections = {"## 结论", "## 关键发现", "## 分析"}
        unsupported = []
        for line in report.splitlines():
            if line.startswith("## "):
                section = line
            clean_line = line.strip()
            if section not in checked_sections or not clean_line or clean_line.startswith(("#", "|", ">")):
                continue
            ids = [int(value) for value in self.citation_pattern.findall(line)]
            if not ids or any(source_id not in by_id for source_id in ids):
                continue
            claim_terms = self._terms(self.citation_pattern.sub("", line))
            source_terms = set().union(*(self._terms(by_id[source_id].usable_text) for source_id in ids))
            if len(claim_terms) >= 4 and len(claim_terms & source_terms) / len(claim_terms) < 0.10:
                unsupported.append(line[:160])
        return unsupported

    @staticmethod
    def _has_repeated_content(report: str) -> bool:
        body = report.split("## 参考来源", 1)[0].split("## 全部来源", 1)[0]
        seen: set[str] = set()
        for block in re.split(r"\n\s*\n", body):
            block = re.sub(r"^#{1,6}\s+.*$", "", block, flags=re.MULTILINE)
            normalized = re.sub(r"\W+", "", block).lower()
            if len(normalized) < 70:
                continue
            if normalized in seen:
                return True
            seen.add(normalized)
        return False

    @staticmethod
    def _contains_template_noise(report: str) -> bool:
        body = report.split("## 参考来源", 1)[0].lower()
        markers = ("skip to content", "切换语言", "select language", "cookie settings", "返回顶部")
        return any(marker in body for marker in markers)

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
        if "## 参考来源" not in report and "## 全部来源" not in report:
            report += "\n\n" + source_table(sources, heading="参考来源")
        return report.strip() + "\n"


_SOURCE_SECTION_PATTERN = re.compile(r"(?ms)^##\s+(?:参考来源|全部来源)\s*$.*\Z")
_OUTER_MARKDOWN_FENCE_PATTERN = re.compile(
    r"\A\s*```(?:markdown|md)?[^\S\r\n]*\r?\n(?P<body>.*)\r?\n```[^\S\r\n]*\s*\Z",
    re.IGNORECASE | re.DOTALL,
)
_DOCUMENT_TITLE_PATTERN = re.compile(r"\A#(?!#)\s+[^\r\n]+(?:\r?\n+|\Z)")
_SECOND_LEVEL_HEADING_PATTERN = re.compile(r"(?m)^##\s+([^\r\n]+?)\s*$")


def normalize_markdown_document(report: str) -> str:
    """移除模型偶尔添加的整篇 Markdown 代码围栏。"""

    normalized = report.strip()
    match = _OUTER_MARKDOWN_FENCE_PATTERN.fullmatch(normalized)
    return match.group("body").strip() if match else normalized


def report_body_without_source_section(report: str) -> str:
    """返回适合正文视图的报告内容，来源仍保留在持久化文档中。"""

    normalized = normalize_markdown_document(report)
    match = _SOURCE_SECTION_PATTERN.search(normalized)
    return normalized[: match.start()].rstrip() if match else normalized


def report_body_for_conversation(report: str) -> str:
    """压缩聊天视图中的文档式标题，完整结构仍保留在下载和资料库中。"""

    body = report_body_without_source_section(report)
    without_title = _DOCUMENT_TITLE_PATTERN.sub("", body, count=1).lstrip()
    visible = without_title or body
    headings = list(_SECOND_LEVEL_HEADING_PATTERN.finditer(visible))
    if len(headings) == 1 and headings[0].group(1).strip() in {"结论", "摘要"}:
        # 简单事实报告只有一个结论时，章节标题会让普通回答显得像大号文档。
        # 会话中直接展示正文即可，下载文件仍保留标准报告结构。
        compact = visible[headings[0].end():].lstrip()
        return compact or visible
    return visible


def canonicalize_source_section(report: str, sources: list[Source], heading: str = "参考来源") -> str:
    """用可信的 ``Source`` 数据替换模型生成的来源区。

    来源标题和 URL 不应由模型自由排版：模型常会输出很长的裸链接，也可能
    漏掉状态信息。统一替换后，旧报告在页面重绘时也能获得一致的可点击样式。
    """

    # 某些模型即使被要求返回 Markdown 全文，仍会在最外层加代码围栏。
    # 若不先解包，页面会显示带复制按钮的代码块，来源区也无法被正确替换。
    normalized = normalize_markdown_document(report)
    match = _SOURCE_SECTION_PATTERN.search(normalized)
    body = normalized[: match.start()].rstrip() if match else normalized
    return f"{body}\n\n{source_table(sources, heading=heading)}".strip() + "\n"


def source_table(sources: list[Source], heading: str = "全部来源") -> str:
    """生成紧凑、可点击且不暴露冗长裸 URL 的来源卡片。"""

    lines = [f"## {heading}", ""]
    if not sources:
        return "\n".join([*lines, "> 暂无可用来源。"])

    for source in sources:
        title = _escape_markdown_label(source.title.strip() or f"来源 {source.source_id}")
        host = urlsplit(source.url).hostname or "无可访问链接"
        host = host.removeprefix("www.")
        if source.provider == "mock":
            status = "模拟来源"
        else:
            status = "已读取正文" if source.fetched else "搜索摘要"
        score = round(source.quality_score * 100)
        metadata = f"`{host}` · {status}"
        if source.provider:
            metadata += f" · Provider `{source.provider}`"
        if score > 0:
            metadata += f" · 质量 {score}/100"
        timing = (
            f"发布日期/更新：{source.published_at or '未知'} · "
            f"检索时间：{source.retrieved_at or '未知'} · 时效：{source.freshness_status}"
        )
        query = " ".join(source.query.split())[:240] or "未记录"
        if source.url.startswith(("http://", "https://")):
            destination = source.url.replace("<", "%3C").replace(">", "%3E").replace(" ", "%20")
            title_markup = f"[{title}](<{destination}>)"
        else:
            title_markup = title
        lines.extend([
            f"> **[{source.source_id}] {title_markup}**  ",
            f"> {metadata}  ",
            f"> 查询：{query}  ",
            f"> {timing}",
            "",
        ])
    return "\n".join(lines).rstrip()


def _escape_markdown_label(value: str) -> str:
    """转义链接标签，避免来源标题破坏 Markdown 结构。"""

    return re.sub(r"([\\\[\]])", r"\\\1", value).replace("\n", " ")
