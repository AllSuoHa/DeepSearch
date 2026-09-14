import json
import tempfile
import unittest
from pathlib import Path

from deepsearch.infrastructure.storage import FileReportStorage, rename_library_artifact


class StorageTests(unittest.TestCase):
    def test_exports_markdown_text_and_json(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = FileReportStorage(Path(directory))
            markdown = "# 标题\n\n## 摘要\n\n**结论** [1]\n"

            markdown_path = storage.save("问题", markdown, "markdown")
            text_path = storage.save("问题", markdown, "text")
            json_path = storage.save("问题", markdown, "json")

            self.assertEqual(markdown_path.suffix, ".md")
            self.assertEqual(text_path.suffix, ".txt")
            self.assertNotIn("**", text_path.read_text(encoding="utf-8"))
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["question"], "问题")
            self.assertEqual(payload["content_markdown"], markdown)
            self.assertEqual(len(storage.list()), 3)

            protected = storage.save(
                '问题 DEEPSEARCH_API_KEY="report-secret"',
                '# 结论\n\nBearer report-token-value',
                "markdown",
            )
            protected_text = protected.read_text(encoding="utf-8")
            self.assertNotIn("report-secret", protected.name)
            self.assertNotIn("report-secret", protected_text)
            self.assertNotIn("report-token-value", protected_text)
            self.assertIn("[REDACTED]", protected_text)

    def test_json_report_rename_updates_question_and_markdown_heading(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "2026-09-09_120000_old.json"
            report.write_text(
                json.dumps({"question": "旧标题", "content_markdown": "# 旧标题\n\n正文"}),
                encoding="utf-8",
            )

            renamed = rename_library_artifact(report, "新标题", (root,))
            payload = json.loads(renamed.read_text(encoding="utf-8"))

            self.assertEqual(payload["question"], "新标题")
            self.assertEqual(payload["content_markdown"], "# 新标题\n\n正文")
            self.assertIn("新标题", renamed.name)


if __name__ == "__main__":
    unittest.main()
