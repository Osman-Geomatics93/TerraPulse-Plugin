"""
MintPy-based InSAR engine (Phase 1 fallback / Phase 2 alternative).

MintPy handles the time-series analysis step only — it expects pre-formed
interferograms as input (from ISCE2, SNAP, or GAMMA preprocessing).

MintPy docs: https://mintpy.readthedocs.io/
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from terrapulse_core.insar.base import (
    BaseInSAREngine,
    EngineResult,
    ProcessingProgress,
    ProgressCallback,
)

logger = logging.getLogger(__name__)


class MintPyEngine(BaseInSAREngine):
    """
    MintPy time-series analysis engine.

    NOTE: MintPy does NOT do preprocessing (SLC → interferograms).
    Use this engine only when you already have a directory of
    unwrapped interferograms + coherence files (ISCE2 / SNAP output).

    Expected input layout (ISCE2 convention)::

        inputs/
            ifgramStack.h5
            geometryRadar.h5
    """

    name = "mintpy"

    def is_available(self) -> bool:
        try:
            import mintpy  # noqa: F401  # type: ignore[import]

            return True
        except ImportError:
            logger.warning("MintPy not installed — engine unavailable.")
            return False

    def run(
        self,
        scene_paths: list[Path],
        output_dir: Path,
        aoi_wkt: str,
        mode: Literal["quick", "standard", "high_precision"] = "standard",
        progress_cb: ProgressCallback | None = None,
    ) -> EngineResult:
        """
        Run MintPy on pre-formed interferograms.

        ``scene_paths`` is re-interpreted here as a list of paths to
        pre-formed interferogram HDF5 files (ifgramStack.h5 etc.).
        Raises ``NotImplementedError`` in Phase 0 — implement in Phase 1.
        """
        raise NotImplementedError(
            "MintPy engine is a Phase 1 deliverable. "
            "Use PyGMTSAREngine for local processing or OpenEOEngine for remote."
        )

    def estimate_runtime_minutes(
        self,
        n_scenes: int,
        aoi_km2: float,
        mode: Literal["quick", "standard", "high_precision"],
    ) -> float:
        # MintPy is fast for the inversion step alone
        base = {"quick": 1.0, "standard": 3.0, "high_precision": 8.0}[mode]
        return base * (n_scenes / 20.0) * max(1.0, aoi_km2 / 2500.0)
