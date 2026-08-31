"""Web 展示层的共享视觉函数。

主题颜色和字体优先放在 ``.streamlit/config.toml``；这里只加载少量
布局增强 CSS，并封装跨页面复用的标题和质量评分组件。
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def inject_app_css() -> None:
    """从独立资源文件注入布局 CSS，避免在各页面散落样式字符串。"""

    css = (PROJECT_ROOT / "assets" / "app.css").read_text(encoding="utf-8")
    st.html(f"<style>{css}</style>")


def render_page_header(kicker: str, title: str, subtitle: str) -> None:
    """渲染全站一致的页面眉题、主标题和说明文字。"""

    st.markdown(f"**:violet[{kicker}]**")
    st.title(title)
    st.caption(subtitle)
    st.space("small")


def render_scorecard(scorecard) -> None:
    """用总分、分项进度和建议展示可解释的研究质量。"""

    with st.container(key="scorecard", border=True):
        lead, detail = st.columns([1, 2.5], gap="large", vertical_alignment="center")
        with lead:
            st.caption("综合研究质量")
            st.metric("质量评分", f"{scorecard.overall}/100", scorecard.grade, label_visibility="collapsed")
            st.caption("评分用于暴露证据风险，不替代人工事实审查。")
        with detail:
            for dimension in scorecard.dimensions:
                st.progress(dimension.score, text=f"{dimension.name}  ·  {dimension.score}")
        if scorecard.recommendations:
            with st.expander("改进建议", icon=":material/lightbulb:"):
                for recommendation in scorecard.recommendations:
                    st.markdown(f"- {recommendation}")
