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


def restore_items(trashed_paths: tuple[str, ...], reports_dir: str, conversation_dir: str) -> None:
    """逐项恢复表格选择；单项失败不会阻止其他有效文件恢复。"""

    trash = build_trash(reports_dir, conversation_dir)
    restored_names: list[str] = []
    errors: list[str] = []
    for trashed_path in trashed_paths:
        try:
            restored_names.append(trash.restore(Path(trashed_path)).name)
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
    # 选择属于旧表格行坐标；文件集合变化后必须清除，避免下一次 rerun
    # 把同一行号误认为另一份文件。
    st.session_state.pop("trash_table", None)
    if restored_names:
        st.cache_data.clear()
        st.session_state.trash_success = f"已恢复 {len(restored_names)} 份文件到资料库。"
    if errors:
        preview = "；".join(errors[:2])
        remainder = f"；另有 {len(errors) - 2} 项失败" if len(errors) > 2 else ""
        st.session_state.trash_error = f"部分文件无法恢复：{preview}{remainder}"


def permanently_delete_items(
    trashed_paths: tuple[str, ...],
    reports_dir: str,
    conversation_dir: str,
) -> None:
    """永久删除已验证的表格选择，并报告批量操作中的局部失败。"""

    trash = build_trash(reports_dir, conversation_dir)
    deleted_count = 0
    errors: list[str] = []
    for trashed_path in trashed_paths:
        try:
            trash.delete(Path(trashed_path))
            deleted_count += 1
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
    st.session_state.pop("trash_table", None)
    if deleted_count:
        st.session_state.trash_success = f"已永久删除 {deleted_count} 份文件，无法恢复。"
    if errors:
        preview = "；".join(errors[:2])
        remainder = f"；另有 {len(errors) - 2} 项失败" if len(errors) > 2 else ""
        st.session_state.trash_error = f"部分文件无法永久删除：{preview}{remainder}"
    # 永久删除确认框运行在 dialog fragment 中；通知弹窗函数发起整页
    # rerun，避免文件已删除但确认框仍停留。
    st.session_state.trash_close_delete_dialog = True


def request_permanent_delete(trashed_paths: tuple[str, ...], item_names: tuple[str, ...]) -> None:
    """在按钮回调阶段冻结选择，避免打开确认框的 rerun 丢失表格行状态。"""

    if trashed_paths and len(trashed_paths) == len(item_names):
        st.session_state.trash_pending_delete = {
            "paths": trashed_paths,
            "names": item_names,
        }


@st.dialog("永久删除", icon=":material/delete_forever:")
def confirm_permanent_delete(
    trashed_paths: tuple[str, ...],
    item_names: tuple[str, ...],
    reports_dir: str,
    conversation_dir: str,
) -> None:
    """永久删除前要求独立二次确认，避免把可恢复操作误当成删除。"""

    if st.session_state.pop("trash_close_delete_dialog", False):
        st.session_state.pop("trash_pending_delete", None)
        st.rerun()

    if len(item_names) == 1:
        st.error(f"确定永久删除“{item_names[0]}”吗？此操作无法撤销。")
    else:
        st.error(f"确定永久删除选中的 {len(item_names)} 份文件吗？此操作无法撤销。")
        st.caption("、".join(item_names[:3]) + (f" 等 {len(item_names)} 份" if len(item_names) > 3 else ""))
    st.caption("只会删除表格中已勾选的回收站文件及其恢复元数据。")
    with st.container(horizontal=True, horizontal_alignment="right"):
        st.button(
            "确认永久删除",
            type="primary",
            icon=":material/delete_forever:",
            on_click=permanently_delete_items,
            args=(trashed_paths, reports_dir, conversation_dir),
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
selection = st.dataframe(
    summary,
    hide_index=True,
    key="trash_table",
    on_select="rerun",
    selection_mode="multi-row",
    column_config={
        "文件": st.column_config.TextColumn("文件", pinned=True),
        "大小（KB）": st.column_config.NumberColumn("大小（KB）", format="%.1f"),
    },
)
selected_indices = [
    int(index)
    for index in selection.selection.rows
    if isinstance(index, int) and 0 <= index < len(items)
]
selected_items = [items[index] for index in selected_indices]
selected_paths = tuple(str(item["trashed_path"]) for item in selected_items)
selected_names = tuple(str(item["name"]) for item in selected_items)
selection_count = len(selected_items)

if selection_count == 0:
    st.caption("请在表格左侧勾选一份或多份文件。")
elif selection_count > 1:
    st.caption(f"已选择 {selection_count} 份文件；下载仅支持单选，恢复和永久删除支持批量操作。")

single_item = selected_items[0] if selection_count == 1 else None
single_path = Path(str(single_item["trashed_path"])) if single_item is not None else None

with st.container(horizontal=True, horizontal_alignment="right"):
    st.download_button(
        f"下载 {str(single_item['format']).upper()}" if single_item is not None else "下载（仅单选）",
        data=single_path.read_bytes() if single_path is not None else b"",
        file_name=str(single_item["name"]) if single_item is not None else "",
        mime={
            "md": "text/markdown",
            "txt": "text/plain",
            "json": "application/json",
        }.get(str(single_item["format"]), "application/octet-stream") if single_item is not None else "application/octet-stream",
        icon=":material/download:",
        disabled=single_item is None,
    )
    st.button(
        f"恢复到资料库（{selection_count}）" if selection_count > 1 else "恢复到资料库",
        icon=":material/restore_from_trash:",
        on_click=restore_items,
        args=(selected_paths, reports_dir, conversation_dir),
        disabled=selection_count == 0,
    )
    st.button(
        f"永久删除（{selection_count}）" if selection_count > 1 else "永久删除",
        icon=":material/delete_forever:",
        disabled=selection_count == 0,
        on_click=request_permanent_delete,
        args=(selected_paths, selected_names),
    )

pending_delete = st.session_state.get("trash_pending_delete")
if isinstance(pending_delete, dict):
    pending_paths = tuple(str(value) for value in pending_delete.get("paths", ()))
    pending_names = tuple(str(value) for value in pending_delete.get("names", ()))
    if pending_paths and len(pending_paths) == len(pending_names):
        confirm_permanent_delete(
            pending_paths,
            pending_names,
            reports_dir,
            conversation_dir,
        )
