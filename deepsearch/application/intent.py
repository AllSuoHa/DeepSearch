"""工作模式识别：显式搜索/研究优先，其余请求由智能判断分流。"""

from __future__ import annotations

import re

from ..domain.models import WorkMode


class IntentClassifier:
    """用可解释关键词完成低成本路由；直接回答不需要用户手动选择。"""

    search_markers = (
        "链接", "网址", "官网", "官方文档", "资源", "哪里看", "在哪里", "在哪看", "在线观看",
        "下载", "找一下", "搜索", "查询", "导航", "价格", "天气", "新闻", "最新", "今天",
        "当前", "现在", "实时", "状态", "website", "link", "where to watch",
    )
    research_markers = (
        "论文", "期刊", "文献", "学术", "比较", "对比", "调研", "研究", "论证", "证据",
        "报告", "综述", "分析", "评估", "方案", "深入", "多来源", "paper", "journal", "research", "literature review",
    )
    strong_research_markers = (
        "调研", "研究", "论证", "报告", "综述", "方案", "深入", "多来源", "research", "literature review",
    )
    chat_markers = (
        "你好", "您好", "嗨", "哈喽", "hello", "hi", "早上好", "下午好", "晚上好",
        "谢谢", "感谢", "辛苦了", "再见", "拜拜", "你是谁", "你叫什么", "你能做什么",
        "有什么功能", "怎么用", "介绍一下自己", "聊聊天", "闲聊", "讲个笑话", "无聊",
        "最近怎么样", "晚安", "你喜欢", "心情", "陪我聊", "how are you",
    )
    direct_answer_markers = (
        "翻译", "译成", "translate", "计算", "算一下", "等于多少", "是多少",
        "解释一下", "什么意思", "是什么", "为什么", "怎么写", "怎么做",
    )
    local_time_markers = (
        "几点", "时间", "几号", "日期", "星期几", "周几", "what time", "current time",
    )

    def resolve(self, question: str, requested: WorkMode | str = WorkMode.AUTO) -> WorkMode:
        """返回最终工作模式；只有搜索和研究可以被用户显式强制。"""

        mode = requested if isinstance(requested, WorkMode) else WorkMode(str(requested))
        if mode in {WorkMode.SEARCH, WorkMode.RESEARCH}:
            return mode
        lowered = question.strip().lower()
        # 强研究意图优先；只有“比较/分析”等弱信号与价格、新闻等实时
        # 搜索信号冲突时，才让搜索接管，避免为简单事实启动完整研究。
        if self._contains_any(lowered, self.strong_research_markers):
            return WorkMode.RESEARCH
        # “现在几点”虽然包含“现在”，但本地时钟比网页检索更直接可靠，
        # 所以它必须先于“现在/当前”等实时搜索信号判定。
        if self.is_local_time_request(lowered):
            return WorkMode.CHAT
        search_score = sum(self._contains(lowered, marker) for marker in self.search_markers)
        research_score = sum(self._contains(lowered, marker) for marker in self.research_markers)
        # 实时性和资源查找信号比“是什么/为什么”等通用问句形式更具体。
        # 先判断搜索，避免“今天天气是什么”被通用问句词误判为直接回答。
        if search_score:
            return WorkMode.SEARCH
        if research_score:
            return WorkMode.RESEARCH
        if self._contains_any(lowered, self.direct_answer_markers) or self._contains_any(lowered, self.chat_markers):
            return WorkMode.CHAT
        # 没有实时性、找链接或深度研究信号时直接回答，避免把简单问题
        # 因“鼓励搜索/研究”的产品文案而无端复杂化。
        return WorkMode.CHAT

    @classmethod
    def is_local_time_request(cls, question: str) -> bool:
        """识别可由本地时钟可靠回答的时间/日期问题。"""

        lowered = question.strip().lower()
        has_time_word = cls._contains_any(lowered, cls.local_time_markers)
        has_current_word = cls._contains_any(
            lowered,
            ("现在", "当前", "此刻", "今天", "今日", "now", "current", "today"),
        )
        return has_time_word and has_current_word

    @classmethod
    def _contains_any(cls, text: str, markers: tuple[str, ...]) -> bool:
        return any(cls._contains(text, marker) for marker in markers)

    @staticmethod
    def _contains(text: str, marker: str) -> bool:
        """英文短词按词边界匹配，避免 hi 误命中 this。"""

        if marker.isascii():
            return re.search(rf"(?<!\w){re.escape(marker)}(?!\w)", text) is not None
        return marker in text
