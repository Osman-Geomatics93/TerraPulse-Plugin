"""
Tests for terrapulse_core.risk — OSM querier + asset ranking.

Phase 3: full implementation tests with mocked Overpass API
and synthetic COGs written via rasterio.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from terrapulse_core.risk.osm import OSMQuerier, OSMAssets, _QUERY_TEMPLATE
from terrapulse_core.risk.ranking import (
    AssetRisk,
    RankingResult,
    RiskRanker,
    _compute_risk_score,
    _iter_asset_rows,
    _sample_centroid,
)
from terrapulse_core.stac.models import BBox


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_synthetic_cog(
    path: Path,
    data: np.ndarray,
    west: float = 30.8,
    south: float = 29.8,
    east: float = 31.6,
    north: float = 30.4,
) -> Path:
    """Write a single-band float32 GeoTIFF (not true COG, but rasterio-readable)."""
    import rasterio
    from rasterio.transform import from_bounds

    h, w = data.shape
    transform = from_bounds(west, south, east, north, w, h)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=h,
        width=w,
        count=1,
        dtype=np.float32,
        crs="EPSG:4326",
        transform=transform,
        nodata=None,
    ) as ds:
        ds.write(data.astype(np.float32), 1)
    return path


def _make_osm_assets_gdf(
    include_buildings: bool = True,
    include_roads: bool = True,
    include_pipelines: bool = False,
    include_critical: bool = False,
    centroid_lon: float = 31.2,
    centroid_lat: float = 30.1,
) -> "OSMAssets":
    """Build a synthetic OSMAssets with geopandas GeoDataFrames."""
    import geopandas as gpd
    from shapely.geometry import LineString, Point, Polygon

    assets = OSMAssets()
    rows_b, rows_r, rows_p, rows_c = [], [], [], []

    if include_buildings:
        poly = Polygon([
            (centroid_lon - 0.001, centroid_lat - 0.001),
            (centroid_lon + 0.001, centroid_lat - 0.001),
            (centroid_lon + 0.001, centroid_lat + 0.001),
            (centroid_lon - 0.001, centroid_lat + 0.001),
            (centroid_lon - 0.001, centroid_lat - 0.001),
        ])
        rows_b.append({"osm_id": 1001, "name": "Test Building", "building": "yes", "geometry": poly})
        assets.buildings = gpd.GeoDataFrame(rows_b, crs="EPSG:4326")

    if include_roads:
        line = LineString([(centroid_lon - 0.002, centroid_lat), (centroid_lon + 0.002, centroid_lat)])
        rows_r.append({"osm_id": 2001, "name": "Test Road", "highway": "primary", "geometry": line})
        assets.roads = gpd.GeoDataFrame(rows_r, crs="EPSG:4326")

    if include_pipelines:
        pipe = LineString([(centroid_lon, centroid_lat - 0.002), (centroid_lon, centroid_lat + 0.002)])
        rows_p.append({"osm_id": 3001, "name": "Test Pipeline", "geometry": pipe})
        assets.pipelines = gpd.GeoDataFrame(rows_p, crs="EPSG:4326")

    if include_critical:
        pt = Point(centroid_lon, centroid_lat)
        rows_c.append({"osm_id": 4001, "name": "Test Hospital", "amenity": "hospital", "geometry": pt})
        assets.critical_nodes = gpd.GeoDataFrame(rows_c, crs="EPSG:4326")

    assets.n_features = (
        len(rows_b) + len(rows_r) + len(rows_p) + len(rows_c)
    )
    return assets


# ---------------------------------------------------------------------------
# OSMAssets data model
# ---------------------------------------------------------------------------

class TestOSMAssets:
    def test_default_state(self) -> None:
        assets = OSMAssets()
        assert assets.n_features == 0
        assert assets.buildings is None
        assert assets.roads is None
        assert assets.pipelines is None
        assert assets.critical_nodes is None
        assert assets.warnings == []
        assert assets.query_time_s == 0.0

    def test_warnings_list_is_independent(self) -> None:
        a1 = OSMAssets()
        a2 = OSMAssets()
        a1.warnings.append("error")
        assert a2.warnings == []


# ---------------------------------------------------------------------------
# OSMQuerier._build_query
# ---------------------------------------------------------------------------

class TestOSMQuerierBuildQuery:
    def test_query_contains_bbox_coords(self) -> None:
        bbox = BBox(west=30.8, south=29.8, east=31.6, north=30.4)
        querier = OSMQuerier()
        query = querier._build_query(bbox)
        assert "30.8" in query
        assert "29.8" in query
        assert "31.6" in query
        assert "30.4" in query

    def test_query_contains_building_filter(self) -> None:
        bbox = BBox(west=30.8, south=29.8, east=31.6, north=30.4)
        query = OSMQuerier()._build_query(bbox)
        assert 'way["building"]' in query

    def test_query_contains_highway_filter(self) -> None:
        bbox = BBox(west=30.8, south=29.8, east=31.6, north=30.4)
        query = OSMQuerier()._build_query(bbox)
        assert 'way["highway"' in query

    def test_query_contains_critical_nodes(self) -> None:
        bbox = BBox(west=30.8, south=29.8, east=31.6, north=30.4)
        query = OSMQuerier()._build_query(bbox)
        assert "hospital" in query or "amenity" in query

    def test_custom_timeout_in_query(self) -> None:
        bbox = BBox(west=30.8, south=29.8, east=31.6, north=30.4)
        query = OSMQuerier(timeout_s=120)._build_query(bbox)
        assert "120" in query


# ---------------------------------------------------------------------------
# OSMQuerier.query_assets — mocked Overpass
# ---------------------------------------------------------------------------

class TestOSMQuerierQueryAssets:
    def test_overpass_error_returns_warning(self) -> None:
        """Network errors should return OSMAssets with warnings, not raise."""
        with patch("overpy.Overpass") as mock_api_cls:
            mock_api = MagicMock()
            mock_api.query.side_effect = Exception("timeout")
            mock_api_cls.return_value = mock_api
            bbox = BBox(west=30.8, south=29.8, east=31.6, north=30.4)
            result = OSMQuerier().query_assets(bbox)
        assert isinstance(result, OSMAssets)
        assert len(result.warnings) > 0
        assert "Overpass API error" in result.warnings[0]

    def test_empty_result_returns_empty_assets(self) -> None:
        """Empty Overpass result → all GeoDataFrames None."""
        mock_result = MagicMock()
        mock_result.ways = []
        mock_result.nodes = []
        with patch("overpy.Overpass") as mock_api_cls:
            mock_api = MagicMock()
            mock_api.query.return_value = mock_result
            mock_api_cls.return_value = mock_api
            bbox = BBox(west=30.8, south=29.8, east=31.6, north=30.4)
            assets = OSMQuerier().query_assets(bbox)
        assert assets.n_features == 0
        assert assets.buildings is None
        assert assets.roads is None

    def test_building_way_parsed(self) -> None:
        """A closed way with building tag → buildings GeoDataFrame."""
        # Build a mock overpy node
        def make_node(lon, lat):
            n = MagicMock()
            n.lon = lon
            n.lat = lat
            return n

        mock_way = MagicMock()
        mock_way.id = 12345
        mock_way.tags = {"building": "residential", "name": "Test House"}
        mock_way.nodes = [
            make_node(31.0, 30.0),
            make_node(31.001, 30.0),
            make_node(31.001, 30.001),
            make_node(31.0, 30.001),
            make_node(31.0, 30.0),  # closed
        ]

        mock_result = MagicMock()
        mock_result.ways = [mock_way]
        mock_result.nodes = []

        with patch("overpy.Overpass") as mock_api_cls:
            mock_api = MagicMock()
            mock_api.query.return_value = mock_result
            mock_api_cls.return_value = mock_api
            bbox = BBox(west=30.8, south=29.8, east=31.6, north=30.4)
            assets = OSMQuerier().query_assets(bbox)

        assert assets.buildings is not None
        assert len(assets.buildings) == 1
        assert assets.buildings.iloc[0]["osm_id"] == 12345

    def test_road_way_parsed(self) -> None:
        """A highway way → roads GeoDataFrame."""
        def make_node(lon, lat):
            n = MagicMock()
            n.lon = lon
            n.lat = lat
            return n

        mock_way = MagicMock()
        mock_way.id = 99001
        mock_way.tags = {"highway": "primary", "name": "Main Road"}
        mock_way.nodes = [make_node(31.0, 30.0), make_node(31.1, 30.0)]

        mock_result = MagicMock()
        mock_result.ways = [mock_way]
        mock_result.nodes = []

        with patch("overpy.Overpass") as mock_api_cls:
            mock_api = MagicMock()
            mock_api.query.return_value = mock_result
            mock_api_cls.return_value = mock_api
            bbox = BBox(west=30.8, south=29.8, east=31.6, north=30.4)
            assets = OSMQuerier().query_assets(bbox)

        assert assets.roads is not None
        assert len(assets.roads) == 1

    def test_critical_node_parsed(self) -> None:
        """Amenity node → critical_nodes GeoDataFrame."""
        mock_node = MagicMock()
        mock_node.id = 55001
        mock_node.lon = 31.2
        mock_node.lat = 30.1
        mock_node.tags = {"amenity": "hospital", "name": "City Hospital"}

        mock_result = MagicMock()
        mock_result.ways = []
        mock_result.nodes = [mock_node]

        with patch("overpy.Overpass") as mock_api_cls:
            mock_api = MagicMock()
            mock_api.query.return_value = mock_result
            mock_api_cls.return_value = mock_api
            bbox = BBox(west=30.8, south=29.8, east=31.6, north=30.4)
            assets = OSMQuerier().query_assets(bbox)

        assert assets.critical_nodes is not None
        assert assets.critical_nodes.iloc[0]["amenity"] == "hospital"

    def test_missing_overpy_raises_import_error(self) -> None:
        """ImportError if overpy not installed."""
        with patch.dict("sys.modules", {"overpy": None}):
            with pytest.raises(ImportError, match="overpy"):
                bbox = BBox(west=30.8, south=29.8, east=31.6, north=30.4)
                OSMQuerier().query_assets(bbox)


# ---------------------------------------------------------------------------
# AssetRisk model
# ---------------------------------------------------------------------------

class TestAssetRisk:
    def test_creation(self) -> None:
        risk = AssetRisk(
            osm_id=12345,
            asset_type="building",
            name="Cairo Tower",
            mean_velocity=-18.5,
            max_velocity=-22.0,
            deformation_class="Linear subsidence/uplift",
            coherence=0.72,
            risk_score=8.5,
        )
        assert risk.osm_id == 12345
        assert risk.risk_score == 8.5
        assert risk.geometry_wkt == ""  # default

    def test_geometry_wkt_field(self) -> None:
        risk = AssetRisk(
            osm_id=1, asset_type="road", name="Road",
            mean_velocity=-5.0, max_velocity=-5.0,
            deformation_class="Linear", coherence=0.8,
            risk_score=3.0,
            geometry_wkt="LINESTRING (31.0 30.0, 31.1 30.0)",
        )
        assert "LINESTRING" in risk.geometry_wkt


# ---------------------------------------------------------------------------
# RankingResult model
# ---------------------------------------------------------------------------

class TestRankingResult:
    def test_default_state(self) -> None:
        result = RankingResult()
        assert result.assets == []
        assert result.n_total_assets == 0
        assert result.n_high_risk == 0
        assert result.n_medium_risk == 0


# ---------------------------------------------------------------------------
# _compute_risk_score helper
# ---------------------------------------------------------------------------

class TestComputeRiskScore:
    def test_zero_velocity_zero_incoherence_scores_zero(self) -> None:
        # vel=0, coh=1.0 → incoherence=0 → score=0
        score = _compute_risk_score(velocity=0.0, coherence=1.0, asset_type="building")
        assert score == pytest.approx(0.0, abs=0.01)

    def test_extreme_velocity_caps_at_ten(self) -> None:
        # vel=1000 mm/yr → vel_norm=1 → 0.6*1*10*1.0 = 6; + 0.4*0*10 = 0 → 6
        score = _compute_risk_score(velocity=1000.0, coherence=1.0, asset_type="building")
        assert score == pytest.approx(6.0, abs=0.01)

    def test_critical_type_gets_highest_weight(self) -> None:
        score_crit = _compute_risk_score(velocity=25.0, coherence=0.5, asset_type="critical")
        score_road = _compute_risk_score(velocity=25.0, coherence=0.5, asset_type="road")
        assert score_crit > score_road

    def test_score_in_range_0_to_10(self) -> None:
        for vel in [-100, -50, -10, 0, 10, 50, 100]:
            for coh in [0.0, 0.5, 1.0]:
                score = _compute_risk_score(vel, coh, "building")
                assert 0.0 <= score <= 10.0, f"score={score} out of range for vel={vel}, coh={coh}"

    def test_subsidence_same_as_uplift(self) -> None:
        """Formula uses abs(velocity)."""
        score_neg = _compute_risk_score(velocity=-20.0, coherence=0.7, asset_type="building")
        score_pos = _compute_risk_score(velocity=20.0, coherence=0.7, asset_type="building")
        assert score_neg == pytest.approx(score_pos, abs=0.001)

    def test_unknown_type_uses_default_weight(self) -> None:
        # Unknown type → falls back to 1.0 (building weight)
        score_unknown = _compute_risk_score(velocity=20.0, coherence=0.7, asset_type="unknown_type")
        score_building = _compute_risk_score(velocity=20.0, coherence=0.7, asset_type="building")
        assert score_unknown == pytest.approx(score_building, abs=0.001)


# ---------------------------------------------------------------------------
# _iter_asset_rows helper
# ---------------------------------------------------------------------------

class TestIterAssetRows:
    def test_empty_assets_yields_nothing(self) -> None:
        assets = OSMAssets()
        rows = list(_iter_asset_rows(assets))
        assert rows == []

    def test_buildings_and_roads_yielded(self) -> None:
        assets = _make_osm_assets_gdf(include_buildings=True, include_roads=True)
        rows = list(_iter_asset_rows(assets))
        types = {t for _, _, _, t in rows}
        assert "building" in types
        assert "road" in types

    def test_pipeline_asset_type(self) -> None:
        assets = _make_osm_assets_gdf(
            include_buildings=False, include_roads=False,
            include_pipelines=True,
        )
        rows = list(_iter_asset_rows(assets))
        assert all(t == "pipeline" for _, _, _, t in rows)

    def test_critical_asset_type(self) -> None:
        assets = _make_osm_assets_gdf(
            include_buildings=False, include_roads=False,
            include_critical=True,
        )
        rows = list(_iter_asset_rows(assets))
        assert all(t == "critical" for _, _, _, t in rows)

    def test_all_four_types(self) -> None:
        assets = _make_osm_assets_gdf(
            include_buildings=True, include_roads=True,
            include_pipelines=True, include_critical=True,
        )
        rows = list(_iter_asset_rows(assets))
        types = {t for _, _, _, t in rows}
        assert types == {"building", "road", "pipeline", "critical"}


# ---------------------------------------------------------------------------
# _sample_centroid helper
# ---------------------------------------------------------------------------

class TestSampleCentroid:
    def _make_transform(self, west=30.8, south=29.8, east=31.6, north=30.4, h=64, w=64):
        from rasterio.transform import from_bounds
        return from_bounds(west, south, east, north, w, h)

    def test_centroid_inside_raster(self) -> None:
        from shapely.geometry import Point
        rng = np.random.default_rng(0)
        vel = rng.uniform(-20, 0, size=(64, 64)).astype(np.float32)
        coh = rng.uniform(0.5, 1.0, size=(64, 64)).astype(np.float32)
        transform = self._make_transform()
        # Centroid at centre of the AOI
        pt = Point(31.2, 30.1)
        v, c = _sample_centroid(pt, vel, transform, (64, 64), coh)
        assert -20.0 <= v <= 0.0
        assert 0.5 <= c <= 1.0

    def test_centroid_outside_raster_returns_defaults(self) -> None:
        from shapely.geometry import Point
        vel = np.zeros((64, 64), dtype=np.float32)
        coh = np.ones((64, 64), dtype=np.float32)
        transform = self._make_transform()
        # Point far outside the raster
        pt = Point(0.0, 0.0)
        v, c = _sample_centroid(pt, vel, transform, (64, 64), coh)
        assert v == 0.0
        assert c == 0.5


# ---------------------------------------------------------------------------
# RiskRanker.rank
# ---------------------------------------------------------------------------

class TestRiskRanker:
    def test_rank_with_none_assets_returns_empty(self, tmp_path: Path) -> None:
        vel_path = tmp_path / "velocity.tif"
        coh_path = tmp_path / "coherence.tif"
        vel_data = np.zeros((64, 64), dtype=np.float32)
        coh_data = np.full((64, 64), 0.7, dtype=np.float32)
        _write_synthetic_cog(vel_path, vel_data)
        _write_synthetic_cog(coh_path, coh_data)
        result = RiskRanker().rank(vel_path, coh_path, None)
        assert isinstance(result, RankingResult)
        assert result.n_total_assets == 0

    def test_rank_returns_correct_count(self, tmp_path: Path) -> None:
        vel_path = tmp_path / "velocity.tif"
        coh_path = tmp_path / "coherence.tif"
        rng = np.random.default_rng(7)
        vel_data = rng.uniform(-30, 0, size=(64, 64)).astype(np.float32)
        coh_data = rng.uniform(0.4, 0.9, size=(64, 64)).astype(np.float32)
        _write_synthetic_cog(vel_path, vel_data)
        _write_synthetic_cog(coh_path, coh_data)

        assets = _make_osm_assets_gdf(
            include_buildings=True, include_roads=True,
            centroid_lon=31.2, centroid_lat=30.1,
        )
        result = RiskRanker().rank(vel_path, coh_path, assets)
        # 1 building + 1 road = 2 assets
        assert result.n_total_assets == 2

    def test_rank_sorted_descending(self, tmp_path: Path) -> None:
        vel_path = tmp_path / "velocity.tif"
        coh_path = tmp_path / "coherence.tif"
        rng = np.random.default_rng(8)
        vel_data = rng.uniform(-30, 0, size=(64, 64)).astype(np.float32)
        coh_data = rng.uniform(0.4, 0.9, size=(64, 64)).astype(np.float32)
        _write_synthetic_cog(vel_path, vel_data)
        _write_synthetic_cog(coh_path, coh_data)

        assets = _make_osm_assets_gdf(
            include_buildings=True, include_roads=True,
            include_pipelines=True, include_critical=True,
        )
        result = RiskRanker().rank(vel_path, coh_path, assets)
        scores = [a.risk_score for a in result.assets]
        assert scores == sorted(scores, reverse=True)

    def test_high_velocity_area_produces_high_risk(self, tmp_path: Path) -> None:
        """Assets over a -45 mm/yr subsidence zone should score high."""
        vel_path = tmp_path / "velocity.tif"
        coh_path = tmp_path / "coherence.tif"
        # High velocity (subsidence) and low coherence everywhere
        vel_data = np.full((64, 64), -45.0, dtype=np.float32)
        coh_data = np.full((64, 64), 0.3, dtype=np.float32)
        _write_synthetic_cog(vel_path, vel_data)
        _write_synthetic_cog(coh_path, coh_data)

        assets = _make_osm_assets_gdf(
            include_buildings=True, include_roads=False,
            centroid_lon=31.2, centroid_lat=30.1,
        )
        result = RiskRanker().rank(vel_path, coh_path, assets)
        assert result.n_total_assets >= 1
        assert result.assets[0].risk_score > 5.0  # should be high

    def test_risk_score_clipped_to_0_10(self, tmp_path: Path) -> None:
        vel_path = tmp_path / "velocity.tif"
        coh_path = tmp_path / "coherence.tif"
        vel_data = np.full((64, 64), -1000.0, dtype=np.float32)  # extreme
        coh_data = np.zeros((64, 64), dtype=np.float32)
        _write_synthetic_cog(vel_path, vel_data)
        _write_synthetic_cog(coh_path, coh_data)

        assets = _make_osm_assets_gdf(include_buildings=True, include_roads=True)
        result = RiskRanker().rank(vel_path, coh_path, assets)
        for asset in result.assets:
            assert 0.0 <= asset.risk_score <= 10.0

    def test_n_high_and_medium_counts_correct(self, tmp_path: Path) -> None:
        vel_path = tmp_path / "velocity.tif"
        coh_path = tmp_path / "coherence.tif"
        vel_data = np.full((64, 64), -40.0, dtype=np.float32)
        coh_data = np.full((64, 64), 0.2, dtype=np.float32)
        _write_synthetic_cog(vel_path, vel_data)
        _write_synthetic_cog(coh_path, coh_data)

        assets = _make_osm_assets_gdf(
            include_buildings=True, include_roads=True,
            include_critical=True,
        )
        result = RiskRanker().rank(vel_path, coh_path, assets)
        # Recount manually
        manual_high = sum(1 for a in result.assets if a.risk_score > 7)
        manual_medium = sum(1 for a in result.assets if 4 < a.risk_score <= 7)
        assert result.n_high_risk == manual_high
        assert result.n_medium_risk == manual_medium
