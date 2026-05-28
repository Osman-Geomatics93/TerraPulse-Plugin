"""
InSAR processing QgsTask â€” full Phase 1 implementation.

Drives the entire pipeline on a background thread:
  1. Write "running" recipe to disk (provenance)
  2. Call EngineIPCClient.run() â†’ Docker container
  3. Forward progress to QgsTask.setProgress() (thread-safe)
  4. Write "completed" or "failed" recipe on finish

Signal flow:
  MainDialog._on_run()
    â†’ InSARTask created + submitted to QgsApplication.taskManager()
    â†’ InSARTask.run()  [background thread]
        â†’ EngineIPCClient.run() [blocks; engine does STAC + download + InSAR]
        â†’ _on_engine_progress() â†’ self.setProgress() + self.setDescription()
    â†’ InSARTask.finished()  [main thread]
        â†’ run_complete(success, run_id) emitted
        â†’ MainDialog opens ResultsDialog or shows error
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from qgis.core import QgsTask
from qgis.PyQt.QtCore import pyqtSignal

if TYPE_CHECKING:
    from terrapulse_core.stac.models import ProcessingMode

logger = logging.getLogger(__name__)

# Default Docker image name (configurable via settings dialog).
# Points to the public Docker Hub image so users don't need to build locally.
DEFAULT_DOCKER_IMAGE = "osmanos93/terrapulse-pygmtsar:latest"


class InSARTask(QgsTask):
    """
    Background task: full TerraPulse InSAR processing pipeline.

    The task communicates with the Docker engine container via
    ``EngineIPCClient`` (JSON over subprocess stdin/stdout).

    Signals
    -------
    run_complete(success: bool, run_id: str)
        Emitted on the main thread when processing finishes (success or failure).
    progress_message(message: str)
        Emitted on the main thread with each human-readable progress update.
    """

    run_complete = pyqtSignal(bool, str)  # (success, run_id)
    progress_message = pyqtSignal(str)

    def __init__(  # noqa: PLR0913  # nosec B107
        self,
        aoi_wkt: str,
        start_date: datetime,
        end_date: datetime,
        output_dir: Path,
        mode: str = "standard",
        orbit_direction: str = "ascending",
        max_scenes: int = 30,
        cdse_username: str = "",
        cdse_password: str = "",  # nosec B107
        docker_image: str = DEFAULT_DOCKER_IMAGE,
    ) -> None:
        super().__init__(
            "TerraPulse InSAR Processing",
            QgsTask.CanCancel,
        )
        self._aoi_wkt = aoi_wkt
        self._start_date = start_date
        self._end_date = end_date
        self._output_dir = output_dir
        self._mode = mode
        self._orbit_direction = orbit_direction
        self._max_scenes = max_scenes
        self._cdse_username = cdse_username
        self._cdse_password = cdse_password
        self._docker_image = docker_image

        self._run_id = str(uuid.uuid4())
        self._result_dir: Path | None = None
        self._velocity_cog: Path | None = None
        self._coherence_cog: Path | None = None
        self._error: str = ""
        self._engine_client: object | None = None  # EngineIPCClient

    # ------------------------------------------------------------------
    # QgsTask implementation
    # ------------------------------------------------------------------

    def run(self) -> bool:
        """
        Executed on a background thread by QgsTaskManager.
        Must NEVER touch Qt widgets or call QGIS API that requires the main thread.
        """
        # Top-level BaseException guard â€” ensures self._error is ALWAYS set
        # even if something unexpected escapes the inner try/except blocks.
        try:
            return self._run_impl()
        except BaseException as exc:  # noqa: BLE001
            self._error = f"Fatal task error ({type(exc).__name__}): {exc}"
            logger.exception(
                "[InSARTask %s] FATAL unhandled error in run()", self._run_id[:8]
            )
            return False

    def _run_impl(self) -> bool:
        """Inner implementation â€” called by run() which wraps it in BaseException guard."""
        logger.info("[InSARTask %s] Starting run.", self._run_id[:8])

        # ---- 1. Write "running" provenance recipe ----
        recipe = None
        writer = None
        try:
            from terrapulse_core.provenance.recipe import RecipeWriter, RunRecipe
            import terrapulse_core

            writer = RecipeWriter(self._output_dir)
            recipe = RunRecipe(
                run_id=self._run_id,
                status="running",
                aoi_wkt=self._aoi_wkt,
                start_date=self._start_date.date().isoformat(),
                end_date=self._end_date.date().isoformat(),
                engine=self._docker_image.split(":")[0].replace("terrapulse-", ""),
                mode=self._mode,
                terrapulse_version=terrapulse_core.__version__,
            )
            writer.write(recipe)
            logger.info("[InSARTask %s] Recipe written (status=running).", self._run_id[:8])
        except BaseException as exc:  # noqa: BLE001
            logger.warning("[InSARTask %s] Could not write recipe: %s", self._run_id[:8], exc)
            recipe = None
            writer = None

        # ---- 2. Check for cancellation ----
        if self.isCanceled():
            self._error = "Cancelled before processing started."
            self._update_recipe(recipe, writer, "failed", self._error)
            return False

        # ---- 3. Quick Docker pre-check (gives a clear error before launching) ----
        try:
            from terrapulse_core.io.engine_ipc import EngineIPCClient

            _docker_ok = EngineIPCClient.is_docker_available()
            logger.info("[InSARTask %s] Docker available: %s", self._run_id[:8], _docker_ok)
            if not _docker_ok:
                self._error = (
                    "Docker is not running. Start Docker Desktop and try again."
                )
                self._update_recipe(recipe, writer, "failed", self._error)
                return False

            _client_tmp = EngineIPCClient(docker_image=self._docker_image)
            _image_ok = _client_tmp._image_exists()
            logger.info(
                "[InSARTask %s] Image '%s' exists: %s",
                self._run_id[:8], self._docker_image, _image_ok,
            )
            if not _image_ok:
                self._error = (
                    f"Docker image '{self._docker_image}' not found locally.\n"
                    f"Pull it from Docker Hub with:\n"
                    f"    docker pull {self._docker_image}\n"
                    f"(or build from source: "
                    f"docker build -f docker/Dockerfile.pygmtsar -t {self._docker_image} .)"
                )
                self._update_recipe(recipe, writer, "failed", self._error)
                return False

        except ModuleNotFoundError as exc:
            self._error = (
                f"Plugin install is broken — could not import {exc.name!r}.\n"
                "The QGIS plugin zip is missing its core module. "
                "Reinstall the plugin from a 0.2.4+ release, or copy "
                "packages/terrapulse_core/src/terrapulse_core/ into the plugin "
                "install directory and restart QGIS."
            )
            logger.exception("[InSARTask %s] Plugin install missing terrapulse_core", self._run_id[:8])
            self._update_recipe(recipe, writer, "failed", self._error)
            return False
        except BaseException as exc:  # noqa: BLE001
            self._error = f"Docker pre-check failed: {type(exc).__name__}: {exc}"
            logger.exception("[InSARTask %s] Docker pre-check error", self._run_id[:8])
            self._update_recipe(recipe, writer, "failed", self._error)
            return False

        # ---- 4. Launch Docker engine via IPC ----
        result: dict[str, object] = {}
        try:
            client = EngineIPCClient(docker_image=self._docker_image)
            self._engine_client = client

            logger.info("[InSARTask %s] Launching Docker engine.", self._run_id[:8])
            result = client.run(
                aoi_wkt=self._aoi_wkt,
                start_date=self._start_date,
                end_date=self._end_date,
                output_dir=self._output_dir,
                mode=self._mode,
                orbit_direction=self._orbit_direction,
                max_scenes=self._max_scenes,
                cdse_username=self._cdse_username,
                cdse_password=self._cdse_password,
                progress_cb=self._on_engine_progress,
            )

        except BaseException as exc:  # noqa: BLE001
            logger.exception("[InSARTask %s] Unexpected engine error.", self._run_id[:8])
            result = {
                "success": False,
                "error_message": f"Unexpected engine error ({type(exc).__name__}): {exc}",
                "velocity_cog": "",
                "coherence_cog": "",
                "displacement_zarr": "",
                "n_scenes_processed": 0,
                "processing_time_seconds": 0.0,
                "warnings": [],
            }

        # ---- 5. Handle result ----
        success = bool(result.get("success", False))

        if success:
            vel = str(result.get("velocity_cog", ""))
            coh = str(result.get("coherence_cog", ""))
            self._velocity_cog = Path(vel) if vel else None
            self._coherence_cog = Path(coh) if coh else None
            self._result_dir = self._output_dir

            if recipe and writer:
                recipe.velocity_cog = vel
                recipe.coherence_cog = coh
                recipe.displacement_zarr = str(result.get("displacement_zarr", ""))
                recipe.warnings = list(result.get("warnings", []))
                self._update_recipe(recipe, writer, "completed")

            logger.info(
                "[InSARTask %s] Completed in %.0f s. Velocity: %s",
                self._run_id[:8],
                float(result.get("processing_time_seconds", 0)),
                vel,
            )
            return True

        else:
            self._error = str(result.get("error_message", "")).strip()
            if not self._error:
                self._error = "Processing failed with no error message. Check QGIS Python Console."
            if recipe and writer:
                recipe.warnings = list(result.get("warnings", []))
                self._update_recipe(recipe, writer, "failed", self._error)
            logger.error("[InSARTask %s] Failed: %s", self._run_id[:8], self._error)
            return False

    def finished(self, result: bool) -> None:
        """Called on the main thread after run() completes."""
        self.run_complete.emit(result, self._run_id)
        if result:
            logger.info("[InSARTask %s] Success â€” emitting run_complete.", self._run_id[:8])
        else:
            logger.warning("[InSARTask %s] Failure â€” emitting run_complete.", self._run_id[:8])

    def cancel(self) -> None:
        """Signal the engine client to terminate the Docker process."""
        logger.info("[InSARTask %s] Cancel requested.", self._run_id[:8])
        if self._engine_client is not None:
            try:
                self._engine_client.cancel()  # type: ignore[union-attr]
            except Exception:
                pass
        super().cancel()

    # ------------------------------------------------------------------
    # Progress callback (called from background thread)
    # ------------------------------------------------------------------

    def _on_engine_progress(self, progress_data: dict[str, object]) -> None:
        """
        Receive progress from EngineIPCClient and forward to QgsTask.

        Thread-safe: QgsTask.setProgress() acquires an internal mutex.
        """
        pct = float(progress_data.get("percent", 0))
        msg = str(progress_data.get("message", ""))
        step = str(progress_data.get("step", ""))

        self.setProgress(pct)
        if msg:
            desc = f"[{step}] {msg}" if step else msg
            self.setDescription(desc)
            # progress_message signal must be emitted on main thread â€”
            # use a queued connection; here we just emit directly (Qt handles
            # cross-thread signals with AutoConnection if declared as pyqtSignal)
            self.progress_message.emit(msg)

        logger.debug("[InSARTask %s] %.0f%% â€” %s", self._run_id[:8], pct, msg)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _update_recipe(
        recipe: object | None,
        writer: object | None,
        status: str,
        error: str = "",
    ) -> None:
        """Update and re-write the provenance recipe. Swallows exceptions."""
        if recipe is None or writer is None:
            return
        try:
            from datetime import timezone

            recipe.status = status  # type: ignore[union-attr]
            recipe.completed_at = datetime.now(timezone.utc).isoformat()  # type: ignore[union-attr]
            if error:
                recipe.error_message = error  # type: ignore[union-attr]
            writer.write(recipe)  # type: ignore[union-attr]
        except Exception as exc:
            logger.warning("Failed to update recipe: %s", exc)

    # ------------------------------------------------------------------
    # Properties (readable from main thread after finished())
    # ------------------------------------------------------------------

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def result_dir(self) -> Path | None:
        return self._result_dir

    @property
    def velocity_cog(self) -> Path | None:
        return self._velocity_cog

    @property
    def coherence_cog(self) -> Path | None:
        return self._coherence_cog

    @property
    def error_message(self) -> str:
        return self._error

