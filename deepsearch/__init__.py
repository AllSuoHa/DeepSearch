"""DeepSearch public package API."""

from .bootstrap import DeepSearchAgent, build_research_service
from .domain.models import ReportSpecification, ResearchBrief
from .infrastructure.config import Settings, load_settings

__all__ = [
    "DeepSearchAgent",
    "ReportSpecification",
    "ResearchBrief",
    "Settings",
    "build_research_service",
    "load_settings",
]
__version__ = "2.1.0"
