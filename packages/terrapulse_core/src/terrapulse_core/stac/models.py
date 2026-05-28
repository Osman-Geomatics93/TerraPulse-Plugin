"""
Typed data models for Sentinel-1 STAC items and scene stacks.

Implementation note
-------------------
This module used to use `pydantic.BaseModel` for runtime validation, but pydantic
is not reliably importable inside QGIS's bundled Python (QGIS 3.44 ships a
pydantic_core that's incompatible with the bundled pydantic, producing
`ImportError: cannot import name 'validate_core_schema'`). Refactored to plain
`@dataclass` + explicit `__post_init__` validation. Same public API, no
runtime dependencies beyond the standard library.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

OrbitDirection = Literal["ascending", "descending"]
Polarisation = Literal["VV", "VH", "VV+VH", "HH", "HV"]
ProcessingLevel = Literal["L1", "L2"]


@dataclass
class BBox:
    """WGS-84 bounding box."""

    west: float
    south: float
    east: float
    north: float

    def __post_init__(self) -> None:
        if not -180 <= self.west <= 180:
            raise ValueError(f"west must be in [-180, 180], got {self.west}")
        if not -90 <= self.south <= 90:
            raise ValueError(f"south must be in [-90, 90], got {self.south}")
        if not -180 <= self.east <= 180:
            raise ValueError(f"east must be in [-180, 180], got {self.east}")
        if not -90 <= self.north <= 90:
            raise ValueError(f"north must be in [-90, 90], got {self.north}")
        if self.west >= self.east:
            raise ValueError("west must be < east")
        if self.south >= self.north:
            raise ValueError("south must be < north")

    @property
    def area_km2(self) -> float:
        """Approximate area in km² (equirectangular, midpoint-latitude correction)."""
        mid_lat_rad = math.radians((self.north + self.south) / 2)
        lat_km = (self.north - self.south) * 111.32
        lon_km = (self.east - self.west) * 111.32 * math.cos(mid_lat_rad)
        return lat_km * lon_km

    def as_list(self) -> list[float]:
        return [self.west, self.south, self.east, self.north]


@dataclass
class SentinelScene:
    """A single Sentinel-1 SLC scene from the STAC catalog."""

    scene_id: str
    datetime: datetime
    bbox: BBox
    orbit_direction: OrbitDirection
    relative_orbit: int
    polarisation: Polarisation
    processing_level: ProcessingLevel
    assets: dict[str, str] = field(default_factory=dict)
    estimated_size_bytes: int = 0


@dataclass
class SceneStack:
    """An ordered stack of Sentinel-1 scenes suitable for SBAS processing."""

    scenes: list[SentinelScene] = field(default_factory=list)
    aoi: BBox | None = None
    orbit_direction: OrbitDirection = "ascending"
    relative_orbit: int = 0
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


@dataclass
class ProcessingMode:
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
    cdse_password: str | None = None  # nosec B107
