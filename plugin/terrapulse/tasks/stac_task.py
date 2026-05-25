"""
STAC scene discovery QgsTask â€” full Phase 1 implementation.

Runs the CDSE STAC query on a QgsTask background thread so the QGIS UI
stays responsive during network I/O (typically 2â€“20 seconds).

Signal flow:
  MainDialog._on_run_stac()
    â†’ STACDiscoveryTask created + submitted to QgsApplication.taskManager()
    â†’ STACDiscoveryTask.run()  [background thread]
        â†’ STACClient.build_stack()
    â†’ STACDiscoveryTask.finished()  [main thread]
        â†’ stack_ready(SceneStack) emitted â†’ MainDialog updates log
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from qgis.core import QgsTask
from qgis.PyQt.QtCore import pyqtSignal

if TYPE_CHECKING:
    from terrapulse_core.stac.models import BBox, SceneStack

logger = logging.getLogger(__name__)


class STACDiscoveryTask(QgsTask):
    """
    Background task: query CDSE STAC for Sentinel-1 scenes.

    Emits ``stack_ready(SceneStack)`` on the main thread when successful.
    Emits ``stack_failed(str)`` on error.

    The calling dialog connects to these signals to update the UI without
    any direct reference to QgsTask internals.
    """

    # Emitted on main thread after successful STAC query
    stack_ready = pyqtSignal(object)  # type: ignore[assignment]  # object = SceneStack
    # Emitted on main thread on failure
    stack_failed = pyqtSignal(str)

    def __init__(
        self,
        aoi: "BBox",
        start_date: datetime,
        end_date: datetime,
        orbit_direction: str = "ascending",
        max_scenes: int = 30,
        catalog_url: str = "",
    ) -> None:
        super().__init__(
            "TerraPulse: STAC Scene Discovery",
            QgsTask.CanCancel,
        )
        self._aoi = aoi
        self._start_date = start_date
        self._end_date = end_date
        self._orbit_direction = orbit_direction
        self._max_scenes = max_scenes
        self._catalog_url = catalog_url
        self._scene_stack: "SceneStack | None" = None
        self._error: str = ""

    # ------------------------------------------------------------------
    # QgsTask implementation
    # ------------------------------------------------------------------

    def run(self) -> bool:
        """
        Execute on a background thread.
        Imports are deferred here â€” safe because terrapulse_core has no Qt dependency.
        """
        logger.info(
            "[STACTask] Querying CDSE STAC: orbit=%s, dates=%s â†’ %s, max=%d",
            self._orbit_direction,
            self._start_date.date(),
            self._end_date.date(),
            self._max_scenes,
        )
        try:
            # Deferred import â€” safe to do in background thread
            from terrapulse_core.stac.client import STACClient, STACQueryError

            kwargs: dict[str, object] = {}
            if self._catalog_url:
                kwargs["catalog_url"] = self._catalog_url

            client = STACClient(**kwargs)

            if self.isCanceled():
                return False

            self._scene_stack = client.build_stack(
                aoi=self._aoi,
                start_date=self._start_date,
                end_date=self._end_date,
                orbit_direction=self._orbit_direction,
                max_scenes=self._max_scenes,
            )

            logger.info(
                "[STACTask] Found %d scenes (%.1f GB, orbit %d).",
                self._scene_stack.n_scenes,
                self._scene_stack.estimate_total_size_gb(),
                self._scene_stack.relative_orbit,
            )
            return True

        except Exception as exc:
            self._error = str(exc)
            logger.error("[STACTask] Failed: %s", exc)
            return False

    def finished(self, result: bool) -> None:
        """Called on the main thread after run() completes."""
        if result and self._scene_stack is not None:
            self.stack_ready.emit(self._scene_stack)
        else:
            err = self._error or "STAC query was cancelled."
            logger.warning("[STACTask] Emitting stack_failed: %s", err)
            self.stack_failed.emit(err)

    def cancel(self) -> None:
        logger.info("[STACTask] Cancelled by user.")
        super().cancel()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def scene_stack(self) -> "SceneStack | None":
        """Available after ``finished()`` is called on success."""
        return self._scene_stack

    @property
    def error_message(self) -> str:
        return self._error

