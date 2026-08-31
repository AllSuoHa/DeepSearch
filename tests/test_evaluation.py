import unittest

from deepsearch.application.evaluation import ResearchQualityEvaluator
from deepsearch.bootstrap import apply_profile
from deepsearch.domain.models import (
    QuestionType,
    ResearchMetrics,
    SearchPlan,
    Source,
    ValidationResult,
)
from deepsearch.infrastructure.config import Settings


class EvaluationTests(unittest.TestCase):
    def test_scorecard_is_explainable_and_rewards_diverse_valid_evidence(self):
        sources = [
            Source("A", "https://docs.python.org/a", "Python release evidence", quality_score=0.95),
            Source("B", "https://example.edu/b", "Independent evidence", quality_score=0.85),
            Source("C", "https://example.com/c", "Additional evidence", quality_score=0.75),
        ]
        plan = SearchPlan("question", QuestionType.RESEARCH, ["one", "two", "three"], ["question"], 2)
        scorecard = ResearchQualityEvaluator().evaluate(plan, sources, ValidationResult(True), ResearchMetrics(), 2)

        self.assertGreaterEqual(scorecard.overall, 80)
        self.assertEqual(len(scorecard.dimensions), 5)
        self.assertIn(scorecard.grade, {"可靠", "卓越"})

    def test_research_profiles_do_not_mutate_base_settings(self):
        settings = Settings(max_rounds=3, max_sources=16, results_per_query=4)
        deep = apply_profile(settings, "深度")

        self.assertEqual(settings.max_rounds, 3)
        self.assertEqual(deep.max_rounds, 4)
        self.assertEqual(deep.max_sources, 28)


if __name__ == "__main__":
    unittest.main()
