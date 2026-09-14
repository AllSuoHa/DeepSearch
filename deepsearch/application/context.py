"""显式解析全新任务、普通对话和真正追问的上下文语义。"""

from __future__ import annotations

import re

from ..domain.models import ContextPolicy, ResearchResult


_FRESHNESS_MARKERS = ("最新", "今天", "今日", "当前", "现在", "重新", "再查", "重新查询", "实时")
_REFERENCE_MARKERS = (
    "为什么", "展开", "继续", "上面", "上述", "前面", "第二点", "第一点", "第三点",
    "其中", "这个结论", "这些来源", "来源可靠吗", "与另一个", "和另一个", "相比呢",
    "详细说说", "具体一点", "还有呢", "它们", "这个方案", "该结论",
)


class ContextResolver:
    """只在语言明确指向上一结果时复用研究，避免 ``last_result`` 污染新问题。"""

    @classmethod
    def resolve(
        cls,
        previous: ResearchResult | None,
        question: str,
        requested: ContextPolicy,
    ) -> ContextPolicy:
        if requested == ContextPolicy.FRESH or previous is None:
            return ContextPolicy.FRESH
        if cls._normalized(previous.question) == cls._normalized(question):
            return ContextPolicy.FRESH
        if any(marker in question.casefold() for marker in _FRESHNESS_MARKERS):
            return ContextPolicy.FRESH
        if requested == ContextPolicy.FOLLOW_UP:
            return ContextPolicy.FOLLOW_UP
        return ContextPolicy.FOLLOW_UP if cls.is_referential(question) else ContextPolicy.FRESH

    @staticmethod
    def is_referential(question: str) -> bool:
        text = " ".join(question.split()).casefold()
        # “量子力学”“Python”等短问题同样可能是独立主题，长度本身不能
        # 证明它在指代上一结果；只接受可解释的指代或承接表达。
        return any(marker in text for marker in _REFERENCE_MARKERS)

    @classmethod
    def standalone_query(cls, previous_question: str, follow_up: str) -> str:
        """把常见指代改成围绕原主题的独立查询，而非用分号机械拼接两句话。"""

        detail = " ".join(follow_up.split()).strip(" ，。！？?!")
        replacements = {
            "为什么": "原因与依据",
            "展开第二点": "第二点的详细依据",
            "展开第一点": "第一点的详细依据",
            "展开第三点": "第三点的详细依据",
            "上面的来源可靠吗": "来源可靠性与一手依据",
            "上述来源可靠吗": "来源可靠性与一手依据",
        }
        for marker, replacement in replacements.items():
            if detail == marker:
                detail = replacement
                break
        detail = re.sub(r"^(?:请)?(?:继续|展开|说明|分析)?(?:上面|上述|前面|其中|这个结论|这些来源)[的：:\s]*", "", detail)
        detail = detail or "补充依据"
        return f"{previous_question.strip()}：{detail}"

    @staticmethod
    def _normalized(value: str) -> str:
        return re.sub(r"[\s，。！？、,.!?：:；;]+", "", value).casefold()
