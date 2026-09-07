import tempfile
import unittest
from pathlib import Path

from deepsearch.domain.models import RiskLevel, SearchResponse, SearchResult
from deepsearch.infrastructure.storage import (
    ConversationStore,
    FileArtifactStorage,
    FileArtifactTrash,
    PromptShortcutStore,
)
from deepsearch.presentation.web.support import research_context_from_messages, search_run_from_messages


class ConversationTests(unittest.TestCase):
    def test_conversation_round_trip_keeps_lightweight_source_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / "conversations")
            conversation = store.create()
            store.append(conversation, {"role": "user", "content": "找资源", "mode": "search", "secret": "never"})
            store.append(conversation, {
                "role": "assistant",
                "content": "直接结果",
                "kind": "search",
                "sources": [{"title": "Official", "url": "https://example.org", "risk_level": "可信来源"}],
                "raw_page": "must not persist",
            })

            restored = store.load(conversation["id"])
            self.assertEqual(restored["title"], "找资源")
            self.assertEqual(len(restored["messages"]), 2)
            saved = (Path(directory) / "conversations" / f"{conversation['id']}.json").read_text(encoding="utf-8")
            self.assertNotIn("never", saved)
            self.assertNotIn("must not persist", saved)
            self.assertEqual(store.list()[0]["id"], conversation["id"])

    def test_conversations_can_list_all_and_delete_without_touching_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ConversationStore(root / "conversations")
            artifact = root / "report.md"
            artifact.write_text("# Keep me", encoding="utf-8")
            conversations = []
            for index in range(3):
                conversation = store.create()
                store.append(conversation, {
                    "role": "user",
                    "content": f"记录 {index}",
                    "artifact_path": str(artifact),
                })
                conversations.append(conversation)

            self.assertEqual(len(store.list(limit=None)), 3)
            self.assertTrue(store.delete(conversations[1]["id"]))
            self.assertIsNone(store.load(conversations[1]["id"]))
            self.assertTrue(artifact.is_file())
            self.assertFalse(store.delete(conversations[1]["id"]))
            with self.assertRaisesRegex(ValueError, "会话记录 ID"):
                store.delete("../outside")

    def test_search_snapshot_is_a_downloadable_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            response = SearchResponse(
                "query",
                "answer",
                [SearchResult("Result", "https://example.org", "snippet", provider="fixture", risk_level=RiskLevel.TRUSTED.value)],
            )
            path = FileArtifactStorage(Path(directory)).save_search(response)
            self.assertTrue(path.is_file())
            self.assertEqual(response.artifact_path, path)
            self.assertIn("https://example.org", path.read_text(encoding="utf-8"))

    def test_saved_research_can_restore_lightweight_follow_up_context(self):
        context = research_context_from_messages([
            {"role": "user", "content": "比较 RAG 与长上下文", "mode": "auto"},
            {
                "role": "assistant",
                "kind": "research",
                "mode": "research",
                "question": "比较 RAG 与长上下文",
                "content": "## 结论\n优先按数据时效性选择。[1]",
                "summary": "数据经常变化时优先 RAG。",
                "rounds": 2,
                "stop_reason": "证据已充分",
                "review_summary": ["结论已前置"],
                "sources": [{
                    "title": "Official guide",
                    "url": "https://example.org/guide",
                    "provider": "fixture",
                    "resource_type": "网页",
                    "risk_level": "可信来源",
                    "published_at": "2026-01-01",
                    "raw_page": "must not become reusable evidence",
                }],
            },
        ])

        self.assertIsNotNone(context)
        assert context is not None
        self.assertEqual(context.question, "比较 RAG 与长上下文")
        self.assertEqual(context.direct_answer, "数据经常变化时优先 RAG。")
        self.assertEqual(context.rounds, 2)
        self.assertEqual(context.review_summary, ("结论已前置",))
        self.assertEqual(context.sources[0].published_at, "2026-01-01")
        self.assertEqual(context.sources[0].usable_text, "")

    def test_saved_search_restores_cards_and_migrates_legacy_snapshot_snippets(self):
        restored = search_run_from_messages([
            {"role": "user", "content": "找官方入口", "mode": "auto"},
            {
                "role": "assistant",
                "kind": "search",
                "mode": "search",
                "question": "找官方入口",
                "summary": "找到一个入口。",
                "content": (
                    "# 找官方入口\n\n找到一个入口。\n\n## 搜索结果\n\n"
                    "### 1. [Official](https://example.org)\n\n"
                    "官方网站 · 可信来源\n\n旧记录中的摘要\n\n"
                    "## 提示\n\n- 旧记录提示\n"
                ),
                "sources": [{
                    "title": "Official",
                    "url": "https://example.org",
                    "provider": "fixture",
                    "resource_type": "官方网站",
                    "risk_level": "可信来源",
                }],
            },
        ])

        self.assertIsNotNone(restored)
        assert restored is not None and restored.search is not None
        self.assertEqual(restored.search.answer, "找到一个入口。")
        self.assertEqual(restored.search.items[0].snippet, "旧记录中的摘要")
        self.assertEqual(restored.search.warnings, ["旧记录提示"])

    def test_prompt_shortcuts_support_persistent_add_edit_and_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prompt-shortcuts.json"
            store = PromptShortcutStore(path)

            self.assertEqual(len(store.list()), 4)
            added = store.add("项目周报", "总结本周项目进度")
            self.assertTrue(store.update(added["id"], "项目复盘", "整理本周进展、风险和下周计划"))
            restored = PromptShortcutStore(path).list()
            self.assertEqual(next(item for item in restored if item["id"] == added["id"])["label"], "项目复盘")
            self.assertTrue(store.delete(added["id"]))
            self.assertFalse(any(item["id"] == added["id"] for item in store.list()))

    def test_library_report_moves_to_project_trash_and_clears_session_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reports = root / "reports"
            artifacts = root / "artifacts"
            conversations = root / "conversations"
            reports.mkdir()
            artifacts.mkdir()
            report = reports / "answer.md"
            report.write_text("# Answer", encoding="utf-8")
            conversation_store = ConversationStore(conversations)
            conversation = conversation_store.create()
            conversation_store.append(conversation, {
                "role": "assistant", "content": "Answer", "artifact_path": str(report)
            })

            trash = FileArtifactTrash(root / "trash", (reports, artifacts))
            moved_to = trash.move(report)
            cleared = conversation_store.clear_artifact_reference(report)

            self.assertFalse(report.exists())
            self.assertTrue(moved_to.is_file())
            self.assertTrue(moved_to.with_name(moved_to.name + ".trash.json").is_file())
            self.assertEqual(trash.list_items()[0]["name"], "answer.md")
            self.assertEqual(cleared, 1)
            self.assertEqual(conversation_store.load(conversation["id"])["messages"][0]["artifact_path"], "")

            restored_to = trash.restore(moved_to)
            self.assertEqual(restored_to, report.resolve())
            self.assertTrue(report.is_file())
            self.assertFalse(moved_to.exists())
            self.assertEqual(trash.list_items(), [])

            moved_again = trash.move(report)
            report.write_text("conflicting copy", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "同名文件"):
                trash.restore(moved_again)
            report.unlink()
            trash.delete(moved_again)
            self.assertFalse(moved_again.exists())
            self.assertEqual(trash.list_items(), [])

            outside = root / "outside.md"
            outside.write_text("not in library", encoding="utf-8")
            with self.assertRaises(ValueError):
                trash.move(outside)
            with self.assertRaises(ValueError):
                trash.delete(outside)
            self.assertTrue(outside.is_file())


if __name__ == "__main__":
    unittest.main()
