"""兼容 OpenAI Chat Completions 协议的最小同步模型客户端。"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Any

from .config import LLMSettings

logger = logging.getLogger(__name__)

_TRANSIENT_HTTP_STATUS = {408, 409, 429, 500, 502, 503, 504}


class LLMRequestError(RuntimeError):
    """模型接口调用失败；消息可安全展示给最终用户。"""


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

        endpoint = self.settings.base_url.rstrip("/") + "/chat/completions"
        payload = json.dumps({
            "model": self.settings.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.2,
        }, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            endpoint, data=payload, method="POST",
            headers={"Authorization": f"Bearer {self.settings.api_key}", "Content-Type": "application/json"},
        )
        last_error = "未知错误"
        # 确定性配置错误（例如 401/404）立即失败；瞬时网络、限流和
        # 服务端错误只重试一次，避免三阶段研究放大无效请求和等待时间。
        for attempt in range(2):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
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
                last_error = f"无法连接模型接口：{reason or '网络连接失败'}"
                logger.warning("LLM 连接失败 attempt=%d error=%s", attempt + 1, reason)
            except (json.JSONDecodeError, KeyError, IndexError, TypeError, UnicodeDecodeError, ValueError) as exc:
                last_error = "模型接口响应格式不兼容：未找到有效的 choices[0].message.content。"
                logger.warning("LLM 响应解析失败 error=%s", type(exc).__name__)
                raise LLMRequestError(last_error) from exc
            except Exception as exc:
                last_error = f"模型调用出现异常：{self._safe_text(exc) or type(exc).__name__}"
                logger.warning("LLM 调用失败 attempt=%d error=%s", attempt + 1, type(exc).__name__)
            if attempt == 0:
                time.sleep(0.3)
        raise LLMRequestError(f"{last_error} 已自动重试一次，仍未成功。")

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
