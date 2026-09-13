import tempfile
import unittest
import json
from pathlib import Path

from deepsearch import AgentRequest, DeepSearchAgent, WorkMode
from deepsearch.application.errors import SearchUnavailableError
from deepsearch.application.intent import IntentClassifier
from deepsearch.application.search_service import SearchService, search_response_markdown
from deepsearch.domain.models import ResourceType, RiskLevel, SearchContentType, SearchResult
from deepsearch.infrastructure.config import Settings, load_settings, save_settings


class MediaSearch:
    name = "fixture"

    def search(self, query: str, limit: int = 5):
        return [
            SearchResult("Stranger Things", "https://www.netflix.com/title/80057281", "Official streaming page", query, self.name),
            SearchResult("Stranger Things", "https://en.wikipedia.org/wiki/Stranger_Things", "Series information", query, self.name),
            SearchResult("Discussion", "https://www.reddit.com/r/StrangerThings/", "Community discussion", query, self.name),
            SearchResult("盗版下载", "https://bad.example/torrent", "磁力 torrent 免费下载", query, self.name),
            SearchResult("Fake", "mock://fake", "not real", query, "mock"),
        ]


class RecordingSearch:
    name = "fixture"

    def __init__(self, url: str):
        self.url = url
        self.calls = []

    def search(self, query: str, limit: int = 5):
        self.calls.append(query)
        return [SearchResult(query, self.url, "result", query, self.name)]


class SearchModeTests(unittest.TestCase):
    def test_auto_mode_routes_resources_to_search_and_papers_to_research(self):
        classifier = IntentClassifier()
        self.assertEqual(classifier.resolve("找一下怪奇物语的影视资源"), WorkMode.SEARCH)
        self.assertEqual(classifier.resolve("找 RAG 论文并比较长上下文方案"), WorkMode.RESEARCH)
        self.assertEqual(classifier.resolve("比较价格", WorkMode.SEARCH), WorkMode.SEARCH)

    def test_media_search_returns_http_links_categories_and_risk_labels(self):
        response = SearchService([MediaSearch()]).search("找一下怪奇物语的影视资源")

        self.assertTrue(response.items)
        self.assertTrue(all(item.url.startswith(("http://", "https://")) for item in response.items))
        self.assertFalse(any("torrent" in item.url for item in response.items))
        self.assertFalse(any(item.provider == "mock" for item in response.items))
        self.assertEqual(response.items[0].resource_type, ResourceType.WATCH.value)
        self.assertEqual(response.items[0].risk_level, RiskLevel.TRUSTED.value)
        self.assertTrue(any("official streaming platform" in query for query in response.queries))
        markdown = search_response_markdown(response)
        self.assertNotIn("## 摘要", markdown)
        self.assertNotIn("## 研究过程", markdown)

    def test_search_mode_does_not_require_an_llm(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(
                runtime_mode="online",
                reports_dir=Path(directory) / "reports",
                cache_dir=Path(directory) / "cache",
            )
            run = DeepSearchAgent(settings, search_providers=[MediaSearch()]).run(
                AgentRequest("找一下怪奇物语的影视资源", WorkMode.AUTO)
            )
            self.assertEqual(run.resolved_mode, WorkMode.SEARCH)
            self.assertIsNotNone(run.search)
            self.assertIsNone(run.research)

    def test_search_content_type_focuses_queries_and_enables_academic_sources(self):
        primary = RecordingSearch("https://example.com/result")
        academic = RecordingSearch("https://doi.org/10.1000/example")

        response = SearchService(
            [primary],
            [academic],
            content_type=SearchContentType.ACADEMIC,
        ).search("RAG")

        self.assertEqual(primary.calls, ["RAG 论文 文献"])
        self.assertEqual(academic.calls, ["RAG 论文 文献"])
        self.assertEqual(response.queries, ["RAG 论文 文献"])

    def test_custom_search_content_type_is_used_as_query_focus(self):
        provider = RecordingSearch("https://example.com/patent")

        response = SearchService([provider], content_type="专利").search("固态电池")

        self.assertEqual(provider.calls, ["固态电池 专利"])
        self.assertEqual(response.queries, ["固态电池 专利"])

    def test_all_failed_searches_raise_instead_of_using_mock(self):
        with self.assertRaises(SearchUnavailableError) as raised:
            SearchService([]).search("anything")
        self.assertIn("网络超时、访问限制或搜索服务临时异常", str(raised.exception))
        self.assertIn("在线模式不会使用 Mock 结果代替", str(raised.exception))

    def test_legacy_runtime_mode_migrates_and_call_override_still_works(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"mode": "mock"}), encoding="utf-8")
            self.assertEqual(load_settings(path).runtime_mode, "mock")
            self.assertEqual(load_settings(path, mode="auto").runtime_mode, "online")

    def test_search_content_type_loads_and_saves(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"search_content_type": "新闻"}), encoding="utf-8")

            settings = load_settings(path)
            self.assertEqual(settings.search_content_type, "新闻")
            settings.search_content_type = "公告"
            save_settings(settings)

            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["search_content_type"], "公告")

    def test_custom_information_types_load_save_and_adopt_selected_type(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps({
                    "search_content_type": "专利",
                    "custom_information_types": ["专利", "财报", "专利", "新闻", ""],
                }),
                encoding="utf-8",
            )

            settings = load_settings(path)
            self.assertEqual(settings.search_content_type, "专利")
            self.assertEqual(settings.custom_information_types, ("专利", "财报"))
            settings.custom_information_types = (*settings.custom_information_types, "访谈")
            save_settings(settings)

            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["custom_information_types"], ["专利", "财报", "访谈"])


if __name__ == "__main__":
    unittest.main()
