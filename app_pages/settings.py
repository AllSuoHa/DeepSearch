"""设置页面：管理研究预算、自主任务和模型连接说明。"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from deepsearch.bootstrap import DeepSearchAgent, apply_profile
from deepsearch.domain.models import ReportSpecification, ResearchBrief
from deepsearch.infrastructure.cache import ResearchCache
from deepsearch.infrastructure.config import save_settings
from deepsearch.presentation.web.styles import render_page_header
from deepsearch.presentation.web.support import get_settings
from deepsearch.scheduling.topics import ResearchTaskManager, ResearchTaskScheduler, ScheduledResearchTask

settings = get_settings()
render_page_header(
    "SYSTEM SETTINGS",
    "设置",
    "管理研究引擎、自动任务和模型连接。API Key 不会在页面中保存或显示。",
)

with st.container(horizontal=True):
    st.badge("LLM 已配置" if not settings.mock_llm else "确定性报告器", color="green" if not settings.mock_llm else "gray")
    st.badge("离线 Mock" if settings.mock_search else "在线优先", color="violet" if settings.mock_search else "blue")
    st.caption(f"配置文件：{settings.config_path.name}")

engine_tab, tasks_tab, integration_tab, model_tab = st.tabs(
    [
        ":material/tune: 研究引擎",
        ":material/event_repeat: 自主任务",
        ":material/sync_alt: 知识库联动",
        ":material/key: 模型连接",
    ]
)

with engine_tab:
    # 表单把多个控件的变化合并成一次 rerun 和一次原子保存。
    st.subheader("研究预算与缓存")
    st.caption("这些设置是均衡模式的默认值；快速和深度模式会为单次研究使用临时预算。")
    with st.form("engine_settings"):
        mode = st.segmented_control("运行模式", ["auto", "mock"], default=settings.mode, required=True)
        search_provider = st.selectbox("主搜索源", ["duckduckgo", "mock"], index=0 if settings.search_provider == "duckduckgo" else 1)
        col1, col2, col3 = st.columns(3)
        max_rounds = col1.number_input("最大轮次", 1, 5, settings.max_rounds)
        max_sources = col2.number_input("最大来源数", 4, 40, settings.max_sources)
        results_per_query = col3.number_input("每个查询结果数", 1, 10, settings.results_per_query)
        col4, col5, col6 = st.columns(3)
        request_timeout = col4.number_input("请求超时（秒）", 2.0, 30.0, settings.request_timeout, 1.0)
        per_domain_limit = col5.number_input("单域名来源上限", 1, 5, settings.per_domain_limit)
        cache_hours = col6.number_input("缓存时长（小时）", 1, 168, max(1, settings.cache_ttl_seconds // 3600))
        cache_enabled = st.toggle("启用搜索与正文缓存", value=settings.cache_enabled)
        submitted = st.form_submit_button("保存设置", icon=":material/save:", type="primary")

    if submitted:
        # 只在用户明确提交时修改配置对象并持久化。
        settings.mode = str(mode)
        settings.search_provider = search_provider
        settings.max_rounds = int(max_rounds)
        settings.max_sources = int(max_sources)
        settings.results_per_query = int(results_per_query)
        settings.request_timeout = float(request_timeout)
        settings.per_domain_limit = int(per_domain_limit)
        settings.cache_ttl_seconds = int(cache_hours) * 3600
        settings.cache_enabled = cache_enabled
        save_settings(settings)
        st.toast("设置已保存，将在下一次研究时生效。", icon=":material/check_circle:")

    with st.container(horizontal=True, horizontal_alignment="right"):
        if st.button("清空研究缓存", icon=":material/delete_sweep:"):
            removed = ResearchCache(settings.cache_dir, settings.cache_ttl_seconds).clear()
            st.toast(f"已清除 {removed} 个缓存条目。", icon=":material/check_circle:")

with tasks_tab:
    # 自主任务把调度规则和研究/报告要求作为一个原子配置保存。
    st.subheader("自主研究任务")
    st.caption("任务到期后会重新执行“搜索—评估—补搜—总结”循环，而不是复用一份静态链接列表。")
    manager = ResearchTaskManager(settings)
    weekday_labels = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}

    with st.form("research_task_add"):
        st.markdown("**任务目标**")
        name = st.text_input("任务名称", placeholder="例如：AI 行业日报")
        question = st.text_area("研究问题", placeholder="例如：总结最近一天大模型与 AI Agent 领域的重要变化")
        objective = st.text_input("报告目标", value="形成包含事实、影响判断和行动建议的总结报告")

        st.markdown("**时间周期**")
        schedule_left, schedule_middle, schedule_right = st.columns(3)
        schedule_type = schedule_left.selectbox(
            "周期",
            ["daily", "weekly", "once"],
            format_func=lambda value: {"daily": "每天", "weekly": "每周", "once": "单次"}[value],
        )
        run_time = schedule_middle.time_input("执行时间")
        run_date = schedule_right.date_input("单次日期")
        weekdays = st.multiselect(
            "每周执行日",
            list(weekday_labels),
            default=[0, 1, 2, 3, 4],
            format_func=lambda value: weekday_labels[value],
        )

        st.markdown("**研究范围**")
        scope_left, scope_right = st.columns(2)
        domain = scope_left.text_input("知识领域", value="人工智能与大模型")
        time_scope = scope_right.selectbox("信息时间范围", ["最近 24 小时", "最近 7 天", "最近 30 天", "不限"])
        information_types = st.pills(
            "信息类型",
            ["新闻", "公告", "知识", "研究", "数据", "政策"],
            default=["新闻", "公告", "研究"],
            selection_mode="multi",
        )

        st.markdown("**报告交付**")
        report_left, report_middle, report_right = st.columns(3)
        output_format = report_left.selectbox("文件格式", ["markdown", "text", "json"])
        target_words = report_middle.number_input("目标篇幅（字）", 300, 5000, 1800, 100)
        profile = report_right.selectbox("研究强度", ["快速", "均衡", "深度"], index=2)
        audience = st.text_input("目标读者", value="技术负责人")
        sections = st.multiselect(
            "报告章节",
            ["摘要", "重大事件", "关键结论", "详细分析", "技术进展", "行业影响", "风险与限制", "建议与下一步", "证据局限与争议"],
            default=["摘要", "重大事件", "技术进展", "行业影响", "建议与下一步"],
        )
        instructions = st.text_area(
            "内容与模板要求",
            value="按重要性排序，区分已确认事实、分析判断和建议；重要结论必须带引用。",
        )
        deliver_to_customer_service = st.checkbox(
            "研究完成后自动提交到 CustomerService 知识库",
            help="投递失败不会重新执行搜索，而会进入本地 outbox 等待重试。",
        )
        data_classification = st.segmented_control(
            "文档密级",
            ["public", "internal", "confidential"],
            default="internal",
            help="CustomerService 默认拒绝 confidential，除非接收端显式放行。",
        )
        add_task = st.form_submit_button("保存自主任务", icon=":material/add_task:", type="primary")

    if add_task:
        try:
            report = ReportSpecification(
                output_format=output_format,
                target_words=int(target_words),
                audience=audience.strip() or "通用读者",
                sections=tuple(sections) or ReportSpecification().sections,
                custom_instructions=instructions.strip(),
            )
            brief = ResearchBrief(
                domain=domain.strip() or "通用",
                objective=objective.strip() or "形成可验证、可执行的总结报告",
                information_types=tuple(information_types or ["知识"]),
                time_scope=time_scope,
                report=report,
            )
            task = ScheduledResearchTask(
                name=name.strip(),
                question=question.strip(),
                schedule_type=schedule_type,
                run_time=run_time.strftime("%H:%M"),
                weekdays=tuple(weekdays),
                run_date=run_date.strftime("%Y-%m-%d") if schedule_type == "once" else "",
                profile=profile,
                deliver_to_customer_service=deliver_to_customer_service,
                data_classification=str(data_classification or "internal"),
                brief=brief,
            )
            manager.add_task(task)
        except ValueError as exc:
            st.error(str(exc), icon=":material/error:")
        else:
            st.toast(f"任务“{name}”已保存。", icon=":material/check_circle:")
            st.rerun()

    tasks = manager.list()
    if tasks:
        rows = [{
            "任务": task.name,
            "周期": {"daily": "每天", "weekly": "每周", "once": "单次"}[task.schedule_type],
            "时间": task.run_time,
            "领域": task.brief.domain,
            "信息类型": "、".join(task.brief.information_types),
            "强度": task.profile,
            "交付": f"{task.brief.report.output_format} / {task.brief.report.target_words} 字",
            "知识库": task.data_classification if task.deliver_to_customer_service else "不提交",
            "状态": "启用" if task.enabled else "停用",
        } for task in tasks]
        st.dataframe(pd.DataFrame(rows), hide_index=True, key="research_tasks_table")
        selected_name = st.selectbox("选择任务", [task.name for task in tasks], key="selected_research_task")
        selected_task = manager.get(selected_name)
        with st.container(horizontal=True, horizontal_alignment="right"):
            if st.button("立即运行", icon=":material/play_arrow:"):
                scheduler = ResearchTaskScheduler(
                    settings,
                    agent_factory=lambda selected_profile: DeepSearchAgent(apply_profile(settings, selected_profile)),
                )
                try:
                    with st.status(f"正在执行“{selected_name}”…", expanded=True) as task_status:
                        result = scheduler.run_now(selected_name, lambda phase, message: task_status.write(f"**{phase}** · {message}"))
                        task_status.update(label=f"任务完成：{result.report_path.name}", state="complete", expanded=False)
                except Exception as exc:
                    st.error(f"任务执行失败：{exc}", icon=":material/error:")
                else:
                    st.success(f"报告已生成：{result.report_path}", icon=":material/check_circle:")
            if selected_task and st.button(
                "停用" if selected_task.enabled else "启用",
                icon=":material/pause_circle:" if selected_task.enabled else ":material/play_circle:",
            ):
                manager.set_enabled(selected_name, not selected_task.enabled)
                st.rerun()
            if st.button("删除", icon=":material/delete:"):
                manager.remove(selected_name)
                st.rerun()
    else:
        st.info("尚未配置自主研究任务。", icon=":material/event_busy:")

with integration_tab:
    st.subheader("CustomerService 知识库联动")
    st.caption("全局开关和单个任务的交付选项必须同时开启；凭据不会写入 config.json。")
    with st.form("customer_service_settings"):
        integration_enabled = st.checkbox(
            "启用 CustomerService 自动投递",
            value=settings.customer_service.enabled,
        )
        customer_service_url = st.text_input(
            "CustomerService 地址",
            value=settings.customer_service.base_url,
            placeholder="http://127.0.0.1:8000",
        )
        timeout_column, retry_column, backoff_column = st.columns(3)
        integration_timeout = timeout_column.number_input(
            "请求超时（秒）", 2.0, 120.0, settings.customer_service.request_timeout, 1.0
        )
        integration_retries = retry_column.number_input(
            "即时重试次数", 1, 10, settings.customer_service.retry_attempts
        )
        integration_backoff = backoff_column.number_input(
            "退避基数（秒）", 0.0, 60.0, settings.customer_service.retry_backoff_seconds, 0.5
        )
        save_integration = st.form_submit_button("保存联动设置", icon=":material/save:", type="primary")

    if save_integration:
        settings.customer_service.enabled = integration_enabled
        settings.customer_service.base_url = customer_service_url.strip().rstrip("/")
        settings.customer_service.request_timeout = float(integration_timeout)
        settings.customer_service.retry_attempts = int(integration_retries)
        settings.customer_service.retry_backoff_seconds = float(integration_backoff)
        save_settings(settings)
        st.toast("联动设置已保存。", icon=":material/check_circle:")

    pending_count = (
        len(list(settings.customer_service.outbox_dir.glob("*.json")))
        if settings.customer_service.outbox_dir.exists()
        else 0
    )
    with st.container(border=True):
        st.metric("待投递报告", pending_count)
        st.caption(f"Outbox：{settings.customer_service.outbox_dir}")
    st.code(
        'DEEPSEARCH_CUSTOMER_SERVICE_INTEGRATION_KEY = "与 CustomerService 一致的专用密钥"\n'
        'DEEPSEARCH_CUSTOMER_SERVICE_API_KEY = "可选：CustomerService 全局 API Key"',
        language="toml",
    )
    st.info(
        "可把以上值放入环境变量或 `.streamlit/secrets.toml`；页面只读取，不显示真实值。",
        icon=":material/security:",
    )

with model_tab:
    # 页面只展示配置方法，绝不回显真实 API Key。
    st.subheader("模型与密钥")
    st.caption("使用环境变量或 `.streamlit/secrets.toml` 配置。缺少 Key 时自动使用确定性报告器。")
    st.code(
        'DEEPSEARCH_API_KEY = "your-key"\nDEEPSEARCH_BASE_URL = "https://api.openai.com/v1"\nDEEPSEARCH_MODEL = "gpt-4o-mini"',
        language="toml",
    )
    st.info("密钥不会写入 `config.json`，保存普通设置时也会主动清空密钥字段。", icon=":material/security:")
