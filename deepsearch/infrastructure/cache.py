"""搜索结果与网页正文的轻量 JSON 磁盘缓存。"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from ..domain.models import SearchResult, Source


@dataclass(slots=True)
class CacheStats:
    """用于单次研究指标计算的累计命中与未命中数。"""

    hits: int = 0
    misses: int = 0


class ResearchCache:
    """按命名空间保存搜索和正文缓存，并统计当前进程命中情况。"""

    def __init__(self, directory: Path, ttl_seconds: int = 21_600, enabled: bool = True) -> None:
        self.directory = directory
        self.ttl_seconds = max(60, ttl_seconds)
        self.enabled = enabled
        self.stats = CacheStats()
        self._write_lock = threading.Lock()

    def get_search(self, provider: str, query: str, limit: int) -> list[SearchResult] | None:
        """读取某搜索源和查询组合的缓存结果。"""

        data = self._get("search", f"{provider}|{limit}|{query}")
        if data is None:
            return None
        try:
            return [SearchResult(**item) for item in data]
        except (TypeError, ValueError):
            return None

    def set_search(self, provider: str, query: str, limit: int, results: list[SearchResult]) -> None:
        """缓存真实搜索结果；调用方负责排除 Mock。"""

        self._set("search", f"{provider}|{limit}|{query}", [asdict(item) for item in results])

    def get_source(self, url: str) -> Source | None:
        """读取 URL 对应的正文缓存。"""

        data = self._get("pages", url)
        if data is None:
            return None
        try:
            return Source(**data)
        except (TypeError, ValueError):
            return None

    def set_source(self, source: Source) -> None:
        """只缓存抓取成功的真实来源。"""

        # 失败结果和 Mock 内容不缓存，避免长期复用临时错误或演示数据。
        if source.fetched and source.provider != "mock":
            self._set("pages", source.url, asdict(source))

    def clear(self) -> int:
        """清空搜索与正文命名空间并返回删除条目数。"""

        removed = 0
        for namespace in ("search", "pages"):
            folder = self.directory / namespace
            if not folder.is_dir():
                continue
            for path in folder.glob("*.json"):
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    continue
        return removed

    def _get(self, namespace: str, key: str):
        """读取未过期缓存；损坏、缺失和过期都按 miss 安全处理。"""

        if not self.enabled:
            return None
        path = self._path(namespace, key)
        try:
            if time.time() - path.stat().st_mtime > self.ttl_seconds:
                self.stats.misses += 1
                try:
                    path.unlink()
                except OSError:
                    pass
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.stats.hits += 1
            return payload["value"]
        except (OSError, json.JSONDecodeError, KeyError):
            self.stats.misses += 1
            return None

    def _set(self, namespace: str, key: str, value) -> None:
        if not self.enabled:
            return
        path = self._path(namespace, key)
        with self._write_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            # 先写临时文件再原子替换，避免进程中断留下半个 JSON。
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"saved_at": time.time(), "value": value}, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)

    def _path(self, namespace: str, key: str) -> Path:
        # 哈希文件名既固定长度，也避免查询文本含非法路径字符。
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.directory / namespace / f"{digest}.json"
