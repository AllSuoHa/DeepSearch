import io
import json
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from deepsearch.bootstrap import build_search_providers
from deepsearch.application.search_service import SearchService
from deepsearch.domain.models import SearchResult
from deepsearch.infrastructure.config import LLMSettings, Settings, save_settings
from deepsearch.infrastructure.search.searxng import SearXNGSearch
from deepsearch.infrastructure.search.tavily import TavilySearch


class Response:
    def __init__(self, payload: dict):
        self.body = io.BytesIO(json.dumps(payload).encode("utf-8"))
        self.headers = Message()
        self.headers["Content-Type"] = "application/json; charset=utf-8"

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit=-1):
        return self.body.read(limit)


class RecordingProvider:
    def __init__(self, name: str, capabilities, results=None, fail=False):
        self.name = name
        self.capabilities = frozenset(capabilities)
        self.results = results or []
        self.fail = fail
        self.calls = []
        self.last_error = ""

    def search(self, query, limit=5):
        self.calls.append(query)
        if self.fail:
            self.last_error = "timeout"
            return []
        return [SearchResult(title, url, snippet, query, self.name) for title, url, snippet in self.results]


class SearchProviderTests(unittest.TestCase):
    def test_wikipedia_is_not_called_for_current_query(self):
        web = RecordingProvider(
            "web", {"general", "current"},
            [("杭州天气预报", "https://weather.example/hangzhou", "杭州今日天气数据")],
        )
        wikipedia = RecordingProvider(
            "wikipedia", {"background"},
            [("中国铁路", "https://zh.wikipedia.org/wiki/rail", "铁路历史")],
        )

        response = SearchService([web, wikipedia]).search("杭州今天天气")

        self.assertTrue(response.items)
        self.assertTrue(web.calls)
        self.assertEqual(wikipedia.calls, [])

    def test_one_failed_provider_does_not_fail_search(self):
        failed = RecordingProvider("failed", {"general", "current"}, fail=True)
        healthy = RecordingProvider(
            "healthy", {"general", "current"},
            [("上海天气", "https://weather.example/shanghai", "上海今天气象信息")],
        )

        response = SearchService([failed, healthy]).search("上海今天天气")

        self.assertEqual(response.items[0].provider, "healthy")
        self.assertEqual(len(failed.calls), 2)
        self.assertEqual(response.provider_failures["failed"], "timeout")

    def test_irrelevant_result_is_removed_before_final_items(self):
        provider = RecordingProvider(
            "web", {"general", "current"},
            [
                ("中国铁路客运史", "https://example.org/rail", "铁路票价历史人物"),
                ("杭州天气预报", "https://example.org/weather", "杭州今日天气与温度"),
            ],
        )

        response = SearchService([provider]).search("杭州今天天气")

        self.assertEqual([item.url for item in response.items], ["https://example.org/weather"])

    def test_tavily_adapter_uses_bearer_auth_and_maps_results(self):
        captured = {}

        def open_request(request, timeout):
            captured["request"] = request
            return Response({"results": [{
                "title": "Official result",
                "url": "https://example.org/result",
                "content": "relevant content",
                "score": 0.8,
                "published_date": "2026-09-14",
            }]})

        provider = TavilySearch("test-key")
        with patch("urllib.request.urlopen", side_effect=open_request):
            results = provider.search("query", 1)

        self.assertEqual(results[0].provider, "tavily")
        self.assertEqual(results[0].published_at, "2026-09-14")
        self.assertEqual(captured["request"].get_header("Authorization"), "Bearer test-key")

    def test_searxng_adapter_maps_engine_and_rejects_embedded_credentials(self):
        provider = SearXNGSearch("https://search.example.org")
        with patch("urllib.request.urlopen", return_value=Response({"results": [{
            "title": "Result", "url": "https://example.org", "content": "snippet", "engines": ["bing"]
        }]})):
            results = provider.search("query", 1)

        self.assertEqual(results[0].provider, "searxng/bing")
        with self.assertRaises(ValueError):
            SearXNGSearch("https://user:password@search.example.org")

    def test_no_secret_is_written_to_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            settings = Settings(
                config_path=path,
                brave_api_key="brave-secret",
                tavily_api_key="tavily-secret",
                llm=LLMSettings(api_key="research-secret"),
                chat_llm=LLMSettings(api_key="chat-secret"),
                searxng_base_url="https://search.example.org",
            )
            save_settings(settings)
            content = path.read_text(encoding="utf-8")

        for secret in ("brave-secret", "tavily-secret", "research-secret", "chat-secret"):
            self.assertNotIn(secret, content)
        self.assertIn("https://search.example.org", content)

        configured = Settings(
            runtime_mode="online",
            search_provider_order=("crossref", "openalex", "duckduckgo", "wikipedia"),
            disabled_search_providers=("crossref", "wikipedia"),
        )
        provider_names = [provider.name for provider in build_search_providers(configured)]
        self.assertEqual(provider_names, ["openalex", "duckduckgo"])

        provider = TavilySearch("tavily-secret")
        with self.assertLogs("deepsearch.infrastructure.search.tavily", level="WARNING") as captured:
            with patch("urllib.request.urlopen", side_effect=OSError("offline")):
                provider.search('DEEPSEARCH_API_KEY="query-secret"', 1)
        logs = "\n".join(captured.output)
        self.assertNotIn("tavily-secret", logs)
        self.assertNotIn("query-secret", logs)


if __name__ == "__main__":
    unittest.main()
