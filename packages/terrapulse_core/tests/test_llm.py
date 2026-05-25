"""
Tests for the LLM narrative clients.

Phase 0: TemplatedFallbackClient is fully testable without any API key.
AnthropicNarrativeClient fallback behaviour is tested via mock.
"""

from __future__ import annotations

import pytest

from terrapulse_core.reporting.llm import (
    AnthropicNarrativeClient,
    LLMNarrativeClient,
    NarrativeContext,
    TemplatedFallbackClient,
)


@pytest.fixture()
def sample_context() -> NarrativeContext:
    return NarrativeContext(
        aoi_description="Cairo, Egypt (30.0°N, 31.2°E)",
        time_window="2023-01-01 to 2023-12-31",
        n_scenes=12,
        dominant_class="Linear subsidence",
        max_subsidence_mm_yr=-18.5,
        n_high_risk_assets=7,
        top_assets=["Ramses Station", "Cairo Tower", "Al-Azhar Mosque", "Ring Road N4", "Gezira Club"],
        mean_coherence=0.68,
        processing_mode="standard",
        engine_name="pygmtsar",
    )


class TestTemplatedFallbackClient:
    def test_implements_protocol(self) -> None:
        client = TemplatedFallbackClient()
        assert isinstance(client, LLMNarrativeClient)

    def test_generate_summary_returns_string(
        self, sample_context: NarrativeContext
    ) -> None:
        client = TemplatedFallbackClient()
        summary = client.generate_summary(sample_context)
        assert isinstance(summary, str)
        assert len(summary) > 50

    def test_summary_contains_key_facts(
        self, sample_context: NarrativeContext
    ) -> None:
        client = TemplatedFallbackClient()
        summary = client.generate_summary(sample_context)
        assert "12" in summary              # n_scenes
        assert "18.5" in summary            # rate
        assert "7" in summary               # high-risk assets

    def test_summary_subsidence_direction(
        self, sample_context: NarrativeContext
    ) -> None:
        client = TemplatedFallbackClient()
        summary = client.generate_summary(sample_context)
        assert "subsidence" in summary.lower()

    def test_summary_uplift_direction(
        self, sample_context: NarrativeContext
    ) -> None:
        client = TemplatedFallbackClient()
        ctx = NarrativeContext(
            **{**sample_context.__dict__, "max_subsidence_mm_yr": +12.0}
        )
        summary = client.generate_summary(ctx)
        assert "uplift" in summary.lower()

    def test_generate_recommendations_returns_string(
        self, sample_context: NarrativeContext
    ) -> None:
        client = TemplatedFallbackClient()
        recs = client.generate_recommendations(sample_context)
        assert isinstance(recs, str)
        assert len(recs) > 50

    def test_never_raises(self, sample_context: NarrativeContext) -> None:
        client = TemplatedFallbackClient()
        # Should not raise even with unusual values
        ctx = NarrativeContext(
            **{**sample_context.__dict__,
               "max_subsidence_mm_yr": 0.0,
               "n_high_risk_assets": 0,
               "top_assets": []}
        )
        summary = client.generate_summary(ctx)
        assert isinstance(summary, str)


class TestAnthropicClientFallback:
    def test_falls_back_to_template_on_import_error(
        self, sample_context: NarrativeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Simulate missing `anthropic` package → should fall back to template.
        """
        import builtins
        original_import = builtins.__import__

        def mock_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "anthropic":
                raise ImportError("No module named 'anthropic'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)

        client = AnthropicNarrativeClient(api_key="fake-key")
        # Should not raise — falls back to template
        summary = client.generate_summary(sample_context)
        assert isinstance(summary, str)
        assert len(summary) > 0
