"""报告持久化端口及本地多格式文件实现。"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from ..application.search_service import search_response_markdown
from ..domain.models import SearchResponse


# 初次启动时提供少量实用快捷输入。它们会立即写入用户数据目录，之后与
# 用户新增的内容完全相同，可以自由修改或删除。
DEFAULT_PROMPT_SHORTCUTS = (
    ("影视平台", "查找我指定影视作品的正规播放平台、官方信息和可信社区结果"),
    ("官方网站", "查找我指定产品或服务的官方网站和官方文档入口"),
    ("今日要闻", "整理我关心领域今天的重要新闻，并优先给出原始来源链接"),
    ("学术文献", "查找我指定主题的论文、期刊和开放获取文献"),
)


class ReportStorage(ABC):
    """报告存储抽象；未来可替换为数据库、对象存储或知识库。"""

    @abstractmethod
    def save(self, question: str, report: str, output_format: str = "markdown") -> Path:
        """保存已通过质量门的报告并返回最终路径。"""

        raise NotImplementedError

    @abstractmethod
    def list(self, query: str = "") -> list[Path]:
        """按新到旧列出资产，可选按文件名或正文筛选。"""

        raise NotImplementedError


class FileReportStorage(ReportStorage):
    """保存经过质量门的报告，并按任务要求导出 Markdown、文本或 JSON。"""

    _extensions = {"markdown": ".md", "text": ".txt", "json": ".json"}

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def save(self, question: str, report: str, output_format: str = "markdown") -> Path:
        """按请求格式保存报告，同秒同名资产也不会被覆盖。"""

        self.directory.mkdir(parents=True, exist_ok=True)
        normalized_format = output_format.lower().strip()
        if normalized_format not in self._extensions:
            raise ValueError(f"不支持的报告格式：{output_format}；可选 markdown、text、json")
        stamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H%M%S")
        slug = _slugify(question)
        extension = self._extensions[normalized_format]
        path = self.directory / f"{stamp}_{slug}{extension}"
        # 同一秒内的同名问题通过递增后缀避免覆盖已有研究资产。
        suffix = 1
        while path.exists():
            path = self.directory / f"{stamp}_{slug}_{suffix}{extension}"
            suffix += 1
        path.write_text(self._serialize(question, report, normalized_format), encoding="utf-8")
        return path

    def list(self, query: str = "") -> list[Path]:
        """列出当前报告目录支持的 Markdown、Text 和 JSON 文件。"""

        if not self.directory.exists():
            return []
        lowered = query.lower()
        paths = sorted(
            (path for path in self.directory.iterdir() if path.suffix.lower() in self._extensions.values()),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        if not query:
            return paths
        return [path for path in paths if lowered in path.name.lower() or lowered in path.read_text(encoding="utf-8", errors="ignore").lower()]

    @staticmethod
    def _serialize(question: str, report: str, output_format: str) -> str:
        if output_format == "markdown":
            return report
        if output_format == "json":
            return json.dumps(
                {
                    "schema_version": "1.0",
                    "question": question,
                    "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "content_markdown": report,
                },
                ensure_ascii=False,
                indent=2,
            )
        # 文本导出保留标题和引用编号，同时移除 Markdown 展示符号。
        text = re.sub(r"^#{1,6}\s+", "", report, flags=re.MULTILINE)
        text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
        text = re.sub(r"_([^_]+)_", r"\1", text)
        text = re.sub(r"^>\s?", "", text, flags=re.MULTILINE)
        return text


class FileArtifactStorage:
    """保存搜索快照等非研究资产，供下载、历史和可靠投递复用。"""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def save_search(self, response: SearchResponse) -> Path:
        """保存可下载、可投递的搜索快照，并回填响应资产路径。"""

        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H%M%S")
        path = self.directory / f"{stamp}_{_slugify(response.query)}_搜索.md"
        suffix = 1
        while path.exists():
            path = self.directory / f"{stamp}_{_slugify(response.query)}_搜索_{suffix}.md"
            suffix += 1
        path.write_text(search_response_markdown(response), encoding="utf-8")
        response.artifact_path = path
        return path


class ConversationStore:
    """单机 JSON 会话历史；不保存密钥或抓取到的完整网页正文。"""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def create(self) -> dict:
        """创建带随机 ID 和本地时区时间戳的空会话。"""

        now = datetime.now().astimezone().isoformat(timespec="seconds")
        conversation = {
            "schema_version": 1,
            "id": uuid4().hex,
            "title": "新对话",
            "created_at": now,
            "updated_at": now,
            "messages": [],
        }
        self.save(conversation)
        return conversation

    def save(self, conversation: dict) -> Path:
        """使用临时文件原子替换会话 JSON。"""

        self.directory.mkdir(parents=True, exist_ok=True)
        conversation["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        target = self.directory / f"{conversation['id']}.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(conversation, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
        return target

    def load(self, conversation_id: str) -> dict | None:
        """校验 ID 后读取会话；损坏或不存在时返回 ``None``。"""

        if not re.fullmatch(r"[0-9a-f]{32}", conversation_id or ""):
            return None
        path = self.directory / f"{conversation_id}.json"
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def append(self, conversation: dict, message: dict) -> None:
        """只保留白名单字段，防止密钥或网页正文意外写入历史。"""

        safe = {
            key: value for key, value in message.items()
            if key in {
                "role", "content", "mode", "kind", "artifact_path", "sources", "delivery",
                "question", "summary", "rounds", "stop_reason", "review_summary",
                "queries", "warnings",
            }
        }
        conversation.setdefault("messages", []).append(safe)
        if conversation.get("title") == "新对话" and safe.get("role") == "user":
            conversation["title"] = str(safe.get("content", "")).strip()[:36] or "新对话"
        self.save(conversation)

    def list(self, limit: int | None = 20) -> list[dict]:
        """按最近更新时间返回轻量会话摘要；``None`` 表示读取全部。"""

        if not self.directory.exists():
            return []
        if limit is not None and limit <= 0:
            return []
        summaries = []
        for path in sorted(self.directory.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            value = self.load(path.stem)
            if value:
                summaries.append({
                    "id": value.get("id", path.stem),
                    "title": value.get("title", "未命名对话"),
                    "updated_at": value.get("updated_at", ""),
                    "message_count": len(value.get("messages", [])),
                })
            if limit is not None and len(summaries) >= limit:
                break
        return summaries

    def delete(self, conversation_id: str) -> bool:
        """永久删除单个会话 JSON；不删除消息引用的报告或搜索快照。"""

        if not re.fullmatch(r"[0-9a-f]{32}", conversation_id or ""):
            raise ValueError("无效的会话记录 ID")
        path = self.directory / f"{conversation_id}.json"
        if not path.is_file():
            return False
        path.unlink()
        return True

    def clear_artifact_reference(self, artifact_path: Path) -> int:
        """清除指向已移走资产的会话引用，同时保留消息文字和来源摘要。"""

        expected = artifact_path.resolve()
        changed_count = 0
        if not self.directory.exists():
            return changed_count
        for path in self.directory.glob("*.json"):
            conversation = self.load(path.stem)
            if not conversation:
                continue
            changed = False
            for message in conversation.get("messages", []):
                value = str(message.get("artifact_path", "")).strip()
                if value and Path(value).resolve() == expected:
                    message["artifact_path"] = ""
                    changed = True
            if changed:
                self.save(conversation)
                changed_count += 1
        return changed_count


class PromptShortcutStore:
    """本地快捷输入存储；只保存显示名称与待填入输入框的文本。"""

    def __init__(self, path: Path, maximum: int = 20) -> None:
        self.path = path
        self.maximum = maximum

    def list(self) -> list[dict[str, str]]:
        """读取并清洗快捷输入；首次访问时创建可编辑的默认内容。"""

        if not self.path.exists():
            shortcuts = [
                {"id": f"default-{index}", "label": label, "prompt": prompt}
                for index, (label, prompt) in enumerate(DEFAULT_PROMPT_SHORTCUTS, 1)
            ]
            self._save(shortcuts)
            return shortcuts
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        values = payload.get("shortcuts", []) if isinstance(payload, dict) else []
        shortcuts = []
        for item in values[: self.maximum]:
            if not isinstance(item, dict):
                continue
            shortcut_id = str(item.get("id", "")).strip()
            label = str(item.get("label", "")).strip()
            prompt = str(item.get("prompt", "")).strip()
            if shortcut_id and label and prompt:
                shortcuts.append({"id": shortcut_id, "label": label[:28], "prompt": prompt[:1600]})
        return shortcuts

    def add(self, label: str, prompt: str) -> dict[str, str]:
        """添加一个名称唯一的快捷输入。"""

        shortcuts = self.list()
        if len(shortcuts) >= self.maximum:
            raise ValueError(f"最多保存 {self.maximum} 个快捷输入")
        clean_label, clean_prompt = self._validate(label, prompt)
        if any(item["label"].casefold() == clean_label.casefold() for item in shortcuts):
            raise ValueError("快捷名称不能重复")
        shortcut = {"id": uuid4().hex, "label": clean_label, "prompt": clean_prompt}
        shortcuts.append(shortcut)
        self._save(shortcuts)
        return shortcut

    def update(self, shortcut_id: str, label: str, prompt: str) -> bool:
        """按稳定 ID 更新收藏，找不到时返回 ``False``。"""

        shortcuts = self.list()
        clean_label, clean_prompt = self._validate(label, prompt)
        if any(
            item["id"] != shortcut_id and item["label"].casefold() == clean_label.casefold()
            for item in shortcuts
        ):
            raise ValueError("快捷名称不能重复")
        for item in shortcuts:
            if item["id"] == shortcut_id:
                item.update(label=clean_label, prompt=clean_prompt)
                self._save(shortcuts)
                return True
        return False

    def delete(self, shortcut_id: str) -> bool:
        """删除一个快捷输入，不影响已生成的会话与报告。"""

        shortcuts = self.list()
        remaining = [item for item in shortcuts if item["id"] != shortcut_id]
        if len(remaining) == len(shortcuts):
            return False
        self._save(remaining)
        return True

    def _save(self, shortcuts: list[dict[str, str]]) -> None:
        """临时文件替换保证异常中断时不会留下半份 JSON。"""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps({"schema_version": 1, "shortcuts": shortcuts}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    @staticmethod
    def _validate(label: str, prompt: str) -> tuple[str, str]:
        clean_label = re.sub(r"\s+", " ", label).strip()
        clean_prompt = prompt.strip()
        if not clean_label or not clean_prompt:
            raise ValueError("快捷名称和填入内容不能为空")
        if len(clean_label) > 28:
            raise ValueError("快捷名称最多 28 个字符")
        if len(clean_prompt) > 1600:
            raise ValueError("提示内容最多 1600 个字符")
        return clean_label, clean_prompt


class FileArtifactTrash:
    """管理项目内回收站，并严格限制可移动、恢复和删除的文件范围。"""

    _supported_suffixes = {".md", ".txt", ".json"}

    def __init__(self, directory: Path, allowed_roots: tuple[Path, ...]) -> None:
        self.directory = directory.resolve()
        self.allowed_roots = tuple(root.resolve() for root in allowed_roots)

    def move(self, artifact_path: Path) -> Path:
        """校验来源目录后移动文件，并写入包含原路径的恢复元数据。"""

        source = artifact_path.resolve(strict=True)
        if not source.is_file() or source.suffix.lower() not in self._supported_suffixes:
            raise ValueError("只能移除资料库中的报告或搜索快照")
        if not any(source.is_relative_to(root) for root in self.allowed_roots):
            raise ValueError("报告不在允许的资料库目录中")

        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        target = self.directory / f"{stamp}-{uuid4().hex[:8]}-{source.name}"
        metadata_path = target.with_name(target.name + ".trash.json")
        source.replace(target)
        try:
            metadata_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "original_path": str(source),
                        "trashed_path": str(target),
                        "moved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError:
            # 元数据写入失败时回滚文件移动，避免资产变得无法定位。
            target.replace(source)
            raise
        return target

    def list_items(self) -> list[dict[str, object]]:
        """列出元数据和资产均有效的回收站项目，损坏条目不会进入页面。"""

        if not self.directory.is_dir():
            return []
        items: list[dict[str, object]] = []
        for metadata_path in self.directory.glob("*.trash.json"):
            artifact_path = metadata_path.with_name(
                metadata_path.name.removesuffix(".trash.json")
            )
            try:
                target, _, payload, original = self._load_item(artifact_path)
                stat = target.stat()
            except (OSError, ValueError, json.JSONDecodeError):
                # 孤立文件或损坏元数据需要人工排查，不能作为可操作条目暴露。
                continue
            items.append({
                "id": target.name,
                "name": original.name,
                "original_path": str(original),
                "trashed_path": str(target),
                "moved_at": str(payload.get("moved_at", "")),
                "format": target.suffix.lower().lstrip("."),
                "size_kb": round(stat.st_size / 1024, 1),
            })
        return sorted(items, key=lambda item: str(item["moved_at"]), reverse=True)

    def restore(self, artifact_path: Path) -> Path:
        """把条目恢复到原目录；若原位置已有文件则拒绝覆盖。"""

        target, metadata_path, _, original = self._load_item(artifact_path)
        if original.exists():
            raise ValueError("原位置已存在同名文件，请先处理冲突后再恢复")
        original.parent.mkdir(parents=True, exist_ok=True)
        target.replace(original)
        try:
            metadata_path.unlink()
        except OSError:
            # 清理元数据失败时回滚恢复，保持回收站条目仍可继续操作。
            original.replace(target)
            raise
        return original

    def delete(self, artifact_path: Path) -> None:
        """永久删除一个已经过元数据校验的回收站条目。"""

        target, metadata_path, _, _ = self._load_item(artifact_path)
        target.unlink()
        try:
            metadata_path.unlink()
        except FileNotFoundError:
            pass

    def _load_item(self, artifact_path: Path) -> tuple[Path, Path, dict, Path]:
        """解析单个条目并复核目录边界，供所有可变操作统一使用。"""

        target = artifact_path.resolve(strict=True)
        if (
            not target.is_file()
            or target.parent != self.directory
            or target.suffix.lower() not in self._supported_suffixes
        ):
            raise ValueError("回收站条目无效")
        metadata_path = target.with_name(target.name + ".trash.json")
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("回收站元数据无效")

        recorded_target = str(payload.get("trashed_path", "")).strip()
        recorded_original = str(payload.get("original_path", "")).strip()
        if not recorded_target or not recorded_original:
            raise ValueError("回收站元数据不完整")
        if Path(recorded_target).resolve(strict=True) != target:
            raise ValueError("回收站资产与元数据不匹配")

        original = Path(recorded_original).resolve()
        if not any(original.is_relative_to(root) for root in self.allowed_roots):
            raise ValueError("原始路径不在允许的资料库目录中")
        return target, metadata_path, payload, original


def _slugify(value: str, limit: int = 40) -> str:
    slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", value, flags=re.UNICODE).strip("-")
    return (slug or "report")[:limit]
