"""产品首页：解释项目价值，并把问题和研究模式传递给研究工作台。"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from deepsearch.presentation.web.support import get_settings


def report_count(directory: Path) -> int:
    """首页只统计支持的研究资产，不读取完整内容。"""

    return sum(
        1 for path in directory.iterdir() if path.is_file() and path.suffix.lower() in {".md", ".txt", ".json"}
    ) if directory.exists() else 0


settings = get_settings()

# 首屏只保留一个主要动作：输入问题并开始研究。
with st.container(key="hero"):
    with st.container(horizontal_alignment="center"):
        st.markdown("**:violet[DEEP RESEARCH AGENT]**")
        st.title("从一个问题，得到一份可靠报告", text_alignment="center")
        st.markdown(
            "自动规划、检索、阅读和交叉验证，并为每条结论保留可追溯来源。",
            text_alignment="center",
        )
    st.space("small")
    with st.form("hero_search", border=False):
        question = st.text_input(
            "研究问题",
            placeholder="输入需要深入研究的问题…",
            label_visibility="collapsed",
        )
        mode, action = st.columns([2, 1], gap="medium", vertical_alignment="bottom")
        with mode:
            profile = st.segmented_control(
                "研究强度",
                ["快速", "均衡", "深度"],
                default="均衡",
                key="home_profile",
                width="stretch",
            )
        with action:
            submitted = st.form_submit_button(
                "开始研究",
                icon=":material/arrow_forward:",
                type="primary",
                width="stretch",
                key="hero-start",
            )
    st.caption("快速适合事实核查，均衡适合常规分析，深度适合技术选型与复杂决策。")

if submitted:
    # 页面之间通过 Session State 传递待研究问题，避免把用户输入放进 URL。
    if question.strip():
        st.session_state.pending_question = question.strip()
        st.session_state.research_profile = profile
        st.switch_page("app_pages/research.py")
    else:
        st.toast("请先输入研究问题。", icon=":material/edit:")

st.space("medium")
stats = st.columns(3)
stats[0].metric("引擎状态", "离线演示" if settings.mock_search else "在线可用")
stats[1].metric("单次来源上限", settings.max_sources)
stats[2].metric("已保存报告", report_count(settings.reports_dir))

st.space("medium")
st.subheader("完整研究，而不只是搜索")
st.caption("系统会在每一轮判断信息是否充分，只有达到停止条件后才生成报告。")
with st.container(key="feature-grid"):
    columns = st.columns(3)
    features = [
        (":material/account_tree:", "自主规划", "拆解复杂问题，并根据证据缺口调整下一轮查询。"),
        (":material/fact_check:", "证据可追溯", "正文、来源评分、引用校验和冲突提示集中呈现。"),
        (":material/monitoring:", "质量可解释", "用五个明确维度说明报告的优势和风险。"),
    ]
    for column, (icon, title, body) in zip(columns, features, strict=True):
        with column.container(border=True, height="stretch"):
            st.subheader(f"{icon} {title}")
            st.caption(body)

st.space("medium")
st.subheader("示例问题")
examples = {
    "Agent 框架": "对比当前主流 Agent 框架的架构、能力边界与生产适用性",
    "RAG 选型": "生成式 AI 应用的 RAG 与长上下文方案应如何选择？",
    "研究评估": "评估深度研究 Agent 的关键指标、常见失败模式与改进路径",
}
selected = st.pills("示例问题", list(examples), label_visibility="collapsed", key="home_example")
if selected:
    # 示例问题默认使用深度模式，便于首次体验完整迭代流程。
    st.session_state.pending_question = examples[selected]
    st.session_state.research_profile = "深度"
    st.switch_page("app_pages/research.py")
