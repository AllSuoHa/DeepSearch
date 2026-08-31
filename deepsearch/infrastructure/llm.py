from __future__ import annotations

import json
import logging
import time
import urllib.request
from abc import ABC, abstractmethod

from .config import LLMSettings

logger = logging.getLogger(__name__)


class LanguageModel(ABC):
    @abstractmethod
    def generate(self, system: str, user: str) -> str:
        raise NotImplementedError


class OpenAICompatibleLLM(LanguageModel):
    """Small OpenAI-compatible chat client with one bounded retry."""

    def __init__(self, settings: LLMSettings, timeout: float = 30.0) -> None:
        self.settings = settings
        self.timeout = timeout

    def generate(self, system: str, user: str) -> str:
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
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = json.loads(response.read().decode("utf-8"))
                return body["choices"][0]["message"]["content"]
            except Exception as exc:
                last_error = exc
                logger.warning("LLM 调用失败 attempt=%d error=%s", attempt + 1, exc)
                if attempt == 0:
                    time.sleep(0.3)
        raise RuntimeError(f"LLM 调用失败，已重试一次: {last_error}")
