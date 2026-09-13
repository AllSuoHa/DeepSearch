"""Streamlit 展示层与研究内核之间的适配工具。"""

from __future__ import annotations

import re
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock
from typing import Callable, TypeVar
from uuid import uuid4

import streamlit as st

from ...bootstrap import DeepSearchAgent, apply_profile
from ...application.search_service import search_response_markdown
from ...domain.models import (
    AgentRunResult,
    ChatResult,
    ChatTurn,
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
    "route": "理解问题",
    "chat": "生成直接回复",
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
            "stage": "理解问题",
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


@dataclass(frozen=True, slots=True)
class BackgroundRunHandle:
    """可跨 Streamlit 页面重跑找回的后台任务句柄。"""

    run_id: str
    conversation_id: str
    question: str
    requested_mode: WorkMode
    tracker: RunProgressTracker
    future: Future[AgentRunResult]
    cancelled: Event = field(default_factory=Event, compare=False, repr=False)


# Streamlit 页面切换会终止原页面脚本，但 cache_resource 线程池中的任务仍可
# 继续。进程级注册表只保存短期句柄，session_state 仅保存 run_id，既避开
# 序列化 Future，也允许返回对话页后接续同一任务而不是重新发起请求。
_BACKGROUND_RUNS: dict[str, BackgroundRunHandle] = {}
_BACKGROUND_RUNS_LOCK = Lock()
_RECENT_SUBMISSIONS: dict[tuple[str, str, str], float] = {}
_SUBMISSION_LOCK = Lock()


def claim_submission(scope_id: str, question: str, mode: WorkMode, cooldown: float = 2.0) -> bool:
    """以进程级原子闸门拒绝同一浏览器短时间内的重复提交。"""

    now = time.monotonic()
    normalized_question = " ".join(question.split())
    key = (scope_id, mode.value, normalized_question)
    with _SUBMISSION_LOCK:
        expired_before = now - max(0.1, cooldown)
        for stale_key, claimed_at in tuple(_RECENT_SUBMISSIONS.items()):
            if claimed_at < expired_before:
                _RECENT_SUBMISSIONS.pop(stale_key, None)
        if key in _RECENT_SUBMISSIONS:
            return False
        _RECENT_SUBMISSIONS[key] = now
        return True


def register_background_run(handle: BackgroundRunHandle) -> None:
    """注册后台任务，供页面切换后的新脚本轮次恢复。"""

    with _BACKGROUND_RUNS_LOCK:
        _BACKGROUND_RUNS[handle.run_id] = handle


def active_background_run(run_id: str) -> BackgroundRunHandle | None:
    """按不可猜测的运行 ID 读取当前进程中的任务。"""

    with _BACKGROUND_RUNS_LOCK:
        return _BACKGROUND_RUNS.get(run_id)


def discard_background_run(run_id: str) -> None:
    """任务结果已同步到页面后释放短期句柄。"""

    with _BACKGROUND_RUNS_LOCK:
        _BACKGROUND_RUNS.pop(run_id, None)


def cancel_background_run(run_id: str) -> None:
    """仅响应用户明确停止或新建对话，不把普通页面切换当作取消。"""

    with _BACKGROUND_RUNS_LOCK:
        handle = _BACKGROUND_RUNS.pop(run_id, None)
    if handle is not None:
        # 热更新前创建的旧句柄可能还没有 cancelled 字段。兼容它们可以
        # 避免开发服务器保留旧 Future 时因新增字段再次触发 AttributeError。
        cancelled = getattr(handle, "cancelled", None)
        if isinstance(cancelled, Event):
            cancelled.set()
        handle.tracker.cancel()
        handle.future.cancel()


def get_settings() -> Settings:
    """加载普通配置，并用 Streamlit Secrets 中的模型配置做安全覆盖。"""

    config_path = st.session_state.get("config_path", str(PROJECT_ROOT / "config.json"))
    settings = load_settings(config_path)
    try:
        api_key = str(st.secrets.get("DEEPSEARCH_API_KEY", ""))
        base_url = str(st.secrets.get("DEEPSEARCH_BASE_URL", settings.llm.base_url))
        model = str(st.secrets.get("DEEPSEARCH_MODEL", settings.llm.model))
        llm_timeout = float(st.secrets.get("DEEPSEARCH_LLM_TIMEOUT", settings.llm.request_timeout))
        chat_api_key = str(st.secrets.get("DEEPSEARCH_CHAT_API_KEY", settings.chat_llm.api_key))
        chat_base_url = str(st.secrets.get("DEEPSEARCH_CHAT_BASE_URL", settings.chat_llm.base_url))
        chat_model = str(st.secrets.get("DEEPSEARCH_CHAT_MODEL", settings.chat_llm.model))
        chat_timeout = float(st.secrets.get("DEEPSEARCH_CHAT_TIMEOUT", settings.chat_llm.request_timeout))
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
    if chat_api_key or chat_base_url or chat_model:
        settings.chat_llm = LLMSettings(
            base_url=chat_base_url,
            model=chat_model,
            api_key=chat_api_key,
            request_timeout=max(1.0, chat_timeout),
        )
    if integration_key:
        settings.customer_service.integration_key = integration_key
    if customer_service_api_key:
        settings.customer_service.api_access_key = customer_service_api_key
    if brave_api_key:
        settings.brave_api_key = brave_api_key
    return settings


def create_agent(profile: str = "均衡", settings: Settings | None = None) -> DeepSearchAgent:
    """按页面选择的研究强度创建单次 Agent，不修改持久化设置。"""

    return DeepSearchAgent(apply_profile(settings or get_settings(), profile))


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
    default_mode = {"chat": "问答", "auto": "问答", "search": "搜索", "research": "研究"}.get(
        get_settings().default_work_mode,
        "问答",
    )
    st.session_state.setdefault("work_mode", default_mode)
    # 热更新时浏览器会话里可能还保留已经删除的“智能判断”标签；就地迁移
    # 避免 segmented_control 因默认值不在新选项中而报错。
    if st.session_state.work_mode not in {"问答", "搜索", "研究"}:
        st.session_state.work_mode = "问答"
        st.session_state.pop("composer-mode", None)
    st.session_state.setdefault("pending_question", "")
    st.session_state.setdefault("pending_run_id", "")
    st.session_state.setdefault("pending_resume", False)
    # 每个浏览器标签页拥有独立作用域；进程级提交闸门据此既能原子去重，
    # 又不会误拦截其他用户恰好提交的相同问题。
    st.session_state.setdefault("submission_scope_id", uuid4().hex)
    st.session_state.setdefault("current_conversation_id", "")
    # pending 由输入提交回调写入，页面开始执行时转为 running；停止按钮
    # 将其改为 stopped 并触发 rerun。状态只保存字符串，不把线程放进会话。
    st.session_state.setdefault("agent_run_state", "idle")
    # Future 与 tracker 留在进程级短期注册表；会话只保存稳定 ID。这样页面
    # 切换不会取消长任务，也不会把不可序列化线程对象写入 Session State。
    st.session_state.setdefault("active_run_id", "")
    # 显式停止后保留最小重启信息；不保存 Future 或隐性模型状态，因为
    # 已中断的 HTTP 生成无法从某个 token 位置可靠断点续传。
    st.session_state.setdefault("resumable_run", {})


def reset_research() -> None:
    """开始新对话，保留全局配置和用户选择的工作模式。"""

    cancel_background_run(str(st.session_state.get("active_run_id", "")))
    st.session_state.messages = []
    st.session_state.last_result = None
    st.session_state.last_run = None
    st.session_state.progress_events = []
    st.session_state.current_conversation_id = ""
    st.session_state.agent_run_state = "idle"
    st.session_state.active_run_id = ""
    st.session_state.resumable_run = {}
    st.session_state.pending_run_id = ""
    st.session_state.pending_resume = False


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
    # 历史智能判断（旧 mode=auto）和直接回答都恢复为显式问答；搜索与研究
    # 仍按结果类型恢复，避免旧会话出现已经删除的模式标签。
    st.session_state.work_mode = {"chat": "问答", "auto": "问答", "search": "搜索", "research": "研究"}.get(
        str(last_mode),
        "问答",
    )
    st.session_state.pop("composer-mode", None)
    latest_kind = next(
        (
            message.get("kind")
            for message in reversed(st.session_state.messages)
            if message.get("role") == "assistant"
        ),
        "",
    )
    # 只有最近结果本身是研究时才恢复追问上下文；避免从一段已结束的
    # 旧研究跨过直接回答/搜索，避免误把新的研究请求当成旧报告追问。
    st.session_state.last_result = (
        research_context_from_messages(st.session_state.messages)
        if latest_kind == "research"
        else None
    )
    # 搜索结果不需要恢复网页正文；保存的轻量来源元数据足以重新生成
    # 与首次运行一致的结果卡片、风险标签和链接按钮。
    st.session_state.last_run = run_from_messages(st.session_state.messages)
    return True


def chat_history_from_messages(messages: list[dict], limit: int = 6) -> tuple[ChatTurn, ...]:
    """提取最近少量纯文本，排除来源元数据、密钥和完整网页正文。"""

    turns = []
    for message in messages:
        role = str(message.get("role", ""))
        if role not in {"user", "assistant"}:
            continue
        if role == "assistant" and message.get("kind") not in {None, "", "chat"}:
            # 搜索快照和研究报告可能很长，也不属于快速回答上下文。
            continue
        content = " ".join(str(message.get("content", "")).split())[:600]
        if content:
            turns.append(ChatTurn(role=role, content=content))
    return tuple(turns[-max(0, limit):])


def run_from_messages(messages: list[dict]) -> AgentRunResult | None:
    """恢复最后一条结构化助手回答，适用于搜索、研究和直接回答。"""

    for index in range(len(messages) - 1, -1, -1):
        if messages[index].get("role") == "assistant":
            return run_from_message(messages, index)
    return None


def run_from_message(messages: list[dict], index: int) -> AgentRunResult | None:
    """按消息自身元数据恢复结果，确保旧回答的来源链接不会随追问消失。"""

    if index < 0 or index >= len(messages):
        return None
    message = messages[index]
    if message.get("role") != "assistant":
        return None
    history = messages[: index + 1]
    kind = str(message.get("kind", ""))
    if kind == "chat":
        run = chat_run_from_messages(history)
    elif kind == "search":
        run = search_run_from_messages(history)
    elif kind == "research":
        result = research_context_from_messages(history)
        run = (
            AgentRunResult(WorkMode.RESEARCH, WorkMode.RESEARCH, research=result)
            if result is not None
            else None
        )
    else:
        return None
    if run is not None:
        requested = str(message.get("requested_mode", "auto"))
        try:
            run.requested_mode = WorkMode(requested)
        except ValueError:
            run.requested_mode = WorkMode.AUTO
    return run


def stable_message_id(message: dict, index: int) -> str:
    """为新旧消息提供稳定组件键；旧记录以位置作为只读兼容标识。"""

    stored = str(message.get("message_id", ""))
    return stored if re.fullmatch(r"[0-9a-f]{32}", stored) else f"legacy-{index}"


def chat_run_from_messages(messages: list[dict]) -> AgentRunResult | None:
    """从最后一条直接回复恢复轻量结果，不创建或查找任何资产文件。"""

    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.get("role") != "assistant":
            continue
        if message.get("kind") != "chat":
            return None
        question = str(message.get("question", "")).strip() or next(
            (
                str(item.get("content", "")).strip()
                for item in reversed(messages[:index])
                if item.get("role") == "user"
            ),
            "直接回答",
        )
        result = ChatResult(
            question=question,
            answer=str(message.get("content", "")),
            used_fallback=bool(message.get("used_fallback", False)),
        )
        return AgentRunResult(WorkMode.AUTO, WorkMode.CHAT, chat=result)
    return None


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


def persist_user_message(content: str, mode: WorkMode, message_id: str = "") -> dict:
    """确保会话存在后保存用户消息和请求模式。"""

    store = conversation_store()
    conversation_id = st.session_state.get("current_conversation_id", "")
    conversation = store.load(conversation_id) if conversation_id else None
    if conversation is None:
        conversation = store.create()
        st.session_state.current_conversation_id = conversation["id"]
    message = {
        "message_id": message_id if re.fullmatch(r"[0-9a-f]{32}", message_id) else uuid4().hex,
        "role": "user",
        "content": content,
        "mode": mode.value,
    }
    store.append(conversation, message)
    return conversation


def _message_from_run(run: AgentRunResult, settings: Settings, message_id: str) -> dict:
    """构造可恢复的安全消息；不读取 Streamlit 上下文。"""

    if run.chat is not None:
        # 直接回答只保存普通会话消息，不生成搜索快照、报告或资料库资产。
        artifact = ""
        content = run.chat.answer
        sources = []
        kind = "chat"
        question = run.chat.question
        summary = run.chat.answer
        rounds = 0
        stop_reason = ""
        review_summary = []
    elif run.search is not None:
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
    return {
        "message_id": message_id if re.fullmatch(r"[0-9a-f]{32}", message_id) else uuid4().hex,
        "role": "assistant",
        "content": content,
        "mode": run.resolved_mode.value,
        "requested_mode": run.requested_mode.value,
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
        "used_fallback": bool(run.chat.used_fallback) if run.chat is not None else False,
    }


def persist_run_result_for_conversation(
    run: AgentRunResult,
    settings: Settings,
    conversation_id: str,
    message_id: str = "",
) -> dict:
    """把后台结果写回原会话，不受用户当前所在页面或会话影响。"""

    store = conversation_store(settings)
    conversation = store.load(conversation_id)
    if conversation is None:
        raise ValueError("原对话已不存在，无法保存后台结果")
    message = _message_from_run(run, settings, message_id)
    store.append(conversation, message)
    return message


def persist_run_result(run: AgentRunResult, message_id: str = "") -> dict:
    """保存当前页面运行结果；兼容非后台调用和既有测试。"""

    settings = get_settings()
    store = conversation_store(settings)
    conversation_id = st.session_state.get("current_conversation_id", "")
    conversation = store.load(conversation_id)
    if conversation is None:
        conversation = store.create()
        conversation_id = conversation["id"]
        st.session_state.current_conversation_id = conversation_id
    return persist_run_result_for_conversation(run, settings, conversation_id, message_id)


def persist_run_error_for_conversation(
    error: Exception,
    snapshot: RunProgressSnapshot,
    settings: Settings,
    conversation_id: str,
    question: str,
    requested_mode: WorkMode,
    message_id: str = "",
) -> dict:
    """保存可恢复的失败卡片，让切换页面不会抹掉诊断与重试入口。"""

    store = conversation_store(settings)
    conversation = store.load(conversation_id)
    if conversation is None:
        raise ValueError("原对话已不存在，无法保存失败信息")
    message = {
        "message_id": message_id if re.fullmatch(r"[0-9a-f]{32}", message_id) else uuid4().hex,
        "role": "assistant",
        "content": str(error),
        "mode": requested_mode.value,
        "requested_mode": requested_mode.value,
        "kind": "error",
        "question": question,
        "stop_reason": snapshot.current_stage,
        "summary": format_elapsed(snapshot.total_seconds),
        "progress_steps": [
            {
                "stage": step.stage,
                "detail": step.detail,
                "duration_seconds": round(step.duration_seconds, 3),
            }
            for step in snapshot.steps
        ],
    }
    store.append(conversation, message)
    return message


def mark_error_retried(message_id: str) -> None:
    """持久标记已触发重试的错误卡片，避免旧按钮重复提交同一任务。"""

    if not re.fullmatch(r"[0-9a-f]{32}", message_id):
        return
    store = conversation_store()
    conversation = store.load(st.session_state.get("current_conversation_id", ""))
    if conversation is None:
        return
    for message in conversation.get("messages", []):
        if message.get("message_id") == message_id and message.get("kind") == "error":
            message["retried"] = True
            store.save(conversation)
            break
    for message in st.session_state.get("messages", []):
        if message.get("message_id") == message_id and message.get("kind") == "error":
            message["retried"] = True
            break


def record_delivery(outcome_status: str) -> None:
    """把手动投递状态写回当前会话的最后一条助手消息。"""

    store = conversation_store()
    conversation = store.load(st.session_state.get("current_conversation_id", ""))
    if not conversation or not conversation.get("messages"):
        return
    conversation["messages"][-1]["delivery"] = outcome_status
    store.save(conversation)
