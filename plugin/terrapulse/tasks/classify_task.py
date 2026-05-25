"""
ClassifyTask â€” Phase 2 ML classification background task.

Reads velocity + coherence COGs, extracts the 6-feature matrix,
classifies pixels using the RandomForest model, runs anomaly detection,
writes label / entropy / confidence / anomaly-score COGs to the run output dir,
and signals the main thread when done.

Emits:
    classification_complete(success: bool, run_id: str)
    progress_message(message: str)
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Callable

from qgis.core import QgsTask
from qgis.PyQt.QtCore import pyqtSignal

logger = logging.getLogger(__name__)


class ClassifyTask(QgsTask):
    """
    QgsTask that runs the full Phase 2 ML pipeline in a background thread.

    Steps
    -----
    1. Read velocity COG (band 1 = LOS velocity mm/yr) with rasterio.
    2. Read coherence COG (band 1 = temporal coherence [0, 1]) with rasterio.
    3. Build synthetic DEM slope/aspect arrays (zeros) if no DEM is provided,
       OR read from dem_cog if supplied.
    4. Wrap arrays as xarray DataArrays so extract_features() can parse dates.
    5. Call ``extract_features()`` â†’ (n_valid, 6) float32 feature matrix.
    6. Load (or train) the RandomForest classifier.
    7. ``classifier.predict()`` â†’ labels (n_valid,) + probabilities (n_valid, 5).
    8. ``compute_uncertainty()`` and ``uncertainty_to_raster()`` â†’ 2-D arrays.
    9. ``AnomalyDetector().fit_predict()`` â†’ anomaly labels (n_valid,).
    10. Write label, entropy, confidence, and anomaly COGs via COGWriter.
    11. Emit ``classification_complete(True, run_id)`` on success.
    """

    classification_complete = pyqtSignal(bool, str)  # (success, run_id)
    progress_message = pyqtSignal(str)

    def __init__(
        self,
        velocity_cog: Path,
        coherence_cog: Path,
        output_dir: Path,
        run_id: str | None = None,
        model_path: Path | None = None,
        dem_cog: Path | None = None,
        anomaly_contamination: float = 0.05,
        parent: object | None = None,
    ) -> None:
        """
        Parameters
        ----------
        velocity_cog:
            Path to velocity COG (band 1 = LOS velocity in mm/yr).
        coherence_cog:
            Path to coherence COG (band 1 = temporal coherence [0, 1]).
        output_dir:
            Directory where classification COGs will be written.
        run_id:
            Unique run identifier.  A UUID4 is generated if not supplied.
        model_path:
            Path to a pickled RandomForestClassifier. If None and the default
            model is missing, the classifier is trained on synthetic data.
        dem_cog:
            Optional DEM COG for slope/aspect features.  If None, flat terrain
            (slope=0Â°, aspect=0Â°) is assumed for all pixels.
        anomaly_contamination:
            Expected fraction of anomalous pixels for IsolationForest.
        """
        super().__init__(
            f"TerraPulse ML classify â€” {(run_id or '')[:8]}",
            QgsTask.CanCancel,
        )
        self._velocity_cog = velocity_cog
        self._coherence_cog = coherence_cog
        self._output_dir = output_dir
        self._run_id = run_id or str(uuid.uuid4())
        self._model_path = model_path
        self._dem_cog = dem_cog
        self._anomaly_contamination = anomaly_contamination

        # Outputs â€” set after successful run
        self._label_cog: Path | None = None
        self._entropy_cog: Path | None = None
        self._confidence_cog: Path | None = None
        self._anomaly_cog: Path | None = None
        self._error_message: str | None = None

    # ------------------------------------------------------------------
    # QgsTask interface
    # ------------------------------------------------------------------

    def run(self) -> bool:  # noqa: C901
        """Execute the ML pipeline.  Called in a worker thread by QgsTaskManager."""
        try:
            import numpy as np
            import rasterio
            from rasterio.transform import Affine

            from terrapulse_core.ml.features import extract_features, pixel_valid_mask
            from terrapulse_core.ml.classifier import DeformationClassifier
            from terrapulse_core.ml.anomaly import AnomalyDetector
            from terrapulse_core.ml.uncertainty import uncertainty_to_raster
            from terrapulse_core.ml.train import train_default_classifier
            from terrapulse_core.io.cog import COGWriter

            self._output_dir.mkdir(parents=True, exist_ok=True)

            self._emit("Reading velocity COGâ€¦")
            self.setProgress(5)

            # ---- 1. Read velocity ----
            with rasterio.open(self._velocity_cog) as ds:
                vel_band = ds.read(1).astype(np.float32)  # (ny, nx)
                profile = ds.profile.copy()
                transform: Affine = ds.transform
                crs = ds.crs

            ny, nx = vel_band.shape

            # Synthesise a 12-step time-series from the 2-D velocity map.
            # This is a proxy; real displacement stacks come from PyGMTSAR output.
            # We create a linear ramp so extract_features() can recover velocity.
            times = np.linspace(0.0, 1.0, 12, dtype=np.float64)
            disp_3d = np.stack([vel_band * t for t in times], axis=0)  # (12, ny, nx)

            self._emit("Reading coherence COGâ€¦")
            self.setProgress(10)

            # ---- 2. Read coherence ----
            with rasterio.open(self._coherence_cog) as ds:
                coherence = ds.read(1).astype(np.float32)  # (ny, nx)

            # ---- 3. DEM slope / aspect ----
            if self._dem_cog and self._dem_cog.exists():
                self._emit("Reading DEM COG for slope/aspectâ€¦")
                with rasterio.open(self._dem_cog) as ds:
                    dem = ds.read(1).astype(np.float32)
                # Simple gradient-based slope / aspect (degrees)
                dy, dx = np.gradient(dem)
                slope = np.degrees(np.arctan(np.sqrt(dx**2 + dy**2)))
                aspect = (np.degrees(np.arctan2(-dy, dx)) + 360) % 360
            else:
                # Flat terrain assumption
                slope = np.zeros((ny, nx), dtype=np.float32)
                aspect = np.zeros((ny, nx), dtype=np.float32)

            if self.isCanceled():
                return False

            self._emit("Extracting featuresâ€¦")
            self.setProgress(20)

            # ---- 4â€“5. Feature extraction ----
            features = extract_features(
                velocity_ts=disp_3d,
                coherence=coherence,
                dem_slope=slope,
                dem_aspect=aspect,
            )
            valid_mask = pixel_valid_mask(disp_3d, coherence, slope, aspect)

            if self.isCanceled():
                return False

            self._emit("Loading / training classifierâ€¦")
            self.setProgress(35)

            # ---- 6. Load (or train) classifier ----
            try:
                clf = DeformationClassifier.load(self._model_path)
            except FileNotFoundError:
                self._emit("No model found â€” training on synthetic data (â‰ˆ5 s)â€¦")
                clf = train_default_classifier(save_path=self._model_path)

            self._emit("Classifying pixelsâ€¦")
            self.setProgress(50)

            # ---- 7. Classify ----
            labels, probabilities = clf.predict(features)

            if self.isCanceled():
                return False

            self._emit("Computing uncertainty mapsâ€¦")
            self.setProgress(65)

            # ---- 8. Uncertainty rasters ----
            label_raster, entropy_raster, confidence_raster = uncertainty_to_raster(
                labels=labels,
                probabilities=probabilities,
                valid_mask=valid_mask,
                shape=(ny, nx),
                nodata_label=-1,
                nodata_float=float("nan"),
            )

            self._emit("Running anomaly detectionâ€¦")
            self.setProgress(75)

            # ---- 9. Anomaly detection ----
            detector = AnomalyDetector(contamination=self._anomaly_contamination)
            anomaly_labels = detector.fit_predict(features)  # 1=normal, -1=anomalous
            from terrapulse_core.ml.uncertainty import uncertainty_to_raster as _u2r
            # Re-use uncertainty_to_raster logic to reconstruct 2-D anomaly array
            anomaly_flat = np.full(ny * nx, 0, dtype=np.int32)
            anomaly_flat[valid_mask] = anomaly_labels.astype(np.int32)
            anomaly_raster = anomaly_flat.reshape(ny, nx)

            if self.isCanceled():
                return False

            self._emit("Writing classification COGsâ€¦")
            self.setProgress(85)

            # ---- 10. Write output COGs ----
            writer = COGWriter()
            rid8 = self._run_id[:8]

            self._label_cog = writer.write(
                data=label_raster[np.newaxis, :, :].astype(np.float32),
                output_path=self._output_dir / f"classification_{rid8}.tif",
                crs=str(crs),
                transform=transform,
                nodata=-1,
                band_names=["deformation_class"],
            )
            self._entropy_cog = writer.write(
                data=entropy_raster[np.newaxis, :, :],
                output_path=self._output_dir / f"entropy_{rid8}.tif",
                crs=str(crs),
                transform=transform,
                nodata=float("nan"),
                band_names=["shannon_entropy_bits"],
            )
            self._confidence_cog = writer.write(
                data=confidence_raster[np.newaxis, :, :],
                output_path=self._output_dir / f"confidence_{rid8}.tif",
                crs=str(crs),
                transform=transform,
                nodata=float("nan"),
                band_names=["max_class_probability"],
            )
            self._anomaly_cog = writer.write(
                data=anomaly_raster[np.newaxis, :, :].astype(np.float32),
                output_path=self._output_dir / f"anomaly_{rid8}.tif",
                crs=str(crs),
                transform=transform,
                nodata=0,
                band_names=["anomaly_label"],
            )

            self.setProgress(100)
            self._emit(f"Classification complete â€” {int(np.sum(valid_mask))} pixels classified.")
            logger.info(
                "ClassifyTask finished: label=%s, entropy=%s",
                self._label_cog,
                self._entropy_cog,
            )
            return True

        except Exception as exc:
            logger.exception("ClassifyTask failed")
            self._error_message = str(exc)
            return False

    def finished(self, result: bool) -> None:
        """Called on the main thread after run() completes."""
        self.classification_complete.emit(result, self._run_id)
        if not result:
            logger.error("Classification failed: %s", self._error_message)

    def cancel(self) -> None:
        """Cancel the running task."""
        logger.info("ClassifyTask cancel requested.")
        super().cancel()

    # ------------------------------------------------------------------
    # Properties (read after finished())
    # ------------------------------------------------------------------

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def label_cog(self) -> Path | None:
        return self._label_cog

    @property
    def entropy_cog(self) -> Path | None:
        return self._entropy_cog

    @property
    def confidence_cog(self) -> Path | None:
        return self._confidence_cog

    @property
    def anomaly_cog(self) -> Path | None:
        return self._anomaly_cog

    @property
    def error_message(self) -> str | None:
        return self._error_message

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _emit(self, message: str) -> None:
        """Emit a human-readable progress message to the main thread."""
        self.progress_message.emit(message)
        logger.debug("ClassifyTask: %s", message)

