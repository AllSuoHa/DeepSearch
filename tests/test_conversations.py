import json
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

from deepsearch.domain.models import RiskLevel, SearchResponse, SearchResult, WorkMode
from deepsearch.infrastructure.config import Settings
from deepsearch.infrastructure.storage import (
    ConversationStore,
    FileArtifactStorage,
    FileArtifactTrash,
    PromptShortcutStore,
    rename_library_artifact,
)
from deepsearch.presentation.web.support import (
    BackgroundRunHandle,
    RunProgressTracker,
    active_background_run,
    cancel_background_run,
    chat_history_from_messages,
    chat_run_from_messages,
    discard_background_run,
    persist_run_error_for_conversation,
    register_background_run,
    research_context_from_messages,
    run_from_message,
    run_from_messages,
    search_run_from_messages,
)


class ConversationTests(unittest.TestCase):
    def test_conversation_round_trip_keeps_lightweight_source_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / "conversations")
            conversation = store.create()
            store.append(conversation, {
                "role": "user",
                "content": '找资源 DEEPSEARCH_API_KEY="conversation-secret"',
                "mode": "search",
                "secret": "never",
            })
            store.append(conversation, {
                "role": "assistant",
                "content": "直接结果",
                "kind": "search",
                "sources": [{"title": "Official", "url": "https://example.org", "risk_level": "可信来源"}],
                "raw_page": "must not persist",
            })

            restored = store.load(conversation["id"])
            self.assertIn("[REDACTED]", restored["title"])
            self.assertEqual(len(restored["messages"]), 2)
            saved = (Path(directory) / "conversations" / f"{conversation['id']}.json").read_text(encoding="utf-8")
            self.assertNotIn("never", saved)
            self.assertNotIn("must not persist", saved)
            self.assertNotIn("conversation-secret", saved)
            self.assertEqual(store.list()[0]["id"], conversation["id"])

    def test_conversation_id_cannot_escape_storage_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ConversationStore(root / "conversations")

            with self.assertRaisesRegex(ValueError, "无效的会话记录 ID"):
                store.save({"id": "../escaped", "messages": []})
            self.assertFalse((root / "escaped.json").exists())

            stored_id = "a" * 32
            store.directory.mkdir(parents=True)
            (store.directory / f"{stored_id}.json").write_text(
                json.dumps({"id": "../escaped", "messages": []}),
                encoding="utf-8",
            )
            self.assertIsNone(store.load(stored_id))

    def test_message_id_makes_background_result_recovery_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / "conversations")
            conversation = store.create()
            message = {
                "message_id": "a" * 32,
                "role": "assistant",
                "kind": "error",
                "content": "temporary failure",
            }

            store.append(conversation, message)
            store.append(conversation, message)

            restored = store.load(conversation["id"])
            self.assertEqual(len(restored["messages"]), 1)

    def test_only_unanswered_last_user_message_can_be_edited(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / "conversations")
            conversation = store.create()
            message_id = "9" * 32
            store.append(conversation, {
                "message_id": message_id,
                "role": "user",
                "content": "原问题",
                "mode": "research",
            })

            self.assertTrue(store.replace_last_user_message(
                conversation["id"], message_id, "修改后的问题", "search"
            ))
            edited = store.load(conversation["id"])
            self.assertEqual(edited["messages"][-1]["content"], "修改后的问题")
            self.assertEqual(edited["messages"][-1]["mode"], "search")
            self.assertEqual(edited["title"], "修改后的问题")

            store.append(edited, {
                "message_id": "8" * 32,
                "role": "assistant",
                "content": "已经回答",
                "kind": "chat",
            })
            self.assertFalse(store.replace_last_user_message(
                conversation["id"], message_id, "不应覆盖", "chat"
            ))

    def test_background_handle_and_failure_survive_page_rerun(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings(conversation_dir=root / "conversations")
            store = ConversationStore(settings.conversation_dir)
            conversation = store.create()
            tracker = RunProgressTracker()
            tracker.update("generate", "正在整理证据")
            future: Future = Future()
            run_id = "b" * 32
            handle = BackgroundRunHandle(
                run_id=run_id,
                conversation_id=conversation["id"],
                question="测试研究任务",
                requested_mode=WorkMode.RESEARCH,
                tracker=tracker,
                future=future,
            )

            register_background_run(handle)
            # 新页面脚本只凭 session 中的 run_id 即可找回同一个 Future。
            recovered = active_background_run(run_id)
            self.assertIs(recovered, handle)
            snapshot = tracker.finish()
            saved = persist_run_error_for_conversation(
                RuntimeError("模型连接失败"),
                snapshot,
                settings,
                conversation["id"],
                handle.question,
                handle.requested_mode,
                run_id,
            )
            discard_background_run(run_id)

            restored = store.load(conversation["id"])
            self.assertIsNone(active_background_run(run_id))
            self.assertEqual(restored["messages"][-1]["kind"], "error")
            self.assertEqual(restored["messages"][-1]["question"], "测试研究任务")
            self.assertEqual(restored["messages"][-1]["message_id"], saved["message_id"])
            self.assertTrue(restored["messages"][-1]["progress_steps"])

    def test_cancel_accepts_handle_created_before_cancelled_field_existed(self):
        tracker = RunProgressTracker()
        future: Future = Future()
        legacy_handle = SimpleNamespace(
            run_id="d" * 32,
            tracker=tracker,
            future=future,
        )

        register_background_run(legacy_handle)
        cancel_background_run(legacy_handle.run_id)

        self.assertIsNone(active_background_run(legacy_handle.run_id))
        self.assertTrue(future.cancelled())

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
                "provider_failures": {"brave": "rate_limited"},
                "sources": [{
                    "title": "Official guide",
                    "url": "https://example.org/guide",
                    "provider": "fixture",
                    "resource_type": "网页",
                    "risk_level": "可信来源",
                    "published_at": "2026-01-01",
                    "retrieved_at": "2026-09-14T08:00:00+00:00",
                    "freshness_status": "dated",
                    "query": "RAG 长上下文 对比",
                    "fetched": True,
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
        self.assertEqual(context.sources[0].query, "RAG 长上下文 对比")
        self.assertTrue(context.sources[0].fetched)
        self.assertEqual(context.provider_failures, {"brave": "rate_limited"})
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

    def test_follow_up_keeps_each_answers_own_source_links(self):
        messages = [
            {"role": "user", "content": "第一次问题", "mode": "auto"},
            {
                "role": "assistant",
                "kind": "search",
                "mode": "search",
                "requested_mode": "auto",
                "question": "第一次问题",
                "summary": "第一条回答",
                "content": "第一条回答",
                "sources": [{"title": "来源一", "url": "https://one.example", "snippet": "一"}],
            },
            {"role": "user", "content": "继续问", "mode": "search"},
            {
                "role": "assistant",
                "kind": "search",
                "mode": "search",
                "requested_mode": "search",
                "question": "继续问",
                "summary": "第二条回答",
                "content": "第二条回答",
                "sources": [{"title": "来源二", "url": "https://two.example", "snippet": "二"}],
            },
        ]

        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / "conversations")
            conversation = store.create()
            for message in messages:
                store.append(conversation, message)
            reloaded = store.load(conversation["id"])
        assert reloaded is not None
        restored_messages = reloaded["messages"]
        first = run_from_message(restored_messages, 1)
        second = run_from_message(restored_messages, 3)
        latest = run_from_messages(restored_messages)

        assert first is not None and first.search is not None
        assert second is not None and second.search is not None
        assert latest is not None and latest.search is not None
        self.assertEqual(first.search.query, "第一次问题")
        self.assertEqual(first.search.items[0].url, "https://one.example")
        self.assertEqual(second.search.items[0].url, "https://two.example")
        self.assertEqual(latest.search.items[0].url, "https://two.example")

    def test_saved_chat_restores_as_chat_and_history_is_bounded(self):
        messages = [
            {"role": "user", "content": f"消息 {index}", "mode": "chat"}
            for index in range(8)
        ]
        messages.append({
            "role": "assistant",
            "kind": "chat",
            "mode": "chat",
            "question": "你好",
            "content": "你好！",
            "artifact_path": "",
            "sources": [],
            "used_fallback": True,
        })

        restored = chat_run_from_messages(messages)
        self.assertIsNotNone(restored)
        assert restored is not None and restored.chat is not None
        self.assertEqual(restored.resolved_mode.value, "chat")
        self.assertEqual(restored.chat.answer, "你好！")
        self.assertIsNone(restored.artifact_path)
        history = chat_history_from_messages(messages, limit=6)
        self.assertEqual(len(history), 6)
        self.assertEqual(history[-1].content, "你好！")

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

    def test_library_report_rename_updates_title_filename_and_session_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reports = root / "reports"
            artifacts = root / "artifacts"
            conversations = root / "conversations"
            reports.mkdir()
            artifacts.mkdir()
            report = artifacts / "2026-09-09_120000_old-title_搜索.md"
            report.write_text("# 旧标题\n\n正文", encoding="utf-8")
            store = ConversationStore(conversations)
            conversation = store.create()
            store.append(conversation, {
                "role": "assistant",
                "content": "正文",
                "artifact_path": str(report),
            })

            renamed = rename_library_artifact(report, "新的文档名称", (reports, artifacts))
            changed = store.replace_artifact_reference(report, renamed)

            self.assertFalse(report.exists())
            self.assertTrue(renamed.is_file())
            self.assertIn("新的文档名称", renamed.name)
            self.assertIn("_搜索", renamed.stem)
            self.assertTrue(renamed.read_text(encoding="utf-8").startswith("# 新的文档名称\n"))
            self.assertEqual(changed, 1)
            saved = store.load(conversation["id"])
            self.assertEqual(saved["messages"][0]["artifact_path"], str(renamed.resolve()))

            outside = root / "outside.md"
            outside.write_text("# 越界", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "资料库"):
                rename_library_artifact(outside, "越界", (reports, artifacts))


if __name__ == "__main__":
    unittest.main()
