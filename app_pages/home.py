"""ChatGPT 风格的统一直接回答、搜索与研究工作台。"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import streamlit as st

from deepsearch.application.verification import canonicalize_source_section
from deepsearch.domain.models import (
    RESEARCH_INFORMATION_TYPES,
    AgentRequest,
    AgentRunResult,
    ReportSpecification,
    ResearchBrief,
    WorkMode,
)
from deepsearch.infrastructure.customer_service import CustomerServicePublisher, DeliveryArtifact, DeliveryError
from deepsearch.presentation.web.delivery import (
    DATA_CLASSIFICATION_OPTIONS,
    conversation_delivery_external_id,
    data_classification_value,
)
from deepsearch.presentation.web.information_types import (
    render_information_type_editor,
    request_information_type_editor,
)
from deepsearch.presentation.web.report_downloads import render_report_download_menu
from deepsearch.presentation.web.scroll_controls import render_scroll_controls
from deepsearch.presentation.web.search_results import render_search_response
from deepsearch.presentation.web.styles import ASSISTANT_ICON, USER_ICON, inject_shortcut_tooltips, render_scorecard
from deepsearch.presentation.web.support import (
    BackgroundRunHandle,
    RunProgressSnapshot,
    RunProgressTracker,
    active_background_run,
    background_executor,
    cancel_background_run,
    chat_history_from_messages,
    claim_submission,
    create_agent,
    discard_background_run,
    format_elapsed,
    get_settings,
    mark_error_retried,
    persist_run_error_for_conversation,
    persist_run_result_for_conversation,
    persist_user_message,
    progress_details_markdown,
    prompt_shortcut_store,
    record_delivery,
    register_background_run,
    run_from_message,
    stable_message_id,
    wait_for_background_result,
)

MODE_LABELS = {
    "问答": WorkMode.CHAT,
    "搜索": WorkMode.SEARCH,
    "研究": WorkMode.RESEARCH,
}


def fill_composer(prompt: str, shortcut_id: str) -> None:
    """快捷输入按钮只预填输入框，必须由用户再次点击发送才会执行。"""

    # 任务执行期间首页仍会保留快捷入口，但不能让它再改写正在运行的
    # 输入状态；否则用户切换回当前会话时可能误以为这是第二个任务。
    if st.session_state.get("agent_run_state") in {"pending", "running"}:
        return
    st.session_state["assistant-draft"] = prompt
    st.session_state["shortcut-tooltip-suppress"] = shortcut_id


def prepare_run() -> None:
    """输入提交前标记待执行，使同一轮页面能立即渲染可靠的停止按钮。"""

    st.session_state.agent_run_state = "pending"
    st.session_state.pending_run_id = uuid4().hex


def request_run_stop() -> None:
    """只在用户明确点击时取消任务；普通页面切换不会触发此路径。"""

    run_id = str(st.session_state.get("active_run_id", ""))
    handle = active_background_run(run_id)
    if handle is not None:
        st.session_state.resumable_run = {
            "conversation_id": handle.conversation_id,
            "question": handle.question,
            "requested_mode": handle.requested_mode.value,
        }
    cancel_background_run(run_id)
    st.session_state.active_run_id = ""
    st.session_state.agent_run_state = "stopped"


def queue_run(question: str, mode: WorkMode) -> None:
    """把追问或模式重跑放入下一轮，并复用与输入提交相同的状态机。"""

    st.session_state.pending_question = question
    st.session_state.pending_work_mode = mode.value
    prepare_run()


def queue_mode_rerun(question: str, mode: WorkMode) -> None:
    """用另一模式重跑原问题，不在会话中重复追加用户消息。"""

    st.session_state.pending_resume = True
    queue_run(question, mode)


def submit_composer(submitted_text: object | None = None) -> None:
    """读取输入框草稿并提交；空内容保持原状态。"""

    # 发送按钮和 CCv2 的 Enter 触发器可能在一次前端重绘中先后回调。
    # pending_question 是本轮尚未消费的提交闸门，running/active_run_id 则
    # 覆盖已经启动的任务；三者都存在时直接忽略后续事件，避免重复建任务。
    active_run_id = str(st.session_state.get("active_run_id", ""))
    if (
        st.session_state.get("agent_run_state") in {"pending", "running"}
        or str(st.session_state.get("pending_question", "")).strip()
        or (active_run_id and active_background_run(active_run_id) is not None)
    ):
        return

    # CCv2 的 Enter 事件携带浏览器中此刻的文本，避免它与 text_area
    # 状态同步恰好并发时读到上一帧草稿；可见发送按钮仍读取 session_state。
    value = (
        submitted_text.get("text", "")
        if isinstance(submitted_text, dict)
        else submitted_text
        if submitted_text is not None
        else st.session_state.get("assistant-draft", "")
    )
    question = str(value).strip()
    if not question:
        return
    mode = MODE_LABELS.get(
        str(st.session_state.get("work_mode", "问答")),
        WorkMode.CHAT,
    )
    if not claim_submission(
        str(st.session_state.get("submission_scope_id", "")),
        question,
        mode,
    ):
        return
    st.session_state["assistant-draft"] = ""
    queue_run(question, mode)


def resume_stopped_run() -> None:
    """重新执行已停止的原问题，并复用原用户消息而不是重复追加。"""

    resumable = dict(st.session_state.get("resumable_run", {}))
    question = str(resumable.get("question", "")).strip()
    if not question:
        return
    try:
        mode = WorkMode(str(resumable.get("requested_mode", "chat")))
    except ValueError:
        mode = WorkMode.CHAT
    st.session_state.resumable_run = {}
    st.session_state.pending_resume = True
    queue_run(question, mode)


def handle_was_cancelled(handle: BackgroundRunHandle) -> bool:
    """兼容热更新前没有 cancelled 字段的进程内旧任务句柄。"""

    cancelled = getattr(handle, "cancelled", None)
    explicitly_stopped = (
        st.session_state.get("agent_run_state") == "stopped"
        and st.session_state.get("active_run_id") != handle.run_id
    )
    return bool((cancelled is not None and cancelled.is_set()) or explicitly_stopped)


def retry_failed_run(message_id: str, question: str, requested_mode: str) -> None:
    """一次性消费错误卡片的重试入口，并复用原问题消息。"""

    try:
        mode = WorkMode(requested_mode)
    except ValueError:
        mode = WorkMode.CHAT
    mark_error_retried(message_id)
    st.session_state.pending_resume = True
    queue_run(question, mode)


def append_message_once(message: dict) -> None:
    """把持久化结果合并回页面状态，避免恢复同一 Future 时重复显示。"""

    message_id = str(message.get("message_id", ""))
    if message_id and any(str(item.get("message_id", "")) == message_id for item in st.session_state.messages):
        return
    st.session_state.messages.append(message)


def render_error_message(message: dict, message_key: str, *, include_status: bool = True) -> None:
    """渲染可跨页面恢复的失败信息和原问题重试入口。"""

    stage = str(message.get("stop_reason", "执行任务"))
    elapsed = str(message.get("summary", ""))
    label = f"未完成 · {stage}" + (f" · 用时 {elapsed}" if elapsed else "")
    if include_status:
        with st.status(label, state="error", expanded=False, type="compact"):
            steps = message.get("progress_steps", [])
            if steps:
                lines = ["**执行步骤与说明**", ""]
                for step in steps:
                    duration = float(step.get("duration_seconds", 0.0) or 0.0)
                    duration_label = "不足 1 秒" if duration < 1 else format_elapsed(duration)
                    lines.append(f"- :material/check_circle: **{step.get('stage', '执行任务')}** · {duration_label}  ")
                    lines.append(f"  {str(step.get('detail', '')).strip() or '该步骤没有附加说明。'}")
                st.markdown("\n".join(lines))
    error_text = str(message.get("content", "运行失败，请稍后重试。"))
    st.error(error_text, icon=":material/error:")
    with st.container(
        horizontal=True,
        wrap=True,
        horizontal_alignment="right",
        vertical_alignment="center",
        gap="small",
        key=f"error-actions-{message_key}",
    ):
        if "模型" in error_text or "SSL" in error_text or "连接" in error_text:
            st.page_link(
                "app_pages/settings.py",
                label="前往模型设置",
                icon=":material/settings:",
                width=148,
            )
        question = str(message.get("question", "")).strip()
        if question and not message.get("retried"):
            st.button(
                "重新执行",
                icon=":material/refresh:",
                key=f"retry-{message_key}",
                width=148,
                on_click=retry_failed_run,
                args=(
                    str(message.get("message_id", "")),
                    question,
                    str(message.get("requested_mode", "auto")),
                ),
            )


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
        # 旧记录可能保存了模型生成的裸 URL 来源区；展示时同样依据结构化
        # Source 重建，确保历史报告和新报告使用一致的可点击卡片。
        st.markdown(canonicalize_source_section(result.report, result.sources))
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


def render_active_background_run(
    handle: BackgroundRunHandle,
    stop_button_slot=None,
    output_slot=None,
) -> None:
    """等待或接回同一后台任务，并把最终状态写回它发起时的会话。"""

    tracker = handle.tracker
    # 运行槽位在耗时等待前已经按 run_id 占位；页面被组件重跑时，新脚本
    # 会接管同一个 Delta 路径，不再把上一轮灰色状态留成第二条“思考中”。
    target = (
        output_slot
        if output_slot is not None
        else st.container(key=f"active-run-output-{handle.run_id}")
    )
    with target, st.chat_message("assistant", avatar=ASSISTANT_ICON):
        # status 的外层 key 与 run_id 固定；流式刷新只改内部 Markdown，避免
        # 用户展开后因组件重建而自动收起。
        with st.container(key=f"thinking-{handle.run_id}"):
            status = st.status("思考中 · 点击查看步骤", expanded=False, type="compact")
            with status:
                progress_detail = st.empty()
        progress_detail.markdown(progress_details_markdown(tracker.snapshot()))

        def render_progress(snapshot: RunProgressSnapshot) -> None:
            progress_detail.markdown(progress_details_markdown(snapshot))

        consumed = False
        try:
            run = wait_for_background_result(handle.future, tracker, render_progress)
        except Exception as exc:
            consumed = True
            failed = tracker.finish()
            if handle_was_cancelled(handle):
                assistant_message = None
            else:
                try:
                    assistant_message = persist_run_error_for_conversation(
                        exc,
                        failed,
                        settings,
                        handle.conversation_id,
                        handle.question,
                        handle.requested_mode,
                        handle.run_id,
                    )
                except ValueError:
                    assistant_message = None
            st.session_state.last_run = None
            st.session_state.last_result = None
            if (
                assistant_message is not None
                and st.session_state.get("current_conversation_id") == handle.conversation_id
            ):
                append_message_once(assistant_message)
            progress_detail.markdown(progress_details_markdown(failed))
            status.update(
                label=f"未完成 · {failed.current_stage} · 用时 {format_elapsed(failed.total_seconds)}",
                state="error",
            )
            if handle_was_cancelled(handle):
                st.caption(":material/stop_circle: 已停止生成 · 未保存未完成的结果")
            elif assistant_message is not None:
                render_error_message(assistant_message, handle.run_id, include_status=False)
            else:
                st.error(str(exc), icon=":material/error:")
        else:
            consumed = True
            completed = tracker.finish()
            completed_mode = {
                WorkMode.CHAT: "问答",
                WorkMode.SEARCH: "搜索",
                WorkMode.RESEARCH: "研究",
            }[run.resolved_mode]
            assistant_message = persist_run_result_for_conversation(
                run,
                settings,
                handle.conversation_id,
                handle.run_id,
            )
            progress_detail.markdown(progress_details_markdown(completed))
            status.update(
                label=f"已完成 · {completed_mode}模式 · 用时 {format_elapsed(completed.total_seconds)}",
                state="complete",
            )
            if st.session_state.get("current_conversation_id") == handle.conversation_id:
                append_message_once(assistant_message)
                st.session_state.last_run = run
                st.session_state.last_result = run.research
                if run.chat is not None:
                    render_chat(run)
                elif run.search is not None:
                    render_search(run, handle.run_id)
                else:
                    render_research(run, handle.run_id)
            else:
                st.info("后台任务已完成，结果已保存到发起任务的原对话。", icon=":material/task_alt:")
        finally:
            # Streamlit 页面切换会以内部 BaseException 终止当前脚本。此时
            # consumed 仍为 False，必须保留注册表与运行状态供返回时接续。
            if consumed:
                if stop_button_slot is not None:
                    stop_button_slot.empty()
                discard_background_run(handle.run_id)
                if st.session_state.get("active_run_id") == handle.run_id:
                    st.session_state.active_run_id = ""
                    st.session_state.agent_run_state = "idle"
                # 立即按最终状态重绘输入区：运行按钮恢复为发送按钮，失败
                # 卡片也只保留当前可用的一次性操作。
                st.rerun()


def finalize_background_run_without_render(handle: BackgroundRunHandle) -> bool:
    """在查看其他对话时静默归档已完成任务，不把其 UI 混入当前记录。"""

    if not handle.future.done():
        return False
    snapshot = handle.tracker.finish()
    try:
        run = handle.future.result()
    except Exception as exc:
        if not handle_was_cancelled(handle):
            try:
                persist_run_error_for_conversation(
                    exc,
                    snapshot,
                    settings,
                    handle.conversation_id,
                    handle.question,
                    handle.requested_mode,
                    handle.run_id,
                )
            except ValueError:
                pass
    else:
        try:
            persist_run_result_for_conversation(
                run,
                settings,
                handle.conversation_id,
                handle.run_id,
            )
        except ValueError:
            pass
    discard_background_run(handle.run_id)
    if st.session_state.get("active_run_id") == handle.run_id:
        st.session_state.active_run_id = ""
        st.session_state.agent_run_state = "idle"
    return True


def render_actions(run) -> None:
    if run.chat is not None:
        # 问答不联网，因此在回复后保留显式升级入口。回调复用原问题，
        # 不会再向会话中写入一条相同的用户消息。
        with st.container(
            horizontal=True,
            wrap=True,
            horizontal_alignment="right",
            vertical_alignment="center",
            gap="xsmall",
            key="chat-rerun-actions",
        ):
            st.button(
                "改用搜索重跑",
                icon=":material/search:",
                key="rerun-chat-search",
                width="content",
                on_click=queue_mode_rerun,
                args=(run.chat.question, WorkMode.SEARCH),
            )
            st.button(
                "改用研究重跑",
                icon=":material/biotech:",
                key="rerun-chat-research",
                width="content",
                on_click=queue_mode_rerun,
                args=(run.chat.question, WorkMode.RESEARCH),
            )
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
        st.button(
            f"改用{alternate_label}重跑",
            icon=":material/refresh:",
            key="rerun-alternate",
            width="content",
            on_click=queue_mode_rerun,
            args=(
                run.search.query if run.search is not None else run.research.question,
                alternate,
            ),
        )
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
render_scroll_controls(on_submit=submit_composer)

# pending 表示本次 rerun 是一次正常提交。running 必须保留：页面切换、
# 设置页往返和普通组件重绘都会重跑脚本，不能据此推断用户要求停止。
if st.session_state.agent_run_state == "pending":
    st.session_state.agent_run_state = "running"

# 欢迎区是主页本身的一部分，不再跟随任务状态或消息数量卸载。否则任务
# 完成后的必要 rerun 会让整块内容消失，视觉上像被导航到了另一个页面。
show_home_intro = True
if show_home_intro:
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
if st.session_state.messages:
    with st.container(key="conversation-thread"):
        for index, message in enumerate(st.session_state.messages):
            # “重新执行”会保留失败记录用于本地追溯；一旦它已被新任务取代，
            # 页面不再重复展示整张红色错误卡，避免被误认为并行回复进程。
            if message.get("kind") == "error" and message.get("retried"):
                continue
            role = message.get("role", "assistant")
            message_key = stable_message_id(message, index)
            with st.chat_message(
                role,
                avatar=ASSISTANT_ICON if role == "assistant" else USER_ICON,
            ):
                message_run = run_from_message(st.session_state.messages, index)
                if role == "assistant" and message.get("kind") == "error":
                    render_error_message(message, message_key)
                elif message_run is not None and message_run.chat is not None:
                    render_chat(message_run)
                elif message_run is not None and message_run.search is not None:
                    render_search(message_run, message_key)
                elif message_run is not None and message_run.research is not None:
                    render_research(message_run, message_key)
                else:
                    st.markdown(message.get("content", ""))

active_run_id = str(st.session_state.get("active_run_id", ""))
current_conversation_id = str(st.session_state.get("current_conversation_id", ""))
active_handle = active_background_run(active_run_id) if active_run_id else None
current_active_handle = (
    active_handle
    if active_handle is not None and active_handle.conversation_id == current_conversation_id
    else None
)
foreign_run_active = active_handle is not None and current_active_handle is None
if foreign_run_active and finalize_background_run_without_render(active_handle):
    # 结果已经写回其原会话；当前记录无需显示状态条，也不再锁定输入框。
    active_run_id = ""
    active_handle = None
    foreign_run_active = False

if st.session_state.agent_run_state == "stopped":
    with st.container(horizontal=True, horizontal_alignment="center", key="run-stop-notice"):
        st.caption(":material/stop_circle: 已停止生成 · 未保存未完成的结果")
    st.session_state.agent_run_state = "idle"

resumable = st.session_state.get("resumable_run", {})
resumable_for_current = bool(
    isinstance(resumable, dict)
    and resumable.get("question")
    and resumable.get("conversation_id") == current_conversation_id
    and current_active_handle is None
)
current_run_active = current_active_handle is not None or (
    st.session_state.agent_run_state == "running" and not active_run_id
)
composer_locked = current_run_active or foreign_run_active or resumable_for_current

# 先占住运行结果在主页面中的稳定位置，再渲染底部输入栏和执行耗时等待。
# pending_run_id 与随后注册的 active_run_id 相同，因此提交轮和恢复轮会复用
# 同一个 key，Streamlit 能正确替换旧元素而不是并排保留淡化副本。
run_output_id = active_run_id or str(st.session_state.get("pending_run_id", ""))
active_run_output_slot = (
    st.container(key=f"active-run-output-{run_output_id}")
    if current_run_active and run_output_id
    else None
)

stop_button_slot = None
with st.bottom:
    with st.container(key="composer-shell"):
        # 输入、模式和操作统一收进一张前景卡片。模式胶囊放在左下角，
        # 与高级选项并列，避免再为三个选项额外占用一整条顶栏。
        with st.container(key="composer-input-card", gap=None):
            st.text_area(
                "问题",
                key="assistant-draft",
                height=68,
                max_chars=1600,
                placeholder=(
                    "另一条对话正在运行，请返回原对话操作…"
                    if foreign_run_active
                    else "随心输入…"
                ),
                disabled=composer_locked,
                label_visibility="collapsed",
                persist_state="session",
            )
            with st.container(
                horizontal=True,
                wrap=False,
                horizontal_alignment="distribute",
                vertical_alignment="center",
                gap="small",
                key="composer-actions",
            ):
                with st.container(
                    horizontal=True,
                    vertical_alignment="center",
                    gap="xsmall",
                    key="composer-tools",
                ):
                    with st.container(key="composer-mode-pill", width="content"):
                        selected_mode = st.segmented_control(
                            "工作模式",
                            list(MODE_LABELS),
                            default=st.session_state.work_mode,
                            key="composer-mode",
                            label_visibility="collapsed",
                            persist_state="session",
                            disabled=composer_locked,
                        )
                        st.session_state.work_mode = selected_mode
                    with st.popover("高级选项", width=100):
                        profile = st.segmented_control(
                            "研究强度",
                            ["快速", "均衡", "深度"],
                            default=st.session_state.research_profile,
                        )
                        st.session_state.research_profile = profile
                        # 知识领域不再作为高级选项暴露；当前流程内部保持通用领域，
                        # 避免移除控件后改变既有研究提示词的默认语义。
                        research_domain = "通用"
                        information_type_options = list(dict.fromkeys([
                            *RESEARCH_INFORMATION_TYPES,
                            *settings.custom_information_types,
                        ]))
                        # 添加入口也使用原生 pills，确保与左侧类型胶囊拥有
                        # 完全相同的高度、圆角和文字/图标对齐方式。
                        with st.container(
                            horizontal=True,
                            vertical_alignment="bottom",
                            gap="xsmall",
                            key="information-type-controls",
                        ):
                            information_types = st.pills(
                                "信息类型",
                                information_type_options,
                                default=["知识", "新闻"],
                                selection_mode="multi",
                                key="assistant-types",
                            )
                            st.pills(
                                "添加信息类型",
                                [":material/add:"],
                                key="add-information-type",
                                label_visibility="collapsed",
                                on_change=request_information_type_editor,
                                args=(
                                    "add-information-type",
                                    "show-home-information-type-editor",
                                ),
                            )
                        if st.session_state.get(
                            "show-home-information-type-editor", False
                        ):
                            render_information_type_editor(
                                settings,
                                editor_key="show-home-information-type-editor",
                                form_key="composer-add-information-type",
                                existing_options=information_type_options,
                                success_message="信息类型已添加，并同步到搜索设置。",
                            )
                        time_scope = st.selectbox(
                            "时间范围", ["不限", "最近 24 小时", "最近 7 天", "最近 30 天", "最近一年"]
                        )
                        region_label = st.selectbox("搜索地区", ["中国大陆", "美国", "全球"])
                        target_words = st.slider("目标篇幅（字）", 500, 5000, 1500, 100)
                        audience = st.text_input("目标读者", value="通用读者")
                        sections = st.multiselect(
                            "报告章节",
                            ["结论", "关键发现", "分析", "对比分析", "局限", "建议", "参考来源"],
                            default=["结论", "关键发现", "分析", "局限", "参考来源"],
                        )
                        custom_instructions = st.text_area(
                            "补充要求", placeholder="例如：给出可执行建议，并区分事实与判断。"
                        )
                with st.container(
                    horizontal=True,
                    horizontal_alignment="right",
                    vertical_alignment="center",
                    gap="xsmall",
                    key="composer-submit-controls",
                ):
                    with st.container(key="composer-model-settings", width="content"):
                        st.page_link(
                            "app_pages/settings.py",
                            label="模型设置",
                            icon=":material/settings:",
                            width="content",
                        )
                    if current_run_active:
                        stop_button_slot = st.empty()
                        stop_button_slot.button(
                            "暂停生成",
                            icon=":material/pause:",
                            key="composer-action-running",
                            on_click=request_run_stop,
                            help="暂停当前生成，之后可继续执行",
                        )
                    elif resumable_for_current:
                        st.button(
                            "继续执行",
                            icon=":material/play_arrow:",
                            type="primary",
                            key="composer-action-resume",
                            on_click=resume_stopped_run,
                            help="从原问题重新开始执行",
                        )
                    elif foreign_run_active:
                        st.button(
                            "其他对话运行中",
                            icon=":material/pending:",
                            key="composer-action-foreign",
                            disabled=True,
                            width=156,
                        )
                    else:
                        st.button(
                            "发送",
                            icon=":material/arrow_upward:",
                            type="primary",
                            key="composer-action-send",
                            on_click=submit_composer,
                            help="提交当前问题",
                        )

prompt = st.session_state.pop("pending_question", "")
pending_work_mode = st.session_state.pop("pending_work_mode", "")
resume_existing_message = bool(st.session_state.pop("pending_resume", False))

if prompt:
    prompt = str(prompt).strip()
    run_id = str(st.session_state.pop("pending_run_id", "")) or uuid4().hex
    st.session_state.agent_run_state = "running"
    requested_mode = WorkMode(pending_work_mode) if pending_work_mode else MODE_LABELS.get(selected_mode, WorkMode.CHAT)
    matching_user_index = next(
        (
            index
            for index in range(len(st.session_state.messages) - 1, -1, -1)
            if st.session_state.messages[index].get("role") == "user"
            and str(st.session_state.messages[index].get("content", "")).strip() == prompt
        ),
        -1,
    )
    reuse_user_message = resume_existing_message and matching_user_index >= 0
    if not reuse_user_message:
        user_message_id = uuid4().hex
        user_message = {
            "message_id": user_message_id,
            "role": "user",
            "content": prompt,
            "mode": requested_mode.value,
        }
        st.session_state.messages.append(user_message)
        persist_user_message(prompt, requested_mode, user_message_id)
    tracker = RunProgressTracker("正在理解你的需求并准备运行…")
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
    # 复用本轮已经从 Secrets 合并好的配置，避免任务创建前再次加载配置时
    # 把设置页显示的本地问答模型丢失。
    agent = create_agent(profile, settings=settings)
    previous = st.session_state.get("last_result")
    # AgentRequest 在页面线程构造，后台线程只执行领域服务；这样即使页面
    # 被切走，任务也不依赖已经失效的 Streamlit ScriptRunContext。
    # “继续执行”和错误卡片重试都复用历史中的原用户消息。构造上下文时
    # 截止到该问题之前，既不重复发送原问题，也不把失败诊断当作模型对话。
    history_messages = (
        st.session_state.messages[:matching_user_index]
        if reuse_user_message
        else st.session_state.messages[:-1]
    )
    request = AgentRequest(
        question=prompt,
        mode=requested_mode,
        brief=brief,
        region={"中国大陆": "CN", "美国": "US", "全球": "ALL"}[region_label],
        conversation_id=st.session_state.current_conversation_id,
        chat_history=chat_history_from_messages(history_messages),
    )

    def execute_run() -> AgentRunResult:
        if requested_mode == WorkMode.RESEARCH and previous is not None:
            result = agent.follow_up(previous, prompt, tracker.update)
            return AgentRunResult(requested_mode, WorkMode.RESEARCH, research=result)
        return agent.run(request, tracker.update)

    handle = BackgroundRunHandle(
        run_id=run_id,
        conversation_id=str(st.session_state.current_conversation_id),
        question=prompt,
        requested_mode=requested_mode,
        tracker=tracker,
        future=background_executor().submit(execute_run),
    )
    register_background_run(handle)
    st.session_state.active_run_id = run_id
    # 用户消息已经加入历史，立即进入一次新的稳定绘制：历史区只负责
    # 渲染这条消息，运行槽位随后接回 Future。若在本轮末尾临时再画一次
    # 用户消息，下次重跑时旧 Delta 会淡化残留，看起来像出现两个用户。
    st.rerun()
elif st.session_state.agent_run_state == "running":
    # 返回对话页时按 run_id 接回原 Future；页面导航不重新提交问题。
    resumed_run_id = str(st.session_state.get("active_run_id", ""))
    any_active_handle = active_background_run(resumed_run_id)
    handle = (
        any_active_handle
        if any_active_handle is not None
        and any_active_handle.conversation_id == str(st.session_state.get("current_conversation_id", ""))
        else None
    )
    if handle is not None:
        render_active_background_run(handle, stop_button_slot, active_run_output_slot)
    elif any_active_handle is None and resumed_run_id:
        # 仅进程重启会使短期注册表丢失；此时明确提示，不伪装为仍在运行。
        st.session_state.active_run_id = ""
        st.session_state.agent_run_state = "idle"
        st.warning("后台任务上下文已丢失，应用可能已重启，请从原问题重新执行。", icon=":material/restart_alt:")

last_run = st.session_state.get("last_run")
if last_run is not None and st.session_state.messages:
    render_actions(last_run)
