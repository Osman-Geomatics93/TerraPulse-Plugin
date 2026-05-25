"""
OpenStreetMap asset query via Overpass API.

Fetches buildings, roads, pipelines and critical infrastructure within an AOI
bounding box. Returns structured containers (optionally as GeoPandas GeoDataFrames
when geopandas + shapely are installed).

Overpass API: https://overpass-api.de/
overpy docs: https://python-overpy.readthedocs.io/
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from terrapulse_core.stac.models import BBox

logger = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_TIMEOUT_S = 60

# Overpass QL — critical infrastructure query
_QUERY_TEMPLATE = """
[out:json][timeout:{timeout}];
(
  way["building"]({s},{w},{n},{e});
  way["highway"~"^(motorway|trunk|primary|secondary|tertiary)$"]({s},{w},{n},{e});
  way["man_made"="pipeline"]({s},{w},{n},{e});
  way["landuse"="industrial"]({s},{w},{n},{e});
  node["amenity"~"^(hospital|school|fire_station|police)$"]({s},{w},{n},{e});
);
out body geom;
"""


@dataclass
class OSMAssets:
    """Container for OSM features returned by the Overpass query."""

    buildings: Any = None         # GeoDataFrame | None
    roads: Any = None             # GeoDataFrame | None
    pipelines: Any = None         # GeoDataFrame | None
    critical_nodes: Any = None    # GeoDataFrame | None
    n_features: int = 0
    query_time_s: float = 0.0
    warnings: list[str] = field(default_factory=list)


class OSMQuerier:
    """
    Queries OpenStreetMap via the Overpass API for critical infrastructure.

    Requires ``overpy`` and ``geopandas`` / ``shapely`` to be installed.
    On network errors the method returns an ``OSMAssets`` with ``warnings``
    instead of raising.
    """

    def __init__(
        self,
        overpass_url: str = OVERPASS_URL,
        timeout_s: int = _TIMEOUT_S,
    ) -> None:
        self._url = overpass_url
        self._timeout = timeout_s

    def query_assets(self, aoi: BBox) -> OSMAssets:
        """
        Fetch buildings, roads, pipelines and critical nodes within ``aoi``.

        Returns
        -------
        ``OSMAssets`` whose GeoDataFrame fields are populated on success.
        On timeout or connection error the GeoDataFrames are None and
        ``OSMAssets.warnings`` contains the error message.

        Raises
        ------
        ImportError
            If ``overpy`` or ``geopandas`` are not installed.
        """
        try:
            import overpy  # type: ignore[import]
            import geopandas as gpd  # type: ignore[import]
            from shapely.geometry import LineString, Point, Polygon  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "overpy and geopandas[shapely] are required for OSM queries. "
                f"Install with: pip install overpy geopandas shapely\n{exc}"
            ) from exc

        t0 = time.monotonic()
        warnings: list[str] = []
        query = self._build_query(aoi)

        try:
            api = overpy.Overpass(url=self._url)
            result = api.query(query)
        except Exception as exc:
            logger.warning("Overpass API query failed: %s", exc)
            return OSMAssets(warnings=[f"Overpass API error: {exc}"])

        # ---- Parse ways ----
        building_rows: list[dict] = []
        road_rows: list[dict] = []
        pipeline_rows: list[dict] = []

        for way in result.ways:
            try:
                coords = [
                    (float(n.lon), float(n.lat))  # (x, y) for Shapely
                    for n in way.nodes
                ]
            except Exception:
                continue
            if len(coords) < 2:
                continue

            name = way.tags.get("name", f"OSM way #{way.id}")
            tags = dict(way.tags)

            if "building" in tags or "landuse" in tags:
                is_closed = coords[0] == coords[-1]
                geom = (
                    Polygon(coords)
                    if (is_closed and len(coords) >= 4)
                    else LineString(coords)
                )
                building_rows.append({
                    "osm_id": way.id,
                    "name": name,
                    "building": tags.get("building", tags.get("landuse", "")),
                    "geometry": geom,
                })

            elif "highway" in tags:
                road_rows.append({
                    "osm_id": way.id,
                    "name": name,
                    "highway": tags.get("highway", ""),
                    "geometry": LineString(coords),
                })

            elif tags.get("man_made") == "pipeline":
                pipeline_rows.append({
                    "osm_id": way.id,
                    "name": name,
                    "geometry": LineString(coords),
                })

        # ---- Parse nodes ----
        critical_rows: list[dict] = []
        for node in result.nodes:
            try:
                geom = Point(float(node.lon), float(node.lat))
            except Exception:
                continue
            critical_rows.append({
                "osm_id": node.id,
                "name": node.tags.get("name", f"OSM node #{node.id}"),
                "amenity": node.tags.get("amenity", ""),
                "geometry": geom,
            })

        def _to_gdf(rows: list[dict]) -> Any:
            if not rows:
                return None
            return gpd.GeoDataFrame(rows, crs="EPSG:4326")

        buildings = _to_gdf(building_rows)
        roads = _to_gdf(road_rows)
        pipelines = _to_gdf(pipeline_rows)
        critical_nodes = _to_gdf(critical_rows)

        n_features = sum(
            len(gdf) for gdf in [buildings, roads, pipelines, critical_nodes]
            if gdf is not None
        )

        elapsed = time.monotonic() - t0
        logger.info(
            "OSM query complete: %d features in %.1f s (buildings=%d roads=%d "
            "pipelines=%d critical=%d)",
            n_features, elapsed,
            len(buildings) if buildings is not None else 0,
            len(roads) if roads is not None else 0,
            len(pipelines) if pipelines is not None else 0,
            len(critical_nodes) if critical_nodes is not None else 0,
        )

        return OSMAssets(
            buildings=buildings,
            roads=roads,
            pipelines=pipelines,
            critical_nodes=critical_nodes,
            n_features=n_features,
            query_time_s=elapsed,
            warnings=warnings,
        )

    def _build_query(self, aoi: BBox) -> str:
        """Build Overpass QL query string for the given bounding box."""
        return _QUERY_TEMPLATE.format(
            s=aoi.south,
            w=aoi.west,
            n=aoi.north,
            e=aoi.east,
            timeout=self._timeout,
        )
