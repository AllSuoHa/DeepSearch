import io
import json
import ssl
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from deepsearch.domain.models import ChatTurn
from deepsearch.infrastructure.config import LLMSettings, Settings, load_settings, save_settings
from deepsearch.infrastructure.llm import LLMRequestError, OpenAICompatibleLLM, _open_url


class FakeResponse:
    def __init__(self, body: dict):
        self.body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body


class FakeStreamingResponse:
    headers = {"Content-Type": "text/event-stream; charset=utf-8"}

    def __init__(self, lines: list[bytes]):
        self.lines = iter(lines)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def readline(self):
        return next(self.lines, b"")


class OpenAICompatibleLLMTests(unittest.TestCase):
    def test_configured_timeout_is_passed_to_transport(self):
        settings = LLMSettings(api_key="secret", request_timeout=240.0)
        response = FakeResponse({"choices": [{"message": {"content": "answer"}}]})

        with patch("urllib.request.urlopen", return_value=response) as urlopen:
            answer = OpenAICompatibleLLM(settings).generate("system", "user")

        self.assertEqual(answer, "answer")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 240.0)
        request_body = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertTrue(request_body["stream"])
        self.assertNotIn("max_completion_tokens", request_body)
        self.assertNotIn("enable_thinking", request_body)

    def test_aliyun_deepseek_v4_disables_nested_thinking(self):
        settings = LLMSettings(
            base_url="https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            model="deepseek-v4-pro",
            api_key="secret",
        )
        response = FakeResponse({"choices": [{"message": {"content": "answer"}}]})

        with patch("deepsearch.infrastructure.llm._open_url", return_value=response) as urlopen:
            answer = OpenAICompatibleLLM(settings).generate("system", "user")

        request_body = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(answer, "answer")
        self.assertFalse(request_body["enable_thinking"])
        self.assertEqual(request_body["max_completion_tokens"], 8192)
        self.assertTrue(urlopen.call_args.kwargs["bypass_system_proxy"])

    def test_direct_transport_bypasses_system_proxy_without_changing_global_state(self):
        request = urllib.request.Request("https://workspace.cn-beijing.maas.aliyuncs.com/v1")
        response = FakeResponse({"ok": True})

        with patch("urllib.request.build_opener") as build_opener:
            build_opener.return_value.open.return_value = response
            opened = _open_url(request, timeout=15.0, bypass_system_proxy=True)

        self.assertIs(opened, response)
        self.assertIsInstance(build_opener.call_args.args[0], urllib.request.ProxyHandler)
        build_opener.return_value.open.assert_called_once_with(request, timeout=15.0)

    def test_non_aliyun_transport_keeps_system_proxy_policy(self):
        settings = LLMSettings(
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
            api_key="secret",
        )
        response = FakeResponse({"choices": [{"message": {"content": "answer"}}]})

        with patch("deepsearch.infrastructure.llm._open_url", return_value=response) as urlopen:
            OpenAICompatibleLLM(settings).generate("system", "user")

        self.assertFalse(urlopen.call_args.kwargs["bypass_system_proxy"])

    def test_streaming_transport_reassembles_complete_response(self):
        settings = LLMSettings(api_key="secret")
        response = FakeStreamingResponse([
            b'data: {"choices":[{"delta":{"content":"part one"},"finish_reason":null}]}\n',
            b'data: {"choices":[{"delta":{"content":" + part two"},"finish_reason":"stop"}]}\n',
            b'data: [DONE]\n',
        ])

        with patch("urllib.request.urlopen", return_value=response):
            answer = OpenAICompatibleLLM(settings).generate("system", "user")

        self.assertEqual(answer, "part one + part two")

    def test_chat_history_is_sent_as_real_role_messages(self):
        settings = LLMSettings(api_key="secret")
        response = FakeResponse({"choices": [{"message": {"content": "answer"}}]})

        with patch("urllib.request.urlopen", return_value=response) as urlopen:
            answer = OpenAICompatibleLLM(settings).generate_chat(
                "system",
                (
                    ChatTurn("user", "first question"),
                    ChatTurn("assistant", "first answer"),
                ),
                "next question",
            )

        request_body = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(answer, "answer")
        self.assertEqual(
            request_body["messages"],
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "first question"},
                {"role": "assistant", "content": "first answer"},
                {"role": "user", "content": "next question"},
            ],
        )

    def test_local_json_generation_enables_ollama_json_mode(self):
        settings = LLMSettings(
            base_url="http://localhost:11434/v1",
            model="deepseek-r1:7b",
            api_key="ollama",
        )
        response = FakeResponse({"choices": [{"message": {"content": '{"ok":true}'}}]})

        with patch("urllib.request.urlopen", return_value=response) as urlopen:
            answer = OpenAICompatibleLLM(settings).generate_json("system", "user")

        request_body = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(answer, '{"ok":true}')
        self.assertEqual(request_body["response_format"], {"type": "json_object"})

    def test_streaming_transport_ignores_empty_choices_metadata(self):
        settings = LLMSettings(api_key="secret")
        response = FakeStreamingResponse([
            b'data: {"choices":[],"usage":{"prompt_tokens":12}}\n',
            b'data: {"choices":[{"delta":{"content":"answer"},"finish_reason":null}]}\n',
            b'data: {"choices":[],"usage":{"completion_tokens":3}}\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n',
            b'data: [DONE]\n',
        ])

        with patch("urllib.request.urlopen", return_value=response):
            answer = OpenAICompatibleLLM(settings).generate("system", "user")

        self.assertEqual(answer, "answer")

    def test_incomplete_stream_retries_without_returning_partial_report(self):
        settings = LLMSettings(api_key="secret")
        responses = [
            FakeStreamingResponse([
                b'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":null}]}\n',
            ]),
            FakeStreamingResponse([
                b'data: {"choices":[{"delta":{"content":"partial again"},"finish_reason":null}]}\n',
            ]),
        ]

        with patch("urllib.request.urlopen", side_effect=responses) as urlopen:
            with patch("time.sleep"):
                with self.assertRaisesRegex(LLMRequestError, "完整响应返回前关闭了加密连接"):
                    OpenAICompatibleLLM(settings).generate("system", "user")

        self.assertEqual(urlopen.call_count, 2)

    def test_http_404_is_explained_without_pointless_retry(self):
        settings = LLMSettings(api_key="secret")
        error = urllib.error.HTTPError(
            "https://example.test/v1/chat/completions",
            404,
            "Not Found",
            None,
            io.BytesIO(b'{"error":{"message":"model not found"}}'),
        )

        with patch("urllib.request.urlopen", side_effect=error) as urlopen:
            with self.assertRaisesRegex(LLMRequestError, "HTTP 404.*model not found.*模型名称"):
                OpenAICompatibleLLM(settings).generate("system", "user")

        self.assertEqual(urlopen.call_count, 1)

    def test_timeout_retries_once_and_gives_actionable_message(self):
        settings = LLMSettings(api_key="secret", request_timeout=180.0)

        with patch("urllib.request.urlopen", side_effect=TimeoutError("read timed out")) as urlopen:
            with patch("time.sleep"):
                with self.assertRaisesRegex(LLMRequestError, "超过 180 秒.*提高"):
                    OpenAICompatibleLLM(settings).generate("system", "user")

        self.assertEqual(urlopen.call_count, 2)

    def test_ssl_eof_retries_once_and_hides_low_level_protocol_noise(self):
        settings = LLMSettings(api_key="secret")
        error = ssl.SSLError("UNEXPECTED_EOF_WHILE_READING token=secret")

        with patch("urllib.request.urlopen", side_effect=error) as urlopen:
            with patch("time.sleep"):
                with self.assertRaises(LLMRequestError) as raised:
                    OpenAICompatibleLLM(settings).generate("system", "user")

        self.assertEqual(urlopen.call_count, 2)
        self.assertIn("完整响应返回前关闭了加密连接", str(raised.exception))
        self.assertIn("不能延长上游连接", str(raised.exception))
        self.assertIn("已自动重试一次", str(raised.exception))
        self.assertNotIn("UNEXPECTED_EOF", str(raised.exception))
        self.assertNotIn("secret", str(raised.exception))

    def test_timeout_loads_and_saves_without_persisting_key(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text(
                json.dumps({"llm": {"request_timeout": 300, "api_key": "must-not-remain"}}),
                encoding="utf-8",
            )

            settings = load_settings(config_path)
            self.assertEqual(settings.llm.request_timeout, 300.0)
            save_settings(settings)
            saved = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["llm"]["request_timeout"], 300.0)
            self.assertEqual(saved["llm"]["api_key"], "")


if __name__ == "__main__":
    unittest.main()
