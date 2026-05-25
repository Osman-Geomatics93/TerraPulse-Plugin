"""
LLM narrative client protocol + concrete implementations.

The ``LLMNarrativeClient`` protocol is the stable interface. Any backend
(Anthropic, templated fallback, future Ollama) implements it identically.
The report renderer only depends on this protocol — never on a concrete SDK.
"""

from __future__ import annotations

import logging
import textwrap
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass
class NarrativeContext:
    """
    Structured data passed to the LLM for narrative generation.
    All fields are plain Python — safe to log and serialise.
    """

    aoi_description: str          # e.g. "Cairo, Egypt (30.0°N, 31.2°E)"
    time_window: str              # e.g. "2022-01-01 to 2023-12-31"
    n_scenes: int
    dominant_class: str           # e.g. "Linear subsidence"
    max_subsidence_mm_yr: float
    n_high_risk_assets: int
    top_assets: list[str]         # top-5 asset names from RankingResult
    mean_coherence: float
    processing_mode: str
    engine_name: str


@runtime_checkable
class LLMNarrativeClient(Protocol):
    """
    Protocol for narrative text generation.
    Implementations must be stateless and thread-safe.
    """

    def generate_summary(
        self,
        context: NarrativeContext,
        max_tokens: int = 400,
    ) -> str:
        """
        Generate a plain-language deformation risk summary.

        Returns a string of ≤``max_tokens`` tokens suitable for embedding
        in a PDF report. Must never raise — return a fallback string on error.
        """
        ...

    def generate_recommendations(
        self,
        context: NarrativeContext,
        max_tokens: int = 300,
    ) -> str:
        """
        Generate actionable recommendations for the identified risk.
        Must never raise.
        """
        ...


class AnthropicNarrativeClient:
    """
    LLM narrative client backed by the Anthropic API.

    Uses claude-haiku-4-5 by default (cheapest, adequate for structured summaries).
    Requires ``anthropic`` package and a valid API key.

    Falls back to ``TemplatedFallbackClient`` on any API error.
    """

    MODEL = "claude-haiku-4-5-20251001"
    _SUMMARY_PROMPT = textwrap.dedent("""\
        You are a geospatial risk analyst. Write a concise, plain-language summary
        (3–4 sentences, no jargon) of the following ground deformation analysis for
        a non-expert audience (city planners, engineers, NGO workers).

        Area: {aoi_description}
        Period: {time_window}
        Scenes analysed: {n_scenes}
        Dominant deformation pattern: {dominant_class}
        Maximum subsidence rate: {max_subsidence_mm_yr:.1f} mm/yr
        High-risk infrastructure assets: {n_high_risk_assets}
        Top at-risk assets: {top_assets}
        Mean interferometric coherence: {mean_coherence:.2f}

        Write in English. Start with the most important finding. Do not use bullet points.
    """)

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._fallback = TemplatedFallbackClient()

    def generate_summary(
        self,
        context: NarrativeContext,
        max_tokens: int = 400,
    ) -> str:
        try:
            import anthropic

            client = anthropic.Anthropic(api_key=self._api_key)
            prompt = self._SUMMARY_PROMPT.format(
                aoi_description=context.aoi_description,
                time_window=context.time_window,
                n_scenes=context.n_scenes,
                dominant_class=context.dominant_class,
                max_subsidence_mm_yr=context.max_subsidence_mm_yr,
                n_high_risk_assets=context.n_high_risk_assets,
                top_assets=", ".join(context.top_assets[:5]),
                mean_coherence=context.mean_coherence,
            )
            message = client.messages.create(
                model=self.MODEL,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return str(message.content[0].text)
        except Exception as exc:
            logger.warning("Anthropic API error — using template fallback: %s", exc)
            return self._fallback.generate_summary(context, max_tokens)

    def generate_recommendations(
        self,
        context: NarrativeContext,
        max_tokens: int = 300,
    ) -> str:
        try:
            import anthropic

            client = anthropic.Anthropic(api_key=self._api_key)
            prompt = (
                f"Based on a ground deformation analysis showing {context.dominant_class} "
                f"at up to {context.max_subsidence_mm_yr:.1f} mm/yr in {context.aoi_description}, "
                f"with {context.n_high_risk_assets} high-risk infrastructure assets, "
                f"provide 3 concise, actionable recommendations for local authorities. "
                f"Plain English, no bullet points, 2–3 sentences each."
            )
            message = client.messages.create(
                model=self.MODEL,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return str(message.content[0].text)
        except Exception as exc:
            logger.warning("Anthropic API error — using template fallback: %s", exc)
            return self._fallback.generate_recommendations(context, max_tokens)


class TemplatedFallbackClient:
    """
    Zero-dependency narrative client using Jinja2 templates.

    Used when no LLM API key is provided, or as fallback on API error.
    Produces deterministic, grammatically correct summaries.
    """

    def generate_summary(
        self,
        context: NarrativeContext,
        max_tokens: int = 400,
    ) -> str:
        direction = "subsidence" if context.max_subsidence_mm_yr < 0 else "uplift"
        rate = abs(context.max_subsidence_mm_yr)
        return (
            f"Analysis of {context.n_scenes} Sentinel-1 scenes over {context.aoi_description} "
            f"({context.time_window}) reveals a dominant pattern of {context.dominant_class.lower()} "
            f"with a maximum {direction} rate of {rate:.1f} mm/yr. "
            f"Mean interferometric coherence is {context.mean_coherence:.2f}, indicating "
            f"{'reliable' if context.mean_coherence > 0.4 else 'moderate'} measurement quality. "
            f"{context.n_high_risk_assets} infrastructure asset(s) are classified as high risk "
            f"based on their location relative to deformation hotspots."
        )

    def generate_recommendations(
        self,
        context: NarrativeContext,
        max_tokens: int = 300,
    ) -> str:
        return (
            "1. Commission geotechnical field surveys at the identified high-risk asset locations "
            "to confirm InSAR findings and assess structural integrity. "
            "2. Monitor deformation trends with future Sentinel-1 acquisitions on a 6-month cadence "
            "to detect acceleration. "
            "3. Review building permits and infrastructure maintenance schedules for assets "
            "in the highest-velocity zones."
        )
