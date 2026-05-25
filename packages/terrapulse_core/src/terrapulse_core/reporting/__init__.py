"""Report rendering: Jinja2 HTML → WeasyPrint PDF + optional LLM narrative."""

from terrapulse_core.reporting.llm import (
    AnthropicNarrativeClient,
    LLMNarrativeClient,
    TemplatedFallbackClient,
)
from terrapulse_core.reporting.renderer import ReportConfig, ReportRenderer

__all__ = [
    "ReportRenderer",
    "ReportConfig",
    "LLMNarrativeClient",
    "AnthropicNarrativeClient",
    "TemplatedFallbackClient",
]
