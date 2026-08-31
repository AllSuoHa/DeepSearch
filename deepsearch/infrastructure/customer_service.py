"""把研究报告可靠、幂等地投递到 CustomerService 知识库。"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from .config import CustomerServiceSettings

logger = logging.getLogger(__name__)

Transport = Callable[[Request, float], tuple[int, bytes]]


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    status: str
    external_id: str
    content_hash: str
    document_id: int | None = None
    idempotent_replay: bool = False
    error: str = ""


class DeliveryError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class CustomerServicePublisher:
    """同步 HTTP 发送器；失败只进入 outbox，不让研究任务重复执行。"""

    def __init__(
        self,
        settings: CustomerServiceSettings,
        *,
        transport: Transport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self._transport = transport or _urlopen_transport
        self._sleep = sleeper
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def publish(self, task: Any, result: Any) -> DeliveryOutcome:
        """投递新报告；关闭联动或网络失败时留下可恢复的路径型 outbox。"""

        record = self._build_record(task, result)
        if not self.settings.enabled:
            self._write_failure(record, DeliveryError("CustomerService 联动未启用"))
            return self._queued_outcome(record, "CustomerService 联动未启用")
        try:
            outcome = self._deliver(record)
        except DeliveryError as exc:
            self._write_failure(record, exc)
            logger.warning(
                "CustomerService 投递进入 outbox external_id=%s retryable=%s error=%s",
                record["external_id"],
                exc.retryable,
                exc,
            )
            return self._queued_outcome(record, str(exc))
        self._outbox_path(record).unlink(missing_ok=True)
        return outcome

    def flush_pending(self, *, force: bool = False) -> list[DeliveryOutcome]:
        """重试到期记录；outbox 只保存报告路径、哈希和非敏感元数据。"""

        if not self.settings.enabled or not self.settings.outbox_dir.exists():
            return []
        outcomes: list[DeliveryOutcome] = []
        for path in sorted(self.settings.outbox_dir.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if not force and not bool(record.get("retryable", True)):
                    continue
                next_retry_at = _parse_datetime(str(record.get("next_retry_at", "")))
                if not force and next_retry_at and next_retry_at > self._clock():
                    continue
                outcome = self._deliver(record)
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                logger.error("忽略损坏的 CustomerService outbox 记录 path=%s error=%s", path, exc)
                continue
            except DeliveryError as exc:
                self._write_failure(record, exc, path=path)
                outcomes.append(self._queued_outcome(record, str(exc)))
            else:
                path.unlink(missing_ok=True)
                outcomes.append(outcome)
        return outcomes

    def _build_record(self, task: Any, result: Any) -> dict[str, Any]:
        report_path = Path(result.report_path).resolve()
        content = _read_report(report_path)
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        task_name = str(task.name).strip()
        external_id = _external_id(task_name)
        scorecard = getattr(result, "scorecard", None)
        metadata = {
            "producer": "DeepSearch",
            "task_name": task_name,
            "question": str(getattr(result, "question", task.question)),
            "profile": str(getattr(task, "profile", "")),
            "rounds": int(getattr(result, "rounds", 0) or 0),
            "stop_reason": str(getattr(result, "stop_reason", "")),
        }
        if scorecard is not None:
            metadata["quality_score"] = getattr(scorecard, "overall", None)
            metadata["quality_grade"] = getattr(scorecard, "grade", None)
        return {
            "schema_version": 1,
            "external_id": external_id,
            "title": _title(task_name, str(getattr(result, "question", task.question))),
            "report_path": str(report_path),
            "content_hash": content_hash,
            "completed_at": self._clock().isoformat(timespec="seconds"),
            "metadata": {key: value for key, value in metadata.items() if value not in (None, "")},
            "data_classification": str(getattr(task, "data_classification", "internal")),
            "attempts": 0,
            "retryable": True,
            "last_error": "",
            "next_retry_at": "",
        }

    def _deliver(self, record: dict[str, Any]) -> DeliveryOutcome:
        self._validate_settings()
        content = _read_report(Path(record["report_path"]))
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if content_hash != record["content_hash"]:
            raise DeliveryError("报告文件在排队后发生变化，拒绝发送", retryable=False)
        if len(content) > 2_000_000:
            raise DeliveryError("报告超过 CustomerService 的 2,000,000 字符限制", retryable=False)
        payload = {
            "external_id": record["external_id"],
            "title": record["title"],
            "content_markdown": content,
            "completed_at": record["completed_at"],
            "metadata": record.get("metadata", {}),
            "chunk_strategy": "markdown",
            "data_classification": record.get("data_classification", "internal"),
        }
        response = self._post_with_retry(payload)
        return DeliveryOutcome(
            status="indexed",
            external_id=record["external_id"],
            content_hash=record["content_hash"],
            document_id=_optional_int(response.get("document_id")),
            idempotent_replay=bool(response.get("idempotent_replay", False)),
        )

    def _post_with_retry(self, payload: dict[str, Any]) -> dict[str, Any]:
        last_error: DeliveryError | None = None
        for attempt in range(self.settings.retry_attempts):
            try:
                return self._post_once(payload)
            except DeliveryError as exc:
                last_error = exc
                if not exc.retryable or attempt + 1 >= self.settings.retry_attempts:
                    raise
                self._sleep(self.settings.retry_backoff_seconds * (2**attempt))
        raise last_error or DeliveryError("CustomerService 投递失败")

    def _post_once(self, payload: dict[str, Any]) -> dict[str, Any]:
        endpoint = urljoin(self.settings.base_url.rstrip("/") + "/", "api/v1/integrations/deepsearch/documents")
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "X-Integration-Key": self.settings.integration_key,
        }
        if self.settings.api_access_key:
            headers["Authorization"] = f"Bearer {self.settings.api_access_key}"
        request = Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            status, body = self._transport(request, self.settings.request_timeout)
        except HTTPError as exc:
            detail = self._redact(_safe_http_detail(exc.read()))
            retryable = exc.code in {408, 425, 429} or exc.code >= 500
            raise DeliveryError(f"CustomerService HTTP {exc.code}: {detail}", retryable=retryable) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise DeliveryError(f"无法连接 CustomerService: {type(exc).__name__}") from exc
        if not 200 <= status < 300:
            retryable = status in {408, 425, 429} or status >= 500
            raise DeliveryError(
                f"CustomerService HTTP {status}: {self._redact(_safe_http_detail(body))}",
                retryable=retryable,
            )
        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DeliveryError("CustomerService 返回了无效 JSON") from exc
        if not isinstance(decoded, dict):
            raise DeliveryError("CustomerService 返回格式不是 JSON 对象")
        return decoded

    def _validate_settings(self) -> None:
        parsed = urlparse(self.settings.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise DeliveryError("CustomerService base_url 必须是有效的 HTTP(S) 地址", retryable=False)
        if not self.settings.integration_key:
            raise DeliveryError("缺少 DEEPSEARCH_CUSTOMER_SERVICE_INTEGRATION_KEY")

    def _redact(self, value: str) -> str:
        for secret in (self.settings.integration_key, self.settings.api_access_key):
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value

    def _write_failure(
        self,
        record: dict[str, Any],
        error: DeliveryError,
        *,
        path: Path | None = None,
    ) -> None:
        attempts = int(record.get("attempts", 0)) + 1
        delay = min(3600.0, self.settings.retry_backoff_seconds * (2 ** min(attempts, 10)))
        record.update(
            attempts=attempts,
            retryable=error.retryable,
            last_error=str(error)[:500],
            next_retry_at=(self._clock() + timedelta(seconds=delay)).isoformat(timespec="seconds"),
        )
        target = path or self._outbox_path(record)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)

    def _outbox_path(self, record: dict[str, Any]) -> Path:
        safe_id = re.sub(r"[^0-9A-Za-z._-]+", "-", str(record["external_id"])).strip("-._")
        return self.settings.outbox_dir / f"{safe_id}-{record['content_hash'][:16]}.json"

    @staticmethod
    def _queued_outcome(record: dict[str, Any], error: str) -> DeliveryOutcome:
        return DeliveryOutcome(
            status="queued",
            external_id=record["external_id"],
            content_hash=record["content_hash"],
            error=error,
        )


def _urlopen_transport(request: Request, timeout: float) -> tuple[int, bytes]:
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL 来自受控配置
        return int(response.status), response.read()


def _read_report(path: Path) -> str:
    if not path.is_file():
        raise DeliveryError(f"报告文件不存在：{path}", retryable=False)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DeliveryError(f"无法读取报告文件：{type(exc).__name__}") from exc
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise DeliveryError("JSON 报告格式无效", retryable=False) from exc
        text = str(data.get("content_markdown", "")) if isinstance(data, dict) else ""
    if not text.strip():
        raise DeliveryError("报告内容为空", retryable=False)
    return text


def _external_id(task_name: str) -> str:
    readable = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "-", task_name).strip("-._")[:72] or "task"
    digest = hashlib.sha256(task_name.strip().casefold().encode("utf-8")).hexdigest()[:16]
    return f"scheduled-{readable}-{digest}"


def _title(task_name: str, question: str) -> str:
    value = f"{task_name}：{question}" if question and question != task_name else task_name
    return value[:255]


def _safe_http_detail(body: bytes) -> str:
    if not body:
        return "无响应正文"
    try:
        value = json.loads(body.decode("utf-8", errors="replace"))
        if isinstance(value, dict):
            detail = value.get("detail", value.get("message", "请求失败"))
            return str(detail)[:300]
    except json.JSONDecodeError:
        pass
    return body.decode("utf-8", errors="replace")[:300]


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
