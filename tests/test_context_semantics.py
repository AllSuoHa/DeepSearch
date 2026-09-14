import tempfile
import unittest
from pathlib import Path

from deepsearch import AgentRequest, ContextPolicy, DeepSearchAgent, WorkMode
from deepsearch.application.context import ContextResolver
from deepsearch.domain.models import (
    QuestionType,
    ResearchResult,
    SearchPlan,
    ValidationResult,
)
from deepsearch.infrastructure.config import LLMSettings, Settings


def research_result(question: str, directory: str) -> ResearchResult:
    plan = SearchPlan(question, QuestionType.FACT, [question], [question])
    return ResearchResult(
        question=question,
        report=f"# {question}\n\n## 结论\n\n测试结论 [1]",
        report_path=Path(directory) / "report.md",
        sources=[],
        rounds=1,
        plan=plan,
        stop_reason="测试",
        validation=ValidationResult(True),
    )


class RecordingResearchService:
    def __init__(self, directory: str):
        self.directory = directory
        self.research_calls = []
        self.follow_up_calls = []

    def research(self, question, progress=None, brief=None):
        self.research_calls.append(question)
        return research_result(question, self.directory)

    def follow_up(self, previous, question, progress=None):
        self.follow_up_calls.append((previous.question, question))
        return research_result(question, self.directory)


class ContextSemanticsTests(unittest.TestCase):
    def make_agent(self, directory: str):
        settings = Settings(
            runtime_mode="online",
            llm=LLMSettings(api_key="research-test-key", model="research-model"),
            reports_dir=Path(directory),
            cache_dir=Path(directory) / "cache",
        )
        agent = DeepSearchAgent(settings, search_providers=[])
        service = RecordingResearchService(directory)
        agent._service = service
        return agent, service

    def test_chat_or_search_result_research_rerun_is_fresh_and_uses_research_model(self):
        with tempfile.TemporaryDirectory() as directory:
            agent, service = self.make_agent(directory)
            run = agent.run(AgentRequest(
                "今天上海天气",
                WorkMode.RESEARCH,
                context_policy=ContextPolicy.FRESH,
                previous_research=None,
            ))

        self.assertEqual(service.research_calls, ["今天上海天气"])
        self.assertEqual(service.follow_up_calls, [])
        self.assertEqual(run.audit.model_name, "research-model")
        self.assertEqual(run.audit.context_policy, ContextPolicy.FRESH)

    def test_explicit_rerun_never_inherits_previous_research(self):
        with tempfile.TemporaryDirectory() as directory:
            agent, service = self.make_agent(directory)
            previous = research_result("旧问题", directory)
            agent.run(AgentRequest(
                "新问题",
                WorkMode.RESEARCH,
                context_policy=ContextPolicy.FRESH,
                previous_research=previous,
            ))

        self.assertEqual(service.research_calls, ["新问题"])
        self.assertEqual(service.follow_up_calls, [])

    def test_unrelated_conversation_question_is_fresh(self):
        previous = research_result("RAG 架构比较", ".")
        self.assertEqual(
            ContextResolver.resolve(previous, "杭州有哪些博物馆", ContextPolicy.CONVERSATION),
            ContextPolicy.FRESH,
        )
        self.assertEqual(
            ContextResolver.resolve(previous, "量子力学", ContextPolicy.CONVERSATION),
            ContextPolicy.FRESH,
        )

    def test_same_or_current_question_is_fresh(self):
        previous = research_result("上海天气怎么样", ".")
        self.assertEqual(
            ContextResolver.resolve(previous, "上海天气怎么样", ContextPolicy.CONVERSATION),
            ContextPolicy.FRESH,
        )
        self.assertEqual(
            ContextResolver.resolve(previous, "重新查询今天上海天气", ContextPolicy.CONVERSATION),
            ContextPolicy.FRESH,
        )

    def test_referential_follow_up_reuses_previous_result(self):
        with tempfile.TemporaryDirectory() as directory:
            agent, service = self.make_agent(directory)
            previous = research_result("比较 RAG 与长上下文", directory)
            run = agent.run(AgentRequest(
                "展开第二点",
                WorkMode.RESEARCH,
                context_policy=ContextPolicy.CONVERSATION,
                previous_research=previous,
            ))

        self.assertEqual(service.research_calls, [])
        self.assertEqual(service.follow_up_calls, [("比较 RAG 与长上下文", "展开第二点")])
        self.assertEqual(run.audit.context_policy, ContextPolicy.FOLLOW_UP)

    def test_context_isolation_requires_previous_result_from_current_session(self):
        previous = research_result("会话 A 的问题", ".")
        self.assertEqual(
            ContextResolver.resolve(None, "展开第二点", ContextPolicy.CONVERSATION),
            ContextPolicy.FRESH,
        )
        self.assertEqual(
            ContextResolver.resolve(previous, "展开第二点", ContextPolicy.CONVERSATION),
            ContextPolicy.FOLLOW_UP,
        )

    def test_follow_up_query_is_rewritten_without_legacy_semicolon_concatenation(self):
        query = ContextResolver.standalone_query("比较 RAG 与长上下文", "展开第二点")
        self.assertEqual(query, "比较 RAG 与长上下文：第二点的详细依据")
        self.assertNotIn("；追问：", query)


if __name__ == "__main__":
    unittest.main()
