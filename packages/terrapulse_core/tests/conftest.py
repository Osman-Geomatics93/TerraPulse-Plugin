"""
Pytest fixtures for terrapulse_core tests.

Key fixture: ``synthetic_sar_stack`` — provides a fake (but structurally
correct) SAR displacement time-series as xarray DataArrays, along with
fake coherence, DEM slope/aspect, and a BBox. No real SAR data needed.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# PROJ database fix — must happen at module-import time, BEFORE rasterio is
# first imported anywhere.  PostgreSQL/PostGIS ships an outdated proj.db
# (DATABASE.LAYOUT.VERSION.MINOR < 5) that breaks rasterio's CRS resolution
# on Windows machines where PostGIS is installed.
# ---------------------------------------------------------------------------
import importlib.util as _ilu
import os as _os
import pathlib as _pathlib

try:
    _spec = _ilu.find_spec("rasterio")
    if _spec and _spec.origin:
        _proj_data_dir = _pathlib.Path(_spec.origin).parent / "proj_data"
        if (_proj_data_dir / "proj.db").exists():
            _proj_data_str = str(_proj_data_dir)
            _os.environ.setdefault("PROJ_DATA", _proj_data_str)
            _os.environ.setdefault("PROJ_LIB", _proj_data_str)
except Exception:
    pass

# ---------------------------------------------------------------------------

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from terrapulse_core.stac.models import BBox, SentinelScene, SceneStack
from terrapulse_core.provenance.recipe import RunRecipe


# ---------------------------------------------------------------------------
# Geometric fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def cairo_bbox() -> BBox:
    """AOI centred on Cairo, Egypt — ~50×50 km."""
    return BBox(west=30.8, south=29.8, east=31.6, north=30.4)


@pytest.fixture()
def small_bbox() -> BBox:
    """Small 10×10 km test AOI."""
    return BBox(west=10.0, south=10.0, east=10.1, north=10.1)


# ---------------------------------------------------------------------------
# STAC / scene stack fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sentinel_scene(cairo_bbox: BBox) -> SentinelScene:
    """A single synthetic Sentinel-1 SLC scene."""
    return SentinelScene(
        scene_id="S1A_IW_SLC__1SDV_20230101T040000_00001",
        datetime=datetime(2023, 1, 1, 4, 0, 0, tzinfo=timezone.utc),
        bbox=cairo_bbox,
        orbit_direction="ascending",
        relative_orbit=87,
        polarisation="VV",
        processing_level="L1",
        assets={"PRODUCT": "https://example.cdse.eu/fake_slc.zip"},
        estimated_size_bytes=4_000_000_000,
    )


@pytest.fixture()
def scene_stack(cairo_bbox: BBox) -> SceneStack:
    """A synthetic 12-scene SBAS-compatible stack (24-day intervals, 1 year)."""
    scenes: list[SentinelScene] = []
    base_date = datetime(2023, 1, 1, tzinfo=timezone.utc)
    for i in range(12):
        dt = base_date + timedelta(days=i * 24)
        scenes.append(
            SentinelScene(
                scene_id=f"S1A_IW_SLC__1SDV_{dt.strftime('%Y%m%d')}T040000_{i:05d}",
                datetime=dt,
                bbox=cairo_bbox,
                orbit_direction="ascending",
                relative_orbit=87,
                polarisation="VV",
                processing_level="L1",
                assets={"PRODUCT": f"https://example.cdse.eu/slc_{i:03d}.zip"},
                estimated_size_bytes=4_000_000_000,
            )
        )
    return SceneStack(
        scenes=scenes,
        aoi=cairo_bbox,
        orbit_direction="ascending",
        relative_orbit=87,
        total_size_bytes=12 * 4_000_000_000,
    )


# ---------------------------------------------------------------------------
# Synthetic SAR data fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def synthetic_sar_stack() -> dict[str, np.ndarray]:
    """
    Synthetic InSAR outputs as NumPy arrays.

    Shape: (n_times=12, height=64, width=64)
    Units: mm displacement

    Contains a mix of:
    - Stable background pixels (noise only)
    - Linear subsidence patch (bottom-left quadrant)
    - Seasonal deformation patch (top-right quadrant)
    - Low-coherence patch (top-left quadrant)
    """
    rng = np.random.default_rng(seed=42)
    n_t, h, w = 12, 64, 64
    times = np.linspace(0, 1.0, n_t)  # decimal years

    # Base: noise
    disp = rng.normal(0, 2.0, size=(n_t, h, w)).astype(np.float32)

    # Linear subsidence (bottom-left quadrant): -15 mm/yr
    for i, t in enumerate(times):
        disp[i, h // 2 :, : w // 2] += -15.0 * t

    # Seasonal (top-right quadrant): 10 mm amplitude annual sinusoid
    for i, t in enumerate(times):
        disp[i, : h // 2, w // 2 :] += 10.0 * np.sin(2 * np.pi * t)

    # Coherence
    coherence = np.full((h, w), 0.7, dtype=np.float32)
    coherence[: h // 2, : w // 2] = 0.15  # low-coherence patch
    coherence += rng.normal(0, 0.05, size=(h, w)).astype(np.float32)
    coherence = np.clip(coherence, 0.0, 1.0)

    # DEM slope (degrees)
    dem_slope = np.abs(rng.normal(5.0, 3.0, size=(h, w))).astype(np.float32)

    # DEM aspect (degrees, 0–360)
    dem_aspect = rng.uniform(0, 360, size=(h, w)).astype(np.float32)

    # Velocity (mm/yr) — mean of displacement / time
    velocity = np.polyfit(times, disp.reshape(n_t, -1), deg=1)[0].reshape(h, w).astype(np.float32)

    return {
        "displacement": disp,       # (n_t, h, w)  mm
        "velocity": velocity,       # (h, w)        mm/yr
        "coherence": coherence,     # (h, w)        [0, 1]
        "dem_slope": dem_slope,     # (h, w)        degrees
        "dem_aspect": dem_aspect,   # (h, w)        degrees
        "times": times,             # (n_t,)        decimal years
    }


# ---------------------------------------------------------------------------
# Provenance fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def run_recipe() -> RunRecipe:
    """A synthetic RunRecipe in 'planned' state."""
    return RunRecipe(
        run_id=str(uuid.uuid4()),
        status="planned",
        aoi_wkt="POLYGON((30.8 29.8, 31.6 29.8, 31.6 30.4, 30.8 30.4, 30.8 29.8))",
        start_date="2023-01-01",
        end_date="2023-12-31",
        engine="pygmtsar",
        mode="standard",
        terrapulse_version="0.1.0",
    )


@pytest.fixture()
def tmp_output_dir(tmp_path: Path) -> Path:
    """Temporary directory for test outputs."""
    out = tmp_path / "terrapulse_test_run"
    out.mkdir()
    return out
