import tempfile
import unittest
from pathlib import Path

from deepsearch import DeepSearchAgent
from deepsearch.application.errors import ResearchModelRequiredError, SearchUnavailableError
from deepsearch.infrastructure.config import LLMSettings, Settings
from deepsearch.infrastructure.search.base import SearchProvider


class EmptySearch(SearchProvider):
    name = "empty"

    def search(self, query: str, limit: int = 5):
        return []


class AgentTests(unittest.TestCase):
    def make_settings(self, directory: str) -> Settings:
        return Settings(
            mode="mock", reports_dir=Path(directory), cache_dir=Path(directory) / "cache",
            results_per_query=4, max_rounds=3,
        )

    def test_simple_end_to_end_has_citations_and_saved_report(self):
        with tempfile.TemporaryDirectory() as directory:
            events = []
            result = DeepSearchAgent(self.make_settings(directory)).research(
                "Python 3.13 什么时候发布", lambda phase, message: events.append((phase, message))
            )
            self.assertEqual(result.rounds, 1)
            self.assertTrue(result.validation.valid)
            self.assertTrue(result.report_path.exists())
            self.assertIn("[1]", result.report)
            self.assertIn("October 7, 2024", result.report.split("## 关键发现", 1)[0])
            self.assertIn("## 结论", result.report)
            self.assertIn("## 关键发现", result.report)
            self.assertIn("## 分析", result.report)
            self.assertIn("## 局限", result.report)
            self.assertIn("## 参考来源", result.report)
            self.assertNotIn("## 研究过程", result.report)
            self.assertEqual(events[0][0], "plan")
            self.assertEqual(len(result.trace), 1)
            self.assertGreaterEqual(result.metrics.search_results, 1)
            self.assertEqual(result.scorecard.overall, 0)
            self.assertEqual(len(result.scorecard.dimensions), 5)
            self.assertNotIn("多源验证", result.report)

    def test_complex_question_iterates_and_builds_comparison_table(self):
        with tempfile.TemporaryDirectory() as directory:
            events = []
            result = DeepSearchAgent(self.make_settings(directory)).research(
                "对比 2025 年主流大模型的推理能力和成本",
                lambda phase, message: events.append((phase, message)),
            )
            self.assertGreaterEqual(result.rounds, 2)
            self.assertTrue(any(phase == "adjust" for phase, _ in events))
            self.assertIn("### 对比表", result.report)
            self.assertGreaterEqual(len(result.sources), 6)
            self.assertTrue(result.validation.valid)

    def test_fresh_follow_up_runs_a_new_research_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = DeepSearchAgent(self.make_settings(directory))
            previous = agent.research("Python 3.13 什么时候发布")
            events = []
            result = agent.follow_up(
                previous,
                "最近有什么更新和新数据？",
                lambda phase, message: events.append((phase, message)),
            )

            self.assertTrue(any(phase == "follow_up" and "追加搜索" in message for phase, message in events))
            self.assertTrue(any(phase == "search" for phase, _ in events))
            self.assertNotEqual(result.stop_reason, "复用已有研究上下文")

    def test_online_research_without_model_stops_before_search(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(mode="auto", reports_dir=Path(directory), cache_dir=Path(directory) / "cache", max_rounds=1)
            with self.assertRaises(ResearchModelRequiredError):
                DeepSearchAgent(settings, search_providers=[EmptySearch()]).research("Python 3.13 什么时候发布")

    def test_empty_online_provider_fails_without_mock_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(
                runtime_mode="online",
                reports_dir=Path(directory),
                cache_dir=Path(directory) / "cache",
                max_rounds=1,
                llm=LLMSettings(api_key="configured-for-test"),
            )
            agent = DeepSearchAgent(settings, search_providers=[EmptySearch()])
            with self.assertRaises(SearchUnavailableError):
                agent.research("Python 3.13 什么时候发布")
            self.assertEqual(list(Path(directory).glob("*.md")), [])


if __name__ == "__main__":
    unittest.main()
