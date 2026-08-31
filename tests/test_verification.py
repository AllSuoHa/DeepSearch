import unittest

from deepsearch.domain.models import SearchPlan, QuestionType, Source
from deepsearch.application.verification import AnswerValidator


class ValidationTests(unittest.TestCase):
    def test_rejects_nonexistent_citation(self):
        plan = SearchPlan("q", QuestionType.FACT, ["q"], ["q"])
        sources = [Source("one", "mock://one", "content", source_id=1)]
        report = "# q\n\n## 摘要\n错引 [9]\n\n## 详细分析\n### 1. q\ntext\n\n## 全部来源"
        result = AnswerValidator().validate(report, sources, plan)
        self.assertFalse(result.valid)
        self.assertIn("无效引用", result.issues[0])

    def test_rejects_detail_claim_without_textual_support(self):
        plan = SearchPlan("q", QuestionType.FACT, ["q"], ["q"])
        sources = [Source("one", "mock://one", "Python was released with interpreter improvements.", source_id=1)]
        report = "# q\n\n## 摘要\nsummary [1]\n\n## 详细分析\n### 1. q\n- 完全无关的火星农业结论和天气预测 [1]\n\n## 全部来源"
        result = AnswerValidator().validate(report, sources, plan)
        self.assertFalse(result.valid)
        self.assertTrue(any("详细论点" in issue for issue in result.issues))


if __name__ == "__main__":
    unittest.main()
