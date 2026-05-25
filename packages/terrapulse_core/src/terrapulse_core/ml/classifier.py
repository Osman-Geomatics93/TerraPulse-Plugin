"""
5-class ground deformation pixel classifier.

Default implementation: scikit-learn RandomForestClassifier trained on
labelled InSAR pixels (shipped as a small pre-trained model artifact).

Classes:
  0  stable          — velocity near 0, no trend, high coherence
  1  linear          — constant subsidence/uplift rate
  2  seasonal        — dominant annual periodicity (aquifer, thermokarst)
  3  accelerating    — increasing velocity trend
  4  anomalous       — low coherence, erratic, or outlier signal
"""

from __future__ import annotations

import logging
from enum import IntEnum
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Number of features expected by the classifier
_N_FEATURES = 6

# Default model shipped with the package (~2 MB)
_DEFAULT_MODEL_PATH = Path(__file__).parent / "models" / "rf_classifier_v1.pkl"


class DeformationClass(IntEnum):
    STABLE = 0
    LINEAR = 1
    SEASONAL = 2
    ACCELERATING = 3
    ANOMALOUS = 4


CLASS_LABELS = {
    DeformationClass.STABLE: "Stable",
    DeformationClass.LINEAR: "Linear subsidence/uplift",
    DeformationClass.SEASONAL: "Seasonal deformation",
    DeformationClass.ACCELERATING: "Accelerating deformation",
    DeformationClass.ANOMALOUS: "Anomalous / incoherent",
}

CLASS_COLORS_HEX = {
    DeformationClass.STABLE: "#2ECC71",       # green
    DeformationClass.LINEAR: "#F39C12",       # amber
    DeformationClass.SEASONAL: "#3498DB",     # blue
    DeformationClass.ACCELERATING: "#E74C3C", # red
    DeformationClass.ANOMALOUS: "#95A5A6",    # grey
}


class DeformationClassifier:
    """
    Wrapper around a trained scikit-learn classifier.

    Usage::

        clf = DeformationClassifier.load()
        labels, proba = clf.predict(feature_matrix)
    """

    def __init__(self, model: Any) -> None:
        self._model = model

    @classmethod
    def load(cls, model_path: Path | None = None) -> DeformationClassifier:
        """
        Load a pre-trained classifier from disk.

        Falls back to ``_DEFAULT_MODEL_PATH`` if ``model_path`` is None.
        Raises ``FileNotFoundError`` if no model is found.
        """
        import pickle  # noqa: S403

        path = model_path or _DEFAULT_MODEL_PATH
        if not path.exists():
            raise FileNotFoundError(
                f"Classifier model not found at {path}. "
                "Run `terrapulse_core.ml.train.train_default_classifier()` "
                "or download the model artifact."
            )
        with open(path, "rb") as f:
            model = pickle.load(f)  # noqa: S301
        logger.info("Loaded classifier from %s", path)
        return cls(model)

    def predict(
        self,
        features: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Classify pixels.

        Parameters
        ----------
        features:
            Shape ``(n_pixels, 6)`` float32 feature matrix from
            ``extract_features()``.

        Returns
        -------
        labels:
            Shape ``(n_pixels,)`` int32 array of ``DeformationClass`` values.
        probabilities:
            Shape ``(n_pixels, 5)`` float32 class probability matrix.
            Column order matches ``DeformationClass`` enum values (0–4).

        Raises
        ------
        ValueError
            If ``features`` does not have shape ``(n_pixels, 6)``.
        RuntimeError
            If no model is loaded.
        """
        if self._model is None:
            raise RuntimeError(
                "No model loaded. Instantiate via DeformationClassifier.load()."
            )

        features = np.asarray(features, dtype=np.float32)
        if features.ndim != 2 or features.shape[1] != _N_FEATURES:
            raise ValueError(
                f"Expected feature matrix of shape (n_pixels, {_N_FEATURES}), "
                f"got {features.shape}."
            )

        labels: np.ndarray = self._model.predict(features).astype(np.int32)
        probabilities: np.ndarray = self._model.predict_proba(features).astype(np.float32)

        logger.debug(
            "classify: %d pixels → classes %s",
            len(labels),
            np.unique(labels).tolist(),
        )
        return labels, probabilities
