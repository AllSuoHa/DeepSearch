"""可重复、完全离线的搜索适配器，用于测试、演示和网络降级。"""

from __future__ import annotations

import hashlib

from deepsearch.domain.models import SearchResult
from .base import SearchProvider


_PYTHON = [
    ("What’s New In Python 3.13", "https://docs.python.org/3/whatsnew/3.13.html", "Python 3.13 adds a new interactive interpreter, experimental free-threaded mode and a basic JIT."),
    ("Python 3.13 release", "https://www.python.org/downloads/release/python-3130/", "Python 3.13.0 was released on October 7, 2024."),
    ("PEP 703 – Making the GIL Optional", "https://peps.python.org/pep-0703/", "Python supports an optional free-threaded build as an experimental feature."),
    ("Python 3.13 documentation", "https://docs.python.org/3.13/", "The official documentation describes language, library and platform changes."),
]

_MODELS = [
    ("OpenAI API pricing", "https://openai.com/api/pricing/", "OpenAI publishes token prices and model capabilities on its official pricing page."),
    ("Anthropic Claude pricing", "https://docs.anthropic.com/en/docs/about-claude/pricing", "Anthropic documents input, output, cache and batch pricing for Claude models."),
    ("Google Gemini API pricing", "https://ai.google.dev/gemini-api/docs/pricing", "Google lists Gemini context limits and tiered token pricing."),
    ("Artificial Analysis Intelligence Index", "https://artificialanalysis.ai/", "Independent benchmarks compare model reasoning quality, latency and price."),
    ("LiveBench leaderboard", "https://livebench.ai/", "LiveBench provides contamination-resistant evaluations across reasoning categories."),
    ("SWE-bench Verified", "https://www.swebench.com/", "SWE-bench Verified measures real-world software issue resolution."),
    ("Stanford AI Index Report", "https://aiindex.stanford.edu/report/", "The AI Index discusses model performance, costs and industry trends."),
    ("LMSYS Chatbot Arena", "https://lmarena.ai/", "Arena uses pairwise human preferences to compare model outputs."),
]


class MockSearch(SearchProvider):
    """同一输入产生结构稳定的候选，保证断网时也能演示完整 Agent 循环。"""

    name = "mock"

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        lower = query.lower()
        if "python" in lower or "3.13" in lower:
            rows = _PYTHON
        elif any(token in lower for token in ("模型", "model", "推理", "成本", "llm")):
            rows = _MODELS
        else:
            digest = hashlib.sha1(query.encode("utf-8")).hexdigest()[:8]
            rows = [
                (f"{query}：概览", f"mock://overview/{digest}", f"关于“{query}”的背景、定义与核心事实。"),
                (f"{query}：实践资料", f"mock://practice/{digest}", f"关于“{query}”的实践、限制与应用案例。"),
                (f"{query}：独立验证", f"mock://verify/{digest}", f"用于交叉核对“{query}”主要结论的独立资料。"),
            ]
        # 自适应查询返回第二批稳定证据，用于证明复杂问题确实会通过迭代获得新信息，
        # 而不是把相同结果机械重复多轮。
        adaptive = any(token in lower for token in ("官方文档", "独立评测", "official", "benchmark"))
        offset = limit if adaptive and len(rows) > limit else 0
        selected = rows[offset:offset + limit] or rows[:limit]
        return [SearchResult(title, url, snippet, query, self.name) for title, url, snippet in selected]
