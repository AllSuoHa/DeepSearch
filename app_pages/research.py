"""研究工作台页面：提交问题、显示实时阶段、报告和上下文追问。"""

from __future__ import annotations

import streamlit as st

from deepsearch.domain.models import ReportSpecification, ResearchBrief
from deepsearch.presentation.web.styles import render_page_header, render_scorecard
from deepsearch.presentation.web.support import create_agent, stream_markdown

SUGGESTIONS = {
    "Agent 生产化": "对比当前主流 Agent 框架的架构、能力边界与生产适用性",
    "RAG 技术选型": "生成式 AI 应用的 RAG 与长上下文方案应如何选择？",
    "研究评估体系": "评估深度研究 Agent 的关键指标、常见失败模式与改进路径",
}
PHASE_LABELS = {
    "plan": "研究规划", "search": "多路搜索", "fetch": "正文读取", "evaluate": "充分性判断",
    "adjust": "策略调整", "verify": "交叉验证", "generate": "报告生成", "validate": "引用校验",
    "score": "质量评分", "saved": "资产保存", "follow_up": "上下文追问",
}
PHASE_PROGRESS = {
    "plan": 8, "search": 24, "fetch": 45, "evaluate": 58, "adjust": 62,
    "verify": 74, "generate": 84, "validate": 92, "score": 97, "saved": 100, "follow_up": 15,
}

render_page_header(
    "Research workbench",
    "研究工作台",
    "在同一空间里完成问题规划、实时研究、报告追问和质量复核。每次运行都是一条可观察、可解释的研究轨迹。",
)

# 最近一次结果既用于顶部质量摘要，也作为下一条消息的追问上下文。
result = st.session_state.get("last_result")
if result is not None:
    render_scorecard(result.scorecard)
    metrics = result.metrics
    columns = st.columns(4)
    columns[0].metric("研究轮次", result.rounds)
    columns[1].metric("证据来源", len(result.sources))
    columns[2].metric("在线正文", metrics.fetched_sources)
    columns[3].metric("耗时", f"{metrics.elapsed_seconds:.1f}s")
    st.caption(f"缓存命中 {metrics.cache_hits} 次 · {result.stop_reason}")

with st.container(key="workbench", border=True):
    control, description = st.columns([1.2, 2], gap="large", vertical_alignment="center")
    with control:
        profile = st.segmented_control(
            "研究强度", ["快速", "均衡", "深度"], default=st.session_state.research_profile,
            key="workbench_profile", help="快速适合事实核查；均衡适合常规分析；深度会扩大轮次与来源容量。",
        )
        st.session_state.research_profile = profile
    description.caption({
        "快速": "1 轮 · 最多 8 个来源 · 适合事实验证",
        "均衡": "按引擎配置运行 · 质量与速度兼顾",
        "深度": "最多 4 轮 · 最多 28 个来源 · 适合复杂决策",
    }[profile])

with st.expander("报告与研究要求", icon=":material/assignment:"):
    st.caption("这些要求会进入查询规划和报告器，不只是显示选项。追问默认继承上一份报告的要求。")
    requirement_left, requirement_right = st.columns(2, gap="large")
    with requirement_left:
        research_domain = st.text_input(
            "知识领域",
            value="通用",
            placeholder="例如：人工智能与大模型",
            key="research_domain",
        )
        information_types = st.pills(
            "信息类型",
            ["新闻", "知识", "公告", "研究", "数据", "政策"],
            default=["知识", "新闻", "公告"],
            selection_mode="multi",
            key="research_information_types",
        )
        time_scope = st.selectbox(
            "时间范围",
            ["不限", "最近 24 小时", "最近 7 天", "最近 30 天", "最近一年"],
            key="research_time_scope",
        )
    with requirement_right:
        output_format = st.segmented_control(
            "文件格式",
            ["markdown", "text", "json"],
            default="markdown",
            key="research_output_format",
        )
        target_words = st.slider("目标篇幅（字）", 300, 5000, 1200, 100, key="research_target_words")
        audience = st.text_input("目标读者", value="通用读者", key="research_audience")
    report_sections = st.multiselect(
        "报告章节",
        ["摘要", "关键结论", "详细分析", "对比分析", "风险与限制", "建议与下一步", "证据局限与争议"],
        default=["摘要", "关键结论", "详细分析", "建议与下一步", "证据局限与争议"],
        key="research_sections",
    )
    custom_instructions = st.text_area(
        "内容与模板要求",
        placeholder="例如：先给结论；按重要性排序；区分已确认事实、推断和建议；最后输出行动清单。",
        key="research_custom_instructions",
    )

# Session State 中保存的消息会在每次 rerun 后重建聊天记录。
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

suggestion = None
if not st.session_state.messages:
    st.caption("选择一个研究模板，或在下方输入自己的问题")
    selected = st.pills("研究模板", list(SUGGESTIONS), label_visibility="collapsed", key="research_suggestion")
    if selected:
        suggestion = SUGGESTIONS[selected]

# pending_question 来自首页模板；pop 可以保证它只自动提交一次。
pending = st.session_state.pop("pending_question", "")
typed = st.chat_input("输入研究问题，Shift + Enter 换行…", key="research_chat", max_chars=1200)
prompt = pending or typed or suggestion

if prompt:
    prompt = prompt.strip()
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        # 研究是同步任务，回调会持续更新同一个进度条和折叠状态块。
        progress_bar = st.progress(3, text="初始化研究上下文")
        status = st.status("正在启动研究智能体…", expanded=True)

        def show_progress(phase: str, message: str) -> None:
            """把应用层阶段事件映射为用户可读的实时 UI。"""

            st.session_state.progress_events.append({"phase": phase, "message": message})
            label = PHASE_LABELS.get(phase, phase)
            progress_bar.progress(PHASE_PROGRESS.get(phase, 50), text=f"{label} · {message}")
            status.write(f"**{label}** · {message}")

        try:
            agent = create_agent(profile)
            previous = st.session_state.get("last_result")
            report_specification = ReportSpecification(
                output_format=str(output_format or "markdown"),
                target_words=int(target_words),
                audience=audience.strip() or "通用读者",
                sections=tuple(report_sections) or ReportSpecification().sections,
                custom_instructions=custom_instructions.strip(),
            )
            brief = ResearchBrief(
                domain=research_domain.strip() or "通用",
                information_types=tuple(information_types or []) or ("知识",),
                time_scope=time_scope,
                report=report_specification,
            )
            # 首次消息启动完整研究；后续消息自动走上下文复用/补搜逻辑。
            result = agent.research(prompt, show_progress, brief) if previous is None else agent.follow_up(previous, prompt, show_progress)
            progress_bar.progress(100, text="研究完成 · 已校验并保存")
            status.update(label="研究完成，报告已通过质量评估", state="complete", expanded=False)
            render_scorecard(result.scorecard)
            st.write_stream(stream_markdown(result.report))
        except Exception as exc:
            status.update(label="研究未完成", state="error", expanded=True)
            st.error(f"研究过程中发生错误：{exc}", icon=":material/error:")
        else:
            # 先保存状态再 rerun，使侧栏、顶部指标和聊天记录保持一致。
            st.session_state.last_result = result
            st.session_state.messages.append({"role": "assistant", "content": result.report})
            st.rerun()
