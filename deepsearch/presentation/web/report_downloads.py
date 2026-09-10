"""聊天页与资料库共用的报告下载菜单。"""

from __future__ import annotations

import streamlit as st

from ...infrastructure.report_export import (
    REPORT_EXPORT_FORMATS,
    ExportedReport,
    ReportExportError,
    export_report,
)


@st.cache_data(max_entries=96, show_spinner=False)
def cached_report_export(markdown: str, title: str, output_format: str) -> ExportedReport:
    """缓存确定性较强的格式转换，避免页面 rerun 重复生成二进制文件。"""

    return export_report(markdown, title, output_format)


def render_report_download_menu(markdown: str, title: str, menu_key: str) -> None:
    """显示紧凑下载入口，并仅生成用户当前选择的文件类型。"""

    labels = {label: format_key for format_key, label in REPORT_EXPORT_FORMATS}
    with st.popover("下载", icon=":material/download:", width=112, wrap=False):
        selected_label = st.selectbox(
            "文件类型",
            list(labels),
            key=f"report-download-format-{menu_key}",
            persist_state="session",
        )
        try:
            exported = cached_report_export(markdown, title, labels[str(selected_label)])
        except (ReportExportError, ValueError) as exc:
            st.error(str(exc), icon=":material/error:")
        else:
            st.download_button(
                f"下载 {exported.format_label}",
                data=exported.data,
                file_name=exported.file_name,
                mime=exported.mime_type,
                icon=":material/download:",
                key=f"report-download-file-{menu_key}-{exported.format_key}",
                width="stretch",
            )
