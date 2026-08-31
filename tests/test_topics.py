import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from deepsearch.domain.models import ReportSpecification, ResearchBrief
from deepsearch.infrastructure.config import Settings
from deepsearch.scheduling.topics import ResearchTaskManager, ScheduledResearchTask, TopicScheduler


class FakeAgent:
    def __init__(self):
        self.calls = []

    def research(self, question, progress=None, brief=None):
        self.calls.append((question, brief))
        return SimpleNamespace(report_path=Path("report.md"))


class FakePublisher:
    def __init__(self, error=None):
        self.error = error
        self.published = []
        self.flushes = 0

    def flush_pending(self):
        self.flushes += 1
        return []

    def publish(self, task, result):
        self.published.append((task, result))
        if self.error:
            raise self.error
        return SimpleNamespace(status="indexed")


class TopicTests(unittest.TestCase):
    def test_due_topic_runs_once_per_day(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(
                mode="mock",
                config_path=Path(directory) / "config.json",
                topics=[{"name": "daily", "query": "AI news", "time": "08:00", "enabled": True}],
            )
            agent = FakeAgent()
            scheduler = TopicScheduler(settings, agent)
            now = datetime(2026, 8, 28, 9, 0)
            self.assertEqual(scheduler.run_due(now), ["daily"])
            self.assertEqual(scheduler.run_due(now), [])
            self.assertEqual([call[0] for call in agent.calls], ["AI news"])

    def test_task_persists_research_and_report_requirements(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(config_path=Path(directory) / "config.json")
            manager = ResearchTaskManager(settings)
            task = ScheduledResearchTask(
                name="AI 日报",
                question="总结大模型行业动态",
                schedule_type="weekly",
                run_time="10:00",
                weekdays=(0, 2, 4),
                profile="深度",
                brief=ResearchBrief(
                    domain="人工智能",
                    information_types=("新闻", "公告"),
                    time_scope="最近 24 小时",
                    report=ReportSpecification(output_format="json", target_words=1800, audience="技术负责人"),
                ),
            )
            manager.add_task(task)

            loaded = manager.get("AI 日报")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.brief.domain, "人工智能")
            self.assertEqual(loaded.brief.report.output_format, "json")
            self.assertEqual(loaded.weekdays, (0, 2, 4))

    def test_weekly_task_only_runs_on_selected_day_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            task = ScheduledResearchTask(
                name="weekly",
                question="AI weekly",
                schedule_type="weekly",
                run_time="10:00",
                weekdays=(0,),
            )
            settings = Settings(config_path=Path(directory) / "config.json", topics=[task.to_dict()])
            agent = FakeAgent()
            scheduler = TopicScheduler(settings, agent)

            self.assertEqual(scheduler.run_due(datetime(2026, 8, 25, 11, 0)), [])  # 周二
            self.assertEqual(scheduler.run_due(datetime(2026, 8, 31, 11, 0)), ["weekly"])  # 周一
            self.assertEqual(scheduler.run_due(datetime(2026, 8, 31, 12, 0)), [])
            self.assertEqual(agent.calls[0][1].report.target_words, 1200)

    def test_once_task_waits_for_date_and_time_then_runs_once(self):
        with tempfile.TemporaryDirectory() as directory:
            task = ScheduledResearchTask(
                name="launch",
                question="release briefing",
                schedule_type="once",
                run_date="2026-09-01",
                run_time="10:00",
            )
            settings = Settings(config_path=Path(directory) / "config.json", topics=[task.to_dict()])
            agent = FakeAgent()
            scheduler = TopicScheduler(settings, agent)

            self.assertEqual(scheduler.run_due(datetime(2026, 8, 31, 12, 0)), [])
            self.assertEqual(scheduler.run_due(datetime(2026, 9, 1, 9, 59)), [])
            self.assertEqual(scheduler.run_due(datetime(2026, 9, 1, 10, 0)), ["launch"])
            self.assertEqual(scheduler.run_due(datetime(2026, 9, 2, 12, 0)), [])

    def test_customer_service_delivery_is_task_scoped_and_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            task = ScheduledResearchTask(
                name="knowledge-sync",
                question="Generate a knowledge report",
                deliver_to_customer_service=True,
                data_classification="public",
            )
            settings = Settings(config_path=Path(directory) / "config.json", topics=[task.to_dict()])
            publisher = FakePublisher()
            scheduler = TopicScheduler(settings, FakeAgent(), publisher=publisher)

            result = scheduler.run_now("knowledge-sync")

            self.assertEqual(result.report_path, Path("report.md"))
            self.assertEqual(len(publisher.published), 1)
            delivered_task = publisher.published[0][0]
            self.assertTrue(delivered_task.deliver_to_customer_service)
            self.assertEqual(delivered_task.data_classification, "public")
            self.assertEqual(publisher.flushes, 1)

    def test_delivery_failure_does_not_repeat_expensive_research(self):
        with tempfile.TemporaryDirectory() as directory:
            task = ScheduledResearchTask(
                name="daily-sync",
                question="AI news",
                run_time="08:00",
                deliver_to_customer_service=True,
            )
            settings = Settings(config_path=Path(directory) / "config.json", topics=[task.to_dict()])
            agent = FakeAgent()
            publisher = FakePublisher(RuntimeError("delivery unavailable"))
            scheduler = TopicScheduler(settings, agent, publisher=publisher)
            now = datetime(2026, 8, 28, 9, 0)

            self.assertEqual(scheduler.run_due(now), ["daily-sync"])
            self.assertEqual(scheduler.run_due(now), [])
            self.assertEqual(len(agent.calls), 1)
            self.assertEqual(len(publisher.published), 1)


if __name__ == "__main__":
    unittest.main()
