"""个人自动任务：按计划执行完整研究并按需投递。"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from deepsearch.bootstrap import DeepSearchAgent, apply_profile
from deepsearch.domain.models import ReportSpecification, ResearchBrief
from deepsearch.presentation.web.styles import render_page_header
from deepsearch.presentation.web.support import (
    RunProgressSnapshot,
    RunProgressTracker,
    background_executor,
    format_elapsed,
    get_settings,
    progress_details_markdown,
    wait_for_background_result,
)
from deepsearch.scheduling.topics import ResearchTaskManager, ResearchTaskScheduler, ScheduledResearchTask

settings = get_settings()
render_page_header(
    "AUTOMATIONS",
    "自动任务",
    "把需要定期处理的信息交给助手。任务会重新检索、整理并生成报告，不复用旧链接。",
)

manager = ResearchTaskManager(settings)
weekday_labels = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}

with st.expander("新建自动任务", icon=":material/add_task:", expanded=not manager.list()):
    with st.form("automation-add"):
        name = st.text_input("任务名称", placeholder="例如：AI 行业日报")
        question = st.text_area("要完成的任务", placeholder="例如：总结最近一天 AI Agent 领域的重要变化")
        schedule_left, schedule_middle, schedule_right = st.columns(3)
        schedule_type = schedule_left.selectbox(
            "周期", ["daily", "weekly", "once"],
            format_func=lambda value: {"daily": "每天", "weekly": "每周", "once": "单次"}[value],
        )
        run_time = schedule_middle.time_input("执行时间")
        run_date = schedule_right.date_input("单次日期")
        weekdays = st.multiselect(
            "每周执行日", list(weekday_labels), default=[0, 1, 2, 3, 4],
            format_func=lambda value: weekday_labels[value],
        )
        domain = st.text_input("知识领域", value="通用")
        information_types = st.pills(
            "信息类型", ["新闻", "公告", "知识", "研究", "数据", "政策"],
            default=["新闻", "公告"], selection_mode="multi",
        )
        time_scope = st.selectbox("信息时间范围", ["最近 24 小时", "最近 7 天", "最近 30 天", "不限"])
        delivery_left, delivery_right = st.columns(2)
        profile = delivery_left.selectbox("研究强度", ["快速", "均衡", "深度"], index=1)
        target_words = delivery_right.number_input("目标篇幅（字）", 500, 5000, 1500, 100)
        deliver = st.checkbox("完成后自动推送到 CustomerService")
        classification = st.segmented_control(
            "文档密级", ["public", "internal", "confidential"], default="internal",
        )
        add_task = st.form_submit_button("保存任务", type="primary", icon=":material/save:")

    if add_task:
        try:
            task = ScheduledResearchTask(
                name=name.strip(),
                question=question.strip(),
                schedule_type=schedule_type,
                run_time=run_time.strftime("%H:%M"),
                weekdays=tuple(weekdays),
                run_date=run_date.strftime("%Y-%m-%d") if schedule_type == "once" else "",
                profile=profile,
                deliver_to_customer_service=deliver,
                data_classification=str(classification or "internal"),
                brief=ResearchBrief(
                    domain=domain.strip() or "通用",
                    information_types=tuple(information_types or ["知识"]),
                    time_scope=time_scope,
                    report=ReportSpecification(target_words=int(target_words)),
                ),
            )
            manager.add_task(task)
        except ValueError as exc:
            st.error(str(exc), icon=":material/error:")
        else:
            st.toast("自动任务已保存。", icon=":material/check_circle:")
            st.rerun()

tasks = manager.list()
if not tasks:
    st.info("还没有自动任务。", icon=":material/event_busy:")
    st.stop()

rows = [{
    "任务": task.name,
    "周期": {"daily": "每天", "weekly": "每周", "once": "单次"}[task.schedule_type],
    "时间": task.run_time,
    "强度": task.profile,
    "推送": task.data_classification if task.deliver_to_customer_service else "不推送",
    "状态": "启用" if task.enabled else "停用",
} for task in tasks]
st.dataframe(pd.DataFrame(rows), hide_index=True, key="automation-table")

selected_name = st.selectbox("选择任务", [task.name for task in tasks])
selected_task = manager.get(selected_name)
with st.container(horizontal=True):
    if st.button("立即运行", type="primary", icon=":material/play_arrow:"):
        if settings.mock_llm and settings.runtime_mode != "mock":
            st.error("自动研究需要先配置模型。", icon=":material/key:")
        else:
            scheduler = ResearchTaskScheduler(
                settings,
                agent_factory=lambda selected_profile: DeepSearchAgent(apply_profile(settings, selected_profile)),
            )
            tracker = RunProgressTracker("正在读取自动任务配置…")
            status = st.status(
                f"正在执行“{selected_name}” · 准备任务 · 已用时 0 秒",
                expanded=False,
                type="compact",
            )
            with status:
                progress_detail = st.empty()
            progress_detail.markdown(progress_details_markdown(tracker.snapshot()))

            def render_task_progress(snapshot: RunProgressSnapshot) -> None:
                """自动任务也在页面线程按秒刷新，与首页保持相同状态语义。"""

                status.update(
                    label=(
                        f"正在执行“{selected_name}” · {snapshot.current_stage} · "
                        f"已用时 {format_elapsed(snapshot.total_seconds)}"
                    ),
                    state="running",
                )
                progress_detail.markdown(progress_details_markdown(snapshot))

            try:
                # 阻塞的检索和模型调用进入共享有限线程池；主线程只负责
                # 读取不可变进度快照并按整秒更新原生 Streamlit 状态条。
                future = background_executor().submit(scheduler.run_now, selected_name, tracker.update)
                result = wait_for_background_result(future, tracker, render_task_progress)
            except Exception as exc:
                failed = tracker.finish()
                progress_detail.markdown(progress_details_markdown(failed))
                status.update(
                    label=(
                        f"任务未完成 · {failed.current_stage} · "
                        f"用时 {format_elapsed(failed.total_seconds)}"
                    ),
                    state="error",
                    expanded=False,
                )
                st.error(str(exc), icon=":material/error:")
            else:
                completed = tracker.finish()
                progress_detail.markdown(progress_details_markdown(completed))
                status.update(
                    label=(
                        f"任务完成 · 用时 {format_elapsed(completed.total_seconds)} · "
                        f"{result.report_path.name}"
                    ),
                    state="complete",
                    expanded=False,
                )
                st.success(f"报告已生成：{result.report_path}", icon=":material/check_circle:")
    if selected_task and st.button("停用" if selected_task.enabled else "启用", icon=":material/pause_circle:"):
        manager.set_enabled(selected_name, not selected_task.enabled)
        st.rerun()
    if st.button("删除", icon=":material/delete:"):
        manager.remove(selected_name)
        st.rerun()
