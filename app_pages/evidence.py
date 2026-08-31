"""证据智能页面：审阅质量评分、搜索轨迹和抓取正文。"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from deepsearch.presentation.web.styles import render_page_header, render_scorecard
from deepsearch.presentation.web.support import source_rows, trace_rows

render_page_header(
    "Evidence intelligence",
    "证据智能",
    "从来源质量、检索轨迹和原始正文三个视角审阅报告，让研究过程经得起追问。",
)

result = st.session_state.get("last_result")
if result is None:
    # 证据页依赖当前会话中的研究结果，不在这里重新执行昂贵研究。
    st.info("完成一次研究后，这里会展示来源评分、搜索轨迹和原始证据。", icon=":material/info:")
    st.stop()

st.caption(result.question)
render_scorecard(result.scorecard)
with st.container(horizontal=True):
    st.badge("校验通过" if result.validation.valid else "需要复核", color="green" if result.validation.valid else "red")
    st.badge(result.plan.question_type.value, color="blue")
    st.badge(result.stop_reason, color="gray")

view = st.segmented_control(
    "查看内容",
    ["来源质量", "搜索轨迹", "正文预览"],
    default="来源质量",
    key="evidence_view",
    persist_state="session",
)

if view == "来源质量":
    # 表格用于精确查看，横向条形图用于快速比较质量和相关度。
    rows = source_rows(result)
    frame = pd.DataFrame(rows)
    st.dataframe(
        frame,
        hide_index=True,
        key="evidence_sources",
        column_config={
            "编号": st.column_config.NumberColumn("编号", width="small"),
            "标题": st.column_config.TextColumn("标题", pinned=True),
            "质量分": st.column_config.ProgressColumn("质量分", min_value=0, max_value=100),
            "相关度": st.column_config.ProgressColumn("相关度", min_value=0, max_value=100),
            "链接": st.column_config.LinkColumn("打开来源", display_text="访问"),
        },
    )
    if not frame.empty:
        chart = frame[["标题", "质量分", "相关度"]].set_index("标题")
        st.bar_chart(chart, horizontal=True, x_label="分数", y_label="来源")
elif view == "搜索轨迹":
    # RoundTrace 让用户看到每轮为什么继续或停止，而不是暴露模型思维链。
    rows = trace_rows(result)
    st.dataframe(pd.DataFrame(rows), hide_index=True, key="research_trace")
    for trace in result.trace:
        with st.container(border=True):
            st.markdown(f"**第 {trace.round_number} 轮** · {trace.duration_seconds:.2f} 秒")
            st.caption(trace.decision)
            st.code("\n".join(trace.queries), language=None)
else:
    # 正文预览只读，避免在展示层意外修改领域来源对象。
    options = {f"[{source.source_id}] {source.title}": source for source in result.sources}
    selected = st.selectbox("选择来源", list(options), key="source_preview")
    source = options[selected]
    with st.container(horizontal=True):
        st.badge(source.provider, color="violet")
        st.link_button("打开原文", source.url, icon=":material/open_in_new:")
    st.caption(f"查询：{source.query} · 质量分 {source.quality_score * 100:.0f} · 相关度 {source.relevance_score * 100:.0f}")
    st.text_area("抓取内容", source.usable_text[:20_000], height=420, disabled=True, key="source_text")
