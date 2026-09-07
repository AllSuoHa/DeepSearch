"""全部对话记录：搜索、分页、恢复与安全删除本地会话。"""

from __future__ import annotations

from datetime import datetime
from math import ceil
from pathlib import Path

import streamlit as st

from deepsearch.infrastructure.storage import ConversationStore
from deepsearch.presentation.web.styles import render_page_header
from deepsearch.presentation.web.support import get_settings, load_conversation, reset_research

PAGE_SIZE = 10


def format_updated_at(value: str) -> str:
    """把 ISO 时间转换为易读的本地时间，异常旧数据保留原文本。"""

    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value or "未知时间"


def delete_conversation_record(conversation_id: str, conversation_dir: str) -> None:
    """删除单个会话文件，并在删除当前会话时同步清空页面上下文。"""

    try:
        deleted = ConversationStore(Path(conversation_dir)).delete(conversation_id)
        if not deleted:
            raise ValueError("该记录已不存在")
    except (OSError, ValueError) as exc:
        st.session_state.conversation_delete_error = f"无法删除记录：{exc}"
    else:
        if st.session_state.get("current_conversation_id") == conversation_id:
            reset_research()
        st.session_state.pop("conversation-pagination", None)
        st.session_state.conversation_delete_success = "对话记录已删除，资料库中的报告仍然保留。"
    finally:
        # dialog 内按钮只会触发 fragment rerun；由确认框读取标记后整页刷新。
        st.session_state.conversation_close_delete_dialog = True


@st.dialog("删除对话记录", icon=":material/delete:")
def confirm_delete_conversation(
    conversation_id: str,
    title: str,
    conversation_dir: str,
) -> None:
    """二次确认会话删除，并明确说明不会连带删除报告资产。"""

    if st.session_state.pop("conversation_close_delete_dialog", False):
        st.rerun()

    st.warning(f"确定删除对话“{title}”吗？删除后无法恢复。")
    st.caption("这里只删除聊天记录，不会删除资料库中的搜索快照或研究报告。")
    with st.container(horizontal=True, horizontal_alignment="right"):
        st.button(
            "确认删除记录",
            type="primary",
            icon=":material/delete_forever:",
            on_click=delete_conversation_record,
            args=(conversation_id, conversation_dir),
        )


settings = get_settings()
store = ConversationStore(settings.conversation_dir)

render_page_header(
    "CONVERSATIONS",
    "全部记录",
    "查找并继续以前的对话，或删除不再需要的本地会话记录。",
)

if message := st.session_state.pop("conversation_delete_success", ""):
    st.toast(message, icon=":material/check_circle:")
if message := st.session_state.pop("conversation_delete_error", ""):
    st.error(message, icon=":material/error:")

with st.form("conversation-filter", border=False):
    with st.container(horizontal=True, vertical_alignment="bottom"):
        query = st.text_input("搜索记录", placeholder="按对话标题搜索")
        st.form_submit_button("搜索", icon=":material/search:")

records = store.list(limit=None)
lowered_query = query.strip().casefold()
if lowered_query:
    records = [record for record in records if lowered_query in str(record["title"]).casefold()]

if not records:
    st.info("没有找到对话记录。", icon=":material/history:")
    st.stop()

st.caption(f"共 {len(records)} 条记录 · 每页 {PAGE_SIZE} 条")
page_count = max(1, ceil(len(records) / PAGE_SIZE))
page_number = st.pagination(page_count, key="conversation-pagination", width="content")
page_start = (page_number - 1) * PAGE_SIZE

for record in records[page_start : page_start + PAGE_SIZE]:
    conversation_id = str(record["id"])
    title = str(record["title"])
    with st.container(border=True, key=f"conversation-record-{conversation_id}"):
        st.markdown(f"**{title}**")
        st.caption(
            f"{record['message_count']} 条消息 · "
            f"更新于 {format_updated_at(str(record['updated_at']))}"
        )
        with st.container(horizontal=True, horizontal_alignment="right"):
            if st.button(
                "打开对话",
                icon=":material/chat:",
                key=f"open-conversation-{conversation_id}",
            ):
                if load_conversation(conversation_id):
                    st.switch_page("app_pages/home.py")
                else:
                    st.error("记录不存在或已经损坏。", icon=":material/error:")
            if st.button(
                "删除记录",
                icon=":material/delete:",
                key=f"delete-conversation-{conversation_id}",
            ):
                confirm_delete_conversation(
                    conversation_id,
                    title,
                    str(settings.conversation_dir),
                )
