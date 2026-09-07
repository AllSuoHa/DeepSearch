import tempfile
import unittest
import json
from pathlib import Path

from deepsearch import AgentRequest, DeepSearchAgent, WorkMode
from deepsearch.application.errors import SearchUnavailableError
from deepsearch.application.intent import IntentClassifier
from deepsearch.application.search_service import SearchService, search_response_markdown
from deepsearch.domain.models import ResourceType, RiskLevel, SearchResult
from deepsearch.infrastructure.config import Settings, load_settings


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

    def test_all_failed_searches_raise_instead_of_using_mock(self):
        with self.assertRaises(SearchUnavailableError):
            SearchService([]).search("anything")

    def test_legacy_runtime_mode_migrates_and_call_override_still_works(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"mode": "mock"}), encoding="utf-8")
            self.assertEqual(load_settings(path).runtime_mode, "mock")
            self.assertEqual(load_settings(path, mode="auto").runtime_mode, "online")


if __name__ == "__main__":
    unittest.main()
