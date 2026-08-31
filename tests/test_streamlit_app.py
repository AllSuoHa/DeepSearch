import importlib.util
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec("streamlit"), "Streamlit UI extra not installed")
class StreamlitAppTests(unittest.TestCase):
    def test_cli_and_web_support_use_repository_root(self):
        """CLI 启动器和页面适配层必须共享同一个仓库根目录。"""

        from deepsearch.presentation.cli import PROJECT_ROOT as cli_root
        from deepsearch.presentation.web.support import PROJECT_ROOT as web_root

        expected = Path(__file__).resolve().parents[1]
        self.assertEqual(cli_root, expected)
        self.assertEqual(web_root, expected)

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

    def test_settings_page_renders_autonomous_task_form(self):
        from streamlit.testing.v1 import AppTest

        root = Path(__file__).resolve().parents[1]
        with patch.dict(os.environ, {"DEEPSEARCH_MODE": "mock"}):
            app = AppTest.from_file(str(root / "app_pages" / "settings.py")).run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("自主任务" in tab.label for tab in app.tabs))
        self.assertTrue(any(item.label == "任务名称" for item in app.text_input))


if __name__ == "__main__":
    unittest.main()
