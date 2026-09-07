"""资料库：检索、预览和下载搜索快照与研究报告。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from deepsearch.infrastructure.customer_service import CustomerServicePublisher, DeliveryError
from deepsearch.infrastructure.storage import ConversationStore, FileArtifactTrash
from deepsearch.presentation.web.delivery import build_library_delivery_artifact
from deepsearch.presentation.web.styles import render_page_header
from deepsearch.presentation.web.support import get_settings


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


def move_report_to_trash(
    report_path: str,
    reports_dir: str,
    artifacts_dir: str,
    conversation_dir: str,
) -> None:
    """按钮回调先完成文件移动；这样对话框关闭后的整页 rerun 仍可靠。"""

    source = Path(report_path)
    try:
        trash = FileArtifactTrash(
            Path(conversation_dir).parent / "trash",
            (Path(reports_dir), Path(artifacts_dir)),
        )
        moved_to = trash.move(source)
        ConversationStore(Path(conversation_dir)).clear_artifact_reference(source)
    except (OSError, ValueError) as exc:
        st.session_state.history_trash_error = f"无法移除报告：{exc}"
    else:
        load_reports.clear()
        st.session_state.pop("history_selected", None)
        st.session_state.history_trash_success = f"已移到回收站：{moved_to.name}"
    finally:
        # 回调发生在 dialog fragment 的 rerun 之前；由弹窗函数读取此标记
        # 并发起整页 rerun，确保无论成功或失败都能退出确认框。
        st.session_state.history_close_trash_dialog = True


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


@st.dialog("移到回收站", icon=":material/delete:")
def confirm_trash_report(
    report_path: str,
    report_title: str,
    reports_dir: str,
    artifacts_dir: str,
    conversation_dir: str,
) -> None:
    """二次确认后安全移动资产，并清理会话中的失效文件引用。"""

    if st.session_state.pop("history_close_trash_dialog", False):
        st.rerun()

    st.warning(f"确定从资料库移除“{report_title}”吗？报告正文不会再出现在资料库。")
    st.caption("文件将移到项目回收站，可从侧栏“回收站”页面恢复，不会永久删除。")
    with st.container(horizontal=True, horizontal_alignment="right"):
        st.button(
            "确认移除",
            type="primary",
            icon=":material/delete_forever:",
            on_click=move_report_to_trash,
            args=(report_path, reports_dir, artifacts_dir, conversation_dir),
        )


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
with st.form("history_filter", border=False):
    with st.container(horizontal=True, vertical_alignment="bottom"):
        query = st.text_input("搜索报告", placeholder="按主题或正文搜索", key="history_query")
        st.form_submit_button("搜索", icon=":material/search:")

# 过滤条件通过 form 提交，不会在每次键盘输入时重复扫描全部文件。
reports = load_reports((str(settings.reports_dir), str(settings.conversation_dir.parent / "artifacts")), query)
if not reports:
    st.info("没有找到匹配内容。完成搜索或研究后，结果会自动出现在这里。", icon=":material/article:")
    st.stop()

summary = pd.DataFrame([{
    "主题": item["title"], "类型": item["kind"], "格式": item["format"], "更新时间": item["updated"], "大小（KB）": item["size_kb"]
} for item in reports])
st.dataframe(
    summary,
    hide_index=True,
    column_config={"更新时间": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm")},
    key="history_table",
)

labels = {f"{item['updated']:%m-%d %H:%M} · {item['title']}": item for item in reports}
# 让选项集参与组件身份计算：报告被移除后，Streamlit 会创建
# 新的下拉框并选中现存的第一项，不会复用固定 key 下的已删除显示值。
selected = st.selectbox("打开报告", list(labels))
report = labels[selected]
with st.container(horizontal=True, horizontal_alignment="right"):
    st.download_button(
        f"下载 {report['format'].upper()}",
        data=report["raw_content"].encode("utf-8"),
        file_name=Path(report["path"]).name,
        mime={"md": "text/markdown", "txt": "text/plain", "json": "application/json"}.get(report["format"], "text/plain"),
        icon=":material/download:",
    )
    asset_key = hashlib.sha256(report["path"].encode("utf-8")).hexdigest()[:12]
    with st.popover("推送", icon=":material/send:", width=104, wrap=False):
        st.markdown("**推送到 CustomerService**")
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
                ["public", "internal", "confidential"],
                default="internal",
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
                    str(classification or "internal"),
                    str(settings.conversation_dir),
                )
    if st.button("移到回收站", icon=":material/delete:", key="history-trash-report"):
        confirm_trash_report(
            report["path"],
            report["title"],
            str(settings.reports_dir),
            str(settings.conversation_dir.parent / "artifacts"),
            str(settings.conversation_dir),
        )
if report["format"] == "txt":
    st.text(report["content"])
else:
    st.markdown(report["content"])
