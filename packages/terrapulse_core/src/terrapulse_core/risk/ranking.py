"""
Asset-at-risk ranking.

Spatially joins InSAR deformation outputs with OSM infrastructure assets
and produces a ranked list of at-risk assets with composite risk scores.

Risk score formula (0–10 scale):
    score = (w_vel * vel_norm  +  w_coh * incoherence) * 10 * type_weight

where:
    vel_norm    = min(|mean_velocity| / V_MAX, 1.0)   normalised velocity
    incoherence = 1 − coherence
    type_weight = per-asset-type multiplier (critical infra weighted higher)
    w_vel = 0.6,  w_coh = 0.4
    V_MAX = 50 mm/yr  (beyond this all assets score 10 on velocity alone)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

logger = logging.getLogger(__name__)

# Risk weight constants
_W_VEL = 0.6
_W_COH = 0.4
_V_MAX = 50.0  # mm/yr — velocity normalisation ceiling

# Asset-type importance multipliers
_TYPE_WEIGHTS: dict[str, float] = {
    "critical": 1.25,    # hospitals, schools, emergency services
    "pipeline": 1.15,    # energy / water infrastructure
    "building": 1.00,    # generic buildings
    "road": 0.90,        # roads (partially self-healing)
}


@dataclass
class AssetRisk:
    """Risk score for a single OSM asset."""

    osm_id: int
    asset_type: str          # "building", "road", "pipeline", "critical"
    name: str                # OSM name tag, or auto-generated ID
    mean_velocity: float     # mm/yr at asset location (negative = subsiding)
    max_velocity: float      # mm/yr worst pixel within asset footprint
    deformation_class: str   # from DeformationClass label
    coherence: float         # mean coherence over asset [0–1]
    risk_score: float        # composite 0–10 score
    geometry_wkt: str = ""   # WKT of asset geometry


@dataclass
class RankingResult:
    """Ranked list of at-risk assets."""

    assets: list[AssetRisk] = field(default_factory=list)
    n_total_assets: int = 0
    n_high_risk: int = 0     # risk_score > 7
    n_medium_risk: int = 0   # 4 < risk_score ≤ 7


class RiskRanker:
    """
    Spatial join + risk scoring of OSM assets against deformation rasters.

    Strategy
    --------
    1. Read velocity and coherence COGs with rasterio.
    2. For each asset: sample raster value at the asset centroid.
    3. Apply the risk formula to produce a score in [0, 10].
    4. Return assets sorted by risk score (descending).
    """

    def rank(
        self,
        velocity_cog: Path,
        coherence_cog: Path,
        osm_assets: Any,           # OSMAssets or None
        classification_raster: Path | None = None,
    ) -> RankingResult:
        """
        Score and rank OSM assets by deformation risk.

        Parameters
        ----------
        velocity_cog:
            COG with band 1 = LOS velocity in mm/yr.
        coherence_cog:
            COG with band 1 = temporal coherence [0, 1].
        osm_assets:
            ``OSMAssets`` instance from ``OSMQuerier.query_assets()``,
            or ``None`` (returns empty ``RankingResult``).
        classification_raster:
            Optional classification label COG from Phase 2 (not yet used
            in the formula — reserved for Phase 4 refinement).

        Returns
        -------
        ``RankingResult`` with assets sorted by descending risk score.
        """
        if osm_assets is None:
            logger.info("RiskRanker: osm_assets is None — returning empty result.")
            return RankingResult()

        # Collect (geometry, osm_id, name, asset_type) tuples
        asset_rows = list(_iter_asset_rows(osm_assets))
        if not asset_rows:
            logger.info("RiskRanker: no assets found in OSMAssets.")
            return RankingResult()

        # Read rasters once
        try:
            import rasterio  # type: ignore[import]
        except ImportError as exc:
            raise ImportError("rasterio is required for RiskRanker.rank()") from exc

        with rasterio.open(velocity_cog) as vel_ds:
            vel_arr = vel_ds.read(1).astype(np.float32)
            vel_transform = vel_ds.transform
            vel_nodata = float(vel_ds.nodata) if vel_ds.nodata is not None else None
            vel_shape = vel_arr.shape

        with rasterio.open(coherence_cog) as coh_ds:
            coh_arr = coh_ds.read(1).astype(np.float32)
            coh_nodata = float(coh_ds.nodata) if coh_ds.nodata is not None else None

        # Replace nodata with sensible defaults
        if vel_nodata is not None:
            vel_arr[np.isclose(vel_arr, vel_nodata)] = 0.0
        if coh_nodata is not None:
            coh_arr[np.isclose(coh_arr, coh_nodata)] = 0.5
        np.nan_to_num(vel_arr, nan=0.0, copy=False)
        np.nan_to_num(coh_arr, nan=0.5, copy=False)

        scored: list[AssetRisk] = []
        for geom, osm_id, name, asset_type in asset_rows:
            vel_val, coh_val = _sample_centroid(
                geom, vel_arr, vel_transform, vel_shape, coh_arr
            )
            risk = _compute_risk_score(vel_val, coh_val, asset_type)

            scored.append(AssetRisk(
                osm_id=osm_id,
                asset_type=asset_type,
                name=name,
                mean_velocity=round(vel_val, 2),
                max_velocity=round(vel_val, 2),   # centroid proxy (no polygon zonal stats)
                deformation_class="Unknown",
                coherence=round(float(coh_val), 3),
                risk_score=round(risk, 2),
                geometry_wkt=geom.wkt if hasattr(geom, "wkt") else "",
            ))

        scored.sort(key=lambda a: a.risk_score, reverse=True)

        n_high = sum(1 for a in scored if a.risk_score > 7)
        n_medium = sum(1 for a in scored if 4 < a.risk_score <= 7)

        logger.info(
            "RiskRanker: %d assets scored — %d high risk, %d medium risk",
            len(scored), n_high, n_medium,
        )
        return RankingResult(
            assets=scored,
            n_total_assets=len(scored),
            n_high_risk=n_high,
            n_medium_risk=n_medium,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _iter_asset_rows(
    osm_assets: Any,
) -> Iterator[tuple[Any, int, str, str]]:
    """
    Yield (geometry, osm_id, name, asset_type) for every row in every
    non-None GeoDataFrame inside ``osm_assets``.
    """
    _gdf_types = [
        ("buildings", "building"),
        ("roads", "road"),
        ("pipelines", "pipeline"),
        ("critical_nodes", "critical"),
    ]
    for attr, asset_type in _gdf_types:
        gdf = getattr(osm_assets, attr, None)
        if gdf is None:
            continue
        for _, row in gdf.iterrows():
            osm_id = int(row.get("osm_id", 0))
            name = str(row.get("name", f"Asset #{osm_id}"))
            geom = row.geometry
            yield geom, osm_id, name, asset_type


def _sample_centroid(
    geom: Any,
    vel_arr: np.ndarray,
    transform: Any,
    shape: tuple[int, int],
    coh_arr: np.ndarray,
) -> tuple[float, float]:
    """
    Sample velocity and coherence raster at the centroid of ``geom``.

    Returns ``(velocity_mm_yr, coherence)`` defaulting to ``(0.0, 0.5)``
    if the centroid falls outside the raster extent.
    """
    try:
        centroid = geom.centroid if hasattr(geom, "centroid") else geom
        x, y = centroid.x, centroid.y
        # rasterio inverse transform: (x, y) → (col, row)
        col, row = ~transform * (x, y)
        col, row = int(col), int(row)
        ny, nx = shape
        if 0 <= row < ny and 0 <= col < nx:
            vel = float(vel_arr[row, col])
            coh = float(coh_arr[row, col]) if coh_arr.shape == shape else 0.5
            return vel, max(0.0, min(1.0, coh))
    except Exception as exc:
        logger.debug("Centroid sampling failed for geometry: %s", exc)
    return 0.0, 0.5


def _compute_risk_score(
    velocity: float,
    coherence: float,
    asset_type: str,
) -> float:
    """
    Compute a composite risk score in [0, 10].

    Parameters
    ----------
    velocity:
        Velocity at asset centroid in mm/yr (negative = subsidence).
    coherence:
        Interferometric coherence at centroid [0, 1].
    asset_type:
        One of "building", "road", "pipeline", "critical".
    """
    vel_norm = min(abs(velocity) / _V_MAX, 1.0)
    incoherence = 1.0 - max(0.0, min(1.0, coherence))
    type_weight = _TYPE_WEIGHTS.get(asset_type, 1.0)
    raw = (_W_VEL * vel_norm + _W_COH * incoherence) * 10.0 * type_weight
    return min(10.0, max(0.0, raw))
