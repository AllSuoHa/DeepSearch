"""来源排序规则：平衡问题相关性、来源质量和域名多样性。"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from urllib.parse import urlsplit

from .models import SearchResult, Source


class SourceRanker:
    """为候选结果和抓取后的来源评分，并限制单一域名垄断。"""

    trusted_hosts = {
        "python.org", "docs.python.org", "peps.python.org", "openai.com",
        "anthropic.com", "google.com", "ai.google.dev", "microsoft.com",
        "github.com", "stanford.edu", "nature.com", "science.org",
        "wikipedia.org",
    }

    def __init__(self, per_domain_limit: int = 2) -> None:
        self.per_domain_limit = max(1, per_domain_limit)

    def rank(self, results: list[SearchResult], question: str, subquestions: list[str], limit: int | None = None) -> list[SearchResult]:
        """先剔除无关候选，再在相关结果中考虑权威性和域名配额。"""

        context = " ".join([question, *subquestions])
        for result in results:
            # provider 回填的 query 与用户问题天然相同，不能把它计入右侧，
            # 否则标题和摘要完全无关的页面也会获得虚假的高相关性。
            relevance = self._similarity(context, f"{result.title} {result.snippet}")
            quality = self._domain_quality(result.url)
            snippet_bonus = min(len(result.snippet) / 500, 0.1)
            authority_weight = min(1.0, relevance * 4.0)
            result.rank_score = round(
                0.82 * relevance + 0.12 * quality * authority_weight + snippet_bonus * authority_weight,
                4,
            )
        results = [item for item in results if item.rank_score >= 0.035 or item.provider == "mock"]
        # URL 和标题是稳定的次级排序键，避免并发结果导致顺序不确定。
        ordered = sorted(results, key=lambda item: (-item.rank_score, item.url, item.title))
        selected, overflow = [], []
        counts: dict[str, int] = defaultdict(int)
        for result in ordered:
            domain = self.domain(result.url)
            if counts[domain] < self.per_domain_limit:
                counts[domain] += 1
                selected.append(result)
            else:
                overflow.append(result)
        if limit is None:
            return selected + overflow
        return (selected[:limit] + overflow[: max(0, limit - len(selected))])[:limit]

    def score_source(self, source: Source, question: str) -> None:
        """正文抓取后重新评分，把内容完整度和抓取成功状态纳入质量。"""

        source.relevance_score = round(
            self._similarity(question, f"{source.title} {source.snippet} {source.usable_text[:3000]}"),
            4,
        )
        content_bonus = min(math.log10(max(len(source.usable_text), 10)) / 10, 0.4)
        fetch_bonus = 0.15 if source.fetched and source.provider != "mock" else 0.0
        # 权威性只能放大已经相关的来源，不能挽救与问题无关的官方页面。
        authority = self._domain_quality(source.url) * 0.35 * min(1.0, source.relevance_score * 4.0)
        source.quality_score = round(
            min(1.0, authority + content_bonus * min(1.0, source.relevance_score * 5.0) + fetch_bonus),
            4,
        )

    @staticmethod
    def is_relevant_source(source: Source) -> bool:
        """研究模型只接收达到最低语义相关性的来源。"""

        return source.provider == "mock" or source.relevance_score >= 0.025

    @classmethod
    def _domain_quality(cls, url: str) -> float:
        host = cls.domain(url)
        if url.startswith("mock://"):
            return 0.35
        if any(host == trusted or host.endswith("." + trusted) for trusted in cls.trusted_hosts):
            return 1.0
        if host.endswith((".gov", ".edu", ".gov.cn", ".edu.cn")):
            return 0.95
        path = urlsplit(url).path.lower()
        if any(marker in path for marker in ("/docs", "/documentation", "/research", "/paper", "/report")):
            return 0.75
        return 0.55

    @staticmethod
    def domain(url: str) -> str:
        """提取统一域名键，Mock 链接使用其虚拟主机名。"""

        if url.startswith("mock://"):
            return urlsplit(url).netloc or "mock"
        return urlsplit(url).netloc.lower().removeprefix("www.")

    @classmethod
    def _similarity(cls, left: str, right: str) -> float:
        left_terms, right_terms = cls._terms(left), cls._terms(right)
        if not left_terms or not right_terms:
            return 0.0
        return len(left_terms & right_terms) / math.sqrt(len(left_terms) * len(right_terms))

    @staticmethod
    def _terms(text: str) -> set[str]:
        terms = set(re.findall(r"[a-zA-Z0-9_.+-]{2,}", text.lower()))
        for phrase in re.findall(r"[\u4e00-\u9fff]{2,}", text):
            terms.update(phrase[index:index + 2] for index in range(len(phrase) - 1))
        return terms
