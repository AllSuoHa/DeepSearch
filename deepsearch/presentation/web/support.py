"""Streamlit 展示层与研究内核之间的适配工具。"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import streamlit as st

from ...bootstrap import DeepSearchAgent, apply_profile
from ...domain.models import ResearchResult
from ...infrastructure.config import LLMSettings, Settings, load_settings

# support.py 位于 deepsearch/presentation/web/；向上四级才是仓库根目录。
# 统一从仓库根目录解析配置、报告和缓存，避免 Web 端在 presentation/ 下
# 意外创建第二套运行数据目录。
PROJECT_ROOT = Path(__file__).resolve().parents[3]


def get_settings() -> Settings:
    """加载普通配置，并用 Streamlit Secrets 中的模型配置做安全覆盖。"""

    config_path = st.session_state.get("config_path", str(PROJECT_ROOT / "config.json"))
    settings = load_settings(config_path)
    try:
        api_key = str(st.secrets.get("DEEPSEARCH_API_KEY", ""))
        base_url = str(st.secrets.get("DEEPSEARCH_BASE_URL", settings.llm.base_url))
        model = str(st.secrets.get("DEEPSEARCH_MODEL", settings.llm.model))
        integration_key = str(st.secrets.get("DEEPSEARCH_CUSTOMER_SERVICE_INTEGRATION_KEY", ""))
        customer_service_api_key = str(st.secrets.get("DEEPSEARCH_CUSTOMER_SERVICE_API_KEY", ""))
    # 未创建 secrets 文件是合法状态，此时继续使用配置与环境变量。
    except (FileNotFoundError, KeyError):
        return settings
    if api_key:
        settings.llm = LLMSettings(base_url=base_url, model=model, api_key=api_key)
    if integration_key:
        settings.customer_service.integration_key = integration_key
    if customer_service_api_key:
        settings.customer_service.api_access_key = customer_service_api_key
    return settings


def create_agent(profile: str = "均衡") -> DeepSearchAgent:
    """按页面选择的研究强度创建单次 Agent，不修改持久化设置。"""

    return DeepSearchAgent(apply_profile(get_settings(), profile))


def stream_markdown(markdown: str) -> Iterator[str]:
    """按段落流式输出 Markdown，兼顾阅读节奏和格式稳定性。"""

    blocks = markdown.split("\n\n")
    for index, block in enumerate(blocks):
        suffix = "\n\n" if index < len(blocks) - 1 else ""
        yield block + suffix


def source_rows(result: ResearchResult) -> list[dict]:
    """把领域来源转换为数据表可直接消费的展示行。"""

    rows = []
    for source in result.sources:
        if source.provider == "mock":
            status = "Mock"
        elif source.fetched:
            status = "正文"
        else:
            status = "摘要"
        rows.append({
            "编号": source.source_id,
            "标题": source.title,
            "域名": source.url.split("/")[2] if "://" in source.url else source.provider,
            "检索源": source.provider,
            "质量分": round(source.quality_score * 100),
            "相关度": round(source.relevance_score * 100),
            "状态": status,
            "链接": source.url,
        })
    return rows


def trace_rows(result: ResearchResult) -> list[dict]:
    """把结构化轮次轨迹转换为中文列名表格。"""

    return [{
        "轮次": trace.round_number,
        "查询数": len(trace.queries),
        "搜索结果": trace.results_found,
        "新增来源": trace.new_sources,
        "在线正文": trace.fetched_sources,
        "耗时（秒）": trace.duration_seconds,
        "充分": trace.sufficient,
        "降级": trace.used_fallback,
        "证据缺口": "；".join(trace.missing_dimensions),
        "下一步查询": "；".join(trace.next_queries),
        "判断": trace.decision,
    } for trace in result.trace]


def init_session_state() -> None:
    """集中声明所有会话状态键；setdefault 保证 rerun 时不会覆盖用户数据。"""

    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("last_result", None)
    st.session_state.setdefault("progress_events", [])
    st.session_state.setdefault("config_path", str(PROJECT_ROOT / "config.json"))
    st.session_state.setdefault("research_profile", "均衡")
    st.session_state.setdefault("pending_question", "")


def reset_research() -> None:
    """只清空当前研究上下文，保留全局配置和研究模式。"""

    st.session_state.messages = []
    st.session_state.last_result = None
    st.session_state.progress_events = []
