"""把研究报告可靠、幂等地投递到 CustomerService 知识库。"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen

from .config import CustomerServiceSettings

logger = logging.getLogger(__name__)

Transport = Callable[[Request, float], tuple[int, bytes]]


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    """一次投递的最终状态；只有远端入库完成后才返回 ``indexed``。"""

    status: str
    external_id: str
    content_hash: str
    job_id: str | None = None
    document_id: str | None = None
    idempotent_replay: bool = False
    error: str = ""


@dataclass(frozen=True, slots=True)
class DeliveryArtifact:
    """可由搜索、研究或定时任务投递的统一本地资产。"""

    external_id: str
    title: str
    content_path: Path
    question: str = ""
    kind: str = "research"
    data_classification: str = "internal"
    metadata: dict[str, Any] = field(default_factory=dict)


class DeliveryError(RuntimeError):
    """投递边界错误，并携带是否适合自动重试的判定。"""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class CustomerServicePublisher:
    """适配 CustomerService v2 异步入库；失败只进入本地 outbox。"""

    def __init__(
        self,
        settings: CustomerServiceSettings,
        *,
        transport: Transport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self._transport = transport or _urlopen_transport
        self._sleep = sleeper
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic

    def publish(self, task: Any, result: Any) -> DeliveryOutcome:
        """兼容定时任务接口，并转入统一资产投递。"""

        return self.publish_artifact(self._artifact_from_task(task, result))

    def _artifact_from_task(self, task: Any, result: Any) -> DeliveryArtifact:
        """把旧定时任务对象收敛为与 Web 入口相同的投递资产。"""

        scorecard = getattr(result, "scorecard", None)
        metadata = {
            "task_name": str(task.name).strip(),
            "profile": str(getattr(task, "profile", "")),
            "rounds": int(getattr(result, "rounds", 0) or 0),
            "stop_reason": str(getattr(result, "stop_reason", "")),
        }
        if scorecard is not None:
            metadata["quality_score"] = getattr(scorecard, "overall", None)
            metadata["quality_grade"] = getattr(scorecard, "grade", None)
        return DeliveryArtifact(
            external_id=_external_id(str(task.name).strip()),
            title=_title(str(task.name).strip(), str(getattr(result, "question", task.question))),
            content_path=Path(result.report_path),
            question=str(getattr(result, "question", task.question)),
            kind="scheduled_research",
            data_classification=str(getattr(task, "data_classification", "internal")),
            metadata=metadata,
        )

    def _build_record(self, task: Any, result: Any) -> dict[str, Any]:
        """兼容 CustomerService 的跨项目契约脚本；新业务入口使用 DeliveryArtifact。"""

        return self._build_artifact_record(self._artifact_from_task(task, result))

    def publish_artifact(self, artifact: DeliveryArtifact) -> DeliveryOutcome:
        """投递任意已落盘结果；失败进入不含正文的安全 outbox。"""

        if not self.settings.enabled:
            raise DeliveryError("CustomerService 联动未启用", retryable=False)
        record = self._build_artifact_record(artifact)

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

    def _build_artifact_record(self, artifact: DeliveryArtifact) -> dict[str, Any]:
        report_path = artifact.content_path.resolve()
        content = _read_report(report_path)
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        metadata = {
            "producer": "DeepSearch",
            "kind": artifact.kind,
            "question": artifact.question,
            **artifact.metadata,
        }
        return {
            "schema_version": 1,
            "external_id": artifact.external_id,
            "title": artifact.title[:255],
            "report_path": str(report_path),
            "content_hash": content_hash,
            "completed_at": self._clock().isoformat(timespec="seconds"),
            "metadata": {key: value for key, value in metadata.items() if value not in (None, "")},
            "data_classification": artifact.data_classification,
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
        # v2 的 POST 只表示“已受理”，不能把 HTTP 202 误报成已经建立索引。
        accepted = self._post_with_retry(payload)
        job_id = _required_text(accepted.get("job_id"), "CustomerService v2 响应缺少 job_id")
        completed = self._wait_until_indexed(record["external_id"], job_id)
        return DeliveryOutcome(
            status="indexed",
            external_id=record["external_id"],
            content_hash=record["content_hash"],
            job_id=job_id,
            document_id=_optional_text(completed.get("document_id")),
        )

    def _wait_until_indexed(self, external_id: str, job_id: str) -> dict[str, Any]:
        """轮询指定 v2 入库任务，避免把 queued/running 当作投递成功。"""

        deadline = self._monotonic() + self.settings.request_timeout
        while True:
            response = self._get_status_with_retry(external_id)
            versions = response.get("versions")
            if not isinstance(versions, list):
                raise DeliveryError("CustomerService v2 状态响应缺少 versions", retryable=False)
            matched = next(
                (item for item in versions if isinstance(item, dict) and item.get("job_id") == job_id),
                None,
            )
            if matched is None:
                raise DeliveryError("CustomerService 未返回刚提交的入库任务", retryable=True)

            status = str(matched.get("status", "")).strip().lower()
            if status == "succeeded":
                if not _optional_text(matched.get("document_id")):
                    raise DeliveryError("CustomerService 入库成功但未返回 document_id", retryable=True)
                return matched
            if status == "failed":
                raise DeliveryError("CustomerService 后台入库失败，请检查其入库任务日志", retryable=True)
            if status not in {"queued", "running"}:
                raise DeliveryError(f"CustomerService 返回未知入库状态：{status or '空'}", retryable=False)

            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise DeliveryError(
                    f"CustomerService 已接收报告，但 {self.settings.request_timeout:g} 秒内未完成入库",
                    retryable=True,
                )
            # 短间隔足以覆盖本地 worker，又避免对状态接口形成忙轮询。
            self._sleep(min(0.5, remaining))

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
        endpoint = urljoin(self.settings.base_url.rstrip("/") + "/", "api/v2/integrations/deepsearch/documents")
        headers = {**self._auth_headers(), "Content-Type": "application/json; charset=utf-8"}
        request = Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        status, decoded = self._send_json(request)
        if status != 202:
            raise DeliveryError(
                f"CustomerService v2 应返回 HTTP 202，实际为 {status}",
                retryable=status >= 500,
            )
        return decoded

    def _get_status_with_retry(self, external_id: str) -> dict[str, Any]:
        """读取 v2 版本状态；瞬时网络错误沿用发送阶段的退避策略。"""

        last_error: DeliveryError | None = None
        for attempt in range(self.settings.retry_attempts):
            try:
                return self._get_status_once(external_id)
            except DeliveryError as exc:
                last_error = exc
                if not exc.retryable or attempt + 1 >= self.settings.retry_attempts:
                    raise
                self._sleep(self.settings.retry_backoff_seconds * (2**attempt))
        raise last_error or DeliveryError("CustomerService 状态查询失败")

    def _get_status_once(self, external_id: str) -> dict[str, Any]:
        endpoint = urljoin(
            self.settings.base_url.rstrip("/") + "/",
            f"api/v2/integrations/deepsearch/documents/{quote(external_id, safe='')}",
        )
        request = Request(endpoint, headers=self._auth_headers(), method="GET")
        status, decoded = self._send_json(request)
        if status != 200:
            raise DeliveryError(
                f"CustomerService v2 状态接口应返回 HTTP 200，实际为 {status}",
                retryable=status >= 500,
            )
        return decoded

    def _auth_headers(self) -> dict[str, str]:
        """集中生成鉴权头，确保 POST 与状态查询使用同一套凭据。"""

        headers = {"Accept": "application/json", "X-Integration-Key": self.settings.integration_key}
        if self.settings.api_access_key:
            headers["Authorization"] = f"Bearer {self.settings.api_access_key}"
        return headers

    def _send_json(self, request: Request) -> tuple[int, dict[str, Any]]:
        """执行单次 JSON 请求，并统一处理脱敏、重试分类和协议错误。"""

        try:
            status, body = self._transport(request, self.settings.request_timeout)
        except HTTPError as exc:
            detail = self._redact(_safe_http_detail(exc.read()))
            raise self._http_error(exc.code, detail) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise DeliveryError(f"无法连接 CustomerService: {type(exc).__name__}") from exc
        if not 200 <= status < 300:
            raise self._http_error(status, self._redact(_safe_http_detail(body)))
        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DeliveryError("CustomerService 返回了无效 JSON") from exc
        if not isinstance(decoded, dict):
            raise DeliveryError("CustomerService 返回格式不是 JSON 对象")
        return status, decoded

    @staticmethod
    def _http_error(status: int, detail: str) -> DeliveryError:
        """把常见联动配置错误翻译成页面可直接执行的修复提示。"""

        if status == 401:
            return DeliveryError("CustomerService 拒绝了联动密钥，请确认两边密钥完全一致", retryable=False)
        if status == 404:
            return DeliveryError("CustomerService v2 联动接口不存在，请确认运行的是最新版 API", retryable=False)
        if status == 422:
            return DeliveryError(f"CustomerService 拒绝此文档：{detail}", retryable=False)
        if status == 503:
            return DeliveryError(f"CustomerService 尚未完成联动配置：{detail}", retryable=True)
        retryable = status in {408, 425, 429} or status >= 500
        return DeliveryError(f"CustomerService HTTP {status}: {detail}", retryable=retryable)

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


def _optional_text(value: Any) -> str | None:
    """CustomerService v2 的 job/document ID 是带前缀字符串，不再转整数。"""

    text = str(value).strip() if value is not None else ""
    return text or None


def _required_text(value: Any, message: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise DeliveryError(message, retryable=False)
    return text
