import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from deepsearch import AgentRequest, DeepSearchAgent, WorkMode
from deepsearch.application.chat_service import ChatService
from deepsearch.application.intent import IntentClassifier
from deepsearch.domain.models import ChatTurn, SearchResult
from deepsearch.infrastructure.config import LLMSettings, Settings, load_settings, save_settings


class CountingSearch:
    name = "fixture"

    def __init__(self):
        self.calls = 0

    def search(self, query: str, limit: int = 5):
        self.calls += 1
        return [SearchResult("Python", "https://python.org", "Official", query, self.name)]


class RecordingChatModel:
    def __init__(self, answer: str = "模型回复"):
        self.answer = answer
        self.calls = []

    def generate(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.answer


class FailingChatModel:
    def generate(self, system: str, user: str) -> str:
        raise RuntimeError("quota exceeded")


class ChatModeTests(unittest.TestCase):
    def test_auto_routes_chat_search_and_research(self):
        classifier = IntentClassifier()
        self.assertEqual(classifier.resolve("你好"), WorkMode.CHAT)
        self.assertEqual(classifier.resolve("谢谢你的帮助"), WorkMode.CHAT)
        self.assertEqual(classifier.resolve("你能做什么"), WorkMode.CHAT)
        self.assertEqual(classifier.resolve("讲个笑话"), WorkMode.CHAT)
        self.assertEqual(classifier.resolve("hi"), WorkMode.CHAT)
        self.assertEqual(classifier.resolve("this is a factual question"), WorkMode.CHAT)
        self.assertEqual(classifier.resolve("现在几点"), WorkMode.CHAT)
        self.assertEqual(classifier.resolve("把 hello 翻译成中文"), WorkMode.CHAT)
        self.assertEqual(classifier.resolve("计算 2 + 3 * 4"), WorkMode.CHAT)
        self.assertEqual(classifier.resolve("Python 是什么"), WorkMode.CHAT)
        self.assertEqual(classifier.resolve("Python 官方文档在哪里"), WorkMode.SEARCH)
        self.assertEqual(classifier.resolve("今天有什么 AI 新闻"), WorkMode.SEARCH)
        self.assertEqual(classifier.resolve("今天天气是什么"), WorkMode.SEARCH)
        self.assertEqual(classifier.resolve("OpenAI 最新模型是什么"), WorkMode.SEARCH)
        self.assertEqual(classifier.resolve("比较 RAG 和长上下文方案"), WorkMode.RESEARCH)

    def test_only_explicit_search_or_research_wins(self):
        classifier = IntentClassifier()
        self.assertEqual(classifier.resolve("你好", WorkMode.SEARCH), WorkMode.SEARCH)
        self.assertEqual(classifier.resolve("你好", WorkMode.RESEARCH), WorkMode.RESEARCH)
        # chat 只保留为历史兼容值，不能再强制绕过智能判断。
        self.assertEqual(classifier.resolve("今天天气如何", WorkMode.CHAT), WorkMode.SEARCH)

    def test_unconfigured_chat_uses_local_reply_without_search_or_artifact(self):
        provider = CountingSearch()
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(
                runtime_mode="online",
                reports_dir=Path(directory) / "reports",
                cache_dir=Path(directory) / "cache",
            )
            run = DeepSearchAgent(settings, search_providers=[provider]).run(
                AgentRequest("你好", WorkMode.AUTO)
            )
        self.assertEqual(run.resolved_mode, WorkMode.CHAT)
        self.assertTrue(run.chat.used_fallback)
        self.assertIn("你好", run.content)
        self.assertEqual(provider.calls, 0)
        self.assertIsNone(run.search)
        self.assertIsNone(run.research)
        self.assertIsNone(run.artifact_path)

    def test_local_help_explains_product_features(self):
        result = ChatService().reply("你能做什么")
        self.assertTrue(result.used_fallback)
        self.assertIn("搜索", result.answer)
        self.assertIn("研究", result.answer)
        self.assertIn("资料库", result.answer)
        self.assertIn("自动", result.answer)

    def test_current_time_and_calculation_use_local_tools_without_model(self):
        model = RecordingChatModel()
        fixed = datetime(2026, 9, 9, 1, 2, 3, tzinfo=timezone.utc)
        service = ChatService(model, now_provider=lambda zone: fixed)

        current_time = service.reply("现在几点", locale="zh-CN", region="CN")
        calculation = service.reply("计算 2 + 3 * 4")

        self.assertEqual(model.calls, [])
        self.assertIn("北京时间 2026年09月09日 09:02:03", current_time.answer)
        self.assertIn("UTC+08:00", current_time.answer)
        self.assertEqual(calculation.answer, "2 + 3 * 4 = 14")

    def test_current_time_in_auto_never_calls_search(self):
        provider = CountingSearch()
        settings = Settings(runtime_mode="online")
        run = DeepSearchAgent(settings, search_providers=[provider]).run(
            AgentRequest("北京时间现在几点", WorkMode.AUTO)
        )
        self.assertEqual(run.resolved_mode, WorkMode.CHAT)
        self.assertIn("北京时间", run.content)
        self.assertEqual(provider.calls, 0)
        self.assertIsNone(run.search)
        self.assertIsNone(run.research)

    def test_specific_question_in_explicit_chat_is_guided_to_safe_mode(self):
        search_guidance = ChatService(RecordingChatModel()).reply("今天天气如何")
        research_guidance = ChatService(RecordingChatModel()).reply("帮我研究 RAG 与长上下文")
        self.assertTrue(search_guidance.used_fallback)
        self.assertIn("选择“搜索”", search_guidance.answer)
        self.assertTrue(research_guidance.used_fallback)
        self.assertIn("选择“研究”", research_guidance.answer)

    def test_chat_model_failure_never_calls_research_model(self):
        result = ChatService(FailingChatModel()).reply("你好")
        self.assertTrue(result.used_fallback)
        self.assertFalse(result.used_model)
        self.assertIn("你好", result.answer)

        settings = Settings(
            runtime_mode="online",
            llm=LLMSettings(api_key="research-only-key"),
        )
        with patch("deepsearch.infrastructure.llm.OpenAICompatibleLLM.generate") as generate:
            run = DeepSearchAgent(settings, search_providers=[CountingSearch()]).run(
                AgentRequest("谢谢", WorkMode.AUTO)
            )
        generate.assert_not_called()
        self.assertTrue(run.chat.used_fallback)

    def test_chat_context_is_bounded_and_output_is_trimmed(self):
        model = RecordingChatModel("答" * 500)
        history = tuple(ChatTurn("user", f"message-{index}-" + "x" * 900) for index in range(10))
        result = ChatService(model).reply('你好，DEEPSEARCH_CHAT_API_KEY="supersecret"', history)
        sent = model.calls[0][1]
        self.assertNotIn("message-0", sent)
        self.assertIn("message-9", sent)
        self.assertNotIn("supersecret", sent)
        self.assertIn("[REDACTED]", sent)
        self.assertLessEqual(len(result.answer), 400)
        self.assertTrue(result.used_model)

    def test_chat_config_loads_from_environment_and_never_saves_key(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text(
                json.dumps({
                    "default_work_mode": "chat",
                    "chat_llm": {
                        "base_url": "https://ignored",
                        "model": "small",
                        "api_key": "must-not-load",
                    },
                }),
                encoding="utf-8",
            )
            with patch.dict("os.environ", {"DEEPSEARCH_CHAT_API_KEY": ""}, clear=False):
                untrusted = load_settings(config_path)
            self.assertEqual(untrusted.chat_llm.api_key, "")
            self.assertEqual(untrusted.default_work_mode, "auto")
            environment = {
                "DEEPSEARCH_CHAT_API_KEY": "chat-secret",
                "DEEPSEARCH_CHAT_BASE_URL": "https://chat.example/v1",
                "DEEPSEARCH_CHAT_MODEL": "light-model",
                "DEEPSEARCH_CHAT_TIMEOUT": "25",
            }
            with patch.dict("os.environ", environment, clear=False):
                settings = load_settings(config_path)
            self.assertTrue(settings.chat_model_available)
            self.assertEqual(settings.chat_llm.model, "light-model")
            self.assertEqual(settings.chat_llm.request_timeout, 25.0)
            save_settings(settings)
            saved = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["chat_llm"]["api_key"], "")


if __name__ == "__main__":
    unittest.main()
