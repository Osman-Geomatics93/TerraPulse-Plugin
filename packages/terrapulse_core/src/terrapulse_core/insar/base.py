"""
Abstract base protocol for all InSAR processing engines.

Any concrete engine (PyGMTSAR, MintPy, OpenEO) must implement this interface.
The QGIS plugin communicates with engines exclusively through this protocol
and the ``engine_ipc`` transport layer — never by direct import.

Design rule: no engine import should appear in QGIS plugin code.
"""

from __future__ import annotations

import abc
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pathlib import Path

ProgressCallback = Callable[["ProcessingProgress"], None]

StepName = Literal[
    "download",
    "coregistration",
    "interferogram",
    "filtering",
    "unwrapping",
    "sbas_inversion",
    "geocoding",
    "done",
    "error",
]


@dataclass
class ProcessingProgress:
    """Emitted at each stage of InSAR processing. Safe to serialise to JSON."""

    step: StepName
    percent: float          # 0–100
    message: str = ""
    scene_index: int = 0    # which scene in the stack (0 = not scene-specific)
    total_scenes: int = 0


@dataclass
class EngineResult:
    """
    Output of a completed InSAR run.

    All paths point to files inside ``output_dir``. Consumers should treat
    these as cloud-optimised GeoTIFFs (COGs) where possible.
    """

    output_dir: Path

    # Primary outputs (always present on success)
    velocity_cog: Path | None = None          # mm/yr mean LOS velocity
    coherence_cog: Path | None = None         # mean temporal coherence 0–1
    displacement_stack: Path | None = None    # zarr store: (time, y, x) mm

    # Ancillary outputs (may be absent in quick mode)
    dem_cog: Path | None = None               # DEM used for processing
    baseline_plot: Path | None = None         # PNG of temporal baseline network

    # Provenance
    log_file: Path | None = None
    recipe_yaml: Path | None = None           # written by provenance layer

    # Status
    success: bool = False
    error_message: str = ""
    warnings: list[str] = field(default_factory=list)

    # Stats
    n_scenes_processed: int = 0
    processing_time_seconds: float = 0.0


class BaseInSAREngine(abc.ABC):
    """
    Abstract interface for InSAR processing engines.

    Concrete subclasses wrap PyGMTSAR, MintPy, or OpenEO.
    They must be importable *only* inside the processing environment
    (Docker / conda), never inside the QGIS process.
    """

    name: str = "base"

    @abc.abstractmethod
    def is_available(self) -> bool:
        """
        Return True if the engine is installed and ready.
        Should not raise — return False on any import/env error.
        """
        ...

    @abc.abstractmethod
    def run(
        self,
        scene_paths: list[Path],
        output_dir: Path,
        aoi_wkt: str,
        mode: Literal["quick", "standard", "high_precision"] = "standard",
        progress_cb: ProgressCallback | None = None,
    ) -> EngineResult:
        """
        Execute the full InSAR pipeline on a list of downloaded SLC paths.

        Parameters
        ----------
        scene_paths:
            Paths to downloaded Sentinel-1 SAFE directories (unzipped) or .zip files.
        output_dir:
            Directory where all outputs will be written.
        aoi_wkt:
            Well-Known Text polygon for the AOI (WGS-84). The engine will crop
            to this polygon before intensive processing.
        mode:
            Processing mode:
            * ``quick``: reduced looks, shorter baseline graph
            * ``standard``: default SBAS settings
            * ``high_precision``: full coherence matrix, multi-look
        progress_cb:
            Optional callable invoked at each processing step with a
            ``ProcessingProgress`` object. Must be thread-safe.

        Returns
        -------
        ``EngineResult`` with paths to all output files.
        Never raises on processing errors — errors are captured in
        ``EngineResult.error_message`` and ``success=False``.
        """
        ...

    @abc.abstractmethod
    def estimate_runtime_minutes(
        self,
        n_scenes: int,
        aoi_km2: float,
        mode: Literal["quick", "standard", "high_precision"],
    ) -> float:
        """
        Estimate processing time in minutes. Used to set QgsTask timeouts
        and to warn the user before starting a long job.
        """
        ...

    def __repr__(self) -> str:
        available = "available" if self.is_available() else "unavailable"
        return f"<{self.__class__.__name__} [{available}]>"
