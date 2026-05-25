"""
PyGMTSAR-based InSAR engine.

Runs inside the Docker container (Dockerfile.pygmtsar).
Never imported directly in the QGIS plugin process.

PyGMTSAR docs: https://github.com/AlexeyPechnikov/pygmtsar
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Literal

from terrapulse_core.insar.base import (
    BaseInSAREngine,
    EngineResult,
    ProcessingProgress,
    ProgressCallback,
)

logger = logging.getLogger(__name__)


class PyGMTSAREngine(BaseInSAREngine):
    """
    Full SLC → SBAS displacement pipeline using PyGMTSAR.

    PyGMTSAR wraps GMTSAR + SNAPHU in a Python interface.
    Requires: GMTSAR installed, SNAPHU on PATH, PyGMTSAR package.
    """

    name = "pygmtsar"

    def is_available(self) -> bool:
        try:
            import pygmtsar  # noqa: F401  # type: ignore[import]

            return True
        except ImportError:
            logger.warning("PyGMTSAR not installed — engine unavailable.")
            return False

    def run(
        self,
        scene_paths: list[Path],
        output_dir: Path,
        aoi_wkt: str,
        mode: Literal["quick", "standard", "high_precision"] = "standard",
        progress_cb: ProgressCallback | None = None,
    ) -> EngineResult:
        start_time = time.monotonic()
        output_dir.mkdir(parents=True, exist_ok=True)
        result = EngineResult(output_dir=output_dir)

        def _emit(step: str, pct: float, msg: str = "") -> None:  # type: ignore[type-arg]
            if progress_cb is not None:
                progress_cb(
                    ProcessingProgress(
                        step=step,  # type: ignore[arg-type]
                        percent=pct,
                        message=msg,
                        total_scenes=len(scene_paths),
                    )
                )

        try:
            if not self.is_available():
                raise RuntimeError("PyGMTSAR is not installed in this environment.")

            import pygmtsar  # type: ignore[import]

            _emit("download", 5.0, f"Preparing {len(scene_paths)} SLC scenes")
            logger.info("PyGMTSAR engine: processing %d scenes", len(scene_paths))

            # --- coregistration ---
            _emit("coregistration", 15.0, "Coregistering SLC stack")
            sbas = pygmtsar.SBAS(
                [str(p) for p in scene_paths],
                basedir=str(output_dir),
            )

            # Mode-specific settings
            looks_rng = {"quick": 8, "standard": 4, "high_precision": 2}[mode]
            looks_azi = {"quick": 4, "standard": 2, "high_precision": 1}[mode]

            sbas.open_scenes(aoi_wkt=aoi_wkt)
            _emit("coregistration", 30.0, "SLC stack opened")

            # --- interferogram formation ---
            _emit("interferogram", 40.0, "Computing interferograms")
            sbas.compute_interferograms(looks_rng=looks_rng, looks_azi=looks_azi)

            # --- filtering ---
            _emit("filtering", 55.0, "Goldstein filtering")
            sbas.goldstein_filter()

            # --- phase unwrapping ---
            _emit("unwrapping", 65.0, "SNAPHU unwrapping")
            sbas.unwrap()

            # --- SBAS inversion ---
            _emit("sbas_inversion", 78.0, "SBAS time-series inversion")
            sbas.invert_sbas()

            # --- geocoding ---
            _emit("geocoding", 90.0, "Geocoding to WGS-84")
            vel_path = output_dir / "velocity.tif"
            coh_path = output_dir / "coherence.tif"
            disp_path = output_dir / "displacement.zarr"

            sbas.export_velocity(str(vel_path), driver="COG")
            sbas.export_coherence(str(coh_path), driver="COG")
            sbas.export_displacement_zarr(str(disp_path))

            elapsed = time.monotonic() - start_time
            result.velocity_cog = vel_path
            result.coherence_cog = coh_path
            result.displacement_stack = disp_path
            result.success = True
            result.n_scenes_processed = len(scene_paths)
            result.processing_time_seconds = elapsed

            _emit("done", 100.0, f"Processing complete in {elapsed/60:.1f} min")
            logger.info("PyGMTSAR engine finished in %.1f s", elapsed)

        except Exception as exc:
            logger.exception("PyGMTSAR engine failed")
            result.success = False
            result.error_message = str(exc)
            if progress_cb is not None:
                progress_cb(
                    ProcessingProgress(step="error", percent=0.0, message=str(exc))
                )

        return result

    def estimate_runtime_minutes(
        self,
        n_scenes: int,
        aoi_km2: float,
        mode: Literal["quick", "standard", "high_precision"],
    ) -> float:
        """
        Empirical estimate based on PyGMTSAR benchmarks.
        Very rough: depends heavily on hardware and network.
        """
        base_per_scene = {"quick": 3.0, "standard": 8.0, "high_precision": 20.0}[mode]
        area_factor = max(1.0, aoi_km2 / 2500.0)
        return base_per_scene * n_scenes * area_factor
