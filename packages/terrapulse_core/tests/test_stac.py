"""
Tests for terrapulse_core.stac — STAC models and client.

Phase 0: tests cover models only (no live network calls).
Phase 1: add VCR/responses-mocked tests for STACClient.search_scenes.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from terrapulse_core.stac.models import BBox, SentinelScene, SceneStack, ProcessingMode


# ---------------------------------------------------------------------------
# BBox
# ---------------------------------------------------------------------------

class TestBBox:
    def test_valid_bbox(self) -> None:
        bb = BBox(west=30.0, south=29.0, east=31.0, north=30.0)
        assert bb.west == 30.0

    def test_invalid_west_east(self) -> None:
        with pytest.raises(ValueError, match="west must be < east"):
            BBox(west=31.0, south=29.0, east=30.0, north=30.0)

    def test_invalid_south_north(self) -> None:
        with pytest.raises(ValueError, match="south must be < north"):
            BBox(west=30.0, south=30.0, east=31.0, north=29.0)

    def test_as_list(self) -> None:
        bb = BBox(west=30.0, south=29.0, east=31.0, north=30.0)
        assert bb.as_list() == [30.0, 29.0, 31.0, 30.0]

    def test_area_km2_cairo(self, cairo_bbox: BBox) -> None:
        """Cairo AOI: ~0.8° × 0.6° box at ~30°N — expected ~2 600 km²."""
        area = cairo_bbox.area_km2
        # lat span ≈ 0.6° × 111 km = 66.6 km
        # lon span ≈ 0.8° × 111.32 × cos(30°) = 0.8 × 96.4 ≈ 77.1 km
        # area ≈ 66.6 × 77.1 ≈ 5135 ... wait, the fixture is 0.6° × 0.8°
        # cairo_bbox: west=30.8, south=29.8, east=31.6, north=30.4
        # Δlat = 0.6°,  Δlon = 0.8°,  mid_lat ≈ 30.1°
        # lat_km ≈ 66.7,  lon_km ≈ 0.8 × 111.32 × cos(30.1°) ≈ 77.1
        # area ≈ 66.7 × 77.1 ≈ 5 143 km²   (broad tolerance for rounding)
        assert 4_500 <= area <= 6_000, f"Expected ~5 100 km², got {area:.0f} km²"

    def test_area_km2_large(self) -> None:
        """10° × 10° box at equator: ~1.23 M km²."""
        bb = BBox(west=0.0, south=0.0, east=10.0, north=10.0)
        area = bb.area_km2
        # lat_km = 10 × 111 = 1 110 km
        # lon_km = 10 × 111.32 × cos(5°) ≈ 10 × 110.9 ≈ 1 109 km
        # area  ≈ 1 110 × 1 109 ≈ 1 231 000 km²
        assert 1_100_000 <= area <= 1_350_000, f"Expected ~1.23 M km², got {area:.0f} km²"

    def test_area_km2_high_latitude(self) -> None:
        """Same degree-extent at 60°N yields ~half the area of equator."""
        equator_box = BBox(west=0.0, south=0.0, east=10.0, north=10.0)
        polar_box = BBox(west=0.0, south=60.0, east=10.0, north=70.0)
        # cos(65°) ≈ 0.423 → lon compression by ~42%
        assert polar_box.area_km2 < equator_box.area_km2 * 0.65


# ---------------------------------------------------------------------------
# SentinelScene
# ---------------------------------------------------------------------------

class TestSentinelScene:
    def test_scene_creation(self, sentinel_scene: SentinelScene) -> None:
        assert sentinel_scene.orbit_direction == "ascending"
        assert sentinel_scene.relative_orbit == 87
        assert sentinel_scene.estimated_size_bytes == 4_000_000_000

    def test_scene_polarisation_valid(self) -> None:
        scene = SentinelScene(
            scene_id="test",
            datetime=datetime(2023, 1, 1, tzinfo=timezone.utc),
            bbox=BBox(west=30.0, south=29.0, east=31.0, north=30.0),
            orbit_direction="descending",
            relative_orbit=14,
            polarisation="VV+VH",
            processing_level="L1",
        )
        assert scene.polarisation == "VV+VH"


# ---------------------------------------------------------------------------
# SceneStack
# ---------------------------------------------------------------------------

class TestSceneStack:
    def test_n_scenes(self, scene_stack: SceneStack) -> None:
        assert scene_stack.n_scenes == 12

    def test_time_span(self, scene_stack: SceneStack) -> None:
        # 12 scenes at 24-day intervals → 11 × 24 = 264 days
        assert scene_stack.time_span_days == 264

    def test_total_size_gb(self, scene_stack: SceneStack) -> None:
        assert scene_stack.estimate_total_size_gb() == pytest.approx(48.0, rel=0.01)

    def test_empty_stack(self, cairo_bbox: BBox) -> None:
        stack = SceneStack(
            scenes=[],
            aoi=cairo_bbox,
            orbit_direction="ascending",
            relative_orbit=87,
        )
        assert stack.n_scenes == 0
        assert stack.time_span_days == 0.0


# ---------------------------------------------------------------------------
# ProcessingMode
# ---------------------------------------------------------------------------

class TestProcessingMode:
    def test_defaults(self) -> None:
        mode = ProcessingMode()
        assert mode.mode == "standard"
        assert mode.engine == "pygmtsar"
        assert mode.max_scenes == 30
        assert mode.anthropic_api_key is None

    def test_custom_mode(self) -> None:
        mode = ProcessingMode(mode="quick", engine="openeo", max_scenes=10)
        assert mode.mode == "quick"
        assert mode.engine == "openeo"
