"""快速回答用例：本地工具、短回复、有限上下文和安全降级。"""

from __future__ import annotations

import ast
import logging
import math
import re
import time
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Callable

from .intent import IntentClassifier
from .ports import ChatModel, Progress
from .prompts import CHAT_SYSTEM
from ..domain.models import ChatResult, ChatTurn

logger = logging.getLogger(__name__)

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)((?:DEEPSEARCH_[A-Z0-9_]*(?:KEY|TOKEN|SECRET)|API[ _-]?KEY|TOKEN|SECRET|PASSWORD)"
    r"\s*[:=]\s*)[\"']?[^\s,;\"']+"
)
_TOKEN_LIKE_VALUE = re.compile(r"\b(?:sk|key|token)-[A-Za-z0-9_-]{8,}\b", re.IGNORECASE)
_LOW_VALUE_HISTORY_ANSWERS = {
    "您好！请问有什么可以帮助您的吗？",
    "您好！请问有什么可以帮助您的吗",
    "你好！你可以直接告诉我想了解什么。",
    "你好！你可以直接告诉我想了解什么",
}
_LOW_VALUE_HISTORY_PREFIXES = (
    "当前未配置问答模型",
    "问答模型已经配置，但本次连接或生成失败",
    "这个问题需要检索当前资料",
)


class ChatService:
    """提供直接回答；本地工具优先，模型失败时也不会转用研究模型。"""

    def __init__(
        self,
        model: ChatModel | None = None,
        maximum_chars: int = 400,
        now_provider: Callable[[tzinfo], datetime] | None = None,
    ) -> None:
        self.model = model
        self.maximum_chars = max(120, maximum_chars)
        self._now_provider = now_provider or datetime.now

    def reply(
        self,
        question: str,
        history: tuple[ChatTurn, ...] = (),
        progress: Progress | None = None,
        *,
        locale: str = "zh-CN",
        region: str = "CN",
    ) -> ChatResult:
        """按用户选择的问答模式生成回复，不在内部改走其他工作模式。"""

        started = time.perf_counter()
        notify = progress or (lambda phase, message: None)
        notify("chat", "正在生成直接回复…")

        local_answer = self._local_tool_reply(question, locale, region)
        if local_answer is not None:
            return ChatResult(
                question=question,
                answer=local_answer,
                elapsed_seconds=time.perf_counter() - started,
            )

        model_error: Exception | None = None
        if self.model is not None:
            try:
                usable_history = self._usable_history(question, history)
                generate_chat = getattr(self.model, "generate_chat", None)
                if callable(generate_chat):
                    answer = generate_chat(
                        CHAT_SYSTEM,
                        usable_history,
                        self._redact(question)[:1600],
                    )
                else:
                    # 兼容只实现最小 ChatModel 协议的测试替身和第三方适配器。
                    answer = self.model.generate(
                        CHAT_SYSTEM,
                        self._user_prompt(question, usable_history),
                    )
                return ChatResult(
                    question=question,
                    answer=self._trim(answer),
                    used_model=True,
                    elapsed_seconds=time.perf_counter() - started,
                )
            except Exception as exc:  # 模型异常必须隔离，且绝不回退到研究模型。
                model_error = exc
                logger.warning("快速回答模型不可用，改用本地回复 error=%s", type(exc).__name__)

        return ChatResult(
            question=question,
            answer=self._local_reply(question, model_error=model_error),
            used_fallback=True,
            elapsed_seconds=time.perf_counter() - started,
        )

    def _user_prompt(self, question: str, history: tuple[ChatTurn, ...]) -> str:
        """为旧模型适配器构造有限上下文，禁止整份历史进入模型。"""

        safe_history = []
        for turn in history[-6:]:
            if turn.role not in {"user", "assistant"}:
                continue
            content = self._redact(" ".join(turn.content.split()))[:600]
            if content:
                safe_history.append(f"{turn.role}: {content}")
        context = "\n".join(safe_history)
        prefix = f"最近对话：\n{context}\n\n" if context else ""
        return f"{prefix}用户当前消息：{self._redact(question)[:1600]}"

    def _usable_history(
        self,
        question: str,
        history: tuple[ChatTurn, ...],
    ) -> tuple[ChatTurn, ...]:
        """仅保留完整且有信息量的对话对，避免失败回复污染后续生成。"""

        exchanges: list[tuple[ChatTurn, ChatTurn]] = []
        pending_user: ChatTurn | None = None
        current_question = self._normalized(question)
        for turn in history[-12:]:
            content = self._redact(" ".join(turn.content.split()))[:600]
            if not content:
                continue
            if turn.role == "user":
                pending_user = ChatTurn("user", content)
                continue
            if turn.role != "assistant" or pending_user is None:
                continue
            if (
                self._normalized(pending_user.content) != current_question
                and not self._is_low_value_history_answer(content)
            ):
                exchanges.append((pending_user, ChatTurn("assistant", content)))
            pending_user = None

        flattened = tuple(turn for pair in exchanges[-3:] for turn in pair)
        return flattened

    @staticmethod
    def _normalized(value: str) -> str:
        return re.sub(r"[\s，。！？、,.!?]+", "", value).lower()

    @staticmethod
    def _is_low_value_history_answer(value: str) -> bool:
        text = value.strip()
        return text in _LOW_VALUE_HISTORY_ANSWERS or text.startswith(_LOW_VALUE_HISTORY_PREFIXES)

    def _local_reply(self, question: str, *, model_error: Exception | None = None) -> str:
        lowered = question.strip().lower()
        greeting = any(marker in lowered for marker in ("你好", "您好", "嗨", "哈喽"))
        greeting = greeting or re.search(r"(?<!\w)(?:hello|hi)(?!\w)", lowered) is not None
        if greeting:
            return "你好！你可以直接告诉我想了解什么。"
        if any(marker in lowered for marker in ("谢谢", "感谢", "辛苦了")):
            return "不客气，很高兴能帮到你。"
        if any(marker in lowered for marker in ("你能做什么", "有什么功能", "怎么用", "你是谁", "介绍一下")):
            return (
                "我是 DeepSearch 助手：可以用搜索快速找官网、链接和当前信息，用研究完成多来源比较、"
                "论证与报告，也能管理会话、资料库和自动研究任务。你也可以直接告诉我想查什么。"
            )
        if any(marker in lowered for marker in ("再见", "拜拜")):
            return "再见！有问题随时来找我。"
        if "晚安" in lowered:
            return "晚安，祝你休息好。"
        if "讲个笑话" in lowered:
            return "当然：程序员最怕哪种虫子？不是蚊子，是 bug。"
        if model_error is not None:
            return (
                "问答模型已经配置，但本次连接或生成失败。若使用本地 Ollama，请确认 Ollama 正在运行、"
                "配置的模型已经下载且名称完全一致，然后直接重试。"
            )
        if any(marker in lowered for marker in ("翻译", "译成", "translate")):
            return "当前未配置问答模型，因此暂时无法可靠完成翻译；配置后可直接重试。"
        return "当前未配置问答模型，因此暂时无法可靠生成答案；请配置后直接重试。"

    def _local_tool_reply(self, question: str, locale: str, region: str) -> str | None:
        """优先执行无需网络的确定性工具，减少不必要的模型调用。"""

        if IntentClassifier.is_local_time_request(question):
            return self._time_reply(question, locale, region)
        calculation = self._calculator_reply(question)
        if calculation is not None:
            return calculation
        if IntentClassifier.is_realtime_fact_request(question):
            return (
                "我当前没有接入实时数据，无法可靠确认这个问题的最新情况。"
                "如果你提供已有数据或具体内容，我可以直接帮你解释和分析。"
            )
        return None

    def _time_reply(self, question: str, locale: str, region: str) -> str:
        lowered = question.strip().lower()
        if "utc" in lowered or "协调世界时" in lowered:
            zone = timezone.utc
            label = "协调世界时"
        elif any(marker in lowered for marker in ("北京时间", "中国时间", "上海时间")):
            zone = timezone(timedelta(hours=8))
            label = "北京时间"
        elif region.upper() == "CN" or locale.lower().startswith("zh"):
            zone = timezone(timedelta(hours=8))
            label = "北京时间"
        else:
            zone = datetime.now().astimezone().tzinfo or timezone.utc
            label = "本地时间"
        current = self._now_provider(zone).astimezone(zone)
        weekdays = "一二三四五六日"
        offset = current.strftime("%z")
        readable_offset = f"{offset[:3]}:{offset[3:]}" if len(offset) == 5 else offset
        return (
            f"现在是{label} {current:%Y年%m月%d日 %H:%M:%S}，"
            f"星期{weekdays[current.weekday()]}（UTC{readable_offset}）。"
        )

    @classmethod
    def _calculator_reply(cls, question: str) -> str | None:
        normalized = (
            question.strip().lower()
            .replace("×", "*")
            .replace("÷", "/")
            .replace("^", "**")
            .replace("（", "(")
            .replace("）", ")")
        )
        expression = re.sub(
            r"(?:请|帮我|计算一下|计算|算一下|算算|等于多少|是多少|结果|等于|多少|[？?=])",
            "",
            normalized,
        ).strip()
        if not expression or not re.fullmatch(r"[\d\s.+\-*/%()]+", expression):
            return None
        if not any(operator in expression for operator in ("+", "-", "*", "/", "%")):
            return None
        try:
            value = cls._evaluate_expression(ast.parse(expression, mode="eval").body)
        except (ArithmeticError, SyntaxError, TypeError, ValueError):
            return None
        if not math.isfinite(float(value)):
            return None
        rendered = str(int(value)) if float(value).is_integer() else f"{float(value):.10g}"
        return f"{expression} = {rendered}"

    @classmethod
    def _evaluate_expression(cls, node: ast.AST) -> float:
        """仅解释数值 AST 白名单，避免把用户算式交给 ``eval`` 执行。"""

        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            value = float(node.value)
            if abs(value) > 1e100:
                raise ValueError("number is too large")
            return value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = cls._evaluate_expression(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and isinstance(
            node.op,
            (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow),
        ):
            left = cls._evaluate_expression(node.left)
            right = cls._evaluate_expression(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.FloorDiv):
                return left // right
            if isinstance(node.op, ast.Mod):
                return left % right
            if abs(right) > 12 or abs(left) > 1e12:
                raise ValueError("exponent is too large")
            return left**right
        raise TypeError("unsupported expression")

    def _trim(self, value: str) -> str:
        text = value.strip()
        if len(text) <= self.maximum_chars:
            return text
        return text[: self.maximum_chars - 1].rstrip() + "…"

    @staticmethod
    def _redact(value: str) -> str:
        """在发送前移除常见密钥赋值和令牌形态，避免模型收到凭据。"""

        text = _SECRET_ASSIGNMENT.sub(r"\1[REDACTED]", value)
        return _TOKEN_LIKE_VALUE.sub("[REDACTED]", text)
