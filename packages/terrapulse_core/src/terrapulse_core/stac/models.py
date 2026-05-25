"""Typed data models for Sentinel-1 STAC items and scene stacks."""

from __future__ import annotations

import math
from datetime import datetime  # noqa: TC003 — Pydantic resolves field types at runtime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class BBox(BaseModel):
    """WGS-84 bounding box."""

    west: float = Field(..., ge=-180, le=180)
    south: float = Field(..., ge=-90, le=90)
    east: float = Field(..., ge=-180, le=180)
    north: float = Field(..., ge=-90, le=90)

    @model_validator(mode="after")
    def _check_bounds(self) -> BBox:
        if self.west >= self.east:
            raise ValueError("west must be < east")
        if self.south >= self.north:
            raise ValueError("south must be < north")
        return self

    @property
    def area_km2(self) -> float:
        """Approximate area in km² (equirectangular, midpoint-latitude correction)."""
        mid_lat_rad = math.radians((self.north + self.south) / 2)
        lat_km = (self.north - self.south) * 111.32
        lon_km = (self.east - self.west) * 111.32 * math.cos(mid_lat_rad)
        return lat_km * lon_km

    def as_list(self) -> list[float]:
        return [self.west, self.south, self.east, self.north]


class SentinelScene(BaseModel):
    """A single Sentinel-1 SLC scene from the STAC catalog."""

    scene_id: str
    datetime: datetime
    bbox: BBox
    orbit_direction: Literal["ascending", "descending"]
    relative_orbit: int
    polarisation: Literal["VV", "VH", "VV+VH", "HH", "HV"]
    processing_level: Literal["L1", "L2"]
    assets: dict[str, str] = Field(
        default_factory=dict,
        description="Asset key → download URL (e.g. 'PRODUCT' → SAFE zip href)",
    )
    estimated_size_bytes: int = Field(
        default=0,
        description="Estimated download size; 0 means unknown",
    )


class SceneStack(BaseModel):
    """An ordered stack of Sentinel-1 scenes suitable for SBAS processing."""

    scenes: list[SentinelScene] = Field(default_factory=list)
    aoi: BBox
    orbit_direction: Literal["ascending", "descending"]
    relative_orbit: int
    total_size_bytes: int = 0

    @property
    def n_scenes(self) -> int:
        return len(self.scenes)

    @property
    def time_span_days(self) -> float:
        if self.n_scenes < 2:
            return 0.0
        dates = sorted(s.datetime for s in self.scenes)
        return (dates[-1] - dates[0]).days

    def estimate_total_size_gb(self) -> float:
        return self.total_size_bytes / 1e9


class ProcessingMode(BaseModel):
    """User-facing processing configuration."""

    mode: Literal["quick", "standard", "high_precision"] = "standard"
    engine: Literal["pygmtsar", "mintpy", "openeo"] = "pygmtsar"
    max_aoi_km2: float = 2500.0  # 50×50 km default cap
    max_scenes: int = 30
    time_window_days: int = 365
    output_dir: str = ""
    anthropic_api_key: str | None = None
    openeo_token: str | None = None
    cdse_username: str | None = None
    cdse_password: str | None = None
