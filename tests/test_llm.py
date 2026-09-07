import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from deepsearch.infrastructure.config import LLMSettings, Settings, load_settings, save_settings
from deepsearch.infrastructure.llm import LLMRequestError, OpenAICompatibleLLM


class FakeResponse:
    def __init__(self, body: dict):
        self.body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body


class OpenAICompatibleLLMTests(unittest.TestCase):
    def test_configured_timeout_is_passed_to_transport(self):
        settings = LLMSettings(api_key="secret", request_timeout=240.0)
        response = FakeResponse({"choices": [{"message": {"content": "answer"}}]})

        with patch("urllib.request.urlopen", return_value=response) as urlopen:
            answer = OpenAICompatibleLLM(settings).generate("system", "user")

        self.assertEqual(answer, "answer")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 240.0)

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
