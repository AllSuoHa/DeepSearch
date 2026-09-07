"""工作模式识别：显式选择优先，歧义请求保守地走搜索。"""

from __future__ import annotations

from ..domain.models import WorkMode


class IntentClassifier:
    """用可解释关键词完成低成本路由；显式用户选择永远优先。"""

    search_markers = (
        "链接", "网址", "官网", "资源", "哪里看", "在哪看", "在线观看", "下载", "找一下",
        "搜索", "查询", "导航", "website", "link", "where to watch",
    )
    research_markers = (
        "论文", "期刊", "文献", "学术", "比较", "对比", "调研", "研究", "论证", "证据",
        "报告", "综述", "分析", "评估", "为什么", "paper", "journal", "literature review",
    )

    def resolve(self, question: str, requested: WorkMode | str = WorkMode.AUTO) -> WorkMode:
        """返回最终工作模式；得分相同或没有信号时保守选择搜索。"""

        mode = requested if isinstance(requested, WorkMode) else WorkMode(str(requested))
        if mode != WorkMode.AUTO:
            return mode
        lowered = question.strip().lower()
        research_score = sum(marker in lowered for marker in self.research_markers)
        search_score = sum(marker in lowered for marker in self.search_markers)
        return WorkMode.RESEARCH if research_score > search_score and research_score > 0 else WorkMode.SEARCH
