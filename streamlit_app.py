"""Streamlit 多页面应用入口。

这里只负责页面注册、全局主题注入和侧栏状态；研究业务全部位于
``deepsearch.application``，页面不直接访问搜索或存储实现。
"""

from __future__ import annotations

import streamlit as st

from deepsearch import __version__
from deepsearch.presentation.web.support import (
    get_settings, init_session_state, load_conversation, recent_conversations, reset_research,
)
from deepsearch.presentation.web.styles import ASSISTANT_ICON, inject_app_css

# 页面配置必须是第一个 Streamlit 调用，避免浏览器首屏闪烁。
st.set_page_config(
    page_title="DeepSearch · 个人 AI 助手",
    page_icon=ASSISTANT_ICON,
    layout="wide",
    initial_sidebar_state="auto",
)

# 每次 Streamlit rerun 都会从头执行入口，因此初始化必须是幂等的。
init_session_state()
settings = get_settings()
inject_app_css()

# st.navigation 是唯一导航源；app_pages 文件保持“直接脚本”结构。
pages = {
    "": [st.Page("app_pages/home.py", title="对话", icon=":material/chat:", default=True)],
    "工具": [
        st.Page("app_pages/conversations.py", title="全部记录", icon=":material/history:"),
        st.Page("app_pages/history.py", title="资料库", icon=":material/folder_open:"),
        st.Page("app_pages/trash.py", title="回收站", icon=":material/delete:"),
        st.Page("app_pages/tasks.py", title="自动任务", icon=":material/event_repeat:"),
        st.Page("app_pages/settings.py", title="设置", icon=":material/settings:"),
    ],
}
page = st.navigation(pages, position="sidebar", expanded=True)

with st.sidebar:
    # 侧栏只承载导航、会话切换与轻量状态。
    st.logo(
        ASSISTANT_ICON,
        size="large",
        link=None,
        icon_image=ASSISTANT_ICON,
    )
    st.caption("个人 AI 搜索与研究助手")
    if st.button("新对话", icon=":material/edit_square:", width="stretch", type="primary"):
        reset_research()
        st.switch_page("app_pages/home.py")
    st.caption("最近记录")
    conversations = recent_conversations(6)
    if conversations:
        for conversation in conversations:
            if st.button(
                str(conversation["title"]),
                key=f"conversation-{conversation['id']}",
                width="stretch",
            ):
                if load_conversation(str(conversation["id"])):
                    st.switch_page("app_pages/home.py")
    else:
        st.caption("开始第一次搜索后会显示在这里")
    st.page_link(
        "app_pages/conversations.py",
        label="查看全部记录",
        icon=":material/history:",
        width="stretch",
    )
    with st.container(horizontal=True):
        st.badge("演示数据" if settings.mock_search else "在线", color="violet" if settings.mock_search else "green")
        st.badge("模型可用" if not settings.mock_llm else "仅搜索", color="blue" if not settings.mock_llm else "gray")
    st.space("small")
    st.caption(f"v{__version__} · 本地优先")

# 运行当前路由页面。各页面共享上面已经初始化的 Session State。
page.run()
