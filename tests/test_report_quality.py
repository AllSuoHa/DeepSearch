import json
import tempfile
import unittest
from pathlib import Path

from deepsearch.application.errors import ReportQualityError
from deepsearch.application.evaluation import ResearchQualityEvaluator
from deepsearch.application.planner import ResearchPlanner
from deepsearch.application.service import ResearchService
from deepsearch.application.verification import AnswerValidator, CrossVerifier, canonicalize_source_section
from deepsearch.domain.models import (
    EvidenceGroup,
    Confidence,
    QuestionType,
    ResearchPolicy,
    ResearchResult,
    SearchPlan,
    SearchResult,
    Source,
    ValidationResult,
)
from deepsearch.domain.ranking import SourceRanker
from deepsearch.infrastructure.cache import ResearchCache
from deepsearch.infrastructure.reporting import MarkdownReporter
from deepsearch.infrastructure.storage import FileReportStorage


class RecordingLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        return self.responses.pop(0)


class OneResultProvider:
    name = "fixture"

    def search(self, query: str, limit: int = 5):
        return [SearchResult("Evidence", "https://example.org/evidence", "useful evidence", query, self.name)]


class OneSourceFetcher:
    def fetch_all(self, results):
        return [Source("Evidence", "https://example.org/evidence", "useful evidence for the answer", query=results[0].query, provider="fixture")]


class AlwaysBadReporter:
    last_review_summary = ()

    def set_progress(self, callback):
        pass

    def generate(self, *args, **kwargs):
        return "# Bad\n\nunsupported"

    def revise(self, *args, **kwargs):
        return "# Still bad\n\nunsupported"


class FailingLLM:
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        raise RuntimeError("模型响应超过 180 秒")


class ReportQualityTests(unittest.TestCase):
    def test_json_parser_accepts_thinking_and_fenced_model_output(self):
        value = '<think>internal notes</think>\n```json\n{"claims": [], "gaps": []}\n```\n完成'

        parsed = MarkdownReporter._parse_json(value, "证据整理")

        self.assertEqual(parsed, {"claims": [], "gaps": []})

    def test_model_error_keeps_stage_and_actionable_detail(self):
        reporter = MarkdownReporter(FailingLLM())
        plan = SearchPlan("q", QuestionType.RESEARCH, ["q"], ["q"], 1)
        source = Source("Paper", "https://example.org/paper", "Useful evidence.", source_id=1)

        with self.assertRaisesRegex(
            ReportQualityError,
            "三阶段报告生成失败（证据整理）：模型响应超过 180 秒",
        ):
            reporter.generate(plan, [source], [], [], 1)

    def test_reporter_runs_evidence_draft_and_independent_review_in_order(self):
        final = "# Choice\n\n## 结论\nUse RAG when freshness matters [1].\n\n## 关键发现\n- Fresh evidence matters [1].\n\n## 分析\n- RAG uses current evidence [1].\n\n## 局限\n- One source only.\n\n## 参考来源\n- [1] https://example.org/paper"
        llm = RecordingLLM([
            json.dumps({"direct_answer": "Use RAG", "claims": [{"claim": "Freshness", "source_ids": [1]}], "conflicts": [], "gaps": []}),
            "# Draft\n\n## 结论\nDraft answer [1].",
            json.dumps({"issues": ["初稿过短，已补全结构"], "final_report": final}, ensure_ascii=False),
        ])
        reporter = MarkdownReporter(llm)
        plan = SearchPlan("RAG or long context", QuestionType.COMPARISON, ["RAG or long context"], ["RAG"], 1)
        source = Source("Paper", "https://example.org/paper", "RAG uses fresh external evidence.", source_id=1)

        report = reporter.generate(plan, [source], [EvidenceGroup("Freshness", [1], Confidence.SINGLE)], [], 1)

        self.assertEqual(len(llm.calls), 3)
        self.assertIn("证据编辑", llm.calls[0][0])
        self.assertIn("研究作者", llm.calls[1][0])
        self.assertIn("独立终稿编辑", llm.calls[2][0])
        self.assertEqual(report, canonicalize_source_section(final, [source]))
        self.assertEqual(reporter.last_review_summary, ("初稿过短，已补全结构",))

    def test_reporter_deterministically_restores_missing_source_list(self):
        final_without_sources = "# Answer\n\n## 结论\nUseful evidence answers the question [1]."
        llm = RecordingLLM([
            json.dumps({"direct_answer": "Useful evidence", "claims": [], "conflicts": [], "gaps": []}),
            final_without_sources,
            json.dumps({"issues": [], "final_report": final_without_sources}),
        ])
        reporter = MarkdownReporter(llm)
        plan = SearchPlan("What is useful?", QuestionType.FACT, ["What is useful?"], ["useful"], 1)
        source = Source("Evidence", "https://example.org/evidence", "Useful evidence answers the question.", source_id=1)

        report = reporter.generate(plan, [source], [], [], 1)

        self.assertIn("## 参考来源", report)
        self.assertIn("https://example.org/evidence", report)
        self.assertEqual(report.count("## 参考来源"), 1)

    def test_invalid_report_is_not_saved_after_single_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            report_dir = Path(directory) / "reports"
            service = ResearchService(
                policy=ResearchPolicy(max_rounds=1, results_per_query=1, max_sources=2),
                providers=[OneResultProvider()],
                fallback=None,
                fetcher=OneSourceFetcher(),
                planner=ResearchPlanner(),
                verifier=CrossVerifier(),
                validator=AnswerValidator(),
                cache=ResearchCache(Path(directory) / "cache", enabled=False),
                ranker=SourceRanker(),
                reporter=AlwaysBadReporter(),
                storage=FileReportStorage(report_dir),
                quality_evaluator=ResearchQualityEvaluator(),
            )

            with self.assertRaises(ReportQualityError):
                service.research("What is the answer?")
            self.assertFalse(report_dir.exists())

    def test_reused_follow_up_is_not_saved_when_quality_gate_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            report_dir = Path(directory) / "reports"
            service = ResearchService(
                policy=ResearchPolicy(max_rounds=1, results_per_query=1, max_sources=2),
                providers=[OneResultProvider()],
                fallback=None,
                fetcher=OneSourceFetcher(),
                planner=ResearchPlanner(),
                verifier=CrossVerifier(),
                validator=AnswerValidator(),
                cache=ResearchCache(Path(directory) / "cache", enabled=False),
                ranker=SourceRanker(),
                reporter=AlwaysBadReporter(),
                storage=FileReportStorage(report_dir),
                quality_evaluator=ResearchQualityEvaluator(),
            )
            plan = ResearchPlanner().plan("What is useful evidence?")
            source = Source(
                "Evidence",
                "https://example.org/evidence",
                "Useful evidence supports the answer.",
                provider="fixture",
                source_id=1,
            )
            previous = ResearchResult(
                question="What is useful evidence?",
                report="## 结论\nUseful evidence supports the answer [1].",
                report_path=Path(directory) / "previous.md",
                sources=[source],
                rounds=1,
                plan=plan,
                stop_reason="证据已充分",
                validation=ValidationResult(True),
            )

            with self.assertRaises(ReportQualityError):
                service.follow_up(previous, "Explain useful evidence")
            self.assertFalse(report_dir.exists())


if __name__ == "__main__":
    unittest.main()
