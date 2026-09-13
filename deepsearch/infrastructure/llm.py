"""兼容 OpenAI Chat Completions 协议的最小同步模型客户端。"""

from __future__ import annotations

import http.client
import json
import logging
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from .config import LLMSettings
from ..domain.models import ChatTurn

logger = logging.getLogger(__name__)

_TRANSIENT_HTTP_STATUS = {408, 409, 429, 500, 502, 503, 504}
_MAX_COMPLETION_TOKENS = 8192


class LLMRequestError(RuntimeError):
    """模型接口调用失败；消息可安全展示给最终用户。"""


def _open_url(
    request: urllib.request.Request,
    *,
    timeout: float,
    bypass_system_proxy: bool,
):
    """打开模型请求；国内百炼端点可绕过不稳定的 Windows 系统代理。"""

    if not bypass_system_proxy:
        return urllib.request.urlopen(request, timeout=timeout)
    # ProxyHandler({}) 只影响当前请求，不修改用户的系统代理或进程环境。
    # 不能通过设置全局 NO_PROXY 实现，否则会意外改变并发搜索/抓取的路由。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(request, timeout=timeout)


class LanguageModel(ABC):
    """报告生成器依赖的文本生成抽象。"""

    @abstractmethod
    def generate(self, system: str, user: str) -> str:
        """根据系统约束与阶段输入返回完整文本。"""

        raise NotImplementedError


class OpenAICompatibleLLM(LanguageModel):
    """OpenAI 兼容客户端；模型阶段编排仍由报告生成器负责。"""

    def __init__(self, settings: LLMSettings, timeout: float | None = None) -> None:
        self.settings = settings
        self.timeout = settings.request_timeout if timeout is None else timeout

    def generate(self, system: str, user: str) -> str:
        """调用模型并提取第一条助手消息，瞬时失败只重试一次。"""

        return self._generate_messages(
            [{"role": "system", "content": system}, {"role": "user", "content": user}]
        )

    def generate_chat(
        self,
        system: str,
        history: Sequence[ChatTurn],
        user: str,
    ) -> str:
        """按真实角色发送短对话，避免把历史误包装成当前用户消息。"""

        messages = [{"role": "system", "content": system}]
        messages.extend(
            {"role": turn.role, "content": turn.content}
            for turn in history
            if turn.role in {"user", "assistant"} and turn.content.strip()
        )
        messages.append({"role": "user", "content": user})
        return self._generate_messages(messages)

    def generate_json(self, system: str, user: str) -> str:
        """请求 JSON 对象；本机 Ollama 使用其兼容接口提供的 JSON 模式。"""

        response_format = {"type": "json_object"} if self._uses_local_endpoint() else None
        return self._generate_messages(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format=response_format,
        )

    def _generate_messages(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: dict[str, str] | None = None,
    ) -> str:
        """发送已经完成角色分离的 Chat Completions 消息。"""

        endpoint = self.settings.base_url.rstrip("/") + "/chat/completions"
        payload_data: dict[str, Any] = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": 0.2,
            # 内部使用 SSE 持续接收数据，避免长报告在服务端完成前没有任何
            # 网络活动而被网关误判为空闲连接。展示层仍只接收完整结果。
            "stream": True,
        }
        if response_format is not None:
            payload_data["response_format"] = response_format
        if self._uses_aliyun_deepseek_v4():
            # DeepSeek V4 在百炼中默认启用高强度思考，且思考 token 与正文
            # 共享很大的输出上限。DeepSearch 已经用三阶段编排完成证据整理、
            # 起草与审校，再嵌套一次模型深思会重复消耗并容易超过中间网关
            # 的连接时限，因此这里使用该模型官方支持的非思考模式。
            payload_data["enable_thinking"] = False
            # DeepSeek V4 的思考与正文默认共享极大的输出窗口；报告目标最多
            # 5000 字，8192 Token 足够交付，同时避免异常长生成占住连接。
            payload_data["max_completion_tokens"] = _MAX_COMPLETION_TOKENS
        payload = json.dumps(payload_data, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            endpoint, data=payload, method="POST",
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
        )
        last_error = "未知错误"
        # 确定性配置错误（例如 401/404）立即失败；瞬时网络、限流和
        # 服务端错误只重试一次，避免三阶段研究放大无效请求和等待时间。
        for attempt in range(2):
            try:
                with _open_url(
                    request,
                    timeout=self.timeout,
                    bypass_system_proxy=self._uses_aliyun_model_endpoint(),
                ) as response:
                    headers = getattr(response, "headers", None)
                    content_type = str(headers.get("Content-Type", "")) if headers else ""
                    if "text/event-stream" in content_type.lower():
                        content = self._read_stream(response)
                    else:
                        body = json.loads(response.read().decode("utf-8"))
                        content = body["choices"][0]["message"]["content"]
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("模型没有返回文本内容")
                return content
            except urllib.error.HTTPError as exc:
                last_error = self._http_error_message(exc)
                retryable = exc.code in _TRANSIENT_HTTP_STATUS
                logger.warning(
                    "LLM 调用失败 attempt=%d status=%d error=%s",
                    attempt + 1,
                    exc.code,
                    last_error,
                )
                if not retryable:
                    raise LLMRequestError(last_error) from exc
            except TimeoutError as exc:
                last_error = (
                    f"模型响应超过 {self.timeout:g} 秒。"
                    "思考模型处理长报告可能更慢，可在设置页提高“模型单次请求超时”。"
                )
                logger.warning("LLM 调用超时 attempt=%d timeout=%s", attempt + 1, self.timeout)
            except urllib.error.URLError as exc:
                reason = self._safe_text(exc.reason)
                if isinstance(exc.reason, ssl.SSLError):
                    last_error = self._ssl_error_message()
                else:
                    last_error = f"无法连接模型接口：{reason or '网络连接失败'}"
                logger.warning("LLM 连接失败 attempt=%d error=%s", attempt + 1, reason)
            except (ssl.SSLError, http.client.IncompleteRead, ConnectionResetError) as exc:
                # 部分 OpenAI 兼容网关会在长响应尚未读完时关闭 TLS 连接。
                # 这通常是瞬时传输故障而非提示词或报告内容错误，可安全重试。
                last_error = self._ssl_error_message()
                logger.warning("LLM TLS 连接中断 attempt=%d error=%s", attempt + 1, type(exc).__name__)
            except (json.JSONDecodeError, KeyError, IndexError, TypeError, UnicodeDecodeError, ValueError) as exc:
                last_error = "模型接口响应格式不兼容：未找到完整的助手文本。"
                logger.warning("LLM 响应解析失败 error=%s", type(exc).__name__)
                raise LLMRequestError(last_error) from exc
            except Exception as exc:
                last_error = f"模型调用出现异常：{self._safe_text(exc) or type(exc).__name__}"
                logger.warning("LLM 调用失败 attempt=%d error=%s", attempt + 1, type(exc).__name__)
            if attempt == 0:
                time.sleep(0.3)
        raise LLMRequestError(f"{last_error} 已自动重试一次，仍未成功。")

    @staticmethod
    def _read_stream(response: Any) -> str:
        """读取 Chat Completions SSE，并拒绝保存被中途截断的半份报告。"""

        fragments: list[str] = []
        completed = False
        while True:
            raw_line = response.readline()
            if not raw_line:
                break
            line = raw_line.decode("utf-8", errors="strict").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                completed = True
                break
            chunk = json.loads(data)
            if not isinstance(chunk, dict):
                continue
            choices = chunk.get("choices")
            # OpenAI 兼容服务可能在 SSE 末尾追加 usage/元数据事件；百炼的
            # 这类事件包含 ``choices: []``，它不是正文缺失，也不应终止流。
            if not isinstance(choices, list) or not choices:
                continue
            choice = choices[0]
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta", {})
            text = delta.get("content", "") if isinstance(delta, dict) else ""
            if isinstance(text, str) and text:
                fragments.append(text)
            if choice.get("finish_reason") is not None:
                completed = True
        if not completed:
            raise http.client.IncompleteRead(b"", None)
        return "".join(fragments)

    @staticmethod
    def _ssl_error_message() -> str:
        """把底层 EOF 转为可操作提示，不向普通用户暴露 OpenSSL 细节。"""

        return (
            "模型服务或中间网络在完整响应返回前关闭了加密连接。"
            "本地请求超时只决定最长等待时间，不能延长上游连接；请稍后重试，"
            "若反复出现请检查 VPN、代理、安全软件或模型服务状态。"
        )

    def _uses_aliyun_deepseek_v4(self) -> bool:
        """识别支持 ``enable_thinking`` 的百炼 DeepSeek V4 兼容端点。"""

        model = self.settings.model.strip().lower()
        return self._uses_aliyun_model_endpoint() and model.startswith("deepseek-v4")

    def _uses_aliyun_model_endpoint(self) -> bool:
        """识别可从中国大陆网络直连的阿里云百炼模型端点。"""

        host = (urllib.parse.urlsplit(self.settings.base_url).hostname or "").lower()
        return host.endswith(".maas.aliyuncs.com") or host in {
            "dashscope.aliyuncs.com",
            "dashscope-intl.aliyuncs.com",
            "dashscope-us.aliyuncs.com",
            "cn-hongkong.dashscope.aliyuncs.com",
        }

    def _uses_local_endpoint(self) -> bool:
        """识别本机 OpenAI 兼容服务，当前用于启用 Ollama JSON 模式。"""

        host = (urllib.parse.urlsplit(self.settings.base_url).hostname or "").lower()
        return host in {"localhost", "127.0.0.1", "::1"}

    def _http_error_message(self, exc: urllib.error.HTTPError) -> str:
        """提取兼容接口的 JSON 错误信息，避免把请求正文或密钥带到页面。"""

        provider_message = ""
        try:
            raw = exc.read(8192).decode("utf-8", errors="replace")
            parsed: Any = json.loads(raw)
            error = parsed.get("error", parsed) if isinstance(parsed, dict) else {}
            if isinstance(error, dict):
                provider_message = str(error.get("message") or error.get("detail") or "")
            elif isinstance(error, str):
                provider_message = error
        except (json.JSONDecodeError, OSError, UnicodeError):
            provider_message = ""

        fallback = str(exc.reason or "请求失败")
        detail = self._safe_text(provider_message or fallback)
        hints = {
            400: "请检查模型参数与请求格式。",
            401: "请检查 API Key 是否有效、是否属于当前业务空间。",
            403: "当前 Key 或业务空间没有调用该模型的权限。",
            404: "请检查 Base URL、业务空间地域和模型名称。",
            429: "调用频率或账户额度受限，请稍后重试并检查额度。",
        }
        hint = hints.get(exc.code, "服务暂时不可用，请稍后重试。" if exc.code >= 500 else "请检查模型服务配置。")
        return f"模型接口返回 HTTP {exc.code}（{detail}）。{hint}"

    def _safe_text(self, value: object) -> str:
        """限制外部错误文本长度，并移除可能被服务端回显的密钥。"""

        text = str(value).replace("\r", " ").replace("\n", " ").strip()
        if self.settings.api_key:
            text = text.replace(self.settings.api_key, "[REDACTED]")
        return text[:400]
