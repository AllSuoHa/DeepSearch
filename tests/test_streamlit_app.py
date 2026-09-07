import importlib.util
import json
import os
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec("streamlit"), "Streamlit UI extra not installed")
class StreamlitAppTests(unittest.TestCase):
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
        history = (root / "app_pages" / "history.py").read_text(encoding="utf-8")
        scroll_controls = (
            root / "deepsearch" / "presentation" / "web" / "scroll_controls.py"
        ).read_text(encoding="utf-8")
        self.assertIn("page_icon=ASSISTANT_ICON", entrypoint)
        self.assertIn("icon_image=ASSISTANT_ICON", entrypoint)
        self.assertIn("avatar=ASSISTANT_ICON", home)
        self.assertIn('st.title("今天想了解什么？", anchor=False, text_alignment="center")', home)
        self.assertIn('key="result-actions"', home)
        self.assertIn('horizontal_alignment="right"', home)
        self.assertIn('with st.popover("推送"', home)
        self.assertIn('with st.popover("推送"', history)
        self.assertIn("build_library_delivery_artifact", history)
        self.assertIn('[class*="st-key-result-card-"]', css)
        self.assertNotIn('st.image("assets/logo-mark.svg"', home)
        self.assertIn("render_scroll_controls()", home)
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
        self.assertEqual([step.stage for step in snapshot.steps], ["准备任务", "制定计划", "搜索来源"])
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
            self.assertEqual(app.chat_input[0].value, expected_prompt)
            self.assertEqual(len(app.chat_message), 0)

            # 模拟一次正在执行的提交。停止按钮使用显式回调，因此点击后
            # 能可靠中断当前 rerun、清理状态，并且不会伪装成助手消息。
            stop_app = AppTest.from_file(str(root / "streamlit_app.py"))
            stop_app.session_state["config_path"] = str(config)
            stop_app.session_state["agent_run_state"] = "pending"
            stop_app.run(timeout=20)
            stop_button = next(button for button in stop_app.button if button.label == "停止生成")
            stop_app = stop_button.click().run(timeout=20)
            self.assertEqual(len(stop_app.exception), 0)
            self.assertEqual(stop_app.session_state["agent_run_state"], "idle")
            self.assertEqual(len(stop_app.warning), 0)
            self.assertEqual(len(stop_app.chat_message), 0)
            self.assertTrue(any("已停止生成" in caption.value for caption in stop_app.caption))
            self.assertFalse(any(button.label == "停止生成" for button in stop_app.button))

            # 完整任务结束后，结果、完成状态和恢复后的提交控件必须出现在
            # 同一轮页面中，不能残留“停止生成”按钮。
            completed_app = AppTest.from_file(str(root / "streamlit_app.py"))
            completed_app.session_state["config_path"] = str(config)
            completed_app.run(timeout=20)
            completed_app = completed_app.chat_input[0].set_value("Python 是什么？").run(timeout=30)
            self.assertEqual(len(completed_app.exception), 0)
            self.assertTrue(any(status.state == "complete" for status in completed_app.status))
            self.assertTrue(any("用时" in status.label for status in completed_app.status))
            self.assertFalse(any(button.label == "停止生成" for button in completed_app.button))
            self.assertTrue(any("思考步骤与说明" in item.value for item in completed_app.markdown))

        source = (root / "app_pages" / "home.py").read_text(encoding="utf-8")
        self.assertIn('submit_mode="disable"', source)
        self.assertIn("on_submit=prepare_run", source)
        self.assertIn('"停止生成"', source)

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
        self.assertTrue(any("研究模型" in label for label in labels))
        self.assertTrue(any("知识库联动" in label for label in labels))

    def test_library_page_exposes_confirmed_move_to_trash(self):
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
            deleted_label = next(option for option in app.selectbox[0].options if "Answer" in option)
            app = app.selectbox[0].select(deleted_label).run(timeout=20)
            app = next(button for button in app.button if button.label == "移到回收站").click().run(timeout=20)
            app = next(button for button in app.button if button.label == "确认移除").click().run(timeout=20)

            self.assertEqual(len(app.exception), 0)
            self.assertFalse(report.exists())
            self.assertTrue(remaining_report.is_file())
            self.assertEqual(len(app.selectbox), 1)
            self.assertNotIn(deleted_label, app.selectbox[0].options)
            self.assertIn("Remaining", app.selectbox[0].value)
            self.assertTrue(any((temporary_root / "data" / "trash").glob("*-answer.md")))
            self.assertFalse(any(button.label == "确认移除" for button in app.button))
            self.assertTrue(any("已移到回收站" in toast.value for toast in app.toast))

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

    def test_automatic_tasks_have_their_own_page(self):
        from streamlit.testing.v1 import AppTest

        root = Path(__file__).resolve().parents[1]
        with patch.dict(os.environ, {"DEEPSEARCH_MODE": "mock"}):
            app = AppTest.from_file(str(root / "app_pages" / "tasks.py")).run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any(item.label == "任务名称" for item in app.text_input))


if __name__ == "__main__":
    unittest.main()
