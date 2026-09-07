"""Streamlit 展示层与研究内核之间的适配工具。"""

from __future__ import annotations

import re
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock
from typing import Callable, TypeVar

import streamlit as st

from ...bootstrap import DeepSearchAgent, apply_profile
from ...application.search_service import search_response_markdown
from ...domain.models import (
    AgentRunResult,
    QuestionType,
    ResearchBrief,
    ResearchResult,
    SearchPlan,
    SearchResponse,
    SearchResult,
    Source,
    ValidationResult,
    WorkMode,
)
from ...infrastructure.config import LLMSettings, Settings, load_settings
from ...infrastructure.storage import ConversationStore, FileArtifactStorage, PromptShortcutStore

# support.py 位于 deepsearch/presentation/web/；向上四级才是仓库根目录。
# 统一从仓库根目录解析配置、报告和缓存，避免 Web 端在 presentation/ 下
# 意外创建第二套运行数据目录。
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# phase 是应用层与展示层之间的稳定协议。这里保留用户能理解的真实步骤，
# 同一个 phase 的重复消息只更新说明；研究补搜后重新进入 search 则会形成
# 新步骤。详细诊断仍写入应用日志和结构化研究轨迹。
_PROGRESS_STAGE_LABELS = {
    "route": "准备任务",
    "plan": "制定计划",
    "search": "搜索来源",
    "fetch": "读取正文",
    "evaluate": "评估证据",
    "adjust": "补充检索",
    "verify": "交叉验证",
    "generate": "整理证据",
    "evidence_map": "整理证据",
    "draft": "生成初稿",
    "review": "审校报告",
    "validate": "验证报告",
    "score": "质量评分",
    "saved": "保存结果",
    "deliver": "投递结果",
    "follow_up": "处理追问",
}

T = TypeVar("T")


class RunCancelledError(RuntimeError):
    """后台任务在阶段边界观察到用户停止请求。"""


@dataclass(frozen=True, slots=True)
class ProgressStepSnapshot:
    """一个可直接展示的步骤快照，不向页面泄露可变线程状态。"""

    stage: str
    detail: str
    duration_seconds: float
    active: bool


@dataclass(frozen=True, slots=True)
class RunProgressSnapshot:
    """某一时刻的总用时、当前阶段和各阶段用时。"""

    total_seconds: float
    current_stage: str
    steps: tuple[ProgressStepSnapshot, ...]


class RunProgressTracker:
    """在线程间安全传递研究进度，并为页面计算真实阶段耗时。

    后台线程只调用 :meth:`update`，绝不直接调用 Streamlit API；页面线程
    读取不可变快照并渲染。这样长模型请求不会阻塞每秒一次的状态刷新。
    """

    def __init__(
        self,
        initial_detail: str = "正在接收请求并准备运行…",
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._clock = clock or time.perf_counter
        self._started_at = self._clock()
        self._finished_at: float | None = None
        self._steps: list[dict[str, object]] = [{
            "stage": "准备任务",
            "detail": initial_detail,
            "started_at": self._started_at,
            "ended_at": None,
        }]
        self._lock = Lock()
        self._changed = Event()
        self._cancelled = Event()

    def update(self, phase: str, message: str) -> None:
        """记录阶段回调；停止请求在下一阶段边界转为明确取消异常。"""

        if self._cancelled.is_set():
            raise RunCancelledError("已停止生成")
        now = self._clock()
        stage = progress_stage_label(phase)
        with self._lock:
            current = self._steps[-1]
            if current["stage"] == stage:
                current["detail"] = message
            else:
                current["ended_at"] = now
                self._steps.append({
                    "stage": stage,
                    "detail": message,
                    "started_at": now,
                    "ended_at": None,
                })
        self._changed.set()

    def finish(self) -> RunProgressSnapshot:
        """冻结最后一步及总用时，并返回用于最终状态展示的快照。"""

        now = self._clock()
        with self._lock:
            if self._finished_at is None:
                self._finished_at = now
                self._steps[-1]["ended_at"] = now
        self._changed.set()
        return self.snapshot()

    def cancel(self) -> None:
        """发出协作式停止信号；网络调用返回后会在下一回调处生效。"""

        self._cancelled.set()
        self._changed.set()

    def snapshot(self) -> RunProgressSnapshot:
        """复制当前进度，避免页面在后台更新时读到半写入数据。"""

        now = self._clock()
        with self._lock:
            total_end = now if self._finished_at is None else self._finished_at
            steps = tuple(
                ProgressStepSnapshot(
                    stage=str(item["stage"]),
                    detail=str(item["detail"]),
                    duration_seconds=max(
                        0.0,
                        float(now if item["ended_at"] is None else item["ended_at"])
                        - float(item["started_at"]),
                    ),
                    active=item["ended_at"] is None,
                )
                for item in self._steps
            )
        return RunProgressSnapshot(
            total_seconds=max(0.0, total_end - self._started_at),
            current_stage=steps[-1].stage,
            steps=steps,
        )

    def wait_for_change(self, timeout: float) -> None:
        """等待新阶段或下一秒刻度，避免页面轮询形成忙循环。"""

        self._changed.clear()
        self._changed.wait(max(0.01, timeout))


@st.cache_resource
def background_executor() -> ThreadPoolExecutor:
    """复用有限线程池执行阻塞研究，避免每次 rerun 创建遗留线程。"""

    return ThreadPoolExecutor(max_workers=4, thread_name_prefix="deepsearch-web")


def get_settings() -> Settings:
    """加载普通配置，并用 Streamlit Secrets 中的模型配置做安全覆盖。"""

    config_path = st.session_state.get("config_path", str(PROJECT_ROOT / "config.json"))
    settings = load_settings(config_path)
    try:
        api_key = str(st.secrets.get("DEEPSEARCH_API_KEY", ""))
        base_url = str(st.secrets.get("DEEPSEARCH_BASE_URL", settings.llm.base_url))
        model = str(st.secrets.get("DEEPSEARCH_MODEL", settings.llm.model))
        llm_timeout = float(st.secrets.get("DEEPSEARCH_LLM_TIMEOUT", settings.llm.request_timeout))
        integration_key = str(st.secrets.get("DEEPSEARCH_CUSTOMER_SERVICE_INTEGRATION_KEY", ""))
        customer_service_api_key = str(st.secrets.get("DEEPSEARCH_CUSTOMER_SERVICE_API_KEY", ""))
        brave_api_key = str(st.secrets.get("DEEPSEARCH_BRAVE_API_KEY", settings.brave_api_key))
    # 未创建 secrets 文件是合法状态，此时继续使用配置与环境变量。
    except (FileNotFoundError, KeyError):
        return settings
    if api_key:
        settings.llm = LLMSettings(
            base_url=base_url,
            model=model,
            api_key=api_key,
            request_timeout=max(10.0, llm_timeout),
        )
    if integration_key:
        settings.customer_service.integration_key = integration_key
    if customer_service_api_key:
        settings.customer_service.api_access_key = customer_service_api_key
    if brave_api_key:
        settings.brave_api_key = brave_api_key
    return settings


def create_agent(profile: str = "均衡") -> DeepSearchAgent:
    """按页面选择的研究强度创建单次 Agent，不修改持久化设置。"""

    return DeepSearchAgent(apply_profile(get_settings(), profile))


def progress_stage_label(phase: str) -> str:
    """把内部事件合并为少量、稳定的用户可见阶段。"""

    return _PROGRESS_STAGE_LABELS.get(phase, "执行任务")


def format_elapsed(seconds: float) -> str:
    """把单调时钟的秒数格式化为紧凑中文用时。"""

    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, remaining_seconds = divmod(remainder, 60)
    if hours:
        return f"{hours} 小时 {minutes} 分 {remaining_seconds} 秒"
    if minutes:
        return f"{minutes} 分 {remaining_seconds} 秒"
    return f"{remaining_seconds} 秒"


def progress_details_markdown(snapshot: RunProgressSnapshot) -> str:
    """生成折叠区中的高层思考步骤、说明和用时，不展示隐性思维链。"""

    lines = ["**思考步骤与说明**", ""]
    for step in snapshot.steps:
        icon = ":material/progress_activity:" if step.active else ":material/check_circle:"
        duration = "不足 1 秒" if step.duration_seconds < 1 else format_elapsed(step.duration_seconds)
        detail = " ".join(step.detail.split()) or "该步骤没有附加说明。"
        lines.append(f"- {icon} **{step.stage}** · {duration}  ")
        lines.append(f"  {detail}")
    return "\n".join(lines)


def wait_for_background_result(
    future: Future[T],
    tracker: RunProgressTracker,
    on_snapshot: Callable[[RunProgressSnapshot], None],
) -> T:
    """按整秒刷新状态，同时在阶段变化时立即刷新说明。

    任务本身在有限线程池中运行；本函数留在 Streamlit 页面线程，因此所有
    UI 更新都符合 ScriptRunContext 约束。等待使用事件而不是固定 sleep，
    阶段变化无需等到下一秒才显示。
    """

    while not future.done():
        snapshot = tracker.snapshot()
        on_snapshot(snapshot)
        # 对齐任务启动后的下一整数秒，确保计时器呈现 0、1、2… 的节奏；
        # 最短 50ms 避免浮点边界导致空转。
        next_second = int(snapshot.total_seconds) + 1
        tracker.wait_for_change(max(0.05, next_second - snapshot.total_seconds))
    return future.result()


def source_rows(result: ResearchResult) -> list[dict]:
    """把领域来源转换为数据表可直接消费的展示行。"""

    rows = []
    for source in result.sources:
        if source.provider == "mock":
            status = "Mock"
        elif source.fetched:
            status = "正文"
        else:
            status = "摘要"
        rows.append({
            "编号": source.source_id,
            "标题": source.title,
            "域名": source.url.split("/")[2] if "://" in source.url else source.provider,
            "检索源": source.provider,
            "质量分": round(source.quality_score * 100),
            "相关度": round(source.relevance_score * 100),
            "状态": status,
            "链接": source.url,
        })
    return rows


def trace_rows(result: ResearchResult) -> list[dict]:
    """把结构化轮次轨迹转换为中文列名表格。"""

    return [{
        "轮次": trace.round_number,
        "查询数": len(trace.queries),
        "搜索结果": trace.results_found,
        "新增来源": trace.new_sources,
        "在线正文": trace.fetched_sources,
        "耗时（秒）": trace.duration_seconds,
        "充分": trace.sufficient,
        "降级": trace.used_fallback,
        "证据缺口": "；".join(trace.missing_dimensions),
        "下一步查询": "；".join(trace.next_queries),
        "判断": trace.decision,
    } for trace in result.trace]


def init_session_state() -> None:
    """集中声明所有会话状态键；setdefault 保证 rerun 时不会覆盖用户数据。"""

    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("last_result", None)
    st.session_state.setdefault("last_run", None)
    st.session_state.setdefault("progress_events", [])
    st.session_state.setdefault("config_path", str(PROJECT_ROOT / "config.json"))
    st.session_state.setdefault("research_profile", "均衡")
    default_mode = {"auto": "智能判断", "search": "搜索", "research": "研究"}.get(
        get_settings().default_work_mode,
        "智能判断",
    )
    st.session_state.setdefault("work_mode", default_mode)
    st.session_state.setdefault("pending_question", "")
    st.session_state.setdefault("current_conversation_id", "")
    # pending 由输入提交回调写入，页面开始执行时转为 running；停止按钮
    # 将其改为 stopped 并触发 rerun。状态只保存字符串，不把线程放进会话。
    st.session_state.setdefault("agent_run_state", "idle")
    # tracker 只在当前 Streamlit 会话内存活，用于停止后台计算；它不会写入
    # 会话 JSON，也不会跨浏览器标签页共享。
    st.session_state.setdefault("active_run_tracker", None)


def reset_research() -> None:
    """开始新对话，保留全局配置和用户选择的工作模式。"""

    st.session_state.messages = []
    st.session_state.last_result = None
    st.session_state.last_run = None
    st.session_state.progress_events = []
    st.session_state.current_conversation_id = ""
    st.session_state.agent_run_state = "idle"
    st.session_state.active_run_tracker = None


def conversation_store(settings: Settings | None = None) -> ConversationStore:
    """按当前配置创建轻量会话仓库。"""

    return ConversationStore((settings or get_settings()).conversation_dir)


def prompt_shortcut_store(settings: Settings | None = None) -> PromptShortcutStore:
    """返回与会话数据并列存放的个人快捷输入存储。"""

    active = settings or get_settings()
    return PromptShortcutStore(active.conversation_dir.parent / "prompt-shortcuts.json")


def recent_conversations(limit: int = 12) -> list[dict]:
    """为侧栏读取最近会话摘要。"""

    return conversation_store().list(limit)


def load_conversation(conversation_id: str) -> bool:
    """恢复消息和最小研究上下文，并同步输入框的工作模式。"""

    conversation = conversation_store().load(conversation_id)
    if not conversation:
        return False
    st.session_state.current_conversation_id = conversation_id
    st.session_state.messages = list(conversation.get("messages", []))
    last_mode = next(
        (message.get("mode") for message in reversed(st.session_state.messages) if message.get("role") == "assistant"),
        "auto",
    )
    st.session_state.work_mode = {"auto": "智能判断", "search": "搜索", "research": "研究"}.get(
        str(last_mode),
        "智能判断",
    )
    st.session_state.pop("composer-mode", None)
    st.session_state.last_result = research_context_from_messages(st.session_state.messages)
    # 搜索结果不需要恢复网页正文；保存的轻量来源元数据足以重新生成
    # 与首次运行一致的结果卡片、风险标签和链接按钮。
    st.session_state.last_run = search_run_from_messages(st.session_state.messages)
    return True


def search_run_from_messages(messages: list[dict]) -> AgentRunResult | None:
    """从最后一条搜索回复恢复结构化卡片，兼容未保存摘要字段的旧会话。"""

    message = next(
        (item for item in reversed(messages) if item.get("role") == "assistant"),
        None,
    )
    if not message or message.get("kind") != "search":
        return None

    legacy_snippets = _search_snapshot_snippets(str(message.get("content", "")))
    items = []
    for index, item in enumerate(message.get("sources", [])):
        if not isinstance(item, dict):
            continue
        stored_reasons = item.get("risk_reasons", ())
        reasons = (
            tuple(str(value) for value in stored_reasons if str(value).strip())
            if isinstance(stored_reasons, (list, tuple))
            else ()
        )
        items.append(SearchResult(
            title=str(item.get("title", "未命名结果")),
            url=str(item.get("url", "")),
            snippet=str(item.get("snippet", "")).strip()
            or (legacy_snippets[index] if index < len(legacy_snippets) else ""),
            query=str(message.get("question", "")),
            provider=str(item.get("provider", "")),
            resource_type=str(item.get("resource_type", "网页")),
            risk_level=str(item.get("risk_level", "未验证")),
            risk_reasons=reasons,
            published_at=str(item.get("published_at", "")),
        ))

    warnings = message.get("warnings", ())
    if not isinstance(warnings, (list, tuple)):
        warnings = ()
    response = SearchResponse(
        query=str(message.get("question", "历史搜索")),
        answer=str(message.get("summary", "")).strip() or "已恢复历史搜索结果。",
        items=items,
        queries=[str(value) for value in message.get("queries", []) if str(value).strip()],
        warnings=[str(value) for value in warnings if str(value).strip()]
        or _search_snapshot_warnings(str(message.get("content", ""))),
        artifact_path=Path(str(message["artifact_path"])) if message.get("artifact_path") else None,
    )
    return AgentRunResult(WorkMode.SEARCH, WorkMode.SEARCH, search=response)


def _search_snapshot_snippets(content: str) -> list[str]:
    """从 2.2 早期 Markdown 快照中提取每条摘要，仅用于旧会话迁移。"""

    snippets = []
    for section in re.split(r"(?m)^###\s+\d+\.\s+", content)[1:]:
        lines = [line.strip() for line in section.splitlines() if line.strip()]
        body = []
        for line in lines[2:]:
            if line.startswith("## "):
                break
            body.append(line)
        snippets.append(" ".join(body))
    return snippets


def _search_snapshot_warnings(content: str) -> list[str]:
    """读取旧搜索快照的提示列表；没有提示章节时返回空列表。"""

    marker = "\n## 提示\n"
    if marker not in content:
        return []
    return [
        line.removeprefix("- ").strip()
        for line in content.split(marker, 1)[1].splitlines()
        if line.strip().startswith("- ")
    ]


def research_context_from_messages(messages: list[dict]) -> ResearchResult | None:
    """从持久化消息重建最小研究上下文；追问会重新检索，不恢复网页正文。"""

    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.get("role") != "assistant" or message.get("kind") != "research":
            continue
        question = str(message.get("question", "")).strip()
        if not question:
            question = next(
                (
                    str(item.get("content", "")).strip()
                    for item in reversed(messages[:index])
                    if item.get("role") == "user" and str(item.get("content", "")).strip()
                ),
                "历史研究",
            )
        sources = [
            Source(
                title=str(item.get("title", "未命名来源")),
                url=str(item.get("url", "")),
                content="",
                provider=str(item.get("provider", "")),
                fetched=False,
                source_id=source_id,
                resource_type=str(item.get("resource_type", "网页")),
                risk_level=str(item.get("risk_level", "未验证")),
                published_at=str(item.get("published_at", "")),
            )
            for source_id, item in enumerate(message.get("sources", []), 1)
            if isinstance(item, dict)
        ]
        report = str(message.get("content", ""))
        plan = SearchPlan(
            question=question,
            question_type=QuestionType.RESEARCH,
            subquestions=[question],
            queries=[question],
            brief=ResearchBrief(),
        )
        return ResearchResult(
            question=question,
            report=report,
            report_path=Path(str(message.get("artifact_path", ""))),
            sources=sources,
            rounds=max(1, int(message.get("rounds", 1) or 1)),
            plan=plan,
            stop_reason=str(message.get("stop_reason", "已恢复历史研究上下文")),
            validation=ValidationResult(True),
            direct_answer=str(message.get("summary", "")),
            review_summary=tuple(str(item) for item in message.get("review_summary", []) if str(item).strip()),
        )
    return None


def persist_user_message(content: str, mode: WorkMode) -> dict:
    """确保会话存在后保存用户消息和请求模式。"""

    store = conversation_store()
    conversation_id = st.session_state.get("current_conversation_id", "")
    conversation = store.load(conversation_id) if conversation_id else None
    if conversation is None:
        conversation = store.create()
        st.session_state.current_conversation_id = conversation["id"]
    message = {"role": "user", "content": content, "mode": mode.value}
    store.append(conversation, message)
    return conversation


def persist_run_result(run: AgentRunResult) -> dict:
    """保存可恢复消息和轻量来源元数据，正文仍由缓存与报告文件管理。"""

    settings = get_settings()
    store = conversation_store(settings)
    conversation_id = st.session_state.get("current_conversation_id", "")
    conversation = store.load(conversation_id)
    if conversation is None:
        conversation = store.create()
        st.session_state.current_conversation_id = conversation["id"]
    if run.search is not None:
        artifact = FileArtifactStorage(settings.conversation_dir.parent / "artifacts").save_search(run.search)
        content = search_response_markdown(run.search)
        sources = [{
            "title": item.title,
            "url": item.url,
            "snippet": item.snippet,
            "provider": item.provider,
            "resource_type": item.resource_type,
            "risk_level": item.risk_level,
            "risk_reasons": list(item.risk_reasons),
            "published_at": item.published_at,
        } for item in run.search.items]
        kind = "search"
        question = run.search.query
        summary = run.search.answer
        rounds = 0
        stop_reason = ""
        review_summary = []
    else:
        result = run.research
        if result is None:
            raise ValueError("运行结果为空")
        artifact = result.report_path
        content = result.report
        sources = [{
            "title": item.title, "url": item.url, "provider": item.provider,
            "resource_type": item.resource_type, "risk_level": item.risk_level,
            "published_at": item.published_at,
        } for item in result.sources]
        kind = "research"
        question = result.question
        summary = result.direct_answer
        rounds = result.rounds
        stop_reason = result.stop_reason
        review_summary = list(result.review_summary)
    message = {
        "role": "assistant",
        "content": content,
        "mode": run.resolved_mode.value,
        "kind": kind,
        "artifact_path": str(artifact),
        "sources": sources,
        "question": question,
        "summary": summary,
        "rounds": rounds,
        "stop_reason": stop_reason,
        "review_summary": review_summary,
        "queries": list(run.search.queries) if run.search is not None else [],
        "warnings": list(run.search.warnings) if run.search is not None else [],
    }
    store.append(conversation, message)
    return message


def record_delivery(outcome_status: str) -> None:
    """把手动投递状态写回当前会话的最后一条助手消息。"""

    store = conversation_store()
    conversation = store.load(st.session_state.get("current_conversation_id", ""))
    if not conversation or not conversation.get("messages"):
        return
    conversation["messages"][-1]["delivery"] = outcome_status
    store.save(conversation)
