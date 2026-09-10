"""ChatGPT 风格的统一直接回答、搜索与研究工作台。"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import streamlit as st

from deepsearch.domain.models import AgentRequest, AgentRunResult, ReportSpecification, ResearchBrief, WorkMode
from deepsearch.infrastructure.customer_service import CustomerServicePublisher, DeliveryArtifact, DeliveryError
from deepsearch.presentation.web.delivery import (
    DATA_CLASSIFICATION_OPTIONS,
    conversation_delivery_external_id,
    data_classification_value,
)
from deepsearch.presentation.web.report_downloads import render_report_download_menu
from deepsearch.presentation.web.scroll_controls import render_scroll_controls
from deepsearch.presentation.web.search_results import render_search_response
from deepsearch.presentation.web.styles import ASSISTANT_ICON, USER_ICON, inject_shortcut_tooltips, render_scorecard
from deepsearch.presentation.web.support import (
    RunProgressSnapshot,
    RunProgressTracker,
    background_executor,
    chat_history_from_messages,
    create_agent,
    format_elapsed,
    get_settings,
    persist_run_result,
    persist_user_message,
    progress_details_markdown,
    prompt_shortcut_store,
    record_delivery,
    run_from_message,
    stable_message_id,
    wait_for_background_result,
)

MODE_LABELS = {
    "智能判断": WorkMode.AUTO,
    "搜索": WorkMode.SEARCH,
    "研究": WorkMode.RESEARCH,
}


def fill_composer(prompt: str, shortcut_id: str) -> None:
    """快捷输入按钮只预填输入框，必须由用户再次点击发送才会执行。"""

    st.session_state["assistant-chat"] = prompt
    st.session_state["shortcut-tooltip-suppress"] = shortcut_id


def prepare_run() -> None:
    """输入提交前标记待执行，使同一轮页面能立即渲染可靠的停止按钮。"""

    st.session_state.agent_run_state = "pending"
    st.session_state.pending_run_id = uuid4().hex


def request_run_stop() -> None:
    """按钮回调通过 rerun 中断旧脚本，并留下轻量的停止反馈。"""

    tracker = st.session_state.get("active_run_tracker")
    if isinstance(tracker, RunProgressTracker):
        tracker.cancel()
    st.session_state.active_run_tracker = None
    st.session_state.agent_run_state = "stopped"


def queue_run(question: str, mode: WorkMode) -> None:
    """把追问或模式重跑放入下一轮，并复用与输入提交相同的状态机。"""

    st.session_state.pending_question = question
    st.session_state.pending_work_mode = mode.value
    prepare_run()


def render_chat(run) -> None:
    """直接回答是普通助手消息，不附带搜索或研究专属模块。"""

    st.markdown(run.chat.answer)


def render_search(run, message_key: str = "latest") -> None:
    render_search_response(run.search, message_key)


def render_research(run, message_key: str = "latest") -> None:
    result = run.research
    # 报告正文单独拥有稳定容器，便于在历史消息和流式结束后的重绘中
    # 保持一致的阅读样式，同时不影响搜索卡片和直接回答。
    with st.container(key=f"research-report-{message_key}"):
        st.markdown(result.report)
    with st.expander(
        "证据详情与审校",
        icon=":material/fact_check:",
        key=f"evidence-{message_key}",
    ):
        with st.container(horizontal=True):
            st.badge(f"{len(result.sources)} 个来源", color="blue")
            st.badge(f"{result.rounds} 轮", color="gray")
            st.badge("引用通过" if result.validation.valid else "需要复核", color="green" if result.validation.valid else "red")
        if result.review_summary:
            st.markdown("**独立审校处理**")
            for issue in result.review_summary:
                st.markdown(f"- {issue}")
        if result.sources:
            st.markdown("**来源链接**")
            for source in result.sources:
                if source.url.startswith(("http://", "https://")):
                    st.markdown(f"{source.source_id}. [{source.title}]({source.url})")
                else:
                    st.caption(f"{source.source_id}. {source.title} · 无可访问链接")
        if result.scorecard.dimensions:
            render_scorecard(result.scorecard, key=f"scorecard-{message_key}")
        for trace in result.trace:
            st.caption(f"第 {trace.round_number} 轮 · {trace.decision}")


def render_actions(run) -> None:
    if run.chat is not None:
        # 直接回答已经完成用户当前意图，不再用升级按钮暗示简单问题
        # 必须转成搜索或研究。
        return

    artifact_path = run.artifact_path
    if artifact_path is None or not Path(artifact_path).is_file():
        return
    content = Path(artifact_path).read_text(encoding="utf-8", errors="replace")
    settings = get_settings()
    # 操作项按自身内容确定宽度；窄屏时整颗按钮换行，避免标签被压缩成省略号。
    with st.container(
        horizontal=True,
        wrap=True,
        horizontal_alignment="right",
        vertical_alignment="center",
        gap="xsmall",
        key="result-actions",
    ):
        title = run.search.query if run.search is not None else run.research.question
        render_report_download_menu(content, title, f"home-{Path(artifact_path).stem}")
        with st.popover("复制", icon=":material/content_copy:", width=104, wrap=False):
            st.caption("使用右上角复制按钮")
            st.code(content, language="markdown", height=180)
        with st.popover("继续追问", icon=":material/chat:", width=132, wrap=False):
            follow_up = st.text_input("追问内容", placeholder="针对当前结果继续问…", key="result-follow-up")
            if st.button("发送追问", type="primary", disabled=not bool(follow_up.strip()), key="send-follow-up"):
                queue_run(follow_up.strip(), run.resolved_mode)
                st.rerun()
        alternate = WorkMode.RESEARCH if run.resolved_mode == WorkMode.SEARCH else WorkMode.SEARCH
        alternate_label = "研究" if alternate == WorkMode.RESEARCH else "搜索"
        if st.button(
            f"改用{alternate_label}重跑",
            icon=":material/refresh:",
            key="rerun-alternate",
            width="content",
        ):
            question = run.search.query if run.search is not None else run.research.question
            queue_run(question, alternate)
            st.rerun()
        with st.popover("推送", icon=":material/send:", width=104, wrap=False):
            st.markdown("**推送到 CustomerService**")
            if not settings.customer_service.enabled:
                st.caption("请先在设置中启用知识库联动。")
                st.page_link("app_pages/settings.py", label="前往设置", icon=":material/settings:")
            elif not settings.customer_service.integration_key:
                st.warning("缺少联动密钥，暂时无法推送。", icon=":material/key:")
                st.caption("请在 `.streamlit/secrets.toml` 配置联动密钥后重启页面。")
            else:
                classification = st.segmented_control(
                    "文档密级", DATA_CLASSIFICATION_OPTIONS, default="内部",
                    key="manual-delivery-classification",
                )
                if st.button("确认推送", type="primary", icon=":material/send:"):
                    conversation_id = st.session_state.get("current_conversation_id", "local")
                    kind = "search" if run.search is not None else "research"
                    question = run.search.query if run.search is not None else run.research.question
                    artifact = DeliveryArtifact(
                        external_id=conversation_delivery_external_id(conversation_id, kind, question),
                        title=question,
                        content_path=Path(artifact_path),
                        question=question,
                        kind=kind,
                        data_classification=data_classification_value(classification),
                        metadata={"resolved_mode": run.resolved_mode.value},
                    )
                    try:
                        outcome = CustomerServicePublisher(settings.customer_service).publish_artifact(artifact)
                    except DeliveryError as exc:
                        st.error(str(exc), icon=":material/error:")
                    else:
                        record_delivery(outcome.status)
                        if outcome.status == "indexed":
                            st.success(
                                f"已写入 CustomerService 知识库（文档 {outcome.document_id}）。",
                                icon=":material/check_circle:",
                            )
                        else:
                            st.warning(
                                f"本次未完成入库，结果已进入安全重试队列：{outcome.error}",
                                icon=":material/schedule:",
                            )


settings = get_settings()
shortcut_store = prompt_shortcut_store(settings)

# 固定滚动控件要在耗时研究之前挂载，运行期间也能立即回顶或到达底部。
render_scroll_controls()

# pending 表示本次 rerun 是一次正常提交；若新 rerun 看到上次仍为
# running，说明旧脚本已被用户交互或页面停止中断，应转入 stopped。
if st.session_state.agent_run_state == "pending":
    st.session_state.agent_run_state = "running"
elif st.session_state.agent_run_state == "running":
    # 运行过程中发生新的整页 rerun，说明原轮次已被用户交互中断。
    # 向后台计算发出协作式取消信号，防止它继续进入后续研究阶段。
    interrupted_tracker = st.session_state.get("active_run_tracker")
    if isinstance(interrupted_tracker, RunProgressTracker):
        interrupted_tracker.cancel()
    st.session_state.active_run_tracker = None
    st.session_state.agent_run_state = "stopped"

if not st.session_state.messages:
    st.space("large")
    with st.container(horizontal_alignment="center", key="empty-state"):
        # 固定宽度内层容器让图标与标题共用同一视觉中线。
        with st.container(horizontal_alignment="center", width=64, key="home-logo"):
            st.markdown(ASSISTANT_ICON)
        # 欢迎语不是正文目录标题，不需要可复制的 URL 锚点。关闭锚点也
        # 避免链条图标参与标题宽度计算，令文字与上方标志共用视觉中轴。
        st.title("今天想了解什么？", anchor=False, text_alignment="center")
        st.caption("找链接、查资料，或把复杂问题交给研究模式。", text_alignment="center")
        shortcuts = shortcut_store.list()
        inject_shortcut_tooltips(
            shortcuts,
            str(st.session_state.pop("shortcut-tooltip-suppress", "")),
        )
        with st.container(horizontal=True, horizontal_alignment="center", key="prompt-shortcuts"):
            for shortcut in shortcuts:
                st.button(
                    shortcut["label"],
                    icon=":material/bookmark:",
                    key=f"shortcut-{shortcut['id']}",
                    on_click=fill_composer,
                    args=(shortcut["prompt"], shortcut["id"]),
                )
            with st.popover("管理快捷输入", icon=":material/edit:"):
                action = st.segmented_control(
                    "操作", ["新增", "修改", "删除"], default="新增", key="shortcut-action"
                )
                if action == "新增":
                    with st.form("shortcut-add", border=False):
                        label = st.text_input("快捷名称", placeholder="例如：项目周报", max_chars=28)
                        shortcut_prompt = st.text_area(
                            "填入内容", placeholder="点击快捷按钮后要填入输入框的内容", max_chars=1600
                        )
                        submitted = st.form_submit_button("添加快捷输入", icon=":material/add:", type="primary")
                    if submitted:
                        try:
                            shortcut_store.add(label, shortcut_prompt)
                        except ValueError as exc:
                            st.error(str(exc), icon=":material/error:")
                        else:
                            st.toast("快捷输入已添加。", icon=":material/bookmark_added:")
                            st.rerun()
                elif action == "修改":
                    if not shortcuts:
                        st.caption("还没有可修改的快捷输入。")
                    else:
                        selected_id = st.selectbox(
                            "选择快捷输入",
                            [item["id"] for item in shortcuts],
                            format_func=lambda value: next(
                                item["label"] for item in shortcuts if item["id"] == value
                            ),
                            key="shortcut-edit-selection",
                        )
                        selected_shortcut = next(item for item in shortcuts if item["id"] == selected_id)
                        with st.form(f"shortcut-edit-{selected_id}", border=False):
                            edited_label = st.text_input(
                                "快捷名称", value=selected_shortcut["label"], max_chars=28
                            )
                            edited_prompt = st.text_area(
                                "填入内容", value=selected_shortcut["prompt"], max_chars=1600
                            )
                            submitted = st.form_submit_button(
                                "保存修改", icon=":material/save:", type="primary"
                            )
                        if submitted:
                            try:
                                updated = shortcut_store.update(selected_id, edited_label, edited_prompt)
                            except ValueError as exc:
                                st.error(str(exc), icon=":material/error:")
                            else:
                                if updated:
                                    st.toast("快捷输入已更新。", icon=":material/check_circle:")
                                    st.rerun()
                else:
                    if not shortcuts:
                        st.caption("还没有可删除的快捷输入。")
                    else:
                        selected_id = st.selectbox(
                            "选择快捷输入",
                            [item["id"] for item in shortcuts],
                            format_func=lambda value: next(
                                item["label"] for item in shortcuts if item["id"] == value
                            ),
                            key="shortcut-delete-selection",
                        )
                        selected_shortcut = next(item for item in shortcuts if item["id"] == selected_id)
                        st.caption(f"删除“{selected_shortcut['label']}”不会影响已有对话。")
                        if st.button("删除快捷输入", icon=":material/delete:", key="shortcut-delete-confirm"):
                            shortcut_store.delete(selected_id)
                            st.toast("快捷输入已删除。", icon=":material/delete:")
                            st.rerun()
else:
    with st.container(key="conversation-thread"):
        for index, message in enumerate(st.session_state.messages):
            role = message.get("role", "assistant")
            message_key = stable_message_id(message, index)
            with st.chat_message(
                role,
                avatar=ASSISTANT_ICON if role == "assistant" else USER_ICON,
            ):
                message_run = run_from_message(st.session_state.messages, index)
                if message_run is not None and message_run.chat is not None:
                    render_chat(message_run)
                elif message_run is not None and message_run.search is not None:
                    render_search(message_run, message_key)
                elif message_run is not None and message_run.research is not None:
                    render_research(message_run, message_key)
                else:
                    st.markdown(message.get("content", ""))

if st.session_state.agent_run_state == "stopped":
    with st.container(horizontal=True, horizontal_alignment="center", key="run-stop-notice"):
        st.caption(":material/stop_circle: 已停止生成 · 未保存未完成的结果")
    st.session_state.agent_run_state = "idle"

stop_button_slot = None
with st.bottom:
    with st.container(key="composer-shell"):
        typed = st.chat_input(
            "输入想问、搜索或研究的问题…",
            key="assistant-chat",
            max_chars=1600,
            submit_mode="disable",
            on_submit=prepare_run,
        )
        with st.container(
            horizontal=True,
            vertical_alignment="center",
            key="composer-toolbar",
        ):
            selected_mode = st.segmented_control(
                "工作模式",
                list(MODE_LABELS),
                default=st.session_state.work_mode,
                key="composer-mode",
                label_visibility="collapsed",
                persist_state="session",
            )
            st.session_state.work_mode = selected_mode
            with st.popover("高级选项", icon=":material/tune:"):
                profile = st.segmented_control("研究强度", ["快速", "均衡", "深度"], default=st.session_state.research_profile)
                st.session_state.research_profile = profile
                research_domain = st.text_input("知识领域", value="通用", key="assistant-domain")
                information_types = st.pills(
                    "信息类型", ["新闻", "知识", "公告", "研究", "数据", "政策"],
                    default=["知识", "新闻"], selection_mode="multi", key="assistant-types",
                )
                time_scope = st.selectbox("时间范围", ["不限", "最近 24 小时", "最近 7 天", "最近 30 天", "最近一年"])
                region_label = st.selectbox("搜索地区", ["中国大陆", "美国", "全球"])
                target_words = st.slider("目标篇幅（字）", 500, 5000, 1500, 100)
                audience = st.text_input("目标读者", value="通用读者")
                sections = st.multiselect(
                    "报告章节", ["结论", "关键发现", "分析", "对比分析", "局限", "建议", "参考来源"],
                    default=["结论", "关键发现", "分析", "局限", "参考来源"],
                )
                custom_instructions = st.text_area("补充要求", placeholder="例如：给出可执行建议，并区分事实与判断。")
            if st.session_state.agent_run_state == "running":
                # 保留占位符引用，任务完成时可立即移除旧的停止按钮，
                # 不必等用户再触发一次 rerun 才恢复正常输入状态。
                stop_button_slot = st.empty()
                stop_button_slot.button(
                    "停止生成",
                    icon=":material/stop_circle:",
                    key="composer-stop-generation",
                    on_click=request_run_stop,
                )

prompt = st.session_state.pop("pending_question", "") or typed
pending_work_mode = st.session_state.pop("pending_work_mode", "")

if prompt:
    prompt = str(prompt).strip()
    run_id = str(st.session_state.pop("pending_run_id", "")) or uuid4().hex
    st.session_state.agent_run_state = "running"
    requested_mode = WorkMode(pending_work_mode) if pending_work_mode else MODE_LABELS.get(selected_mode, WorkMode.AUTO)
    user_message_id = uuid4().hex
    user_message = {
        "message_id": user_message_id,
        "role": "user",
        "content": prompt,
        "mode": requested_mode.value,
    }
    st.session_state.messages.append(user_message)
    persist_user_message(prompt, requested_mode, user_message_id)
    with st.chat_message("user", avatar=USER_ICON):
        st.markdown(prompt)
    with st.chat_message("assistant", avatar=ASSISTANT_ICON):
        tracker = RunProgressTracker("正在理解你的需求并准备运行…")
        st.session_state.active_run_tracker = tracker
        # 状态容器本身在运行期间保持稳定，只更新内部占位符。过去每秒
        # status.update(label=...) 都会重建折叠头，导致用户刚展开又收起。
        with st.container(key=f"thinking-{run_id}"):
            status = st.status("思考中 · 点击查看步骤", expanded=False, type="compact")
            with status:
                progress_detail = st.empty()
        progress_detail.markdown(progress_details_markdown(tracker.snapshot()))

        def render_progress(snapshot: RunProgressSnapshot) -> None:
            """仅在页面线程更新 UI；后台 Agent 只写入 tracker。"""

            progress_detail.markdown(progress_details_markdown(snapshot))

        specification = ReportSpecification(
            target_words=int(target_words),
            audience=audience.strip() or "通用读者",
            sections=tuple(sections) or ReportSpecification().sections,
            custom_instructions=custom_instructions.strip(),
        )
        brief = ResearchBrief(
            domain=research_domain.strip() or "通用",
            information_types=tuple(information_types or ["知识"]),
            time_scope=time_scope,
            report=specification,
        )
        try:
            agent = create_agent(profile)
            previous = st.session_state.get("last_result")
            # AgentRequest 必须在页面线程中构造；后台线程不得读取
            # st.session_state，否则会缺少 Streamlit ScriptRunContext。
            request = AgentRequest(
                question=prompt,
                mode=requested_mode,
                brief=brief,
                region={"中国大陆": "CN", "美国": "US", "全球": "ALL"}[region_label],
                conversation_id=st.session_state.current_conversation_id,
                # 当前用户消息已经加入列表，因此从切片中排除它；辅助函数
                # 还会限制条数与长度，避免整份历史进入轻量模型。
                chat_history=chat_history_from_messages(st.session_state.messages[:-1]),
            )

            def execute_run() -> AgentRunResult:
                """后台只执行阻塞研究；持久化和所有 Streamlit 调用留在主线程。"""

                if requested_mode == WorkMode.RESEARCH and previous is not None:
                    result = agent.follow_up(previous, prompt, tracker.update)
                    return AgentRunResult(requested_mode, WorkMode.RESEARCH, research=result)
                return agent.run(request, tracker.update)

            future = background_executor().submit(execute_run)
            run = wait_for_background_result(future, tracker, render_progress)
            assistant_message = persist_run_result(run, run_id)
        except Exception as exc:
            st.session_state.agent_run_state = "idle"
            st.session_state.active_run_tracker = None
            failed = tracker.finish()
            progress_detail.markdown(progress_details_markdown(failed))
            status.update(
                label=(
                    f"未完成 · {failed.current_stage} · "
                    f"用时 {format_elapsed(failed.total_seconds)}"
                ),
                state="error",
            )
            if stop_button_slot is not None:
                stop_button_slot.empty()
            st.error(str(exc), icon=":material/error:")
            if "模型" in str(exc):
                st.page_link("app_pages/settings.py", label="前往模型设置", icon=":material/settings:")
        else:
            st.session_state.agent_run_state = "idle"
            st.session_state.active_run_tracker = None
            completed = tracker.finish()
            completed_mode = {
                WorkMode.CHAT: "直接回答",
                WorkMode.SEARCH: "搜索",
                WorkMode.RESEARCH: "研究",
            }[run.resolved_mode]
            progress_detail.markdown(progress_details_markdown(completed))
            status.update(
                label=(
                    f"已完成 · {completed_mode}模式 · "
                    f"用时 {format_elapsed(completed.total_seconds)}"
                ),
                state="complete",
            )
            if stop_button_slot is not None:
                stop_button_slot.empty()
            st.session_state.last_run = run
            st.session_state.last_result = run.research
            st.session_state.messages.append(assistant_message)
            if run.chat is not None:
                render_chat(run)
            elif run.search is not None:
                render_search(run, run_id)
            else:
                render_research(run, run_id)

last_run = st.session_state.get("last_run")
if last_run is not None and st.session_state.messages:
    render_actions(last_run)
