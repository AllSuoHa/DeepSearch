"""跨入口的敏感文本脱敏，供日志、会话和导出边界复用。"""

from __future__ import annotations

import re
from typing import Any


_SECRET_ASSIGNMENT = re.compile(
    r"(?i)((?:DEEPSEARCH_[A-Z0-9_]*(?:KEY|TOKEN|SECRET)|API[ _-]?KEY|"
    r"ACCESS[ _-]?TOKEN|TOKEN|SECRET|PASSWORD)\s*[:=]\s*)[\"']?[^\s,;\"']+"
)
_TOKEN_VALUE = re.compile(
    r"(?i)\b(?:sk|key|token|ghp|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{8,}\b"
)
_BEARER_VALUE = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+\-/=]{8,}")
_URL_SECRET = re.compile(
    r"(?i)([?&](?:api_?key|access_?token|token|key|secret|password)=)[^&#\s]+"
)


def redact_sensitive_text(value: str) -> str:
    """保留字段名和文本结构，只移除常见凭据值。"""

    text = _SECRET_ASSIGNMENT.sub(r"\1[REDACTED]", str(value))
    text = _TOKEN_VALUE.sub("[REDACTED]", text)
    text = _BEARER_VALUE.sub(r"\1[REDACTED]", text)
    return _URL_SECRET.sub(r"\1[REDACTED]", text)


def redact_sensitive_value(value: Any) -> Any:
    """递归清理即将持久化的 JSON 兼容结构。"""

    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, dict):
        return {key: redact_sensitive_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_sensitive_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive_value(item) for item in value)
    return value
