"""配置加载、环境变量覆盖与安全持久化。"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

_CONFIG_LOCK = threading.Lock()


@dataclass(slots=True)
class LLMSettings:
    """OpenAI 兼容模型连接参数。

    ``request_timeout`` 约束单次阶段调用，而不是整份研究的总时长；
    正常在线研究会连续调用证据整理、初稿和独立审校三个阶段。
    """

    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    api_key: str = ""
    request_timeout: float = 180.0


@dataclass(slots=True)
class CustomerServiceSettings:
    """CustomerService 知识库投递配置；所有凭据仅来自环境变量。"""

    enabled: bool = False
    base_url: str = "http://127.0.0.1:8000"
    integration_key: str = ""
    api_access_key: str = ""
    request_timeout: float = 15.0
    retry_attempts: int = 3
    retry_backoff_seconds: float = 1.0
    outbox_dir: Path = Path(".cache/customer-service-outbox")


@dataclass(slots=True)
class Settings:
    """项目完整运行配置；路径会相对于配置文件位置解析。"""

    # ``mode`` 是 2.1 兼容别名；新代码使用 runtime_mode，避免与工作模式混淆。
    mode: str = "auto"
    runtime_mode: str = ""
    default_work_mode: str = "auto"
    search_provider: str = "duckduckgo"
    search_provider_order: tuple[str, ...] = ("brave", "duckduckgo", "wikipedia")
    brave_api_key: str = ""
    max_rounds: int = 3
    results_per_query: int = 4
    max_sources: int = 16
    request_timeout: float = 8.0
    fetch_workers: int = 8
    reports_dir: Path = Path("reports")
    cache_dir: Path = Path(".cache/deepsearch")
    conversation_dir: Path = Path("data/conversations")
    cache_ttl_seconds: int = 21_600
    cache_enabled: bool = True
    per_domain_limit: int = 2
    log_level: str = "INFO"
    config_path: Path = Path("config.json")
    llm: LLMSettings = field(default_factory=LLMSettings)
    customer_service: CustomerServiceSettings = field(default_factory=CustomerServiceSettings)
    topics: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.runtime_mode:
            self.runtime_mode = "mock" if self.mode == "mock" else "online"
        if self.runtime_mode not in {"online", "mock"}:
            raise ValueError("runtime_mode 必须是 online 或 mock")
        if self.default_work_mode not in {"auto", "search", "research"}:
            raise ValueError("default_work_mode 必须是 auto、search 或 research")
        self.mode = "mock" if self.runtime_mode == "mock" else "auto"

    @property
    def research_tasks(self) -> list[dict[str, Any]]:
        """2.1 名称；底层继续复用 topics 字段以兼容旧调用者。"""

        return self.topics

    @property
    def mock_search(self) -> bool:
        """只有显式演示模式才允许创建或接受 Mock 搜索结果。"""

        return self.runtime_mode == "mock"

    @property
    def mock_llm(self) -> bool:
        """判断当前是否只能使用确定性演示报告器。"""

        return self.runtime_mode == "mock" or not self.llm.api_key


def load_settings(path: str | Path | None = None, **overrides: Any) -> Settings:
    """按“默认值 → JSON → 环境变量 → 调用参数”优先级合并配置。"""

    config_path = Path(path or os.getenv("DEEPSEARCH_CONFIG", "config.json")).resolve()
    raw: dict[str, Any] = {}
    if config_path.exists():
        raw = json.loads(config_path.read_text(encoding="utf-8"))

    llm_raw = raw.get("llm", {})
    llm_defaults = LLMSettings()
    # 密钥优先从环境变量读取，避免必须写入本地配置文件。
    llm = LLMSettings(
        base_url=os.getenv("DEEPSEARCH_BASE_URL", llm_raw.get("base_url", llm_defaults.base_url)),
        model=os.getenv("DEEPSEARCH_MODEL", llm_raw.get("model", llm_defaults.model)),
        api_key=os.getenv("DEEPSEARCH_API_KEY", llm_raw.get("api_key", "")),
        request_timeout=max(
            10.0,
            float(
                os.getenv(
                    "DEEPSEARCH_LLM_TIMEOUT",
                    llm_raw.get("request_timeout", llm_defaults.request_timeout),
                )
            ),
        ),
    )
    reports_value = raw.get("reports_dir", "reports")
    reports_dir = Path(reports_value)
    if not reports_dir.is_absolute():
        reports_dir = config_path.parent / reports_dir
    cache_dir = Path(raw.get("cache_dir", ".cache/deepsearch"))
    if not cache_dir.is_absolute():
        cache_dir = config_path.parent / cache_dir
    conversation_dir = Path(raw.get("conversation_dir", "data/conversations"))
    if not conversation_dir.is_absolute():
        conversation_dir = config_path.parent / conversation_dir
    customer_service_raw = raw.get("customer_service", {})
    customer_service_defaults = CustomerServiceSettings()
    outbox_dir = Path(customer_service_raw.get("outbox_dir", customer_service_defaults.outbox_dir))
    if not outbox_dir.is_absolute():
        outbox_dir = config_path.parent / outbox_dir
    customer_service = CustomerServiceSettings(
        enabled=_environment_bool(
            "DEEPSEARCH_CUSTOMER_SERVICE_ENABLED",
            bool(customer_service_raw.get("enabled", customer_service_defaults.enabled)),
        ),
        base_url=os.getenv(
            "DEEPSEARCH_CUSTOMER_SERVICE_URL",
            str(customer_service_raw.get("base_url", customer_service_defaults.base_url)),
        ),
        integration_key=os.getenv("DEEPSEARCH_CUSTOMER_SERVICE_INTEGRATION_KEY", ""),
        api_access_key=os.getenv("DEEPSEARCH_CUSTOMER_SERVICE_API_KEY", ""),
        request_timeout=float(
            customer_service_raw.get("request_timeout", customer_service_defaults.request_timeout)
        ),
        retry_attempts=max(
            1,
            int(customer_service_raw.get("retry_attempts", customer_service_defaults.retry_attempts)),
        ),
        retry_backoff_seconds=max(
            0.0,
            float(
                customer_service_raw.get(
                    "retry_backoff_seconds",
                    customer_service_defaults.retry_backoff_seconds,
                )
            ),
        ),
        outbox_dir=outbox_dir,
    )

    settings = Settings(
        mode=str(raw.get("mode", "auto")),
        runtime_mode=os.getenv(
            "DEEPSEARCH_RUNTIME_MODE",
            str(raw.get("runtime_mode", "mock" if os.getenv("DEEPSEARCH_MODE", raw.get("mode")) == "mock" else "online")),
        ),
        default_work_mode=str(raw.get("default_work_mode", "auto")),
        search_provider=raw.get("search_provider", "duckduckgo"),
        search_provider_order=tuple(raw.get("search_provider_order", ("brave", "duckduckgo", "wikipedia"))),
        brave_api_key=os.getenv("DEEPSEARCH_BRAVE_API_KEY", ""),
        max_rounds=int(raw.get("max_rounds", 3)),
        results_per_query=int(raw.get("results_per_query", 4)),
        max_sources=int(raw.get("max_sources", 16)),
        request_timeout=float(raw.get("request_timeout", 8.0)),
        fetch_workers=int(raw.get("fetch_workers", 8)),
        reports_dir=reports_dir,
        cache_dir=cache_dir,
        conversation_dir=conversation_dir,
        cache_ttl_seconds=int(raw.get("cache_ttl_seconds", 21_600)),
        cache_enabled=bool(raw.get("cache_enabled", True)),
        per_domain_limit=int(raw.get("per_domain_limit", 2)),
        log_level=raw.get("log_level", "INFO"),
        config_path=config_path,
        llm=llm,
        customer_service=customer_service,
        topics=list(raw.get("research_tasks", raw.get("topics", []))),
    )
    # 忽略未知覆盖项，避免调用者拼写错误破坏 dataclass 构造。
    valid = {key: value for key, value in overrides.items() if hasattr(settings, key) and value is not None}
    if "mode" in valid and "runtime_mode" not in valid:
        valid["runtime_mode"] = "mock" if valid["mode"] == "mock" else "online"
    return replace(settings, **valid)


def save_settings(settings: Settings) -> None:
    """原子保存普通配置；无论内存中是否有密钥，都不会落盘。"""

    data = {
        "mode": settings.mode,
        "runtime_mode": settings.runtime_mode,
        "default_work_mode": settings.default_work_mode,
        "search_provider": settings.search_provider,
        "search_provider_order": list(settings.search_provider_order),
        "max_rounds": settings.max_rounds,
        "results_per_query": settings.results_per_query,
        "max_sources": settings.max_sources,
        "request_timeout": settings.request_timeout,
        "fetch_workers": settings.fetch_workers,
        "reports_dir": str(settings.reports_dir),
        "cache_dir": str(settings.cache_dir),
        "conversation_dir": str(settings.conversation_dir),
        "cache_ttl_seconds": settings.cache_ttl_seconds,
        "cache_enabled": settings.cache_enabled,
        "per_domain_limit": settings.per_domain_limit,
        "log_level": settings.log_level,
        "llm": {
            "base_url": settings.llm.base_url,
            "model": settings.llm.model,
            "request_timeout": settings.llm.request_timeout,
            # 密钥只属于环境变量或 Streamlit Secrets，禁止写入 config.json。
            "api_key": "",
        },
        "customer_service": {
            "enabled": settings.customer_service.enabled,
            "base_url": settings.customer_service.base_url,
            "request_timeout": settings.customer_service.request_timeout,
            "retry_attempts": settings.customer_service.retry_attempts,
            "retry_backoff_seconds": settings.customer_service.retry_backoff_seconds,
            "outbox_dir": str(settings.customer_service.outbox_dir),
            # 两类密钥都只允许从环境变量或 Streamlit Secrets 注入。
            "integration_key": "",
            "api_access_key": "",
        },
        "research_tasks": settings.topics,
    }
    with _CONFIG_LOCK:
        settings.config_path.parent.mkdir(parents=True, exist_ok=True)
        # 临时文件 + replace 防止写入中断导致配置损坏。
        temporary = settings.config_path.with_suffix(settings.config_path.suffix + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(settings.config_path)


def _environment_bool(name: str, default: bool) -> bool:
    """严格解析布尔环境变量，拒绝把拼写错误静默当作 ``False``。"""

    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"环境变量 {name} 必须是 true/false、1/0、yes/no 或 on/off")
