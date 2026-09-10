"""聊天页与资料库共用的搜索结果卡片。"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import urlsplit

import streamlit as st

from ...domain.models import SearchResponse, SearchResult


def _provider_from_url(url: str) -> str:
    """旧快照未保存 provider 时，从域名生成简洁的回退名称。"""

    host = urlsplit(url).netloc.removeprefix("www.")
    if not host:
        return "网页"
    if "wikipedia.org" in host:
        return "wikipedia"
    return host.split(".")[0] or host


def search_response_from_markdown(
    content: str,
    artifact_path: Path | None = None,
) -> SearchResponse:
    """把没有会话引用的旧 Markdown 搜索快照恢复为卡片数据。"""

    lines = content.splitlines()
    query = next(
        (line.removeprefix("# ").strip() for line in lines if line.startswith("# ")),
        "历史搜索",
    )
    search_marker = next(
        (index for index, line in enumerate(lines) if line.strip() == "## 搜索结果"),
        len(lines),
    )
    answer = " ".join(
        line.strip()
        for line in lines[1:search_marker]
        if line.strip() and not line.startswith("## ")
    ) or "已恢复历史搜索结果。"

    header_pattern = re.compile(r"^###\s+\d+\.\s+\[(?P<title>.+)]\((?P<url>.+)\)\s*$")
    item_starts = [
        index for index, line in enumerate(lines)
        if header_pattern.match(line.strip())
    ]
    items: list[SearchResult] = []
    for position, start in enumerate(item_starts):
        match = header_pattern.match(lines[start].strip())
        if match is None:
            continue
        end = item_starts[position + 1] if position + 1 < len(item_starts) else len(lines)
        section = [line.strip() for line in lines[start + 1:end]]
        section = [line for line in section if line]
        if "## 提示" in section:
            section = section[:section.index("## 提示")]
        metadata = section[0] if section else ""
        metadata_parts = [part.strip() for part in metadata.split(" · ") if part.strip()]
        resource_type = metadata_parts[0] if metadata_parts else "网页"
        risk_level = metadata_parts[1] if len(metadata_parts) > 1 else "未验证"
        published_at = metadata_parts[2] if len(metadata_parts) > 2 else ""
        url = match.group("url").strip()
        items.append(SearchResult(
            title=match.group("title").strip(),
            url=url,
            snippet=" ".join(section[1:]),
            query=query,
            provider=_provider_from_url(url),
            resource_type=resource_type,
            risk_level=risk_level,
            published_at=published_at,
        ))

    warnings: list[str] = []
    if "## 提示" in lines:
        warning_start = lines.index("## 提示") + 1
        warnings = [
            line.strip().removeprefix("- ")
            for line in lines[warning_start:]
            if line.strip().startswith("- ")
        ]
    return SearchResponse(
        query=query,
        answer=answer,
        items=items,
        warnings=warnings,
        artifact_path=artifact_path,
    )


def render_search_response(response: SearchResponse, message_key: str = "latest") -> None:
    """按来源分组渲染搜索结果；聊天页和资料库必须保持完全一致。"""

    st.markdown(response.answer)
    for warning in response.warnings:
        st.caption(f":material/info: {warning}")

    grouped: dict[str, list[SearchResult]] = {}
    for item in response.items:
        grouped.setdefault(item.resource_type, []).append(item)
    for group, items in grouped.items():
        st.markdown(f"#### {group}")
        for index, item in enumerate(items):
            host = urlsplit(item.url).netloc.removeprefix("www.")
            identity = hashlib.sha1(item.url.encode()).hexdigest()
            with st.container(
                border=True,
                key=f"result-card-{message_key}-{identity[:12]}-{index}",
            ):
                st.markdown(f"**[{item.title}]({item.url})**")
                with st.container(horizontal=True):
                    color = (
                        "green" if item.risk_level == "可信来源"
                        else "orange" if item.risk_level == "谨慎访问"
                        else "gray"
                    )
                    st.badge(item.risk_level, color=color)
                    source_label = " · ".join(
                        value for value in (host, item.provider, item.published_at)
                        if value
                    )
                    st.caption(source_label)
                if item.snippet:
                    st.caption(item.snippet)
                if item.risk_reasons:
                    st.warning("；".join(item.risk_reasons), icon=":material/shield:")
                if item.url.startswith(("http://", "https://")):
                    st.link_button("打开链接", item.url, icon=":material/open_in_new:")
                else:
                    st.button(
                        "演示占位链接",
                        disabled=True,
                        icon=":material/science:",
                        key=f"mock-link-{message_key}-{index}-{identity[:10]}",
                    )
