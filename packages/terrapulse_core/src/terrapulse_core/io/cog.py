"""
Cloud-Optimised GeoTIFF (COG) read/write helpers.

Write: converts a NumPy array to a GDAL COG GeoTIFF (DEFLATE-compressed,
       tiled 512×512, overviews pre-built).
Read:  streams a spatial window from a local or remote COG without loading
       the full file into memory.

Both local paths and remote URLs (via GDAL /vsicurl/) are supported.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.windows import from_bounds as window_from_bounds

if TYPE_CHECKING:
    from affine import Affine  # installed with rasterio

logger = logging.getLogger(__name__)

# COG creation options for the GDAL COG driver
# These produce fully valid COGs validated by cogeo-mosaic / gdal_translate -of COG
COG_CREATION_OPTIONS: dict[str, str] = {
    "compress": "DEFLATE",
    "predictor": "2",          # horizontal differencing — good for continuous data
    "zlevel": "6",
    "blockxsize": "512",
    "blockysize": "512",
    "tiled": "YES",
    "interleave": "band",
}

# Overview levels to build (4 levels: 2, 4, 8, 16)
_OVERVIEW_LEVELS = [2, 4, 8, 16]
_OVERVIEW_RESAMPLING = Resampling.average


class COGWriteError(Exception):
    """Raised on COG write failure."""


class COGWriter:
    """
    Write a NumPy array to a Cloud-Optimised GeoTIFF.

    Strategy (GDAL best practice):
    1. Write data to a temporary plain GTiff.
    2. Build overviews on the temp file.
    3. Copy to final path with ``driver="COG"`` + creation options.

    This avoids the GDAL COG driver's limitation that overviews must exist
    before the COG is created when ``copy_src_overviews=YES``.
    """

    def write(
        self,
        data: np.ndarray,
        output_path: Path,
        crs: str | CRS,
        transform: Affine,
        nodata: float | None = None,
        band_names: list[str] | None = None,
    ) -> Path:
        """
        Write ``data`` as a COG GeoTIFF to ``output_path``.

        Parameters
        ----------
        data:
            Shape ``(bands, height, width)`` or ``(height, width)`` (auto-expanded).
        output_path:
            Destination path. Parent directory is created if needed.
        crs:
            CRS as EPSG string (e.g. ``"EPSG:4326"``) or rasterio CRS object.
        transform:
            Affine geotransform (top-left corner + pixel size).
        nodata:
            NoData fill value. None means no NoData mask.
        band_names:
            List of band descriptions (optional). Length must match ``data.shape[0]``.

        Returns
        -------
        Path to the written COG file.

        Raises
        ------
        COGWriteError
            If the data shape is invalid or rasterio write fails.
        """
        # Normalise to 3-D (bands, height, width)
        if data.ndim == 2:
            data = data[np.newaxis, ...]
        elif data.ndim != 3:
            raise COGWriteError(
                f"data must be 2-D (h, w) or 3-D (bands, h, w), got shape {data.shape}"
            )

        n_bands, height, width = data.shape
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if isinstance(crs, str):
            crs = CRS.from_string(crs)

        dtype = data.dtype
        if dtype == np.float64:
            data = data.astype(np.float32)
            dtype = np.float32

        tmp_path = output_path.with_suffix(".tmp.tif")

        try:
            # Step 1: write plain GTiff
            with rasterio.open(
                tmp_path,
                mode="w",
                driver="GTiff",
                height=height,
                width=width,
                count=n_bands,
                dtype=dtype,
                crs=crs,
                transform=transform,
                nodata=nodata,
            ) as tmp_ds:
                tmp_ds.write(data)
                if band_names:
                    for i, name in enumerate(band_names[:n_bands], start=1):
                        tmp_ds.update_tags(i, name=name)

            # Step 2: build overviews on the temp file
            with rasterio.open(tmp_path, "r+") as tmp_ds:
                tmp_ds.build_overviews(_OVERVIEW_LEVELS, _OVERVIEW_RESAMPLING)
                tmp_ds.update_tags(ns="rio_overview", resampling=_OVERVIEW_RESAMPLING.name)

            # Step 3: copy to COG using GDAL COG driver
            with rasterio.open(tmp_path, "r") as src:
                profile = src.profile.copy()
                profile.update(
                    driver="COG",
                    **COG_CREATION_OPTIONS,
                    copy_src_overviews=True,
                )
                with rasterio.open(output_path, "w", **profile) as dst:
                    dst.write(src.read())
                    dst.update_tags(**src.tags())

        finally:
            if tmp_path.exists():
                tmp_path.unlink()

        logger.info(
            "COG written: %s (%d bands, %dx%d, %s)",
            output_path.name, n_bands, width, height, crs.to_epsg(),
        )
        return output_path


def read_cog_window(
    cog_path_or_url: str | Path,
    bbox_wgs84: tuple[float, float, float, float],
    target_crs: str = "EPSG:4326",
    overview_level: int | None = None,
    bands: list[int] | None = None,
) -> tuple[np.ndarray, Affine]:
    """
    Stream a spatial window from a COG without loading the full file.

    Parameters
    ----------
    cog_path_or_url:
        Local path or GDAL VSI URL (e.g. ``/vsicurl/https://example.com/vel.tif``).
    bbox_wgs84:
        ``(west, south, east, north)`` in WGS-84 degrees.
    target_crs:
        Target CRS for the window. Must match the file CRS or reprojection
        is needed (use rasterio.warp for that; not done here).
    overview_level:
        0 = full resolution; 1 = first overview; None = auto-select based on
        bbox area relative to full extent.
    bands:
        List of 1-based band indices to read. None = all bands.

    Returns
    -------
    ``(data_array, window_transform)``
      data_array: shape ``(n_bands, height, width)``, float32
      window_transform: affine transform of the returned data window

    Raises
    ------
    rasterio.errors.RasterioIOError
        If the file/URL is not accessible.
    ValueError
        If the bbox does not intersect the raster extent.
    """
    path_str = str(cog_path_or_url)

    # Prepend /vsicurl/ for remote http(s) URLs if not already done
    if path_str.startswith("http://") or path_str.startswith("https://"):
        path_str = f"/vsicurl/{path_str}"

    west, south, east, north = bbox_wgs84

    with rasterio.open(path_str) as ds:
        file_crs = ds.crs
        request_crs = CRS.from_string(target_crs)

        # Reproject bbox to file CRS if necessary
        if file_crs != request_crs:
            from rasterio.warp import transform_bounds

            west, south, east, north = transform_bounds(
                request_crs, file_crs, west, south, east, north
            )

        # Check for intersection
        bounds = ds.bounds
        if east <= bounds.left or west >= bounds.right or north <= bounds.bottom or south >= bounds.top:
            raise ValueError(
                f"Requested bbox ({west:.4f},{south:.4f},{east:.4f},{north:.4f}) "
                f"does not intersect raster extent ({bounds})."
            )

        # Clamp to raster extent
        w = max(west, bounds.left)
        s = max(south, bounds.bottom)
        e = min(east, bounds.right)
        n = min(north, bounds.top)

        # Select overview level
        if overview_level is None:
            overview_level = _auto_overview(ds, w, s, e, n)

        # Build the read window
        window = window_from_bounds(w, s, e, n, ds.transform)
        band_indices = bands or list(range(1, ds.count + 1))

        if overview_level == 0 or not ds.overviews(1):
            data = ds.read(
                indexes=band_indices,
                window=window,
                out_dtype=np.float32,
            )
            win_transform = ds.window_transform(window)
        else:
            # Read from overview
            ovr_idx = min(overview_level - 1, len(ds.overviews(1)) - 1)
            ovr_window = window.scale(
                1 / (2 ** (ovr_idx + 1)), 1 / (2 ** (ovr_idx + 1))
            )
            data = ds.read(
                indexes=band_indices,
                window=ovr_window,
                out_dtype=np.float32,
                overview_level=ovr_idx,
            )
            win_transform = ds.window_transform(window)

    logger.debug(
        "COG window read: %s  bands=%s  shape=%s  overview=%s",
        Path(path_str).name, band_indices, data.shape, overview_level,
    )
    return data, win_transform


def _auto_overview(
    ds: rasterio.DatasetReader,
    west: float,
    south: float,
    east: float,
    north: float,
) -> int:
    """
    Auto-select overview level so the returned window is ≤ 2048×2048 pixels.
    Returns 0 for full resolution, 1 for first overview, etc.
    """
    overviews = ds.overviews(1)
    if not overviews:
        return 0

    # Estimate number of pixels at full resolution
    window = window_from_bounds(west, south, east, north, ds.transform)
    h = int(abs(window.height))
    w = int(abs(window.width))

    for level, factor in enumerate(overviews, start=1):
        if h / factor <= 2048 and w / factor <= 2048:
            return level
    return len(overviews)  # use coarsest overview
