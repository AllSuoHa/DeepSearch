"""Streamlit 多页面应用入口。

这里只负责页面注册、全局主题注入和侧栏状态；研究业务全部位于
``deepsearch.application``，页面不直接访问搜索或存储实现。
"""

from __future__ import annotations

import streamlit as st

from deepsearch import __version__
from deepsearch.presentation.web.support import get_settings, init_session_state, reset_research
from deepsearch.presentation.web.styles import inject_app_css

# 页面配置必须是第一个 Streamlit 调用，避免浏览器首屏闪烁。
st.set_page_config(
    page_title="DeepSearch research workspace",
    page_icon=":material/travel_explore:",
    layout="wide",
    initial_sidebar_state="auto",
)

# 每次 Streamlit rerun 都会从头执行入口，因此初始化必须是幂等的。
init_session_state()
settings = get_settings()
inject_app_css()

# st.navigation 是唯一导航源；app_pages 文件保持“直接脚本”结构。
pages = [
    st.Page("app_pages/home.py", title="首页", icon=":material/home:", default=True),
    st.Page("app_pages/research.py", title="研究", icon=":material/travel_explore:"),
    st.Page("app_pages/evidence.py", title="证据", icon=":material/fact_check:"),
    st.Page("app_pages/history.py", title="报告", icon=":material/article:"),
    st.Page("app_pages/settings.py", title="设置", icon=":material/tune:"),
]
page = st.navigation(pages, position="top")

with st.sidebar:
    # 侧栏仅展示全局状态和新建研究入口，不承载报告主体。
    st.logo("assets/logo-mark.svg", size="large", link=None)
    st.subheader("DeepSearch")
    st.caption("可追溯的深度研究")
    with st.container(horizontal=True):
        st.badge("离线 Mock" if settings.mode == "mock" else "在线优先", color="violet" if settings.mode == "mock" else "green")
        st.badge("LLM 已配置" if not settings.mock_llm else "确定性报告器", color="blue" if not settings.mock_llm else "gray")
    last_result = st.session_state.get("last_result")
    if last_result is not None:
        st.caption(f"最近报告：{last_result.report_path.name}")
        st.caption(f"{last_result.rounds} 轮 · {len(last_result.sources)} 个来源 · {last_result.metrics.elapsed_seconds:.1f} 秒")
    if st.button("开始新研究", icon=":material/add:", width="stretch"):
        reset_research()
        st.switch_page("app_pages/research.py")
    st.space("small")
    st.caption(f"v{__version__} · Autonomous research")

# 运行当前路由页面。各页面共享上面已经初始化的 Session State。
page.run()
