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
from ..domain.models import ChatResult, ChatTurn, WorkMode

logger = logging.getLogger(__name__)

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)((?:DEEPSEARCH_[A-Z0-9_]*(?:KEY|TOKEN|SECRET)|API[ _-]?KEY|TOKEN|SECRET|PASSWORD)"
    r"\s*[:=]\s*)[\"']?[^\s,;\"']+"
)
_TOKEN_LIKE_VALUE = re.compile(r"\b(?:sk|key|token)-[A-Za-z0-9_-]{8,}\b", re.IGNORECASE)


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
        self._classifier = IntentClassifier()

    def reply(
        self,
        question: str,
        history: tuple[ChatTurn, ...] = (),
        progress: Progress | None = None,
        *,
        locale: str = "zh-CN",
        region: str = "CN",
    ) -> ChatResult:
        """生成直接回复；实时网页事实和研究请求仍会被安全拦截。"""

        started = time.perf_counter()
        notify = progress or (lambda phase, message: None)
        notify("chat", "正在生成直接回复…")

        inferred = self._classifier.resolve(question, WorkMode.AUTO)
        if inferred != WorkMode.CHAT:
            answer = self._mode_guidance(inferred)
            return ChatResult(
                question=question,
                answer=answer,
                used_fallback=True,
                elapsed_seconds=time.perf_counter() - started,
            )

        local_answer = self._local_tool_reply(question, locale, region)
        if local_answer is not None:
            return ChatResult(
                question=question,
                answer=local_answer,
                elapsed_seconds=time.perf_counter() - started,
            )

        if self.model is not None:
            try:
                answer = self.model.generate(CHAT_SYSTEM, self._user_prompt(question, history))
                return ChatResult(
                    question=question,
                    answer=self._trim(answer),
                    used_model=True,
                    elapsed_seconds=time.perf_counter() - started,
                )
            except Exception as exc:  # 模型异常必须隔离，且绝不回退到研究模型。
                logger.warning("快速回答模型不可用，改用本地回复 error=%s", type(exc).__name__)

        return ChatResult(
            question=question,
            answer=self._local_reply(question),
            used_fallback=True,
            elapsed_seconds=time.perf_counter() - started,
        )

    def _user_prompt(self, question: str, history: tuple[ChatTurn, ...]) -> str:
        """只携带最近六条短消息，禁止整份历史或网页正文进入模型。"""

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

    def _local_reply(self, question: str) -> str:
        lowered = question.strip().lower()
        if any(marker in lowered for marker in ("你好", "您好", "嗨", "哈喽", "hello", "hi")):
            return "你好！你可以直接提问；需要当前资料时选择搜索，需要多来源分析时选择研究。"
        if any(marker in lowered for marker in ("谢谢", "感谢", "辛苦了")):
            return "不客气！需要查找资料时可以使用搜索，要做比较、调研或报告时可以使用研究。"
        if any(marker in lowered for marker in ("你能做什么", "有什么功能", "怎么用", "你是谁", "介绍一下")):
            return (
                "我是 DeepSearch 助手：可以用搜索快速找官网、链接和当前信息，用研究完成多来源比较、"
                "论证与报告，也能管理会话、资料库和自动研究任务。你也可以直接告诉我想查什么。"
            )
        if any(marker in lowered for marker in ("再见", "拜拜")):
            return "再见！下次想查资料或深入研究某个问题时，随时来找我。"
        if "晚安" in lowered:
            return "晚安，祝你休息好。下次想聊天、查资料或做深入研究时再见！"
        if "讲个笑话" in lowered:
            return "当然：程序员最怕哪种虫子？不是蚊子，是 bug。还想聊点别的，或者查一份资料吗？"
        if any(marker in lowered for marker in ("翻译", "译成", "translate")):
            return "这个请求不需要搜索。当前未配置快速回答模型，因此暂时无法可靠完成翻译；配置后可直接重试。"
        return "这个问题不需要搜索。当前未配置快速回答模型，因此暂时无法可靠生成答案；请配置后直接重试。"

    def _local_tool_reply(self, question: str, locale: str, region: str) -> str | None:
        """优先执行无需网络的确定性工具，避免简单请求被升级为搜索。"""

        if self._classifier.is_local_time_request(question):
            return self._time_reply(question, locale, region)
        return self._calculator_reply(question)

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

    @staticmethod
    def _mode_guidance(mode: WorkMode) -> str:
        if mode == WorkMode.RESEARCH:
            return "这个问题需要多来源研究。请明确选择“研究”后再运行，以生成可核查的结构化报告。"
        return "这个问题需要检索当前资料。请明确选择“搜索”后再运行，以获取可访问来源。"

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
