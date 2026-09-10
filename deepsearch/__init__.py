"""DeepSearch public package API."""

from .bootstrap import DeepSearchAgent, build_research_service
from .domain.models import (
    AgentRequest,
    AgentRunResult,
    ChatResult,
    ChatTurn,
    ReportSpecification,
    ResearchBrief,
    SearchResponse,
    WorkMode,
)
from .infrastructure.config import Settings, load_settings

__all__ = [
    "DeepSearchAgent",
    "AgentRequest",
    "AgentRunResult",
    "ChatResult",
    "ChatTurn",
    "ReportSpecification",
    "ResearchBrief",
    "SearchResponse",
    "WorkMode",
    "Settings",
    "build_research_service",
    "load_settings",
]
__version__ = "2.2.0"
