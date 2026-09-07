"""回收站：查看、恢复或永久删除从资料库移除的本地资产。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from deepsearch.infrastructure.storage import FileArtifactTrash
from deepsearch.presentation.web.styles import render_page_header
from deepsearch.presentation.web.support import get_settings


def build_trash(reports_dir: str, conversation_dir: str) -> FileArtifactTrash:
    """使用与资料库页面相同的目录约束创建回收站管理器。"""

    conversations = Path(conversation_dir)
    return FileArtifactTrash(
        conversations.parent / "trash",
        (Path(reports_dir), conversations.parent / "artifacts"),
    )


def restore_item(trashed_path: str, reports_dir: str, conversation_dir: str) -> None:
    """恢复按钮回调：不覆盖同名文件，并把执行结果留到 rerun 后展示。"""

    try:
        restored = build_trash(reports_dir, conversation_dir).restore(Path(trashed_path))
    except (OSError, ValueError) as exc:
        st.session_state.trash_error = f"无法恢复：{exc}"
    else:
        # 资料库列表有短时缓存；恢复后清除它可让文件立即重新出现。
        st.cache_data.clear()
        st.session_state.pop("trash_selected", None)
        st.session_state.trash_success = f"已恢复到资料库：{restored.name}"


def permanently_delete_item(trashed_path: str, reports_dir: str, conversation_dir: str) -> None:
    """永久删除按钮回调，只接收当前列表中已经验证过的资产路径。"""

    try:
        build_trash(reports_dir, conversation_dir).delete(Path(trashed_path))
    except (OSError, ValueError) as exc:
        st.session_state.trash_error = f"无法永久删除：{exc}"
    else:
        st.session_state.pop("trash_selected", None)
        st.session_state.trash_success = "已永久删除，文件无法恢复。"
    finally:
        # 永久删除确认框同样运行在 dialog fragment 中；通知弹窗函数
        # 发起整页 rerun，避免文件已删除但确认框仍停留。
        st.session_state.trash_close_delete_dialog = True


@st.dialog("永久删除", icon=":material/delete_forever:")
def confirm_permanent_delete(
    trashed_path: str,
    item_name: str,
    reports_dir: str,
    conversation_dir: str,
) -> None:
    """永久删除前要求独立二次确认，避免把可恢复操作误当成删除。"""

    if st.session_state.pop("trash_close_delete_dialog", False):
        st.rerun()

    st.error(f"确定永久删除“{item_name}”吗？此操作无法撤销。")
    st.caption("只会删除当前选中的回收站文件及其恢复元数据。")
    with st.container(horizontal=True, horizontal_alignment="right"):
        st.button(
            "确认永久删除",
            type="primary",
            icon=":material/delete_forever:",
            on_click=permanently_delete_item,
            args=(trashed_path, reports_dir, conversation_dir),
        )


def display_moved_at(value: str) -> str:
    """把 ISO 时间转成紧凑本地时间；旧元数据异常时保留原文本。"""

    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value or "未知时间"


settings = get_settings()
reports_dir = str(settings.reports_dir)
conversation_dir = str(settings.conversation_dir)
trash = build_trash(reports_dir, conversation_dir)

render_page_header(
    "TRASH",
    "回收站",
    "从资料库移除的报告会保留在这里；你可以恢复，也可以在二次确认后永久删除。",
)
if message := st.session_state.pop("trash_success", ""):
    st.toast(message, icon=":material/check_circle:")
if message := st.session_state.pop("trash_error", ""):
    st.error(message, icon=":material/error:")

items = trash.list_items()
if not items:
    st.info("回收站为空。", icon=":material/delete:")
    st.stop()

summary = pd.DataFrame([{
    "文件": item["name"],
    "格式": str(item["format"]).upper(),
    "移入时间": display_moved_at(str(item["moved_at"])),
    "大小（KB）": item["size_kb"],
    "原位置": item["original_path"],
} for item in items])
st.dataframe(summary, hide_index=True, key="trash_table")

items_by_id = {str(item["id"]): item for item in items}
selected_id = st.selectbox(
    "选择文件",
    list(items_by_id),
    format_func=lambda item_id: (
        f"{display_moved_at(str(items_by_id[item_id]['moved_at']))} · "
        f"{items_by_id[item_id]['name']}"
    ),
    key="trash_selected",
)
item = items_by_id[selected_id]
trashed_path = Path(str(item["trashed_path"]))

with st.container(horizontal=True, horizontal_alignment="right"):
    st.download_button(
        f"下载 {str(item['format']).upper()}",
        data=trashed_path.read_bytes(),
        file_name=str(item["name"]),
        mime={
            "md": "text/markdown",
            "txt": "text/plain",
            "json": "application/json",
        }.get(str(item["format"]), "application/octet-stream"),
        icon=":material/download:",
    )
    st.button(
        "恢复到资料库",
        icon=":material/restore_from_trash:",
        on_click=restore_item,
        args=(str(trashed_path), reports_dir, conversation_dir),
    )
    if st.button("永久删除", icon=":material/delete_forever:"):
        confirm_permanent_delete(
            str(trashed_path),
            str(item["name"]),
            reports_dir,
            conversation_dir,
        )
