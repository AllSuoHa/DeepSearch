import importlib.util
import json
import os
import tempfile
import tomllib
import unittest
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4


@unittest.skipUnless(importlib.util.find_spec("streamlit"), "Streamlit UI extra not installed")
class StreamlitAppTests(unittest.TestCase):
    def test_custom_information_type_is_shared_by_settings_and_advanced_options(self):
        from streamlit.testing.v1 import AppTest

        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            config.write_text(
                json.dumps({
                    "runtime_mode": "mock",
                    "search_content_type": "专利",
                    "custom_information_types": ["专利"],
                }),
                encoding="utf-8",
            )

            home = AppTest.from_file(str(root / "streamlit_app.py"))
            home.session_state["config_path"] = str(config)
            home.run(timeout=20)
            information_types = next(item for item in home.pills if item.label == "信息类型")

            settings_page = AppTest.from_file(str(root / "app_pages" / "settings.py"))
            settings_page.session_state["config_path"] = str(config)
            settings_page.run(timeout=20)
            search_content = next(
                item for item in settings_page.segmented_control
                if item.label == "默认搜索内容"
            )

        self.assertEqual(len(home.exception), 0)
        self.assertEqual(len(settings_page.exception), 0)
        self.assertIn("专利", information_types.options)
        self.assertIn("专利", search_content.options)
        self.assertEqual(search_content.value, "专利")

    def test_submission_claim_is_atomic_for_duplicate_frontend_events(self):
        from deepsearch.domain.models import WorkMode
        from deepsearch.presentation.web.support import claim_submission

        scope_id = uuid4().hex
        with ThreadPoolExecutor(max_workers=8) as executor:
            accepted = list(executor.map(
                lambda _: claim_submission(scope_id, "今天 天气怎么样", WorkMode.CHAT),
                range(8),
            ))

        self.assertEqual(sum(accepted), 1)

    def test_streamlit_chrome_is_transparent_without_clipping_sidebar_controls(self):
        """隐藏工具栏视觉层，但保留透明高度容纳折叠按钮和 logo。"""

        root = Path(__file__).resolve().parents[1]
        config = tomllib.loads((root / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
        css = (root / "assets" / "app.css").read_text(encoding="utf-8")

        self.assertEqual(config["client"]["toolbarMode"], "minimal")
        self.assertIn('[data-testid="stHeader"]', css)
        self.assertIn("height: 3.75rem;", css)
        self.assertIn("border-bottom: 0;", css)
        self.assertIn("overflow: visible;", css)
        self.assertIn('[data-testid="stSidebarCollapsedControl"]', css)
        self.assertIn('[data-testid="stDecoration"]', css)

        entrypoint = (root / "streamlit_app.py").read_text(encoding="utf-8")
        home = (root / "app_pages" / "home.py").read_text(encoding="utf-8")
        settings_page = (root / "app_pages" / "settings.py").read_text(encoding="utf-8")
        history = (root / "app_pages" / "history.py").read_text(encoding="utf-8")
        scroll_controls = (
            root / "deepsearch" / "presentation" / "web" / "scroll_controls.py"
        ).read_text(encoding="utf-8")
        information_type_editor = (
            root / "deepsearch" / "presentation" / "web" / "information_types.py"
        ).read_text(encoding="utf-8")
        styles = (root / "deepsearch" / "presentation" / "web" / "styles.py").read_text(
            encoding="utf-8"
        )
        user_avatar = (root / "assets" / "user-avatar.svg").read_text(encoding="utf-8")
        self.assertIn("page_icon=ASSISTANT_ICON", entrypoint)
        self.assertIn("icon_image=ASSISTANT_ICON", entrypoint)
        self.assertIn("avatar=ASSISTANT_ICON", home)
        self.assertIn('else USER_ICON', home)
        self.assertIn('ASSISTANT_ICON = ":material/travel_explore:"', styles)
        self.assertIn('USER_ICON = str(PROJECT_ROOT / "assets" / "user-avatar.svg")', styles)
        self.assertTrue((root / "assets" / "logo-mark.svg").is_file())
        self.assertTrue((root / "assets" / "user-avatar.svg").is_file())
        self.assertIn('stroke="#172033"', user_avatar)
        self.assertIn('stroke-width="2.5"', user_avatar)
        self.assertNotIn("linearGradient", user_avatar)
        self.assertIn('st.title("今天想了解什么？", anchor=False, text_alignment="center")', home)
        self.assertIn('key="result-actions"', home)
        self.assertIn('key="chat-rerun-actions"', home)
        self.assertIn('"改用搜索重跑"', home)
        self.assertIn('"改用研究重跑"', home)
        self.assertIn('"默认搜索内容"', settings_page)
        self.assertNotIn('st.multiselect(\n            "自定义信息类型"', settings_page)
        self.assertIn('st.pills(\n                                "添加信息类型"', home)
        self.assertIn('[":material/add:"]', home)
        self.assertIn('key="information-type-controls"', home)
        self.assertIn('key="add-information-type"', home)
        self.assertIn('key="settings-search-content-controls"', settings_page)
        self.assertIn('key="settings-add-information-type"', settings_page)
        self.assertNotIn('with st.popover("添加信息类型"', home)
        self.assertNotIn("@st.dialog", information_type_editor)
        self.assertIn("render_information_type_editor", information_type_editor)
        self.assertIn('horizontal=True', information_type_editor)
        self.assertIn('label_visibility="collapsed"', information_type_editor)
        self.assertIn('key=f"{form_key}-name"', information_type_editor)
        self.assertNotIn('st.text_input("知识领域"', home)
        self.assertIn("*settings.custom_information_types", home)
        self.assertIn('horizontal_alignment="right"', home)
        self.assertNotIn('.st-key-add-information-type button', css)
        self.assertIn('[data-testid="stChatMessageAvatarCustom"]', css)
        self.assertIn('border: 0 !important;', css)
        self.assertIn('background: transparent !important;', css)
        self.assertIn('[data-testid="stIconMaterial"]', css)
        self.assertIn('font-size: 2rem !important;', css)
        self.assertIn('.st-key-composer-input-card', css)
        self.assertIn('.st-key-composer-mode-pill', css)
        self.assertNotIn('.st-key-composer-mode-row', css)
        self.assertIn('[data-testid="stTextAreaRootElement"]', css)
        self.assertIn('.st-key-composer-action-send button', css)
        self.assertIn('border-radius: 999px;', css)
        self.assertIn('[data-testid="stBottomBlockContainer"]', css)
        self.assertIn('.st-key-home-scroll-controls', css)
        self.assertIn('key=f"research-report-{message_key}"', home)
        self.assertIn('[class*="st-key-research-report-"]', css)
        self.assertIn('key=f"research-report-library-{asset_key}"', history)
        self.assertNotIn('.st-key-history-report-preview', css)
        self.assertIn("render_search_response(preview", history)
        self.assertIn("render_report_download_menu(content, title", home)
        self.assertIn("render_report_download_menu(", history)
        self.assertIn('with st.popover("推送"', home)
        self.assertIn('with st.popover("推送"', history)
        self.assertNotIn('with st.popover("修改名称"', history)
        self.assertIn('push_tab, rename_tab = st.tabs(["推送文档", "修改名称"])', history)
        self.assertIn('key="history-filter-bar"', history)
        self.assertIn("st-key-history-report-row-", css)
        self.assertIn('columns[1].markdown(str(item["title"]))', history)
        self.assertIn('with st.popover("推送", icon=":material/send:", width=148', history)
        self.assertNotIn("history-trash-report", history)
        self.assertIn('[data-testid="InputInstructions"]', css)
        self.assertIn('[data-testid="stHorizontalBlock"]', css)
        self.assertIn('key=f"history-report-select-{row_key}"', history)
        self.assertIn('header[0].caption("选择", text_alignment="center")', history)
        self.assertIn('horizontal_alignment="center"', history)
        self.assertIn('vertical_alignment="center"', history)
        self.assertIn('[class*="st-key-history-report-select-"]', css)
        self.assertIn('[data-testid="stExpanderDetails"] > [data-testid="stVerticalBlock"]', css)
        self.assertIn("gap: 0 !important;", css)
        self.assertIn("justify-content: center;", css)
        self.assertIn('label > div:not([data-testid])', css)
        self.assertIn("build_library_delivery_artifact", history)
        self.assertIn("DATA_CLASSIFICATION_OPTIONS", history)
        self.assertNotIn('["public", "internal", "confidential"]', history)
        self.assertNotIn("st.dataframe(", history)
        self.assertIn("render_report_group(report_kind", history)
        self.assertIn('[class*="st-key-result-card-"]', css)
        self.assertIn("st.markdown(ASSISTANT_ICON)", home)
        self.assertIn("render_scroll_controls(on_submit=submit_composer)", home)
        self.assertIn("st.components.v2.component", scroll_controls)
        self.assertNotIn("components.v1", scroll_controls)
        self.assertIn('aria-label="回到顶部"', scroll_controls)
        self.assertIn('aria-label="到达底部"', scroll_controls)
        self.assertIn('document.querySelector(\'[data-testid="stAppScrollToBottomContainer"]\')', scroll_controls)
        self.assertIn('document.querySelector(\'[data-testid="stMain"]\')', scroll_controls)
        self.assertIn("document.addEventListener('wheel', handleWheel", scroll_controls)
        self.assertIn("classList.toggle('is-visible'", scroll_controls)
        self.assertIn("window.setTimeout(hideControls, 1400)", scroll_controls)
        self.assertIn("const cancelScrollAnimation", scroll_controls)
        self.assertIn("requestAnimationFrame(tick)", scroll_controls)
        self.assertNotIn("behavior: 'smooth'", scroll_controls)
        self.assertIn("new MutationObserver(scheduleUpdate)", scroll_controls)
        self.assertIn("right.maximum - left.maximum", scroll_controls)
        self.assertIn('[data-testid="stMainBlockContainer"]', scroll_controls)
        self.assertIn(".st-key-conversation-thread", scroll_controls)
        self.assertIn("contentRight + 12", scroll_controls)
        self.assertIn("window.innerHeight - composerTop + 8", scroll_controls)
        self.assertIn("handleComposerKeyDown", scroll_controls)
        self.assertIn("__deepsearchComposerSubmitHandled", scroll_controls)
        self.assertIn("target.setRangeText('\\\\n'", scroll_controls)
        self.assertIn("inputType: 'insertLineBreak'", scroll_controls)
        self.assertIn("setTriggerValue('submit', { id: submissionId, text: target.value })", scroll_controls)
        self.assertIn("event.stopImmediatePropagation()", scroll_controls)
        self.assertIn("on_submit_change", scroll_controls)
        self.assertIn('get("home-scroll-controls")', scroll_controls)
        self.assertIn("render_scroll_controls(on_submit=submit_composer)", home)
        self.assertIn("show_home_intro = True", home)
        self.assertIn('str(st.session_state.get("pending_question", "")).strip()', home)
        self.assertIn("claim_submission(", home)
        self.assertIn('run_output_id = active_run_id or str(st.session_state.get("pending_run_id", ""))', home)
        self.assertIn('key=f"active-run-output-{run_output_id}"', home)
        self.assertIn("render_active_background_run(handle, stop_button_slot, active_run_output_slot)", home)
        prompt_branch = home.split("if prompt:", 1)[1].split(
            'elif st.session_state.agent_run_state == "running":', 1
        )[0]
        self.assertNotIn('st.chat_message("user"', prompt_branch)
        self.assertIn("register_background_run(handle)", prompt_branch)
        self.assertIn("st.rerun()", prompt_branch)
        submission_branch = home.split("if prompt:", 1)[1].split(
            'elif st.session_state.agent_run_state == "running":',
            1,
        )[0]
        self.assertNotIn('with st.chat_message("user"', submission_branch)
        self.assertIn("register_background_run(handle)", submission_branch)
        self.assertIn("st.rerun()", submission_branch)
        self.assertNotIn("button.disabled", scroll_controls)
        self.assertNotIn(":disabled", scroll_controls)

    def test_cli_and_web_support_use_repository_root(self):
        """CLI 启动器和页面适配层必须共享同一个仓库根目录。"""

        from deepsearch.presentation.cli import PROJECT_ROOT as cli_root
        from deepsearch.presentation.web.support import PROJECT_ROOT as web_root

        expected = Path(__file__).resolve().parents[1]
        self.assertEqual(cli_root, expected)
        self.assertEqual(web_root, expected)

    def test_progress_is_grouped_collapsible_and_reports_elapsed_time(self):
        from deepsearch.presentation.web.support import (
            RunProgressTracker,
            format_elapsed,
            progress_details_markdown,
            progress_stage_label,
            wait_for_background_result,
        )

        root = Path(__file__).resolve().parents[1]
        home = (root / "app_pages" / "home.py").read_text(encoding="utf-8")
        tasks = (root / "app_pages" / "tasks.py").read_text(encoding="utf-8")

        self.assertEqual(progress_stage_label("search"), "搜索来源")
        self.assertEqual(progress_stage_label("fetch"), "读取正文")
        self.assertEqual(progress_stage_label("validate"), "验证报告")
        self.assertEqual(format_elapsed(5.9), "5 秒")
        self.assertEqual(format_elapsed(65), "1 分 5 秒")

        clock = [0.0]
        tracker = RunProgressTracker(clock=lambda: clock[0])
        tracker.update("plan", "正在制定搜索计划…")
        clock[0] = 2.0
        tracker.update("search", "正在执行第一轮搜索…")
        clock[0] = 5.0
        snapshot = tracker.snapshot()
        self.assertEqual([step.stage for step in snapshot.steps], ["理解问题", "制定计划", "搜索来源"])
        self.assertEqual(snapshot.steps[1].duration_seconds, 2.0)
        self.assertEqual(snapshot.steps[2].duration_seconds, 3.0)
        self.assertIn("思考步骤与说明", progress_details_markdown(snapshot))

        class ThreeTickFuture:
            def __init__(self):
                self.polls = 0

            def done(self):
                self.polls += 1
                return self.polls > 3

            @staticmethod
            def result():
                return "done"

        tick_clock = [0.0]
        tick_tracker = RunProgressTracker(clock=lambda: tick_clock[0])
        tick_tracker.wait_for_change = lambda timeout: tick_clock.__setitem__(0, tick_clock[0] + timeout)
        ticks = []
        result = wait_for_background_result(
            ThreeTickFuture(),
            tick_tracker,
            lambda item: ticks.append(int(item.total_seconds)),
        )
        self.assertEqual(result, "done")
        self.assertEqual(ticks, [0, 1, 2])

        self.assertIn('expanded=False, type="compact"', home)
        self.assertIn('key=f"thinking-{handle.run_id}"', home)
        self.assertIn("message_run = run_from_message(st.session_state.messages, index)", home)
        progress_update = home.split("def render_progress", 1)[1].split("consumed = False", 1)[0]
        self.assertNotIn("status.update", progress_update)
        self.assertIn('type="compact"', tasks)
        self.assertIn("用时", home)
        self.assertIn("用时", tasks)
        self.assertNotIn("status.write", home)
        self.assertNotIn("status.write", tasks)
        self.assertIn("stop_button_slot.empty()", home)

    def test_web_command_starts_root_streamlit_entry(self):
        """deepsearch web 应启动根目录入口，并把工作目录固定在仓库根。"""

        from deepsearch.presentation.cli import PROJECT_ROOT, _run_web

        with (
            patch("deepsearch.presentation.cli.importlib.util.find_spec", return_value=object()),
            patch(
                "deepsearch.presentation.cli.subprocess.run",
                return_value=SimpleNamespace(returncode=0),
            ) as run,
        ):
            self.assertEqual(_run_web(8501), 0)

        command = run.call_args.args[0]
        self.assertEqual(command[-2], str(PROJECT_ROOT / "streamlit_app.py"))
        self.assertEqual(run.call_args.kwargs["cwd"], PROJECT_ROOT)

    def test_default_page_renders_without_exception(self):
        from streamlit.testing.v1 import AppTest

        root = Path(__file__).resolve().parents[1]
        with patch.dict(os.environ, {"DEEPSEARCH_MODE": "mock"}):
            app = AppTest.from_file(str(root / "streamlit_app.py")).run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(app.title)
        self.assertTrue(app.button)

    def test_home_keeps_shortcuts_as_drafts_and_exposes_stop_control(self):
        from streamlit.testing.v1 import AppTest

        from deepsearch.infrastructure.storage import DEFAULT_PROMPT_SHORTCUTS

        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            config.write_text(
                json.dumps({"runtime_mode": "mock", "conversation_dir": "data/conversations"}),
                encoding="utf-8",
            )
            app = AppTest.from_file(str(root / "streamlit_app.py"))
            app.session_state["config_path"] = str(config)
            app.run(timeout=20)
            expected_prompt = dict(DEFAULT_PROMPT_SHORTCUTS)["影视平台"]
            shortcut = next(button for button in app.button if button.key == "shortcut-default-1")
            self.assertFalse(shortcut.help)
            app = shortcut.click().run(timeout=20)

            self.assertEqual(len(app.exception), 0)
            draft = next(item for item in app.text_area if item.key == "assistant-draft")
            self.assertEqual(draft.value, expected_prompt)
            self.assertEqual(len(app.chat_message), 0)

            # 模拟一次正在执行的提交。停止按钮使用显式回调，因此点击后
            # 能可靠中断当前 rerun、清理状态，并且不会伪装成助手消息。
            stop_app = AppTest.from_file(str(root / "streamlit_app.py"))
            stop_app.session_state["config_path"] = str(config)
            stop_app.session_state["agent_run_state"] = "pending"
            stop_app.run(timeout=20)
            stop_button = next(button for button in stop_app.button if button.label == "暂停生成")
            stop_app = stop_button.click().run(timeout=20)
            self.assertEqual(len(stop_app.exception), 0)
            self.assertEqual(stop_app.session_state["agent_run_state"], "idle")
            self.assertEqual(len(stop_app.warning), 0)
            self.assertEqual(len(stop_app.chat_message), 0)
            self.assertTrue(any("已停止生成" in caption.value for caption in stop_app.caption))
            self.assertFalse(any(button.label == "暂停生成" for button in stop_app.button))

            # 完整任务结束后，结果、完成状态和恢复后的提交控件必须出现在
            # 同一轮页面中，不能残留“停止生成”按钮。
            completed_app = AppTest.from_file(str(root / "streamlit_app.py"))
            completed_app.session_state["config_path"] = str(config)
            completed_app.run(timeout=20)
            draft = next(item for item in completed_app.text_area if item.key == "assistant-draft")
            completed_app = draft.set_value("现在几点").run(timeout=20)
            send_button = next(button for button in completed_app.button if button.label == "发送")
            completed_app = send_button.click().run(timeout=30)
            self.assertEqual(len(completed_app.exception), 0)
            self.assertFalse(any(button.label == "暂停生成" for button in completed_app.button))
            self.assertTrue(any(button.label == "发送" for button in completed_app.button))
            self.assertTrue(any("北京时间" in item.value for item in completed_app.markdown))
            self.assertTrue(any(item.value == "今天想了解什么？" for item in completed_app.title))

        source = (root / "app_pages" / "home.py").read_text(encoding="utf-8")
        self.assertIn('key="assistant-draft"', source)
        self.assertIn('key="composer-input-card", gap=None', source)
        self.assertIn('key="composer-mode-pill", width="content"', source)
        self.assertNotIn('key="composer-mode-row"', source)
        self.assertIn('with st.popover("高级选项", width=100)', source)
        self.assertIn("on_click=submit_composer", source)
        self.assertIn('"暂停生成"', source)

    def test_saved_model_failure_renders_after_page_return_with_retry(self):
        """错误重试只能消费一次，并复用持久化的原用户消息。"""

        from streamlit.testing.v1 import AppTest
        from deepsearch.infrastructure.storage import ConversationStore

        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            config = temporary_root / "config.json"
            config.write_text(
                json.dumps({"runtime_mode": "mock", "conversation_dir": "data/conversations"}),
                encoding="utf-8",
            )
            store = ConversationStore(temporary_root / "data" / "conversations")
            conversation = store.create()
            user_message = {
                "message_id": "b" * 32,
                "role": "user",
                "content": "测试研究问题",
                "mode": "research",
            }
            error_message = {
                "message_id": "c" * 32,
                "role": "assistant",
                "kind": "error",
                "mode": "research",
                "requested_mode": "research",
                "question": "测试研究问题",
                "content": "模型服务在响应完成前关闭了加密连接。",
                "stop_reason": "整理证据",
                "summary": "2 分 24 秒",
                "progress_steps": [{
                    "stage": "整理证据",
                    "detail": "正在生成证据映射",
                    "duration_seconds": 144.0,
                }],
            }
            store.append(conversation, user_message)
            store.append(conversation, error_message)

            app = AppTest.from_file(str(root / "streamlit_app.py"))
            app.session_state["config_path"] = str(config)
            app.session_state["current_conversation_id"] = conversation["id"]
            app.session_state["messages"] = [user_message, error_message]
            app.run(timeout=20)
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(any(item.state == "error" for item in app.status))
            self.assertTrue(any("关闭了加密连接" in item.value for item in app.error))
            retry_button = next(button for button in app.button if button.label == "重新执行")
            app = retry_button.click().run(timeout=30)
            restored = store.load(conversation["id"])

        self.assertEqual(len(app.exception), 0)
        self.assertFalse(any(button.label == "重新执行" for button in app.button))
        self.assertTrue(any(button.label == "发送" for button in app.button))
        self.assertFalse(any("关闭了加密连接" in item.value for item in app.error))
        self.assertEqual(sum(message.get("role") == "user" for message in restored["messages"]), 1)
        self.assertTrue(restored["messages"][1]["retried"])
        self.assertEqual(restored["messages"][-1]["role"], "assistant")
        home_source = (root / "app_pages" / "home.py").read_text(encoding="utf-8")
        self.assertIn('label="前往模型设置"', home_source)

    def test_active_run_is_not_rendered_inside_another_conversation(self):
        """后台状态必须按对话归属渲染，其他记录只显示自己的消息。"""

        from streamlit.testing.v1 import AppTest

        from deepsearch.domain.models import WorkMode
        from deepsearch.presentation.web.support import (
            BackgroundRunHandle,
            RunProgressTracker,
            cancel_background_run,
            register_background_run,
        )

        root = Path(__file__).resolve().parents[1]
        run_id = "e" * 32
        handle = BackgroundRunHandle(
            run_id=run_id,
            conversation_id="a" * 32,
            question="原对话中的研究",
            requested_mode=WorkMode.RESEARCH,
            tracker=RunProgressTracker(),
            future=Future(),
        )
        register_background_run(handle)
        try:
            with tempfile.TemporaryDirectory() as directory:
                config = Path(directory) / "config.json"
                config.write_text(
                    json.dumps({"runtime_mode": "mock", "conversation_dir": "data/conversations"}),
                    encoding="utf-8",
                )
                app = AppTest.from_file(str(root / "streamlit_app.py"))
                app.session_state["config_path"] = str(config)
                app.session_state["current_conversation_id"] = "b" * 32
                app.session_state["active_run_id"] = run_id
                app.session_state["agent_run_state"] = "running"
                app.session_state["messages"] = [{
                    "message_id": "f" * 32,
                    "role": "user",
                    "content": "另一条对话",
                    "mode": "auto",
                }]
                app.run(timeout=20)
        finally:
            cancel_background_run(run_id)

        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.chat_message), 1)
        self.assertFalse(any("思考中" in status.label for status in app.status))
        self.assertFalse(any(button.label == "停止生成" for button in app.button))
        draft = next(item for item in app.text_area if item.key == "assistant-draft")
        self.assertTrue(draft.disabled)

    def test_stopped_run_can_restart_without_duplicating_user_message(self):
        """继续执行从原问题重跑，但历史中不再追加一条相同用户消息。"""

        from streamlit.testing.v1 import AppTest

        from deepsearch.infrastructure.storage import ConversationStore

        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            config = temporary_root / "config.json"
            config.write_text(
                json.dumps({"runtime_mode": "mock", "conversation_dir": "data/conversations"}),
                encoding="utf-8",
            )
            store = ConversationStore(temporary_root / "data" / "conversations")
            conversation = store.create()
            user_message = {
                "message_id": "1" * 32,
                "role": "user",
                "content": "继续测试研究",
                "mode": "research",
            }
            store.append(conversation, user_message)

            app = AppTest.from_file(str(root / "streamlit_app.py"))
            app.session_state["config_path"] = str(config)
            app.session_state["current_conversation_id"] = conversation["id"]
            app.session_state["messages"] = [user_message]
            app.session_state["resumable_run"] = {
                "conversation_id": conversation["id"],
                "question": "继续测试研究",
                "requested_mode": "research",
            }
            app.run(timeout=20)
            continue_button = next(button for button in app.button if button.label == "继续执行")
            app = continue_button.click().run(timeout=30)

            restored = store.load(conversation["id"])

        self.assertEqual(len(app.exception), 0)
        self.assertFalse(any(button.label == "继续执行" for button in app.button))
        self.assertTrue(any(button.label == "发送" for button in app.button))
        self.assertEqual(
            sum(message.get("role") == "user" for message in restored["messages"]),
            1,
        )
        self.assertEqual(restored["messages"][-1]["role"], "assistant")

    def test_question_mode_handles_current_time_without_search_or_artifact_actions(self):
        from streamlit.testing.v1 import AppTest

        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            config = temporary_root / "config.json"
            config.write_text(
                json.dumps({
                    "runtime_mode": "mock",
                    "conversation_dir": "data/conversations",
                    "reports_dir": "reports",
                }),
                encoding="utf-8",
            )
            app = AppTest.from_file(str(root / "streamlit_app.py"))
            app.session_state["config_path"] = str(config)
            app.run(timeout=20)
            mode_control = next(item for item in app.segmented_control if item.label == "工作模式")
            self.assertEqual(mode_control.options, ["问答", "搜索", "研究"])
            self.assertEqual(mode_control.value, "问答")

            draft = next(item for item in app.text_area if item.key == "assistant-draft")
            app = draft.set_value("现在几点").run(timeout=20)
            send_button = next(button for button in app.button if button.label == "发送")
            app = send_button.click().run(timeout=20)
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(any("北京时间" in item.value for item in app.markdown))
            self.assertTrue(any(button.label == "改用搜索重跑" for button in app.button))
            self.assertTrue(any(button.label == "改用研究重跑" for button in app.button))
            self.assertFalse(any(button.label == "开始研究" for button in app.button))
            self.assertFalse(any(button.label == "下载" for button in app.button))
            self.assertFalse(any(button.label == "推送" for button in app.button))
            self.assertFalse((temporary_root / "data" / "artifacts").exists())
            self.assertFalse((temporary_root / "reports").exists())
            conversation_files = list((temporary_root / "data" / "conversations").glob("*.json"))
            self.assertEqual(len(conversation_files), 1)
            saved = json.loads(conversation_files[0].read_text(encoding="utf-8"))
            assistant = saved["messages"][-1]
            self.assertEqual(assistant["kind"], "chat")
            self.assertEqual(assistant["artifact_path"], "")
            self.assertEqual(assistant["sources"], [])

            search_button = next(button for button in app.button if button.label == "改用搜索重跑")
            app = search_button.click().run(timeout=30)
            rerun_saved = json.loads(conversation_files[0].read_text(encoding="utf-8"))
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(
                sum(message.get("role") == "user" for message in rerun_saved["messages"]),
                1,
            )
            self.assertEqual(rerun_saved["messages"][-1]["kind"], "search")

    def test_sidebar_recent_records_have_no_tooltip_and_all_records_can_be_deleted(self):
        from streamlit.testing.v1 import AppTest

        from deepsearch.infrastructure.storage import ConversationStore

        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            config = temporary_root / "config.json"
            config.write_text(
                json.dumps({
                    "runtime_mode": "mock",
                    "conversation_dir": "data/conversations",
                }),
                encoding="utf-8",
            )
            store = ConversationStore(temporary_root / "data" / "conversations")
            for index in range(8):
                conversation = store.create()
                store.append(conversation, {
                    "role": "user",
                    "content": f"历史记录 {index}",
                    "mode": "search",
                })

            app = AppTest.from_file(str(root / "streamlit_app.py"))
            app.session_state["config_path"] = str(config)
            app.run(timeout=20)
            recent_buttons = [
                button for button in app.button
                if str(button.key or "").startswith("conversation-")
            ]
            self.assertEqual(len(recent_buttons), 6)
            self.assertTrue(all(not button.help for button in recent_buttons))

            records_app = AppTest.from_file(str(root / "app_pages" / "conversations.py"))
            records_app.session_state["config_path"] = str(config)
            records_app.run(timeout=20)
            self.assertEqual(len(records_app.exception), 0)
            delete_button = next(button for button in records_app.button if button.label == "删除记录")
            deleted_id = str(delete_button.key).removeprefix("delete-conversation-")
            records_app = delete_button.click().run(timeout=20)
            records_app = next(
                button for button in records_app.button if button.label == "确认删除记录"
            ).click().run(timeout=20)

            self.assertEqual(len(records_app.exception), 0)
            self.assertIsNone(store.load(deleted_id))
            self.assertEqual(len(store.list(limit=None)), 7)
            self.assertFalse(any(button.label == "确认删除记录" for button in records_app.button))
            self.assertTrue(any("对话记录已删除" in toast.value for toast in records_app.toast))

    def test_settings_page_renders_search_model_and_integration_tabs(self):
        from streamlit.testing.v1 import AppTest

        root = Path(__file__).resolve().parents[1]
        with patch.dict(os.environ, {"DEEPSEARCH_MODE": "mock"}):
            app = AppTest.from_file(str(root / "app_pages" / "settings.py")).run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        labels = [tab.label for tab in app.tabs]
        self.assertTrue(any("搜索" in label for label in labels))
        self.assertTrue(any("研究" in label and "快速回答" in label for label in labels))
        self.assertTrue(any("知识库联动" in label for label in labels))

    def test_library_page_uses_the_shared_confirmed_trash_action(self):
        from streamlit.testing.v1 import AppTest

        from deepsearch.infrastructure.storage import FileArtifactTrash

        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            reports = temporary_root / "reports"
            reports.mkdir()
            report = reports / "answer.md"
            report.write_text("# Answer\n\nBody", encoding="utf-8")
            remaining_report = reports / "remaining.md"
            remaining_report.write_text("# Remaining\n\nBody", encoding="utf-8")
            os.utime(report, (200, 200))
            os.utime(remaining_report, (100, 100))
            config = temporary_root / "config.json"
            config.write_text(
                json.dumps({
                    "runtime_mode": "mock",
                    "reports_dir": "reports",
                    "conversation_dir": "data/conversations",
                }),
                encoding="utf-8",
            )
            app = AppTest.from_file(str(root / "app_pages" / "history.py"))
            app.session_state["config_path"] = str(config)
            app.run(timeout=20)
            deleted_label = next(
                option
                for option in next(
                    item for item in app.selectbox if item.label == "打开报告"
                ).options
                if "Answer" in option
            )
            app = next(
                item for item in app.checkbox if item.label == "选择文档"
            ).check().run(timeout=20)
            app = next(
                button for button in app.button if button.label == "移入回收站（1）"
            ).click().run(timeout=20)
            app = next(
                button for button in app.button if button.label == "确认批量移除"
            ).click().run(timeout=20)

            self.assertEqual(len(app.exception), 0)
            self.assertFalse(report.exists())
            self.assertTrue(remaining_report.is_file())
            report_picker = next(item for item in app.selectbox if item.label == "打开报告")
            self.assertNotIn(deleted_label, report_picker.options)
            self.assertIn("Remaining", report_picker.value)
            self.assertTrue(any((temporary_root / "data" / "trash").glob("*-answer.md")))
            self.assertFalse(any(button.label == "确认批量移除" for button in app.button))
            self.assertFalse(any(button.label == "移到回收站" for button in app.button))
            self.assertTrue(any("1 份文档" in toast.value for toast in app.toast))

            trash_app = AppTest.from_file(str(root / "app_pages" / "trash.py"))
            trash_app.session_state["config_path"] = str(config)
            trash_app.run(timeout=20)
            self.assertEqual(len(trash_app.exception), 0)
            trash_app = next(
                button for button in trash_app.button if button.label == "恢复到资料库"
            ).click().run(timeout=20)
            self.assertEqual(len(trash_app.exception), 0)
            self.assertTrue(report.is_file())

            # 回收站的永久删除确认框使用相同关闭协议，避免出现同类残留。
            trash = FileArtifactTrash(
                temporary_root / "data" / "trash",
                (reports, temporary_root / "data" / "artifacts"),
            )
            trashed_again = trash.move(report)
            trash_app = AppTest.from_file(str(root / "app_pages" / "trash.py"))
            trash_app.session_state["config_path"] = str(config)
            trash_app.run(timeout=20)
            trash_app = next(
                button for button in trash_app.button if button.label == "永久删除"
            ).click().run(timeout=20)
            trash_app = next(
                button for button in trash_app.button if button.label == "确认永久删除"
            ).click().run(timeout=20)
            self.assertEqual(len(trash_app.exception), 0)
            self.assertFalse(trashed_again.exists())
            self.assertFalse(any(button.label == "确认永久删除" for button in trash_app.button))
            self.assertTrue(any("已永久删除" in toast.value for toast in trash_app.toast))

    def test_library_groups_reports_and_supports_confirmed_batch_trash(self):
        from streamlit.testing.v1 import AppTest

        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            reports = temporary_root / "reports"
            artifacts = temporary_root / "data" / "artifacts"
            reports.mkdir(parents=True)
            artifacts.mkdir(parents=True)
            first = reports / "research.md"
            second = artifacts / "search_搜索.md"
            first.write_text("# 研究文档\n\n正文", encoding="utf-8")
            second.write_text("# 搜索文档\n\n正文", encoding="utf-8")
            config = temporary_root / "config.json"
            config.write_text(
                json.dumps({
                    "runtime_mode": "mock",
                    "reports_dir": "reports",
                    "conversation_dir": "data/conversations",
                }),
                encoding="utf-8",
            )

            app = AppTest.from_file(str(root / "app_pages" / "history.py"))
            app.session_state["config_path"] = str(config)
            app.run(timeout=20)

            self.assertEqual(len(app.exception), 0)
            self.assertEqual(
                {item.label for item in app.expander},
                {"搜索快照（1）", "研究报告（1）"},
            )
            self.assertEqual(
                next(item for item in app.selectbox if item.label == "排序方式").options,
                list(("最近更新", "最早更新", "名称升序", "名称降序", "文件从大到小", "文件从小到大")),
            )
            selectors = [item for item in app.checkbox if item.label == "选择文档"]
            app = selectors[0].check().run(timeout=20)
            selectors = [item for item in app.checkbox if item.label == "选择文档"]
            app = selectors[1].check().run(timeout=20)
            batch_button = next(
                button for button in app.button if button.label == "移入回收站（2）"
            )
            app = batch_button.click().run(timeout=20)
            app = next(
                button for button in app.button if button.label == "确认批量移除"
            ).click().run(timeout=20)

            self.assertEqual(len(app.exception), 0)
            self.assertFalse(first.exists())
            self.assertFalse(second.exists())
            self.assertEqual(len(list((temporary_root / "data" / "trash").glob("*.trash.json"))), 2)
            self.assertTrue(any("2 份文档" in toast.value for toast in app.toast))

    def test_library_search_preview_reuses_chat_result_cards(self):
        """资料库应从会话恢复来源字段，并显示聊天页同款搜索卡片。"""

        from streamlit.testing.v1 import AppTest

        from deepsearch.infrastructure.storage import ConversationStore

        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            artifacts = temporary_root / "data" / "artifacts"
            conversations = temporary_root / "data" / "conversations"
            reports = temporary_root / "reports"
            artifacts.mkdir(parents=True)
            reports.mkdir()
            artifact = artifacts / "2026-09-09_120000_官方入口_搜索.md"
            artifact.write_text(
                "# 官方入口\n\n找到一个入口。\n\n## 搜索结果\n\n"
                "### 1. [官方文档](https://docs.example.org/guide)\n\n"
                "官方信息 · 可信来源\n\n保存的摘要\n",
                encoding="utf-8",
            )
            store = ConversationStore(conversations)
            conversation = store.create()
            store.append(conversation, {
                "role": "assistant",
                "kind": "search",
                "mode": "search",
                "artifact_path": str(artifact),
                "question": "官方入口",
                "summary": "找到一个入口。",
                "content": artifact.read_text(encoding="utf-8"),
                "sources": [{
                    "title": "官方文档",
                    "url": "https://docs.example.org/guide",
                    "snippet": "结构化摘要",
                    "provider": "fixture-search",
                    "resource_type": "官方信息",
                    "risk_level": "可信来源",
                }],
            })
            config = temporary_root / "config.json"
            config.write_text(
                json.dumps({
                    "runtime_mode": "mock",
                    "reports_dir": "reports",
                    "conversation_dir": "data/conversations",
                }),
                encoding="utf-8",
            )

            app = AppTest.from_file(str(root / "app_pages" / "history.py"))
            app.session_state["config_path"] = str(config)
            app.run(timeout=20)

            self.assertEqual(len(app.exception), 0)
            markdown_values = [item.value for item in app.markdown]
            self.assertIn("#### 官方信息", markdown_values)
            self.assertIn(
                "**[官方文档](https://docs.example.org/guide)**",
                markdown_values,
            )
            self.assertTrue(any("结构化摘要" in item.value for item in app.caption))
            self.assertTrue(any("docs.example.org · fixture-search" in item.value for item in app.caption))
            link = next(item for item in app.get("link_button") if item.label == "打开链接")
            self.assertEqual(link.url, "https://docs.example.org/guide")
            self.assertFalse(any("### 1. [官方文档]" in value for value in markdown_values))

    def test_library_download_menu_switches_between_report_formats(self):
        from streamlit.testing.v1 import AppTest

        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            reports = temporary_root / "reports"
            reports.mkdir()
            (reports / "report.md").write_text("# 中文报告\n\n## 结论\n\n正文", encoding="utf-8")
            config = temporary_root / "config.json"
            config.write_text(
                json.dumps({
                    "runtime_mode": "mock",
                    "reports_dir": "reports",
                    "conversation_dir": "data/conversations",
                }),
                encoding="utf-8",
            )

            app = AppTest.from_file(str(root / "app_pages" / "history.py"))
            app.session_state["config_path"] = str(config)
            app.run(timeout=20)

            format_picker = next(item for item in app.selectbox if item.label == "文件类型")
            self.assertEqual(
                format_picker.options,
                ["Markdown", "Word", "PDF", "TXT", "JSON", "HTML"],
            )
            self.assertTrue(any(item.label == "下载 Markdown" for item in app.get("download_button")))

            app = format_picker.select("Word").run(timeout=20)
            word_download = next(
                item for item in app.get("download_button") if item.label == "下载 Word"
            )
            self.assertTrue(str(word_download.key).endswith("-docx"))
            self.assertEqual(len(app.exception), 0)

    def test_library_rename_form_updates_the_selected_document(self):
        from streamlit.testing.v1 import AppTest

        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            reports = temporary_root / "reports"
            reports.mkdir()
            report = reports / "2026-09-09_120000_old-name.md"
            report.write_text("# 旧名称\n\n正文", encoding="utf-8")
            config = temporary_root / "config.json"
            config.write_text(
                json.dumps({
                    "runtime_mode": "mock",
                    "reports_dir": "reports",
                    "conversation_dir": "data/conversations",
                }),
                encoding="utf-8",
            )

            app = AppTest.from_file(str(root / "app_pages" / "history.py"))
            app.session_state["config_path"] = str(config)
            app.run(timeout=20)
            name_input = next(item for item in app.text_input if item.label == "新名称")
            app = name_input.set_value("新文档名称").run(timeout=20)
            app = next(button for button in app.button if button.label == "保存名称").click().run(timeout=20)

            self.assertEqual(len(app.exception), 0)
            self.assertFalse(report.exists())
            renamed = next(reports.glob("*.md"))
            self.assertIn("新文档名称", renamed.name)
            self.assertTrue(renamed.read_text(encoding="utf-8").startswith("# 新文档名称\n"))
            picker = next(item for item in app.selectbox if item.label == "打开报告")
            self.assertIn("新文档名称", picker.value)
            self.assertTrue(any("文档已重命名" in toast.value for toast in app.toast))

    def test_automatic_tasks_have_their_own_page(self):
        from streamlit.testing.v1 import AppTest

        root = Path(__file__).resolve().parents[1]
        with patch.dict(os.environ, {"DEEPSEARCH_MODE": "mock"}):
            app = AppTest.from_file(str(root / "app_pages" / "tasks.py")).run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any(item.label == "任务名称" for item in app.text_input))


if __name__ == "__main__":
    unittest.main()
