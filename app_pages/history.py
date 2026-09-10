"""资料库：检索、预览和下载搜索快照与研究报告。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import streamlit as st

from deepsearch.infrastructure.customer_service import CustomerServicePublisher, DeliveryError
from deepsearch.infrastructure.storage import (
    ConversationStore,
    FileArtifactTrash,
    rename_library_artifact,
)
from deepsearch.presentation.web.delivery import (
    DATA_CLASSIFICATION_OPTIONS,
    build_library_delivery_artifact,
    data_classification_value,
)
from deepsearch.presentation.web.report_downloads import render_report_download_menu
from deepsearch.presentation.web.search_results import (
    render_search_response,
    search_response_from_markdown,
)
from deepsearch.presentation.web.styles import render_page_header
from deepsearch.presentation.web.support import get_settings, search_run_from_messages


SORT_OPTIONS = (
    "最近更新",
    "最早更新",
    "名称升序",
    "名称降序",
    "文件从大到小",
    "文件从小到大",
)


def sort_reports(reports: list[dict], order: str) -> list[dict]:
    """按中文排序选项返回新列表，不修改缓存中的原始结果。"""

    if order == "最早更新":
        return sorted(reports, key=lambda item: item["updated"])
    if order == "名称升序":
        return sorted(reports, key=lambda item: str(item["title"]).casefold())
    if order == "名称降序":
        return sorted(reports, key=lambda item: str(item["title"]).casefold(), reverse=True)
    if order == "文件从大到小":
        return sorted(reports, key=lambda item: float(item["size_kb"]), reverse=True)
    if order == "文件从小到大":
        return sorted(reports, key=lambda item: float(item["size_kb"]))
    return sorted(reports, key=lambda item: item["updated"], reverse=True)


def report_selection_key(report_path: str) -> str:
    """用稳定路径摘要绑定每个勾选框，避免同名文档互相覆盖。"""

    identity = hashlib.sha256(report_path.encode("utf-8")).hexdigest()[:16]
    return f"history-report-selected-{identity}"


def set_report_selections(report_paths: tuple[str, ...], selected: bool) -> None:
    """批量设置当前筛选结果的选择状态。"""

    for report_path in report_paths:
        st.session_state[report_selection_key(report_path)] = selected


@st.cache_data(ttl="10s", max_entries=8)
def load_reports(directories: tuple[str, ...], query: str) -> list[dict]:
    """读取报告目录；短 TTL 兼顾新报告可见性和频繁 rerun 性能。"""

    lowered = query.strip().lower()
    rows = []
    supported = {".md", ".txt", ".json"}
    paths = []
    for directory in directories:
        root = Path(directory)
        if root.exists():
            paths.extend(path for path in root.iterdir() if path.is_file() and path.suffix.lower() in supported)
    for path in sorted(paths, key=lambda item: item.stat().st_mtime, reverse=True):
        raw_content = path.read_text(encoding="utf-8", errors="replace")
        content = raw_content
        title = path.stem
        if path.suffix.lower() == ".json":
            try:
                payload = json.loads(raw_content)
                content = str(payload.get("content_markdown", raw_content))
                title = str(payload.get("question", path.stem))
            except json.JSONDecodeError:
                pass
        elif content:
            title = content.splitlines()[0].removeprefix("# ")
        if lowered and lowered not in path.name.lower() and lowered not in content.lower():
            continue
        rows.append({
            "title": title,
            "path": str(path),
            "updated": datetime.fromtimestamp(path.stat().st_mtime),
            "size_kb": round(path.stat().st_size / 1024, 1),
            "content": content,
            "raw_content": raw_content,
            "format": path.suffix.lower().lstrip("."),
            "kind": "搜索快照" if "_搜索" in path.stem else "研究报告",
        })
    return rows


@st.cache_data(ttl="10s", max_entries=16)
def load_search_preview(
    report_path: str,
    content: str,
    conversation_dir: str,
):
    """优先恢复会话中的完整来源字段，旧快照则兼容解析 Markdown。"""

    expected = Path(report_path).resolve()
    store = ConversationStore(Path(conversation_dir))
    for summary in store.list(limit=None):
        conversation = store.load(str(summary.get("id", "")))
        if not conversation:
            continue
        for message in reversed(conversation.get("messages", [])):
            referenced_path = str(message.get("artifact_path", "")).strip()
            if message.get("kind") != "search" or not referenced_path:
                continue
            if Path(referenced_path).resolve() != expected:
                continue
            restored = search_run_from_messages([message])
            if restored is not None and restored.search is not None:
                restored.search.artifact_path = expected
                return restored.search
    return search_response_from_markdown(content, expected)


def move_reports_to_trash(
    report_paths: tuple[str, ...],
    reports_dir: str,
    artifacts_dir: str,
    conversation_dir: str,
) -> None:
    """批量移动已勾选资产；单个失败不会阻止其余安全移入回收站。"""

    trash = FileArtifactTrash(
        Path(conversation_dir).parent / "trash",
        (Path(reports_dir), Path(artifacts_dir)),
    )
    store = ConversationStore(Path(conversation_dir))
    moved_count = 0
    errors: list[str] = []
    for report_path in report_paths:
        source = Path(report_path)
        try:
            trash.move(source)
            store.clear_artifact_reference(source)
        except (OSError, ValueError) as exc:
            errors.append(f"{source.name}：{exc}")
        else:
            moved_count += 1
            st.session_state.pop(report_selection_key(report_path), None)

    if moved_count:
        load_reports.clear()
        st.session_state.history_trash_success = f"已将 {moved_count} 份文档移到回收站。"
    if errors:
        preview = "；".join(errors[:3])
        remainder = f"；另有 {len(errors) - 3} 项失败" if len(errors) > 3 else ""
        st.session_state.history_trash_error = f"部分文档无法移除：{preview}{remainder}"
    st.session_state.history_close_batch_trash_dialog = True


def rename_report(
    report_path: str,
    new_title: str,
    reports_dir: str,
    artifacts_dir: str,
    conversation_dir: str,
) -> None:
    """修改文档标题与文件名，并同步更新历史会话中的资产路径。"""

    source = Path(report_path)
    try:
        renamed = rename_library_artifact(
            source,
            new_title,
            (Path(reports_dir), Path(artifacts_dir)),
        )
        ConversationStore(Path(conversation_dir)).replace_artifact_reference(source, renamed)
    except (OSError, ValueError) as exc:
        st.session_state.history_rename_error = f"无法修改文档名称：{exc}"
    else:
        load_reports.clear()
        st.session_state.history_focus_path = str(renamed)
        st.session_state.history_rename_success = f"文档已重命名为“{new_title.strip()}”。"
    st.rerun()


def push_report_to_customer_service(
    report: dict,
    classification: str,
    conversation_dir: str,
) -> None:
    """投递当前资料库资产，并把结果留给下一轮页面统一提示。"""

    settings = get_settings()
    try:
        # 构造逻辑会回查会话引用，从资料库推送时仍复用首页 external ID。
        artifact = build_library_delivery_artifact(
            report,
            Path(conversation_dir),
            classification,
        )
        outcome = CustomerServicePublisher(settings.customer_service).publish_artifact(artifact)
    except DeliveryError as exc:
        st.session_state.history_delivery_error = f"推送失败：{exc}"
    else:
        if outcome.status == "indexed":
            st.session_state.history_delivery_success = (
                f"已写入 CustomerService 知识库（文档 {outcome.document_id}）。"
            )
        else:
            st.session_state.history_delivery_warning = (
                f"本次未完成入库，资产已进入安全重试队列：{outcome.error}"
            )
    st.rerun()


@st.dialog("批量移到回收站", icon=":material/delete_sweep:")
def confirm_batch_trash_reports(
    report_paths: tuple[str, ...],
    report_titles: tuple[str, ...],
    reports_dir: str,
    artifacts_dir: str,
    conversation_dir: str,
) -> None:
    """确认批量移除范围，避免勾选后误操作。"""

    if st.session_state.pop("history_close_batch_trash_dialog", False):
        st.rerun()

    st.warning(f"确定将已选的 {len(report_paths)} 份文档移到回收站吗？")
    shown_titles = "、".join(f"“{title}”" for title in report_titles[:5])
    if len(report_titles) > 5:
        shown_titles += f"等 {len(report_titles)} 份文档"
    st.caption(shown_titles)
    st.caption("文件可从侧栏“回收站”页面恢复，不会永久删除。")
    with st.container(horizontal=True, horizontal_alignment="right"):
        st.button(
            "确认批量移除",
            type="primary",
            icon=":material/delete_sweep:",
            on_click=move_reports_to_trash,
            args=(report_paths, reports_dir, artifacts_dir, conversation_dir),
        )


def render_report_group(kind: str, items: list[dict]) -> None:
    """以可折叠中文列表展示一个功能分类，并提供稳定的逐项勾选。"""

    group_key = "search" if kind == "搜索快照" else "research"
    with st.container(key=f"history-report-group-{group_key}", gap=None):
        with st.expander(f"{kind}（{len(items)}）", expanded=True):
            with st.container(key=f"history-report-header-{group_key}"):
                header = st.columns([0.55, 5.2, 1.1, 2.1, 1.1], vertical_alignment="center")
                header[0].caption("选择", text_alignment="center")
                header[1].caption("主题")
                header[2].caption("格式")
                header[3].caption("更新时间")
                header[4].caption("大小")
            for item in items:
                row_key = hashlib.sha256(str(item["path"]).encode("utf-8")).hexdigest()[:12]
                with st.container(key=f"history-report-row-{row_key}", border=False):
                    columns = st.columns(
                        [0.55, 5.2, 1.1, 2.1, 1.1],
                        vertical_alignment="center",
                    )
                    # 让选择控件由 Streamlit 的布局容器负责双向居中。
                    # 单靠 checkbox 内部 DOM 的 CSS，在不同缩放比例和浏览器下
                    # 容易只居中 15px 的标签自身，而不是整个“选择”单元格。
                    with columns[0].container(
                        key=f"history-report-select-{row_key}",
                        height=36,
                        border=False,
                        horizontal_alignment="center",
                        vertical_alignment="center",
                        gap=None,
                    ):
                        st.checkbox(
                            "选择文档",
                            key=report_selection_key(str(item["path"])),
                            label_visibility="collapsed",
                        )
                    columns[1].markdown(str(item["title"]))
                    columns[2].write(str(item["format"]).upper())
                    columns[3].write(f"{item['updated']:%Y-%m-%d %H:%M}")
                    columns[4].write(f"{item['size_kb']:g} KB")


settings = get_settings()
render_page_header(
    "LIBRARY",
    "资料库",
    "查找、预览、下载、推送或安全移除搜索快照与研究报告；旧报告不会被自动改写。",
)
if message := st.session_state.pop("history_trash_success", ""):
    st.toast(message, icon=":material/delete:")
if message := st.session_state.pop("history_trash_error", ""):
    st.error(message, icon=":material/error:")
if message := st.session_state.pop("history_delivery_success", ""):
    st.toast(message, icon=":material/check_circle:")
if message := st.session_state.pop("history_delivery_warning", ""):
    st.warning(message, icon=":material/schedule:")
if message := st.session_state.pop("history_delivery_error", ""):
    st.error(message, icon=":material/error:")
if message := st.session_state.pop("history_rename_success", ""):
    st.toast(message, icon=":material/edit:")
if message := st.session_state.pop("history_rename_error", ""):
    st.error(message, icon=":material/error:")
with st.form("history_filter", border=False):
    with st.container(
        key="history-filter-bar",
        horizontal=True,
        vertical_alignment="center",
        gap="small",
    ):
        query = st.text_input(
            "搜索报告",
            placeholder="按主题或正文搜索",
            key="history_query",
            label_visibility="collapsed",
            width="stretch",
        )
        sort_order = st.selectbox(
            "排序方式",
            SORT_OPTIONS,
            key="history_sort_order",
            label_visibility="collapsed",
            width=190,
        )
        st.form_submit_button("搜索", icon=":material/search:", width="content")

# 过滤条件通过 form 提交，不会在每次键盘输入时重复扫描全部文件。
reports = load_reports((str(settings.reports_dir), str(settings.conversation_dir.parent / "artifacts")), query)
if not reports:
    st.info("没有找到匹配内容。完成搜索或研究后，结果会自动出现在这里。", icon=":material/article:")
    st.stop()

all_paths = tuple(str(item["path"]) for item in reports)
selected_reports = [
    item for item in reports
    if st.session_state.get(report_selection_key(str(item["path"])), False)
]
with st.container(
    key="history-bulk-actions",
    horizontal=True,
    horizontal_alignment="right",
    vertical_alignment="center",
    gap="small",
):
    st.caption(f"已选 {len(selected_reports)} 项")
    st.button(
        "全选",
        icon=":material/select_all:",
        on_click=set_report_selections,
        args=(all_paths, True),
        type="tertiary",
    )
    st.button(
        "清除",
        icon=":material/deselect:",
        on_click=set_report_selections,
        args=(all_paths, False),
        type="tertiary",
    )
    if st.button(
        f"移入回收站（{len(selected_reports)}）",
        icon=":material/delete_sweep:",
        disabled=not selected_reports,
        key="history-batch-trash",
    ):
        confirm_batch_trash_reports(
            tuple(str(item["path"]) for item in selected_reports),
            tuple(str(item["title"]) for item in selected_reports),
            str(settings.reports_dir),
            str(settings.conversation_dir.parent / "artifacts"),
            str(settings.conversation_dir),
        )

reports = sort_reports(reports, str(sort_order))
for report_kind in ("搜索快照", "研究报告"):
    grouped_reports = [item for item in reports if item["kind"] == report_kind]
    if grouped_reports:
        render_report_group(report_kind, grouped_reports)

labels: dict[str, dict] = {}
for item in reports:
    label = f"{item['updated']:%m-%d %H:%M} · {item['title']}"
    if label in labels:
        label = f"{label} · {Path(item['path']).name}"
    labels[label] = item
# 让选项集参与组件身份计算：报告被移除后，Streamlit 会创建
# 新的下拉框并选中现存的第一项，不会复用固定 key 下的已删除显示值。
preferred_path = st.session_state.pop("history_focus_path", "")
label_options = list(labels)
selected_index = next(
    (index for index, label in enumerate(label_options) if labels[label]["path"] == preferred_path),
    0,
)
selected = st.selectbox("打开报告", label_options, index=selected_index)
report = labels[selected]
asset_key = hashlib.sha256(report["path"].encode("utf-8")).hexdigest()[:12]
with st.container(
    key="history-current-actions",
    horizontal=True,
    horizontal_alignment="right",
    vertical_alignment="center",
):
    render_report_download_menu(
        str(report["content"]),
        str(report["title"]),
        f"library-{asset_key}",
    )
    with st.popover("推送", icon=":material/send:", width=148, wrap=False):
        push_tab, rename_tab = st.tabs(["推送文档", "修改名称"])
        with push_tab:
            if not settings.customer_service.enabled:
                # 资料库页面也会被 AppTest 独立加载；此时没有多页面路由表，
                # 因而使用侧栏路径提示，不调用依赖完整导航上下文的 page_link。
                st.caption("请先从侧栏打开“设置 → 知识库联动”并启用 CustomerService。")
            elif not settings.customer_service.integration_key:
                # 缺少密钥属于本地配置错误，此时不开放提交按钮，避免反复制造
                # 永远无法成功的 outbox 记录。
                st.warning("缺少联动密钥，暂时无法推送。", icon=":material/key:")
                st.caption("请在 `.streamlit/secrets.toml` 配置 `DEEPSEARCH_CUSTOMER_SERVICE_INTEGRATION_KEY` 后重启页面。")
            else:
                classification = st.segmented_control(
                    "文档密级",
                    DATA_CLASSIFICATION_OPTIONS,
                    default="内部",
                    key=f"history-delivery-classification-{asset_key}",
                )
                if st.button(
                    "确认推送",
                    type="primary",
                    icon=":material/send:",
                    key=f"history-delivery-confirm-{asset_key}",
                ):
                    push_report_to_customer_service(
                        report,
                        data_classification_value(classification),
                        str(settings.conversation_dir),
                    )
        with rename_tab:
            with st.form(f"history-rename-form-{asset_key}", border=False):
                new_title = st.text_input(
                    "新名称",
                    value=str(report["title"]),
                    max_chars=120,
                    key=f"history-rename-title-{asset_key}",
                )
                rename_submitted = st.form_submit_button(
                    "保存名称",
                    type="primary",
                    icon=":material/save:",
                )
            if rename_submitted:
                rename_report(
                    str(report["path"]),
                    new_title,
                    str(settings.reports_dir),
                    str(settings.conversation_dir.parent / "artifacts"),
                    str(settings.conversation_dir),
                )
if report["format"] == "txt":
    st.text(report["content"])
elif report["kind"] == "搜索快照":
    preview = load_search_preview(
        str(report["path"]),
        str(report["content"]),
        str(settings.conversation_dir),
    )
    if preview.items:
        render_search_response(preview, f"library-{asset_key}")
    else:
        # 极早期或手工导入的搜索文件可能没有结构化条目，仍保留可读回退。
        st.markdown(report["content"])
else:
    with st.container(key=f"research-report-library-{asset_key}"):
        st.markdown(report["content"])
