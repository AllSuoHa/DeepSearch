import json
import tempfile
import unittest
from pathlib import Path

from deepsearch.infrastructure.storage import FileReportStorage


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


if __name__ == "__main__":
    unittest.main()
