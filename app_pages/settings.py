"""搜索、模型、联动与本地数据设置。"""

from __future__ import annotations

import streamlit as st

from deepsearch.infrastructure.cache import ResearchCache
from deepsearch.infrastructure.config import save_settings
from deepsearch.presentation.web.styles import render_page_header
from deepsearch.presentation.web.support import get_settings

settings = get_settings()
render_page_header("PREFERENCES", "设置", "管理搜索连接、研究/快速回答模型、知识库联动和本地数据。密钥不会显示或写入配置文件。")

search_tab, model_tab, integration_tab, data_tab = st.tabs([
    ":material/search: 搜索",
    ":material/neurology: 研究与快速回答模型",
    ":material/sync_alt: 知识库联动",
    ":material/database: 本地数据",
])

with search_tab:
    st.subheader("搜索行为")
    with st.form("search-settings"):
        runtime_label = st.segmented_control(
            "数据来源", ["在线", "演示"],
            default="演示" if settings.runtime_mode == "mock" else "在线",
            help="演示模式只使用 Mock 数据；在线模式绝不会用 Mock 伪装真实结果。",
        )
        work_label = st.segmented_control(
            "默认工作模式", ["智能判断", "搜索", "研究"],
            default={
                "auto": "智能判断", "search": "搜索", "research": "研究",
            }.get(settings.default_work_mode, "智能判断"),
        )
        columns = st.columns(3)
        max_rounds = columns[0].number_input("研究最大轮次", 1, 5, settings.max_rounds)
        max_sources = columns[1].number_input("研究来源上限", 4, 40, settings.max_sources)
        request_timeout = columns[2].number_input("请求超时（秒）", 2.0, 30.0, settings.request_timeout, 1.0)
        saved = st.form_submit_button("保存搜索设置", type="primary", icon=":material/save:")
    if saved:
        settings.runtime_mode = "mock" if runtime_label == "演示" else "online"
        settings.mode = "mock" if settings.runtime_mode == "mock" else "auto"
        settings.default_work_mode = {
            "智能判断": "auto", "搜索": "search", "研究": "research",
        }[work_label]
        settings.max_rounds = int(max_rounds)
        settings.max_sources = int(max_sources)
        settings.request_timeout = float(request_timeout)
        save_settings(settings)
        st.toast("搜索设置已保存。", icon=":material/check_circle:")

    with st.container(border=True):
        st.markdown("**搜索源状态**")
        with st.container(horizontal=True):
            st.badge("Brave 已配置" if settings.brave_api_key else "Brave 未配置", color="green" if settings.brave_api_key else "gray")
            st.badge("DuckDuckGo 回退", color="blue")
            st.badge("Wikipedia 回退", color="blue")
            st.badge("OpenAlex + Crossref", color="violet")
        st.caption("Brave 未配置时仍可使用免费来源，但稳定性与覆盖率可能较低。")
        st.code('DEEPSEARCH_BRAVE_API_KEY = "your-brave-search-key"', language="toml")

with model_tab:
    st.subheader("三阶段研究模型")
    with st.container(border=True):
        st.badge("模型可用" if not settings.mock_llm else "尚未配置", color="green" if not settings.mock_llm else "orange")
        st.caption(f"当前模型：{settings.llm.model} · {settings.llm.base_url}")
        st.markdown("研究模式会依次执行证据整理、初稿生成和独立审校重写。没有模型时研究会明确停止，搜索仍可使用。")
    with st.form("model-settings"):
        model_timeout = st.number_input(
            "模型单次请求超时（秒）",
            min_value=30.0,
            max_value=900.0,
            value=float(settings.llm.request_timeout),
            step=30.0,
            help="思考模型处理长报告时首个响应可能较慢；此超时独立于网页抓取超时。",
        )
        saved_model = st.form_submit_button("保存模型设置", type="primary", icon=":material/save:")
    if saved_model:
        settings.llm.request_timeout = float(model_timeout)
        save_settings(settings)
        st.toast("模型设置已保存。", icon=":material/check_circle:")
    st.code(
        'DEEPSEARCH_API_KEY = "your-key"\n'
        'DEEPSEARCH_BASE_URL = "https://api.openai.com/v1"\n'
        'DEEPSEARCH_MODEL = "gpt-4o-mini"\n'
        'DEEPSEARCH_LLM_TIMEOUT = 180',
        language="toml",
    )
    st.caption("可放入环境变量或 `.streamlit/secrets.toml`，保存普通设置时不会落盘。")

    st.space("small")
    st.subheader("快速回答模型")
    chat_configured = all((
        settings.chat_llm.api_key,
        settings.chat_llm.base_url,
        settings.chat_llm.model,
    ))
    with st.container(border=True):
        if chat_configured and settings.runtime_mode == "mock":
            st.badge("已配置 · 演示模式未调用", color="blue")
        else:
            st.badge(
                "已配置" if chat_configured else "未配置 · 使用本地回复",
                color="green" if chat_configured else "orange",
            )
        st.caption(f"当前 Base URL：{settings.chat_llm.base_url or '未设置'}")
        st.caption(f"当前模型：{settings.chat_llm.model or '未设置'}")
        st.caption(f"请求超时：{settings.chat_llm.request_timeout:g} 秒")
        st.markdown(
            "快速回答模型与研究模型相互独立。未配置、超时或额度不足时，只处理本地时间、"
            "简单计算、问候和功能介绍，不会改用搜索或研究模型。"
        )
    with st.form("chat-model-settings"):
        chat_base_url = st.text_input(
            "快速回答模型 Base URL",
            value=settings.chat_llm.base_url,
            placeholder="OpenAI Chat Completions 兼容 API 地址",
        )
        chat_model_name = st.text_input(
            "快速回答模型名称",
            value=settings.chat_llm.model,
            placeholder="由所选服务商提供",
        )
        chat_timeout = st.number_input(
            "快速回答请求超时（秒）",
            min_value=5.0,
            max_value=180.0,
            value=float(settings.chat_llm.request_timeout),
            step=5.0,
        )
        saved_chat_model = st.form_submit_button(
            "保存快速回答模型设置", type="primary", icon=":material/save:",
        )
    if saved_chat_model:
        settings.chat_llm.base_url = chat_base_url.strip().rstrip("/")
        settings.chat_llm.model = chat_model_name.strip()
        settings.chat_llm.request_timeout = float(chat_timeout)
        save_settings(settings)
        st.toast("快速回答模型设置已保存；API Key 仍需通过 Secrets 或环境变量配置。", icon=":material/check_circle:")
    st.code(
        'DEEPSEARCH_CHAT_API_KEY = ""\n'
        'DEEPSEARCH_CHAT_BASE_URL = ""\n'
        'DEEPSEARCH_CHAT_MODEL = ""\n'
        'DEEPSEARCH_CHAT_TIMEOUT = 30',
        language="toml",
    )
    st.caption(
        "Streamlit Cloud 不读取开发电脑里的本地 Secrets；请在应用 Settings → Secrets 中填写。"
        "“轻量模型”不代表一定免费，费用取决于所选服务商。"
    )

with integration_tab:
    st.subheader("CustomerService")
    st.caption("搜索和研究完成后都会显示手动推送入口；DeepSearch 会等待 v2 后台入库完成后再提示成功。")
    with st.form("customer-service-settings"):
        integration_enabled = st.toggle("启用知识库联动", value=settings.customer_service.enabled)
        customer_service_url = st.text_input("服务地址", value=settings.customer_service.base_url, placeholder="http://127.0.0.1:8000")
        columns = st.columns(3)
        timeout = columns[0].number_input("超时（秒）", 2.0, 120.0, settings.customer_service.request_timeout, 1.0)
        retries = columns[1].number_input("即时重试", 1, 10, settings.customer_service.retry_attempts)
        backoff = columns[2].number_input("退避基数（秒）", 0.0, 60.0, settings.customer_service.retry_backoff_seconds, 0.5)
        saved_integration = st.form_submit_button("保存联动设置", type="primary", icon=":material/save:")
    if saved_integration:
        settings.customer_service.enabled = integration_enabled
        settings.customer_service.base_url = customer_service_url.strip().rstrip("/")
        settings.customer_service.request_timeout = float(timeout)
        settings.customer_service.retry_attempts = int(retries)
        settings.customer_service.retry_backoff_seconds = float(backoff)
        save_settings(settings)
        st.toast("联动设置已保存。", icon=":material/check_circle:")
    with st.container(border=True):
        # 页面只展示凭据是否存在，不读取或回显密钥正文。
        if not settings.customer_service.enabled:
            st.badge("联动未启用", color="gray")
        elif not settings.customer_service.integration_key:
            st.badge("缺少联动密钥", color="orange")
            st.warning(
                "页面无法推送是因为 `DEEPSEARCH_CUSTOMER_SERVICE_INTEGRATION_KEY` 尚未配置。",
                icon=":material/key:",
            )
        else:
            st.badge("DeepSearch 端已配置", color="green")
            st.caption("还需确保 CustomerService 的 `DEEPSEARCH_INTEGRATION_KEY` 使用完全相同的值。")
        st.caption(
            f"当前 API 根地址：`{settings.customer_service.base_url}`。这里应填写 API 端口（通常为 8000），不是 Streamlit 页面端口。"
        )
    st.code(
        'DEEPSEARCH_CUSTOMER_SERVICE_INTEGRATION_KEY = "integration-key"\n'
        'DEEPSEARCH_CUSTOMER_SERVICE_API_KEY = "optional-api-key"',
        language="toml",
    )
    st.caption("修改 `.streamlit/secrets.toml` 后需要重启 DeepSearch，普通设置保存不会写入或覆盖密钥。")

with data_tab:
    st.subheader("本地存储")
    st.caption(f"会话：{settings.conversation_dir}")
    st.caption(f"报告：{settings.reports_dir}")
    st.caption(f"搜索缓存：{settings.cache_dir}")
    if st.button("清空搜索与正文缓存", icon=":material/delete_sweep:"):
        removed = ResearchCache(settings.cache_dir, settings.cache_ttl_seconds).clear()
        st.toast(f"已清除 {removed} 个缓存条目；会话和报告未删除。", icon=":material/check_circle:")
