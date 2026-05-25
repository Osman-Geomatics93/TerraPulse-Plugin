"""
Feature extraction for the deformation classifier.

Input:  xarray DataArrays (or NumPy arrays) — displacement time-series, coherence, DEM
Output: NumPy feature matrix (n_valid_pixels × 6)

Features extracted per pixel (all are per-pixel scalar summaries):
  0  mean_velocity_mm_yr   — linear trend slope of displacement time-series (mm/yr)
  1  velocity_trend        — quadratic acceleration coefficient (mm/yr²)
  2  seasonal_amplitude    — peak-to-trough of annual sinusoid fit (mm)
  3  mean_coherence        — temporal mean coherence [0–1]
  4  dem_slope_deg         — terrain slope in degrees [0–90]
  5  dem_aspect_norm       — normalised aspect [0–1]  (0=N, 0.5=S)

All five features are computed simultaneously via a single vectorised
``numpy.linalg.lstsq`` call across all valid pixels.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import xarray as xr

logger = logging.getLogger(__name__)

N_FEATURES = 6
FEATURE_NAMES = [
    "mean_velocity_mm_yr",
    "velocity_trend",
    "seasonal_amplitude",
    "mean_coherence",
    "dem_slope_deg",
    "dem_aspect_norm",
]


def extract_features(
    velocity_ts: xr.DataArray | np.ndarray,  # (time, y, x) displacement in mm
    coherence: xr.DataArray | np.ndarray,    # (y, x) coherence [0, 1]
    dem_slope: xr.DataArray | np.ndarray,    # (y, x) slope in degrees
    dem_aspect: xr.DataArray | np.ndarray,   # (y, x) aspect in degrees [0, 360)
) -> np.ndarray:
    """
    Extract the 6-feature matrix from InSAR outputs.

    Accepts both ``xarray.DataArray`` (with optional ``time`` coordinate) and
    plain NumPy arrays.  When a DataArray with a ``datetime64`` time coordinate
    is supplied the true acquisition dates are used; otherwise monthly spacing
    is assumed.

    Returns
    -------
    np.ndarray of shape ``(n_valid_pixels, 6)`` with dtype ``float32``.
    Pixels where coherence, slope, aspect, or any displacement time step is NaN
    are excluded from the output.

    Raises
    ------
    ValueError
        If ``velocity_ts`` is not 3-D, spatial dimensions are inconsistent,
        fewer than 4 time steps are available, or all pixels are NaN-masked.
    """
    # ------------------------------------------------------------------
    # 1. Convert to numpy
    # ------------------------------------------------------------------
    vel_arr = np.asarray(velocity_ts, dtype=np.float64)        # (n_time, ny, nx)
    coh_arr = np.asarray(coherence, dtype=np.float64)          # (ny, nx)
    slope_arr = np.asarray(dem_slope, dtype=np.float64)        # (ny, nx)
    aspect_arr = np.asarray(dem_aspect, dtype=np.float64)      # (ny, nx)

    # ------------------------------------------------------------------
    # 2. Validate shapes
    # ------------------------------------------------------------------
    if vel_arr.ndim != 3:
        raise ValueError(
            f"velocity_ts must be 3-D (time, y, x), got {vel_arr.ndim}D "
            f"with shape {vel_arr.shape}"
        )
    n_time, ny, nx = vel_arr.shape

    for name, arr in [("coherence", coh_arr), ("dem_slope", slope_arr), ("dem_aspect", aspect_arr)]:
        if arr.shape != (ny, nx):
            raise ValueError(
                f"{name} shape {arr.shape} does not match velocity_ts spatial "
                f"dimensions ({ny}, {nx})"
            )

    if n_time < 4:
        raise ValueError(
            f"At least 4 time steps are required for feature extraction, got {n_time}. "
            "Use a longer time series or reduce the number of interferograms."
        )

    # ------------------------------------------------------------------
    # 3. Build time axis in decimal years
    # ------------------------------------------------------------------
    times: np.ndarray | None = None
    try:
        t_vals = velocity_ts.time.values  # type: ignore[union-attr]
        t_ns = t_vals.astype("datetime64[ns]").astype(np.float64)
        t0 = t_ns[0]
        times = (t_ns - t0) / (365.25 * 24.0 * 3600.0 * 1e9)
    except (AttributeError, TypeError, ValueError):
        pass

    if times is None:
        # No xarray time coordinate — assume monthly spacing
        times = np.arange(n_time, dtype=np.float64) / 12.0

    # ------------------------------------------------------------------
    # 4. Flatten and mask
    # ------------------------------------------------------------------
    vel_2d = vel_arr.reshape(n_time, -1)   # (n_time, n_pix)
    coh_1d = coh_arr.ravel()
    slope_1d = slope_arr.ravel()
    aspect_1d = aspect_arr.ravel()

    valid: np.ndarray = (
        ~np.isnan(coh_1d)
        & ~np.any(np.isnan(vel_2d), axis=0)
        & ~np.isnan(slope_1d)
        & ~np.isnan(aspect_1d)
    )

    if not np.any(valid):
        raise ValueError(
            "All pixels are NaN-masked. Verify that coherence, slope, aspect, "
            "and displacement arrays contain valid (non-NaN) data."
        )

    idx = np.where(valid)[0]
    n_valid = int(idx.size)
    vel_valid = vel_2d[:, idx]  # (n_time, n_valid)

    # ------------------------------------------------------------------
    # 5. Vectorised least-squares fit — all pixels simultaneously
    #
    # Model:  d(t) = v·t  +  acc·t²  +  a·sin(2πt)  +  b·cos(2πt)  +  c
    # Column indices in X:
    #   0 → v    (linear velocity, mm/yr)
    #   1 → acc  (quadratic acceleration, mm/yr²)
    #   2 → a    (seasonal sin coefficient)
    #   3 → b    (seasonal cos coefficient)
    #   4 → c    (constant offset)
    # ------------------------------------------------------------------
    t = times
    A = np.column_stack([
        t,
        t ** 2,
        np.sin(2.0 * np.pi * t),
        np.cos(2.0 * np.pi * t),
        np.ones(n_time),
    ])  # (n_time, 5)

    # Solve for all valid pixels in one call: X shape (5, n_valid)
    X, *_ = np.linalg.lstsq(A, vel_valid, rcond=None)

    # ------------------------------------------------------------------
    # 6. Assemble feature matrix
    # ------------------------------------------------------------------
    features = np.empty((n_valid, N_FEATURES), dtype=np.float32)

    features[:, 0] = X[0].astype(np.float32)                             # mean_velocity_mm_yr
    features[:, 1] = X[1].astype(np.float32)                             # velocity_trend (mm/yr²)
    features[:, 2] = np.sqrt(X[2] ** 2 + X[3] ** 2).astype(np.float32)  # seasonal_amplitude
    features[:, 3] = coh_1d[idx].astype(np.float32)                      # mean_coherence
    features[:, 4] = np.clip(slope_1d[idx], 0.0, 90.0).astype(np.float32)  # dem_slope_deg
    features[:, 5] = (aspect_1d[idx] % 360.0 / 360.0).astype(np.float32)   # dem_aspect_norm

    logger.info(
        "extract_features: %d valid pixels / %d total (%.1f%% valid), "
        "time span = %.2f yr",
        n_valid, ny * nx, 100.0 * n_valid / (ny * nx), float(t[-1] - t[0]),
    )
    return features


def pixel_valid_mask(
    velocity_ts: xr.DataArray | np.ndarray,
    coherence: xr.DataArray | np.ndarray,
    dem_slope: xr.DataArray | np.ndarray,
    dem_aspect: xr.DataArray | np.ndarray,
) -> np.ndarray:
    """
    Return the flat boolean mask of valid pixels used by ``extract_features``.

    The mask has shape ``(ny * nx,)`` and ``True`` where a pixel is valid
    (no NaN in coherence, slope, aspect, or any displacement time step).

    This mask is needed by ``uncertainty_to_raster`` to reconstruct 2-D arrays
    from the flat ML output vectors.
    """
    vel_arr = np.asarray(velocity_ts, dtype=np.float64)
    coh_arr = np.asarray(coherence, dtype=np.float64).ravel()
    slope_arr = np.asarray(dem_slope, dtype=np.float64).ravel()
    aspect_arr = np.asarray(dem_aspect, dtype=np.float64).ravel()

    if vel_arr.ndim != 3:
        raise ValueError(f"velocity_ts must be 3-D, got {vel_arr.ndim}D")

    vel_2d = vel_arr.reshape(vel_arr.shape[0], -1)
    return (
        ~np.isnan(coh_arr)
        & ~np.any(np.isnan(vel_2d), axis=0)
        & ~np.isnan(slope_arr)
        & ~np.isnan(aspect_arr)
    )


def _fit_seasonal(
    times: np.ndarray,  # decimal years
    values: np.ndarray,  # mm, shape (n_times,)
) -> float:
    """
    Fit a sinusoid with 1-year period and return amplitude.
    Uses least-squares fit: v(t) = a·sin(2πt) + b·cos(2πt) + c + d·t
    """
    n = len(times)
    if n < 4:
        return 0.0
    A = np.column_stack([
        np.sin(2 * np.pi * times),
        np.cos(2 * np.pi * times),
        np.ones(n),
        times,
    ])
    try:
        coeffs, *_ = np.linalg.lstsq(A, values, rcond=None)
        amplitude = float(np.sqrt(coeffs[0] ** 2 + coeffs[1] ** 2))
    except np.linalg.LinAlgError:
        amplitude = 0.0
    return amplitude
