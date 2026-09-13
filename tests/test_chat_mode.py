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


class RecordingRoleAwareChatModel:
    def __init__(self, answer: str = "模型回复"):
        self.answer = answer
        self.calls = []

    def generate_chat(self, system: str, history, user: str) -> str:
        self.calls.append((system, history, user))
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

    def test_every_explicit_mode_wins(self):
        classifier = IntentClassifier()
        self.assertEqual(classifier.resolve("你好", WorkMode.SEARCH), WorkMode.SEARCH)
        self.assertEqual(classifier.resolve("你好", WorkMode.RESEARCH), WorkMode.RESEARCH)
        self.assertEqual(classifier.resolve("今天天气如何", WorkMode.CHAT), WorkMode.CHAT)

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
        current_moment = service.reply("现在是什么时候", locale="zh-CN", region="CN")
        calculation = service.reply("计算 2 + 3 * 4")

        self.assertEqual(model.calls, [])
        self.assertIn("北京时间 2026年09月09日 09:02:03", current_time.answer)
        self.assertIn("北京时间 2026年09月09日 09:02:03", current_moment.answer)
        self.assertIn("UTC+08:00", current_time.answer)
        self.assertEqual(calculation.answer, "2 + 3 * 4 = 14")

    def test_local_fallback_does_not_treat_english_substrings_as_greetings(self):
        result = ChatService().reply("What is this?")

        self.assertTrue(result.used_fallback)
        self.assertIn("未配置问答模型", result.answer)
        self.assertNotIn("你好", result.answer)

    def test_event_time_question_is_not_mistaken_for_the_local_clock(self):
        classifier = IntentClassifier()

        self.assertTrue(classifier.is_local_time_request("现在是什么时候"))
        self.assertFalse(classifier.is_local_time_request("现在什么时候发布"))
        self.assertFalse(classifier.is_local_time_request("现在什么时候下雨"))

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

    def test_explicit_chat_answers_in_place_without_mode_guidance(self):
        chat_model = RecordingChatModel()
        factual_answer = ChatService(chat_model).reply("Python 闭包是什么")
        comparison_answer = ChatService(chat_model).reply("比较 RAG 与长上下文")
        self.assertTrue(factual_answer.used_model)
        self.assertTrue(comparison_answer.used_model)
        self.assertEqual(factual_answer.answer, "模型回复")
        self.assertEqual(comparison_answer.answer, "模型回复")
        self.assertEqual(len(chat_model.calls), 2)
        self.assertTrue(all("不要建议或要求用户改用搜索" in system for system, _ in chat_model.calls))

    def test_explicit_chat_realtime_question_never_searches(self):
        provider = CountingSearch()
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(
                runtime_mode="online",
                reports_dir=Path(directory) / "reports",
                cache_dir=Path(directory) / "cache",
            )
            run = DeepSearchAgent(settings, search_providers=[provider]).run(
                AgentRequest("今天天气怎么样", WorkMode.CHAT)
        )
        self.assertEqual(run.requested_mode, WorkMode.CHAT)
        self.assertEqual(run.resolved_mode, WorkMode.CHAT)
        self.assertIn("没有接入实时数据", run.content)
        self.assertNotIn("搜索", run.content)
        self.assertNotIn("研究", run.content)
        self.assertEqual(provider.calls, 0)

    def test_offline_realtime_answer_does_not_call_model(self):
        model = RecordingChatModel("上海今天晴，最高 24℃")

        result = ChatService(model).reply("上海今天的天气怎么样")

        self.assertEqual(model.calls, [])
        self.assertIn("没有接入实时数据", result.answer)
        self.assertFalse(result.used_model)

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

    def test_configured_model_failure_is_not_reported_as_unconfigured(self):
        result = ChatService(FailingChatModel()).reply("解释一下 Python 闭包")

        self.assertTrue(result.used_fallback)
        self.assertIn("已经配置", result.answer)
        self.assertIn("连接或生成失败", result.answer)
        self.assertNotIn("未配置问答模型", result.answer)

    def test_local_ollama_is_available_without_real_api_key(self):
        settings = Settings(
            runtime_mode="mock",
            chat_llm=LLMSettings(
                base_url="http://localhost:11434/v1",
                model="deepseek-r1:7b",
                api_key="",
            ),
        )

        self.assertTrue(settings.chat_model_is_local)
        self.assertTrue(settings.chat_model_available)
        self.assertEqual(settings.effective_chat_llm.api_key, "ollama")
        with patch(
            "deepsearch.infrastructure.llm.OpenAICompatibleLLM.generate_chat",
            return_value="本地模型回答",
        ) as generate:
            run = DeepSearchAgent(settings, search_providers=[CountingSearch()]).run(
                AgentRequest("解释一下 Python 闭包", WorkMode.CHAT)
            )
        generate.assert_called_once()
        self.assertEqual(run.content, "本地模型回答")

    def test_remote_chat_model_still_requires_api_key(self):
        settings = Settings(
            runtime_mode="online",
            chat_llm=LLMSettings(
                base_url="https://chat.example/v1",
                model="small",
                api_key="",
            ),
        )

        self.assertFalse(settings.chat_model_is_local)
        self.assertFalse(settings.chat_model_available)

    def test_chat_context_is_bounded_and_output_is_trimmed(self):
        model = RecordingChatModel("答" * 500)
        history = tuple(
            turn
            for index in range(5)
            for turn in (
                ChatTurn("user", f"message-{index}-" + "x" * 900),
                ChatTurn("assistant", f"answer-{index}"),
            )
        )
        result = ChatService(model).reply('你好，DEEPSEARCH_CHAT_API_KEY="supersecret"', history)
        sent = model.calls[0][1]
        self.assertNotIn("message-0", sent)
        self.assertIn("message-4", sent)
        self.assertNotIn("supersecret", sent)
        self.assertIn("[REDACTED]", sent)
        self.assertLessEqual(len(result.answer), 400)
        self.assertTrue(result.used_model)

    def test_chat_history_uses_roles_and_drops_repetitive_failure_exchanges(self):
        model = RecordingRoleAwareChatModel()
        history = (
            ChatTurn("user", "Python 闭包是什么"),
            ChatTurn("assistant", "闭包会保留其定义作用域中的变量。"),
            ChatTurn("user", "今天天气怎么样"),
            ChatTurn("assistant", "您好！请问有什么可以帮助您的吗？"),
            ChatTurn("user", "Python 装饰器是什么"),
            ChatTurn("assistant", "当前未配置问答模型，因此暂时无法可靠生成答案。"),
        )

        result = ChatService(model).reply("Python 装饰器是什么", history)

        self.assertTrue(result.used_model)
        _, sent_history, current = model.calls[0]
        self.assertEqual(current, "Python 装饰器是什么")
        self.assertEqual(
            sent_history,
            (
                ChatTurn("user", "Python 闭包是什么"),
                ChatTurn("assistant", "闭包会保留其定义作用域中的变量。"),
            ),
        )

    def test_chat_config_loads_from_environment_and_never_saves_key(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text(
                json.dumps({
                    "default_work_mode": "auto",
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
            self.assertEqual(untrusted.default_work_mode, "chat")
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
