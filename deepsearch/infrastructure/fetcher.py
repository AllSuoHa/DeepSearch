"""并发网页正文抓取与最小 HTML 文本抽取。"""

from __future__ import annotations

import html
import io
import ipaddress
import logging
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from typing import Any

from ..domain.models import SearchResult, Source

logger = logging.getLogger(__name__)


def _validate_fetch_url(url: str) -> None:
    """拒绝明显的本机/私网目标；公开搜索结果不应访问内部服务。"""

    try:
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname or ""
    except ValueError as exc:
        raise ValueError("来源 URL 格式无效") from exc
    if parsed.scheme not in {"http", "https"} or not host:
        raise ValueError("只读取公开 HTTP(S) 来源")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("来源 URL 不得包含内嵌凭据")
    normalized_host = host.rstrip(".").lower()
    if (
        normalized_host == "localhost"
        or normalized_host.endswith((".localhost", ".local", ".internal", ".home.arpa"))
        or "." not in normalized_host
    ):
        raise ValueError("拒绝访问本机或内部网络来源")
    try:
        address = ipaddress.ip_address(normalized_host)
    except ValueError:
        # 操作系统仍接受 127.1 等缩写 IPv4；inet_aton 可在真正发起请求前
        # 把这类文本还原，避免绕过上面的标准字面地址检查。
        try:
            address = ipaddress.ip_address(socket.inet_aton(normalized_host))
        except OSError:
            return
    if not address.is_global:
        raise ValueError("拒绝访问本机或内部网络来源")


class _PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    """对每次 HTTP 重定向复用公网 URL 约束。"""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        _validate_fetch_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_public_url(request: urllib.request.Request, timeout: float) -> Any:
    """打开已校验的公网请求，并阻止重定向跳入明显的内部地址。"""

    _validate_fetch_url(request.full_url)
    return urllib.request.build_opener(_PublicRedirectHandler()).open(request, timeout=timeout)


class _TextExtractor(HTMLParser):
    """标准库 HTML 文本抽取器，跳过脚本、样式和导航等低价值区域。"""

    ignored = {"script", "style", "noscript", "svg", "nav", "footer", "form", "header", "aside", "button"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self.ignored:
            self.depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self.ignored and self.depth:
            self.depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.depth and data.strip():
            self.parts.append(data.strip())


class WebFetcher:
    """带超时、并发、内容限制和摘要降级的来源抓取器。"""

    def __init__(self, timeout: float = 8.0, workers: int = 8, max_chars: int = 20_000) -> None:
        self.timeout = timeout
        self.workers = workers
        self.max_chars = max_chars

    def fetch_all(self, results: list[SearchResult]) -> list[Source]:
        """并发抓取全部候选；单个 future 失败不会中止整批任务。"""

        if not results:
            return []
        # 预分配结果槽位，保证返回顺序与排序后的候选一致。
        sources: list[Source | None] = [None] * len(results)
        with ThreadPoolExecutor(max_workers=min(self.workers, len(results))) as executor:
            futures = {executor.submit(self.fetch, result): index for index, result in enumerate(results)}
            for future in as_completed(futures):
                index = futures[future]
                try:
                    sources[index] = future.result()
                except Exception as exc:  # defensive boundary around worker failures
                    result = results[index]
                    logger.warning("抓取任务异常 error=%s", type(exc).__name__)
                    sources[index] = Source(
                        result.title, result.url, "", result.snippet, result.query,
                        result.provider, False, str(exc),
                        resource_type=result.resource_type,
                        risk_level=result.risk_level,
                        risk_reasons=result.risk_reasons,
                        published_at=result.published_at,
                        authors=result.authors,
                        doi=result.doi,
                        retrieved_at=result.retrieved_at,
                        freshness_status=result.freshness_status,
                    )
        return [source for source in sources if source is not None]

    def fetch(self, result: SearchResult) -> Source:
        """抓取单个页面；失败时保留摘要和错误信息供后续透明降级。"""

        if result.provider == "mock" or result.url.startswith("mock://"):
            return Source(
                result.title, result.url,
                f"{result.snippet} 此内容由离线 Mock 搜索源提供，用于验证完整工作流；真实研究请切换在线模式。",
                result.snippet, result.query, result.provider, True,
                resource_type=result.resource_type,
                risk_level=result.risk_level,
                risk_reasons=result.risk_reasons,
                published_at=result.published_at,
                authors=result.authors,
                doi=result.doi,
                retrieved_at=result.retrieved_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
                freshness_status=result.freshness_status,
            )
        request = urllib.request.Request(
            result.url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; DeepSearch/1.0)", "Accept": "text/html,application/xhtml+xml"},
        )
        try:
            with _open_public_url(request, self.timeout) as response:
                content_type = response.headers.get_content_type()
                # 限制响应体大小，避免异常大页面占用过多内存。
                raw = response.read(2_000_000)
                charset = response.headers.get_content_charset() or "utf-8"
            if content_type == "application/pdf" or result.url.lower().split("?", 1)[0].endswith(".pdf"):
                text = self._extract_pdf(raw)
            elif content_type not in {"text/html", "text/plain", "application/xhtml+xml"}:
                raise ValueError(f"不支持的内容类型: {content_type}")
            else:
                decoded = raw.decode(charset, errors="replace")
                text = decoded if content_type == "text/plain" else self._extract(decoded)
            text = self._sanitize(text)
            if len(text) < 120:
                raise ValueError("正文过短或网页阻止抓取")
            return Source(
                result.title, result.url, text[: self.max_chars], result.snippet, result.query,
                result.provider, True, resource_type=result.resource_type,
                risk_level=result.risk_level, risk_reasons=result.risk_reasons,
                published_at=result.published_at, authors=result.authors, doi=result.doi,
                retrieved_at=result.retrieved_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
                freshness_status=result.freshness_status,
            )
        except Exception as exc:
            logger.warning("抓取失败 error=%s", type(exc).__name__)
            return Source(
                result.title, result.url, "", result.snippet, result.query, result.provider, False, str(exc),
                resource_type=result.resource_type, risk_level=result.risk_level,
                risk_reasons=result.risk_reasons, published_at=result.published_at,
                authors=result.authors, doi=result.doi,
                retrieved_at=result.retrieved_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
                freshness_status=result.freshness_status,
            )

    @staticmethod
    def _extract(document: str) -> str:
        """从 HTML 中提取可读文本并合并多余空白。"""

        try:
            import trafilatura

            extracted = trafilatura.extract(
                document,
                include_comments=False,
                include_tables=True,
                favor_precision=True,
            )
            if extracted and len(extracted.strip()) >= 120:
                return re.sub(r"\s+", " ", extracted).strip()
        except Exception:
            # 第三方正文抽取不可用时保留标准库下限。
            pass
        parser = _TextExtractor()
        parser.feed(document)
        return re.sub(r"\s+", " ", html.unescape(" ".join(parser.parts))).strip()

    @staticmethod
    def _extract_pdf(raw: bytes) -> str:
        """读取公开可访问 PDF；不尝试解密或绕过访问控制。"""

        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ValueError("PDF 正文读取需要安装 pypdf") from exc
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            raise ValueError("不读取加密 PDF")
        parts = []
        for page in reader.pages[:80]:
            parts.append(page.extract_text() or "")
        return re.sub(r"\s+", " ", " ".join(parts)).strip()

    @staticmethod
    def _sanitize(text: str) -> str:
        """Remove NUL and non-printing controls that would corrupt Markdown."""
        return "".join(character for character in text if character in "\n\t" or ord(character) >= 32)
