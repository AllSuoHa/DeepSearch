import unittest

from deepsearch.domain.models import SearchPlan, QuestionType, Source
from deepsearch.application.verification import AnswerValidator, canonicalize_source_section


class ValidationTests(unittest.TestCase):
    def test_source_section_is_rebuilt_as_clickable_cards_without_raw_urls(self):
        sources = [
            Source(
                "Example [Guide]",
                "https://www.example.org/research/guide",
                "supported content",
                source_id=1,
                fetched=True,
                quality_score=0.86,
            )
        ]

        report = canonicalize_source_section(
            "# Report\n\n## 结论\nSupported [1].\n\n"
            "## 参考来源\n1. Example: https://www.example.org/research/guide",
            sources,
        )

        self.assertEqual(report.count("## 参考来源"), 1)
        self.assertIn("[Example \\[Guide\\]](<https://www.example.org/research/guide>)", report)
        self.assertIn("`example.org` · 已读取正文 · 质量 86/100", report)
        self.assertNotIn("Example: https://", report)

    def test_simple_fact_does_not_require_analysis_section(self):
        plan = SearchPlan("release date", QuestionType.FACT, ["release date"], ["release date"])
        sources = [Source("one", "https://example.org", "The release date is 2025.", source_id=1)]
        report = (
            "# Release date\n\n## 结论\nThe release date is 2025 [1].\n\n"
            "## 参考来源\n- [1] https://example.org"
        )

        result = AnswerValidator().validate(report, sources, plan)

        self.assertTrue(result.valid, result.issues)

    def test_complex_question_reports_exact_missing_analysis_section(self):
        plan = SearchPlan("compare", QuestionType.COMPARISON, ["compare"], ["compare"])
        sources = [Source("one", "https://example.org", "Comparison evidence.", source_id=1)]
        report = "# Compare\n\n## 结论\nComparison evidence [1].\n\n## 参考来源\n- [1] https://example.org"

        result = AnswerValidator().validate(report, sources, plan)

        self.assertFalse(result.valid)
        self.assertIn("缺少必需章节：## 分析", result.issues)

    def test_rejects_nonexistent_citation(self):
        plan = SearchPlan("q", QuestionType.FACT, ["q"], ["q"])
        sources = [Source("one", "mock://one", "content", source_id=1)]
        report = "# q\n\n## 结论\n错引 [9]\n\n## 分析\n### 1. q\ntext\n\n## 参考来源"
        result = AnswerValidator().validate(report, sources, plan)
        self.assertFalse(result.valid)
        self.assertIn("无效引用", result.issues[0])

    def test_rejects_detail_claim_without_textual_support(self):
        plan = SearchPlan("q", QuestionType.FACT, ["q"], ["q"])
        sources = [Source("one", "mock://one", "Python was released with interpreter improvements.", source_id=1)]
        report = "# q\n\n## 结论\nsummary [1]\n\n## 分析\n### 1. q\n- 完全无关的火星农业结论和天气预测 [1]\n\n## 参考来源"
        result = AnswerValidator().validate(report, sources, plan)
        self.assertFalse(result.valid)
        self.assertTrue(any("详细论点" in issue for issue in result.issues))

    def test_rejects_repeated_blocks_and_navigation_noise(self):
        plan = SearchPlan("q", QuestionType.FACT, ["q"], ["q"])
        sources = [Source("one", "https://example.org", "supported factual content", source_id=1)]
        repeated = "This paragraph repeats enough words to be detected as duplicated report filler without adding useful information."
        report = (
            "# q\n\n## 结论\nSupported factual content [1].\n\n"
            f"## 关键发现\n{repeated}\n\n{repeated}\n\n"
            "## 分析\nSupported factual content [1]. Skip to content\n\n"
            "## 局限\n- one source\n\n## 参考来源\n- [1] https://example.org"
        )
        result = AnswerValidator().validate(report, sources, plan)
        self.assertTrue(any("重复内容" in issue for issue in result.issues))
        self.assertTrue(any("模板噪声" in issue for issue in result.issues))


if __name__ == "__main__":
    unittest.main()
