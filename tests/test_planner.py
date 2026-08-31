import unittest

from deepsearch.domain.models import QuestionType, ResearchBrief, Source
from deepsearch.application.planner import ResearchPlanner


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.planner = ResearchPlanner()

    def test_simple_fact_uses_single_round(self):
        plan = self.planner.plan("Python 3.13 什么时候发布")
        self.assertEqual(plan.question_type, QuestionType.FACT)
        self.assertEqual(plan.minimum_rounds, 1)
        self.assertEqual(len(plan.subquestions), 1)

    def test_comparison_is_decomposed_and_requires_iteration(self):
        plan = self.planner.plan("对比 2025 年主流大模型的推理能力和成本")
        self.assertEqual(plan.question_type, QuestionType.COMPARISON)
        self.assertEqual(plan.minimum_rounds, 2)
        self.assertEqual(len(plan.subquestions), 3)
        source = Source("x", "mock://x", "推理能力与成本资料", query=plan.queries[0], source_id=1)
        decision = self.planner.evaluate(plan, [source], 1, 1, 0)
        self.assertFalse(decision.sufficient)
        self.assertTrue(decision.next_queries)

    def test_research_brief_scopes_every_initial_query(self):
        brief = ResearchBrief(domain="人工智能", information_types=("新闻", "公告"), time_scope="最近 24 小时")
        plan = self.planner.plan("大模型行业动态报告", brief)

        self.assertTrue(all("人工智能" in query for query in plan.queries))
        self.assertTrue(all("新闻 公告" in query for query in plan.queries))
        self.assertTrue(all("最近 24 小时" in query for query in plan.queries))
        self.assertEqual(plan.brief, brief)


if __name__ == "__main__":
    unittest.main()
