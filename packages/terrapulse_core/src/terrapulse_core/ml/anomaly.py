"""
Anomaly detection on InSAR time-series.

Default: scikit-learn IsolationForest on the 6-feature representation.
No GPU required. Flags pixels with atypical deformation signatures.

Usage::

    detector = AnomalyDetector(contamination=0.05)
    detector.fit(features)          # features: (n_pixels, 6) float32
    labels = detector.predict(features)   # 1=normal, -1=anomalous
    scores = detector.anomaly_score(features)  # lower → more anomalous
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)


class AnomalyDetector:
    """
    Isolation Forest anomaly detector for InSAR pixel time-series.

    Parameters
    ----------
    contamination:
        Expected fraction of anomalous pixels.
        ``0.05`` = 5 % anomaly rate (IsolationForest parameter).
    random_state:
        Random seed for reproducibility.
    n_estimators:
        Number of trees in the IsolationForest.
    """

    def __init__(
        self,
        contamination: float = 0.05,
        random_state: int = 42,
        n_estimators: int = 100,
    ) -> None:
        if not 0.0 < contamination < 0.5:
            raise ValueError(
                f"contamination must be in (0, 0.5), got {contamination}"
            )
        self._contamination = contamination
        self._random_state = random_state
        self._n_estimators = n_estimators
        self._model: object | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fit(self, features: np.ndarray) -> "AnomalyDetector":
        """
        Fit the IsolationForest on the feature matrix.

        Parameters
        ----------
        features:
            Shape ``(n_pixels, n_features)`` float32.

        Returns self for chaining.
        """
        from sklearn.ensemble import IsolationForest  # type: ignore[import]

        features = np.asarray(features, dtype=np.float32)
        if features.ndim != 2:
            raise ValueError(
                f"features must be 2-D (n_pixels, n_features), got {features.ndim}D"
            )
        if len(features) < 10:
            raise ValueError(
                f"At least 10 samples are required for IsolationForest, got {len(features)}"
            )

        self._model = IsolationForest(
            contamination=self._contamination,
            n_estimators=self._n_estimators,
            random_state=self._random_state,
            n_jobs=-1,
        )
        self._model.fit(features)  # type: ignore[union-attr]

        logger.info(
            "AnomalyDetector fitted: %d pixels, contamination=%.3f",
            len(features),
            self._contamination,
        )
        return self

    def predict(self, features: np.ndarray) -> np.ndarray:
        """
        Predict anomaly labels for each pixel.

        Returns
        -------
        np.ndarray of shape ``(n_pixels,)`` with values:

        - ``+1`` → normal pixel
        - ``-1`` → anomalous pixel  (IsolationForest convention)

        Raises
        ------
        RuntimeError
            If ``fit()`` has not been called.
        """
        self._require_fitted()
        features = np.asarray(features, dtype=np.float32)
        result: np.ndarray = self._model.predict(features)  # type: ignore[union-attr]
        return result

    def anomaly_score(self, features: np.ndarray) -> np.ndarray:
        """
        Return continuous anomaly scores (lower = more anomalous).

        Scores are the negative average path length normalised by the expected
        path length for a sample of the same size (IsolationForest convention).
        Typical range: roughly ``[-0.7, 0.1]``.

        Returns
        -------
        np.ndarray of shape ``(n_pixels,)`` float64.

        Raises
        ------
        RuntimeError
            If ``fit()`` has not been called.
        """
        self._require_fitted()
        features = np.asarray(features, dtype=np.float32)
        scores: np.ndarray = self._model.score_samples(features)  # type: ignore[union-attr]
        return scores

    def fit_predict(self, features: np.ndarray) -> np.ndarray:
        """Convenience: ``fit(features)`` then ``predict(features)``."""
        return self.fit(features).predict(features)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def contamination(self) -> float:
        """Expected anomaly fraction used during fitting."""
        return self._contamination

    @property
    def is_fitted(self) -> bool:
        """True after ``fit()`` has been called successfully."""
        return self._model is not None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_fitted(self) -> None:
        if self._model is None:
            raise RuntimeError(
                "AnomalyDetector has not been fitted. Call fit() first."
            )
