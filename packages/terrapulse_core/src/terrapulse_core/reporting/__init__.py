"""Report rendering: Jinja2 HTML → WeasyPrint PDF + optional LLM narrative."""

from terrapulse_core.reporting.renderer import ReportRenderer, ReportConfig
from terrapulse_core.reporting.llm import (
    LLMNarrativeClient,
    AnthropicNarrativeClient,
    TemplatedFallbackClient,
)

__all__ = [
    "ReportRenderer",
    "ReportConfig",
    "LLMNarrativeClient",
    "AnthropicNarrativeClient",
    "TemplatedFallbackClient",
]
