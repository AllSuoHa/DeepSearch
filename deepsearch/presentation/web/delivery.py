"""Web 页面手动投递所需的稳定资产身份。

首页和资料库都可以推送同一个落盘资产。这里集中维护 external ID 规则，
避免两个入口为同一份报告生成不同身份，导致 CustomerService 重复建文档。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path

from ...infrastructure.customer_service import DeliveryArtifact
from ...infrastructure.storage import ConversationStore


def conversation_delivery_external_id(conversation_id: str, kind: str, question: str) -> str:
    """返回与首页历史规则兼容的会话资产 ID。"""

    identity = hashlib.sha256(
        f"{conversation_id}|{kind}|{question}".encode("utf-8")
    ).hexdigest()[:20]
    return f"conversation-{identity}"


def build_library_delivery_artifact(
    report: Mapping[str, object],
    conversation_dir: Path,
    data_classification: str,
) -> DeliveryArtifact:
    """把资料库行转换为可幂等投递的统一资产。

    优先查找引用该文件的最近会话，以便复用首页已经采用的 external ID；
    手工放入资料库、没有会话来源的文件则使用稳定的绝对路径摘要。
    """

    content_path = Path(str(report["path"])).resolve()
    fallback_kind = "search" if report.get("kind") == "搜索快照" else "research"
    title = str(report.get("title") or content_path.stem).strip() or content_path.stem
    kind = fallback_kind
    question = title
    external_id = ""
    resolved_mode = fallback_kind

    store = ConversationStore(conversation_dir)
    for summary in store.list(limit=None):
        conversation_id = str(summary.get("id", ""))
        conversation = store.load(conversation_id)
        if not conversation:
            continue
        for message in reversed(conversation.get("messages", [])):
            referenced_path = str(message.get("artifact_path", "")).strip()
            if not referenced_path or Path(referenced_path).resolve() != content_path:
                continue
            kind = str(message.get("kind") or fallback_kind)
            question = str(message.get("question") or title)
            resolved_mode = str(message.get("mode") or kind)
            external_id = conversation_delivery_external_id(conversation_id, kind, question)
            break
        if external_id:
            break

    if not external_id:
        identity = hashlib.sha256(
            f"{kind}|{content_path}".encode("utf-8")
        ).hexdigest()[:20]
        external_id = f"library-{identity}"

    classification = (
        data_classification
        if data_classification in {"public", "internal", "confidential"}
        else "internal"
    )
    return DeliveryArtifact(
        external_id=external_id,
        title=question,
        content_path=content_path,
        question=question,
        kind=kind,
        data_classification=classification,
        metadata={
            "resolved_mode": resolved_mode,
            "source": "library",
            "format": str(report.get("format") or content_path.suffix.lstrip(".")),
        },
    )
