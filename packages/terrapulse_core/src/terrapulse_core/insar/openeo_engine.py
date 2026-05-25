"""
OpenEO remote processing engine.

Submits InSAR jobs to the Copernicus Data Space Ecosystem OpenEO endpoint.
No local SLC downloads required — all processing happens in the cloud.

CDSE OpenEO: https://openeo.dataspace.copernicus.eu/
openeo-python-client: https://open-eo.github.io/openeo-python-client/
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

from terrapulse_core.insar.base import (
    BaseInSAREngine,
    EngineResult,
    ProgressCallback,
)

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

CDSE_OPENEO_URL = "https://openeo.dataspace.copernicus.eu/"
_POLL_INTERVAL_S = 30
_MAX_WAIT_S = 3600 * 6  # 6 hours max


class OpenEOEngine(BaseInSAREngine):
    """
    Remote InSAR engine via OpenEO on CDSE.

    Requires an OpenEO account on CDSE with processing credits.
    Token is passed via ``ProcessingMode.openeo_token``.
    """

    name = "openeo"

    def __init__(self, token: str | None = None) -> None:
        self._token = token

    def is_available(self) -> bool:
        try:
            import openeo  # noqa: F401  # type: ignore[import]

            return True
        except ImportError:
            logger.warning("openeo package not installed — engine unavailable.")
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
        Submit an InSAR job to CDSE OpenEO and poll until completion.

        ``scene_paths`` is ignored — OpenEO accesses CDSE data internally.
        The AOI and time range must be encoded in the process graph.

        NOTE: Phase 0 stub — process graph construction is Phase 1.
        """
        raise NotImplementedError(
            "OpenEO engine process graph construction is a Phase 1 deliverable. "
            "Set engine='pygmtsar' in ProcessingMode for now."
        )

    def estimate_runtime_minutes(
        self,
        n_scenes: int,
        aoi_km2: float,
        mode: Literal["quick", "standard", "high_precision"],
    ) -> float:
        # OpenEO is faster due to distributed compute but has queue wait time
        base = {"quick": 15.0, "standard": 30.0, "high_precision": 60.0}[mode]
        return base + 10.0  # +10 min queue estimate

    def estimate_credits(
        self,
        n_scenes: int,
        aoi_km2: float,
        mode: Literal["quick", "standard", "high_precision"],
    ) -> float:
        """
        Rough OpenEO credit estimate. CDSE charges by processing unit.
        This is a heuristic — always do a dry-run before submitting.
        """
        credits_per_scene = {"quick": 0.5, "standard": 1.5, "high_precision": 4.0}[mode]
        area_factor = max(1.0, aoi_km2 / 2500.0)
        return credits_per_scene * n_scenes * area_factor
