"""
STAC item writer.

Each completed TerraPulse run emits a STAC 1.0 item JSON that references
the output COGs and zarr store.  This makes outputs discoverable by any
STAC-compatible tool (QGIS, GeoServer, stac-browser, etc.).

STAC spec: https://stacspec.org/
pystac docs: https://pystac.readthedocs.io/
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from terrapulse_core.provenance.recipe import RunRecipe
    from terrapulse_core.stac.models import BBox

logger = logging.getLogger(__name__)

TERRAPULSE_STAC_EXTENSION = (
    "https://terrapulse.example.com/stac-extension/v0.1/schema.json"
)

# STAC MediaType constants (inline to avoid pystac version dependency)
_MEDIA_COG = "image/tiff; application=geotiff; profile=cloud-optimized"
_MEDIA_ZARR = "application/vnd+zarr"
_MEDIA_YAML = "text/yaml"
_MEDIA_HTML = "text/html"
_MEDIA_JSON = "application/json"


class STACItemWriter:
    """
    Writes a STAC 1.0 Item JSON for a completed TerraPulse run.

    Uses ``pystac`` if available; falls back to a hand-crafted dict if not.
    """

    def write(
        self,
        recipe: RunRecipe,
        aoi: BBox,
        output_dir: Path,
    ) -> Path:
        """
        Generate and write a STAC item JSON to ``output_dir``.

        The item includes the following assets:
        - ``velocity`` — LOS velocity COG (mm/yr)
        - ``coherence`` — temporal coherence COG [0, 1]
        - ``displacement`` — displacement time-series zarr
        - ``report_html`` — HTML report (if present)
        - ``recipe`` — YAML provenance file

        Custom properties (``terrapulse:`` namespace):
        - engine, mode, n_scenes, processing_time_s, terrapulse_version

        Parameters
        ----------
        recipe:
            Completed ``RunRecipe`` (status == "completed").
        aoi:
            AOI bounding box in WGS-84.
        output_dir:
            Directory where the STAC JSON is written.

        Returns
        -------
        Path to the written ``stac_{run_id[:8]}.json`` file.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        item_dict = self._build_item_dict(recipe, aoi, output_dir)

        json_path = output_dir / f"stac_{recipe.run_id[:8]}.json"
        json_path.write_text(
            json.dumps(item_dict, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("STAC item written: %s", json_path)
        return json_path

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_item_dict(
        self,
        recipe: RunRecipe,
        aoi: BBox,
        output_dir: Path,
    ) -> dict[str, Any]:
        """Build the STAC 1.0 item as a plain dict (no pystac dependency)."""
        now_iso = datetime.now(UTC).isoformat()

        # GeoJSON polygon for the AOI
        geometry = {
            "type": "Polygon",
            "coordinates": [[
                [aoi.west, aoi.south],
                [aoi.east, aoi.south],
                [aoi.east, aoi.north],
                [aoi.west, aoi.north],
                [aoi.west, aoi.south],
            ]],
        }
        bbox = [aoi.west, aoi.south, aoi.east, aoi.north]

        # Collect assets
        assets: dict[str, Any] = {}

        if recipe.velocity_cog:
            assets["velocity"] = {
                "href": str(recipe.velocity_cog),
                "type": _MEDIA_COG,
                "title": "LOS Velocity (mm/yr)",
                "roles": ["data"],
            }

        if recipe.coherence_cog:
            assets["coherence"] = {
                "href": str(recipe.coherence_cog),
                "type": _MEDIA_COG,
                "title": "Temporal Coherence",
                "roles": ["data"],
            }

        if recipe.displacement_zarr:
            assets["displacement"] = {
                "href": str(recipe.displacement_zarr),
                "type": _MEDIA_ZARR,
                "title": "Displacement Time-series",
                "roles": ["data"],
            }

        if recipe.report_html:
            assets["report_html"] = {
                "href": str(recipe.report_html),
                "type": _MEDIA_HTML,
                "title": "Deformation Analysis Report",
                "roles": ["overview"],
            }

        # Recipe YAML
        recipe_path = output_dir / f"recipe_{recipe.run_id}.yaml"
        if recipe_path.exists():
            assets["recipe"] = {
                "href": str(recipe_path),
                "type": _MEDIA_YAML,
                "title": "Processing Provenance (YAML)",
                "roles": ["metadata"],
            }

        # Item datetime: use completed_at or created_at
        item_dt = recipe.completed_at or recipe.created_at or now_iso

        properties: dict[str, Any] = {
            "datetime": item_dt,
            "created": recipe.created_at or now_iso,
            "updated": now_iso,
            "terrapulse:engine": recipe.engine,
            "terrapulse:mode": recipe.mode,
            "terrapulse:n_scenes": len(recipe.scenes) if recipe.scenes else 0,
            "terrapulse:aoi_wkt": recipe.aoi_wkt,
            "terrapulse:start_date": recipe.start_date,
            "terrapulse:end_date": recipe.end_date,
            "terrapulse:version": recipe.terrapulse_version or "0.1.0",
        }

        if recipe.warnings:
            properties["terrapulse:warnings"] = recipe.warnings

        return {
            "type": "Feature",
            "stac_version": "1.0.0",
            "stac_extensions": [TERRAPULSE_STAC_EXTENSION],
            "id": f"terrapulse_{recipe.run_id}",
            "geometry": geometry,
            "bbox": bbox,
            "properties": properties,
            "assets": assets,
            "links": [],
        }
