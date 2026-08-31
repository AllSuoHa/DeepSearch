import tempfile
import unittest
from pathlib import Path

from deepsearch import DeepSearchAgent
from deepsearch.infrastructure.config import Settings
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
            self.assertIn("October 7, 2024", result.report.split("## 关键结论", 1)[0])
            self.assertIn("## 建议与下一步", result.report)
            self.assertIn("## 研究过程", result.report)
            self.assertIn("## 全部来源", result.report)
            self.assertEqual(events[0][0], "plan")
            self.assertEqual(len(result.trace), 1)
            self.assertGreaterEqual(result.metrics.search_results, 1)
            self.assertGreater(result.scorecard.overall, 0)
            self.assertEqual(len(result.scorecard.dimensions), 5)

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

    def test_empty_online_provider_degrades_to_mock(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(mode="auto", reports_dir=Path(directory), cache_dir=Path(directory) / "cache", max_rounds=1)
            result = DeepSearchAgent(settings, search_providers=[EmptySearch()]).research("Python 3.13 什么时候发布")
            self.assertTrue(result.sources)
            self.assertTrue(all(source.provider == "mock" for source in result.sources))


if __name__ == "__main__":
    unittest.main()
