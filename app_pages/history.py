"""报告中心：检索、预览和下载多格式本地研究资产。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from deepsearch.presentation.web.styles import render_page_header
from deepsearch.presentation.web.support import get_settings


@st.cache_data(ttl="10s", max_entries=8)
def load_reports(directory: str, query: str) -> list[dict]:
    """读取报告目录；短 TTL 兼顾新报告可见性和频繁 rerun 性能。"""

    root = Path(directory)
    if not root.exists():
        return []
    lowered = query.strip().lower()
    rows = []
    supported = {".md", ".txt", ".json"}
    paths = [path for path in root.iterdir() if path.is_file() and path.suffix.lower() in supported]
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
        })
    return rows


settings = get_settings()
render_page_header(
    "Research library",
    "报告中心",
    "搜索、预览和下载研究资产。支持 Markdown、纯文本和 JSON 交付，同时保留可追溯引用。",
)
with st.form("history_filter", border=False):
    with st.container(horizontal=True, vertical_alignment="bottom"):
        query = st.text_input("搜索报告", placeholder="按主题或正文搜索", key="history_query")
        st.form_submit_button("搜索", icon=":material/search:")

# 过滤条件通过 form 提交，不会在每次键盘输入时重复扫描全部文件。
reports = load_reports(str(settings.reports_dir), query)
if not reports:
    st.info("没有找到匹配的报告。完成研究后，报告会自动出现在这里。", icon=":material/article:")
    st.stop()

summary = pd.DataFrame([{
    "主题": item["title"], "格式": item["format"], "更新时间": item["updated"], "大小（KB）": item["size_kb"]
} for item in reports])
st.dataframe(
    summary,
    hide_index=True,
    column_config={"更新时间": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm")},
    key="history_table",
)

labels = {f"{item['updated']:%m-%d %H:%M} · {item['title']}": item for item in reports}
selected = st.selectbox("打开报告", list(labels), key="history_selected")
report = labels[selected]
with st.container(horizontal=True, horizontal_alignment="right"):
    st.download_button(
        f"下载 {report['format'].upper()}",
        data=report["raw_content"].encode("utf-8"),
        file_name=Path(report["path"]).name,
        mime={"md": "text/markdown", "txt": "text/plain", "json": "application/json"}.get(report["format"], "text/plain"),
        icon=":material/download:",
    )
if report["format"] == "txt":
    st.text(report["content"])
else:
    st.markdown(report["content"])
