"""报告持久化端口及本地多格式文件实现。"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path


class ReportStorage(ABC):
    """报告存储抽象；未来可替换为数据库、对象存储或知识库。"""

    @abstractmethod
    def save(self, question: str, report: str, output_format: str = "markdown") -> Path:
        raise NotImplementedError

    @abstractmethod
    def list(self, query: str = "") -> list[Path]:
        raise NotImplementedError


class FileReportStorage(ReportStorage):
    """保存经过质量门的报告，并按任务要求导出 Markdown、文本或 JSON。"""

    _extensions = {"markdown": ".md", "text": ".txt", "json": ".json"}

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def save(self, question: str, report: str, output_format: str = "markdown") -> Path:
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


def _slugify(value: str, limit: int = 40) -> str:
    slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", value, flags=re.UNICODE).strip("-")
    return (slug or "report")[:limit]
