"""
Tests for terrapulse_core.reporting + terrapulse_core.provenance.stac_writer.

Covers:
  - NarrativeContext dataclass
  - TemplatedFallbackClient.generate_summary / generate_recommendations
  - ReportConfig / ReportOutput data models
  - ReportRenderer.render() → HTML file written, content valid
  - _encode_image helper
  - _render_pdf graceful fallback (no WeasyPrint)
  - STACItemWriter.write() → valid STAC 1.0 JSON
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from terrapulse_core.reporting.llm import NarrativeContext, TemplatedFallbackClient
from terrapulse_core.reporting.renderer import (
    ReportConfig,
    ReportOutput,
    ReportRenderer,
    _encode_image,
    _render_pdf,
)
from terrapulse_core.provenance.stac_writer import STACItemWriter, TERRAPULSE_STAC_EXTENSION
from terrapulse_core.provenance.recipe import RunRecipe
from terrapulse_core.risk.ranking import AssetRisk, RankingResult
from terrapulse_core.stac.models import BBox


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_narrative_ctx(
    n_high: int = 3,
    max_sub: float = -22.5,
) -> NarrativeContext:
    return NarrativeContext(
        aoi_description="Cairo, Egypt (30.0°N, 31.2°E)",
        time_window="2022-01-01 to 2023-12-31",
        n_scenes=12,
        dominant_class="Linear subsidence/uplift",
        max_subsidence_mm_yr=max_sub,
        n_high_risk_assets=n_high,
        top_assets=["Building A", "Road B", "Hospital C"],
        mean_coherence=0.65,
        processing_mode="standard",
        engine_name="pygmtsar",
    )


def _make_ranking_result(n_assets: int = 3) -> RankingResult:
    assets = [
        AssetRisk(
            osm_id=1000 + i,
            asset_type="building",
            name=f"Asset {i}",
            mean_velocity=-10.0 - i,
            max_velocity=-12.0 - i,
            deformation_class="Linear",
            coherence=0.6,
            risk_score=8.0 - i,
        )
        for i in range(n_assets)
    ]
    return RankingResult(
        assets=assets,
        n_total_assets=n_assets,
        n_high_risk=1,
        n_medium_risk=1,
    )


def _make_recipe(output_dir: Path) -> RunRecipe:
    run_id = str(uuid.uuid4())
    recipe = RunRecipe(
        run_id=run_id,
        status="completed",
        aoi_wkt="POLYGON((30.8 29.8, 31.6 29.8, 31.6 30.4, 30.8 30.4, 30.8 29.8))",
        start_date="2022-01-01",
        end_date="2023-12-31",
        engine="pygmtsar",
        mode="standard",
        terrapulse_version="0.1.0",
        velocity_cog=str(output_dir / "velocity.tif"),
        coherence_cog=str(output_dir / "coherence.tif"),
    )
    return recipe


# ---------------------------------------------------------------------------
# NarrativeContext
# ---------------------------------------------------------------------------

class TestNarrativeContext:
    def test_instantiation(self) -> None:
        ctx = _make_narrative_ctx()
        assert ctx.aoi_description == "Cairo, Egypt (30.0°N, 31.2°E)"
        assert ctx.n_scenes == 12
        assert ctx.dominant_class == "Linear subsidence/uplift"

    def test_all_fields_present(self) -> None:
        ctx = _make_narrative_ctx()
        assert isinstance(ctx.top_assets, list)
        assert isinstance(ctx.mean_coherence, float)
        assert isinstance(ctx.max_subsidence_mm_yr, float)


# ---------------------------------------------------------------------------
# TemplatedFallbackClient
# ---------------------------------------------------------------------------

class TestTemplatedFallbackClient:
    def test_generate_summary_returns_string(self) -> None:
        client = TemplatedFallbackClient()
        ctx = _make_narrative_ctx()
        summary = client.generate_summary(ctx)
        assert isinstance(summary, str)
        assert len(summary) > 10

    def test_generate_recommendations_returns_string(self) -> None:
        client = TemplatedFallbackClient()
        ctx = _make_narrative_ctx()
        recs = client.generate_recommendations(ctx)
        assert isinstance(recs, str)
        assert len(recs) > 10

    def test_summary_contains_aoi(self) -> None:
        client = TemplatedFallbackClient()
        ctx = _make_narrative_ctx()
        summary = client.generate_summary(ctx)
        # Should mention something from the context
        assert any(kw in summary.lower() for kw in ["cairo", "subsidence", "deformation", "12"])

    def test_summary_never_raises(self) -> None:
        """Even with edge-case values, no exception."""
        client = TemplatedFallbackClient()
        ctx = NarrativeContext(
            aoi_description="", time_window="",
            n_scenes=0, dominant_class="",
            max_subsidence_mm_yr=0.0,
            n_high_risk_assets=0,
            top_assets=[],
            mean_coherence=0.0,
            processing_mode="",
            engine_name="",
        )
        summary = client.generate_summary(ctx)
        assert isinstance(summary, str)

    def test_protocol_compliance(self) -> None:
        from terrapulse_core.reporting.llm import LLMNarrativeClient
        client = TemplatedFallbackClient()
        assert isinstance(client, LLMNarrativeClient)


# ---------------------------------------------------------------------------
# ReportConfig / ReportOutput models
# ---------------------------------------------------------------------------

class TestReportConfig:
    def test_defaults(self, tmp_path: Path) -> None:
        cfg = ReportConfig(output_dir=tmp_path)
        assert cfg.title == "TerraPulse Deformation Analysis"
        assert cfg.generate_pdf is False
        assert cfg.generate_html is True
        assert cfg.include_interactive_charts is False
        assert cfg.run_id == ""
        assert cfg.engine_name == "pygmtsar"
        assert cfg.processing_mode == "standard"

    def test_custom_values(self, tmp_path: Path) -> None:
        cfg = ReportConfig(
            output_dir=tmp_path,
            title="Test Report",
            run_id="abc-123",
            engine_name="openeo",
            processing_mode="quick",
        )
        assert cfg.title == "Test Report"
        assert cfg.run_id == "abc-123"
        assert cfg.engine_name == "openeo"


class TestReportOutput:
    def test_default_not_successful(self) -> None:
        out = ReportOutput()
        assert out.success is False
        assert out.html_path is None
        assert out.pdf_path is None
        assert out.warnings == []


# ---------------------------------------------------------------------------
# _encode_image
# ---------------------------------------------------------------------------

class TestEncodeImage:
    def test_none_returns_none(self) -> None:
        assert _encode_image(None) is None

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert _encode_image(tmp_path / "nonexistent.png") is None

    def test_real_file_returns_base64(self, tmp_path: Path) -> None:
        png_path = tmp_path / "test.png"
        # Write minimal binary content
        png_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
        result = _encode_image(png_path)
        assert result is not None
        assert isinstance(result, str)
        # Verify it's valid base64
        import base64
        decoded = base64.b64decode(result)
        assert len(decoded) > 0


# ---------------------------------------------------------------------------
# _render_pdf — graceful fallback
# ---------------------------------------------------------------------------

class TestRenderPdf:
    def test_no_weasyprint_returns_none(self, tmp_path: Path) -> None:
        """Without WeasyPrint, _render_pdf returns None and appends warning."""
        import sys
        from unittest.mock import patch

        warnings: list[str] = []
        with patch.dict("sys.modules", {"weasyprint": None}):
            result = _render_pdf("<html><body>test</body></html>", tmp_path, warnings)
        assert result is None
        assert len(warnings) == 1
        assert "weasyprint" in warnings[0].lower() or "pdf" in warnings[0].lower()


# ---------------------------------------------------------------------------
# ReportRenderer.render()
# ---------------------------------------------------------------------------

class TestReportRenderer:
    def test_render_writes_html_file(self, tmp_path: Path) -> None:
        cfg = ReportConfig(output_dir=tmp_path, run_id="test-run-001")
        renderer = ReportRenderer(cfg)
        ctx = _make_narrative_ctx()
        ranking = _make_ranking_result(3)
        output = renderer.render(ctx, ranking)
        assert output.success is True
        assert output.html_path is not None
        assert output.html_path.exists()

    def test_html_contains_title(self, tmp_path: Path) -> None:
        cfg = ReportConfig(output_dir=tmp_path, title="My Test Report")
        renderer = ReportRenderer(cfg)
        output = renderer.render(_make_narrative_ctx(), _make_ranking_result())
        html = output.html_path.read_text(encoding="utf-8")
        assert "My Test Report" in html

    def test_html_contains_aoi_description(self, tmp_path: Path) -> None:
        cfg = ReportConfig(output_dir=tmp_path)
        renderer = ReportRenderer(cfg)
        output = renderer.render(_make_narrative_ctx(), _make_ranking_result())
        html = output.html_path.read_text(encoding="utf-8")
        assert "Cairo" in html

    def test_html_contains_n_scenes(self, tmp_path: Path) -> None:
        cfg = ReportConfig(output_dir=tmp_path)
        renderer = ReportRenderer(cfg)
        ctx = _make_narrative_ctx()
        output = renderer.render(ctx, _make_ranking_result())
        html = output.html_path.read_text(encoding="utf-8")
        assert "12" in html  # n_scenes=12

    def test_render_without_images_still_succeeds(self, tmp_path: Path) -> None:
        cfg = ReportConfig(output_dir=tmp_path)
        renderer = ReportRenderer(cfg)
        output = renderer.render(
            _make_narrative_ctx(),
            _make_ranking_result(),
            velocity_png=None,
            classification_png=None,
        )
        assert output.success is True

    def test_render_creates_output_dir(self, tmp_path: Path) -> None:
        nested = tmp_path / "nested" / "output"
        cfg = ReportConfig(output_dir=nested)
        renderer = ReportRenderer(cfg)
        renderer.render(_make_narrative_ctx(), _make_ranking_result())
        assert nested.exists()

    def test_generate_html_false_no_file(self, tmp_path: Path) -> None:
        cfg = ReportConfig(output_dir=tmp_path, generate_html=False)
        renderer = ReportRenderer(cfg)
        output = renderer.render(_make_narrative_ctx(), _make_ranking_result())
        assert output.html_path is None
        assert not (tmp_path / "report.html").exists()

    def test_generate_pdf_false_no_pdf(self, tmp_path: Path) -> None:
        """generate_pdf=False → no PDF, even if WeasyPrint available."""
        cfg = ReportConfig(output_dir=tmp_path, generate_pdf=False)
        renderer = ReportRenderer(cfg)
        output = renderer.render(_make_narrative_ctx(), _make_ranking_result())
        assert output.pdf_path is None

    def test_empty_ranking_result(self, tmp_path: Path) -> None:
        """Empty ranking result → renders without error."""
        cfg = ReportConfig(output_dir=tmp_path)
        renderer = ReportRenderer(cfg)
        output = renderer.render(_make_narrative_ctx(), RankingResult())
        assert output.success is True

    def test_warnings_list_accessible(self, tmp_path: Path) -> None:
        cfg = ReportConfig(output_dir=tmp_path)
        renderer = ReportRenderer(cfg)
        output = renderer.render(_make_narrative_ctx(), _make_ranking_result())
        assert isinstance(output.warnings, list)


# ---------------------------------------------------------------------------
# STACItemWriter
# ---------------------------------------------------------------------------

class TestSTACItemWriter:
    def test_write_returns_json_path(self, tmp_path: Path) -> None:
        recipe = _make_recipe(tmp_path)
        aoi = BBox(west=30.8, south=29.8, east=31.6, north=30.4)
        path = STACItemWriter().write(recipe, aoi, tmp_path)
        assert path.exists()
        assert path.suffix == ".json"

    def test_output_is_valid_json(self, tmp_path: Path) -> None:
        recipe = _make_recipe(tmp_path)
        aoi = BBox(west=30.8, south=29.8, east=31.6, north=30.4)
        path = STACItemWriter().write(recipe, aoi, tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    def test_stac_type_is_feature(self, tmp_path: Path) -> None:
        recipe = _make_recipe(tmp_path)
        aoi = BBox(west=30.8, south=29.8, east=31.6, north=30.4)
        path = STACItemWriter().write(recipe, aoi, tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["type"] == "Feature"

    def test_stac_version_1_0(self, tmp_path: Path) -> None:
        recipe = _make_recipe(tmp_path)
        aoi = BBox(west=30.8, south=29.8, east=31.6, north=30.4)
        path = STACItemWriter().write(recipe, aoi, tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["stac_version"] == "1.0.0"

    def test_terrapulse_extension_in_stac_extensions(self, tmp_path: Path) -> None:
        recipe = _make_recipe(tmp_path)
        aoi = BBox(west=30.8, south=29.8, east=31.6, north=30.4)
        path = STACItemWriter().write(recipe, aoi, tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert TERRAPULSE_STAC_EXTENSION in data["stac_extensions"]

    def test_geometry_is_polygon(self, tmp_path: Path) -> None:
        recipe = _make_recipe(tmp_path)
        aoi = BBox(west=30.8, south=29.8, east=31.6, north=30.4)
        path = STACItemWriter().write(recipe, aoi, tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["geometry"]["type"] == "Polygon"

    def test_bbox_matches_aoi(self, tmp_path: Path) -> None:
        recipe = _make_recipe(tmp_path)
        aoi = BBox(west=30.8, south=29.8, east=31.6, north=30.4)
        path = STACItemWriter().write(recipe, aoi, tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        bbox = data["bbox"]
        assert bbox[0] == pytest.approx(30.8)
        assert bbox[1] == pytest.approx(29.8)
        assert bbox[2] == pytest.approx(31.6)
        assert bbox[3] == pytest.approx(30.4)

    def test_terrapulse_properties_present(self, tmp_path: Path) -> None:
        recipe = _make_recipe(tmp_path)
        aoi = BBox(west=30.8, south=29.8, east=31.6, north=30.4)
        path = STACItemWriter().write(recipe, aoi, tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        props = data["properties"]
        assert "terrapulse:engine" in props
        assert "terrapulse:mode" in props
        assert "terrapulse:aoi_wkt" in props
        assert "terrapulse:version" in props

    def test_assets_include_velocity_and_coherence(self, tmp_path: Path) -> None:
        recipe = _make_recipe(tmp_path)
        aoi = BBox(west=30.8, south=29.8, east=31.6, north=30.4)
        path = STACItemWriter().write(recipe, aoi, tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assets = data["assets"]
        assert "velocity" in assets
        assert "coherence" in assets

    def test_velocity_asset_has_cog_media_type(self, tmp_path: Path) -> None:
        recipe = _make_recipe(tmp_path)
        aoi = BBox(west=30.8, south=29.8, east=31.6, north=30.4)
        path = STACItemWriter().write(recipe, aoi, tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        vel_asset = data["assets"]["velocity"]
        assert "geotiff" in vel_asset["type"].lower() or "tiff" in vel_asset["type"].lower()

    def test_item_id_contains_run_id(self, tmp_path: Path) -> None:
        recipe = _make_recipe(tmp_path)
        aoi = BBox(west=30.8, south=29.8, east=31.6, north=30.4)
        path = STACItemWriter().write(recipe, aoi, tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert recipe.run_id in data["id"]

    def test_filename_uses_short_run_id(self, tmp_path: Path) -> None:
        recipe = _make_recipe(tmp_path)
        aoi = BBox(west=30.8, south=29.8, east=31.6, north=30.4)
        path = STACItemWriter().write(recipe, aoi, tmp_path)
        assert recipe.run_id[:8] in path.name

    def test_empty_assets_no_velocity_key(self, tmp_path: Path) -> None:
        """Recipe with no velocity_cog → no velocity asset in STAC."""
        recipe = RunRecipe(
            run_id=str(uuid.uuid4()),
            status="completed",
            aoi_wkt="POLYGON((30.8 29.8, 31.6 29.8, 31.6 30.4, 30.8 30.4, 30.8 29.8))",
        )
        aoi = BBox(west=30.8, south=29.8, east=31.6, north=30.4)
        path = STACItemWriter().write(recipe, aoi, tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "velocity" not in data["assets"]

    def test_links_is_list(self, tmp_path: Path) -> None:
        recipe = _make_recipe(tmp_path)
        aoi = BBox(west=30.8, south=29.8, east=31.6, north=30.4)
        path = STACItemWriter().write(recipe, aoi, tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data["links"], list)

    def test_geometry_ring_is_closed(self, tmp_path: Path) -> None:
        """GeoJSON polygon ring first == last point."""
        recipe = _make_recipe(tmp_path)
        aoi = BBox(west=30.8, south=29.8, east=31.6, north=30.4)
        path = STACItemWriter().write(recipe, aoi, tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        ring = data["geometry"]["coordinates"][0]
        assert ring[0] == ring[-1]
