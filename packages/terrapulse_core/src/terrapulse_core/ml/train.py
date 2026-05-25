"""
Training utilities for the TerraPulse ML pipeline.

Provides functions to:
- Generate synthetic labelled training data for each deformation class.
- Train and persist the default RandomForest classifier.
- Fit an AnomalyDetector on real or synthetic feature data.

CLI usage (from the repo root)::

    py -3.11 -m terrapulse_core.ml.train

This will train the default classifier and save it to
``ml/models/rf_classifier_v1.pkl`` inside the package.
"""

from __future__ import annotations

import logging
import pickle
from typing import TYPE_CHECKING

import numpy as np

from terrapulse_core.ml.anomaly import AnomalyDetector
from terrapulse_core.ml.classifier import (
    _DEFAULT_MODEL_PATH,
    DeformationClass,
    DeformationClassifier,
)

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-class Gaussian parameter table
# Each entry: (mean, std) for the 6 features in order:
#   [mean_velocity_mm_yr, velocity_trend, seasonal_amplitude,
#    mean_coherence, dem_slope_deg, dem_aspect_norm]
# ---------------------------------------------------------------------------
_CLASS_PARAMS: dict[int, list[tuple[float, float]]] = {
    int(DeformationClass.STABLE): [
        (0.0,   1.5),    # mean_velocity: near zero
        (0.0,   0.05),   # velocity_trend: essentially flat
        (1.0,   0.8),    # seasonal_amplitude: small
        (0.80,  0.08),   # coherence: high
        (8.0,   6.0),    # dem_slope_deg: gentle
        (0.5,   0.29),   # dem_aspect_norm: uniform
    ],
    int(DeformationClass.LINEAR): [
        (0.0,   15.0),   # mean_velocity: large abs value (sign varies)
        (0.0,   0.10),   # velocity_trend: near-zero (constant rate)
        (2.0,   1.5),    # seasonal_amplitude: small
        (0.62,  0.10),   # coherence: moderate
        (12.0,  9.0),    # dem_slope_deg: varies
        (0.5,   0.29),   # dem_aspect_norm: uniform
    ],
    int(DeformationClass.SEASONAL): [
        (0.0,   2.0),    # mean_velocity: low
        (0.0,   0.08),   # velocity_trend: small
        (12.0,  4.0),    # seasonal_amplitude: LARGE
        (0.70,  0.08),   # coherence: moderate-high
        (4.0,   3.5),    # dem_slope_deg: flat terrain (aquifer zones)
        (0.5,   0.29),   # dem_aspect_norm: uniform
    ],
    int(DeformationClass.ACCELERATING): [
        (-10.0, 5.0),    # mean_velocity: moderate subsidence bias
        (-2.0,  0.8),    # velocity_trend: LARGE negative (accelerating)
        (3.0,   2.0),    # seasonal_amplitude: moderate
        (0.52,  0.12),   # coherence: lower (active deformation zone)
        (12.0,  8.0),    # dem_slope_deg: varies
        (0.5,   0.29),   # dem_aspect_norm: uniform
    ],
    int(DeformationClass.ANOMALOUS): [
        (0.0,   18.0),   # mean_velocity: erratic, high variance
        (0.0,   5.0),    # velocity_trend: erratic
        (8.0,   8.0),    # seasonal_amplitude: erratic
        (0.20,  0.10),   # coherence: LOW — key discriminator
        (18.0,  14.0),   # dem_slope_deg: can be steep
        (0.5,   0.29),   # dem_aspect_norm: uniform
    ],
}


def generate_synthetic_training_data(
    n_samples_per_class: int = 600,
    random_state: int = 42,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Generate synthetic labelled training data for the deformation classifier.

    Each class is generated as a multivariate Gaussian with parameters chosen
    to reflect realistic InSAR statistical signatures.  Physical constraints are
    applied after sampling (coherence ∈ [0, 1], slope ∈ [0, 90°], etc.).

    Parameters
    ----------
    n_samples_per_class:
        Number of synthetic pixels per deformation class (5 classes).
        Total dataset size = 5 × n_samples_per_class.
    random_state:
        NumPy random seed for reproducibility.

    Returns
    -------
    X : float32 array of shape ``(5 * n_samples_per_class, 6)``
    y : int32 array of shape ``(5 * n_samples_per_class,)``
    """
    rng = np.random.default_rng(random_state)
    X_parts: list[np.ndarray] = []
    y_parts: list[np.ndarray] = []

    for cls_int, params in _CLASS_PARAMS.items():
        n = n_samples_per_class
        block = np.column_stack([
            rng.normal(mu, sigma, n) for mu, sigma in params
        ]).astype(np.float32)

        # ---- Physical constraints ----
        # Coherence ∈ [0, 1]
        block[:, 3] = np.clip(block[:, 3], 0.0, 1.0)
        # Slope ∈ [0, 90]
        block[:, 4] = np.clip(block[:, 4], 0.0, 90.0)
        # Aspect norm ∈ [0, 1]
        block[:, 5] = np.clip(block[:, 5], 0.0, 1.0)
        # Seasonal amplitude ≥ 0
        block[:, 2] = np.abs(block[:, 2])

        # LINEAR class has bimodal velocity (subsidence + uplift equally likely)
        if cls_int == int(DeformationClass.LINEAR):
            signs = rng.choice([-1.0, 1.0], size=n)
            block[:, 0] = np.abs(block[:, 0]) * signs

        X_parts.append(block)
        y_parts.append(np.full(n, cls_int, dtype=np.int32))

    X = np.vstack(X_parts)
    y = np.concatenate(y_parts)

    # Shuffle to prevent ordering bias during training
    perm = rng.permutation(len(y))
    return X[perm], y[perm]


def train_default_classifier(
    save_path: Path | None = None,
    n_samples_per_class: int = 600,
    random_state: int = 42,
) -> DeformationClassifier:
    """
    Train the default RandomForest classifier on synthetic data and save to disk.

    Parameters
    ----------
    save_path:
        Where to save the pickled model.
        Defaults to the package's ``ml/models/rf_classifier_v1.pkl``.
    n_samples_per_class:
        Training samples per deformation class.
    random_state:
        Random seed for both data generation and the RF estimator.

    Returns
    -------
    Trained ``DeformationClassifier`` (already persisted to disk).
    """
    from sklearn.ensemble import RandomForestClassifier  # type: ignore[import]

    path = save_path or _DEFAULT_MODEL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Generating synthetic training data: %d samples × 5 classes = %d total",
        n_samples_per_class,
        5 * n_samples_per_class,
    )
    X, y = generate_synthetic_training_data(n_samples_per_class, random_state)

    logger.info("Training RandomForestClassifier (n_estimators=200, max_depth=15)…")
    rf = RandomForestClassifier(
        n_estimators=200,
        max_depth=15,
        min_samples_leaf=5,
        class_weight="balanced",
        random_state=random_state,
        n_jobs=-1,
    )
    rf.fit(X, y)

    with open(path, "wb") as fh:
        pickle.dump(rf, fh, protocol=pickle.HIGHEST_PROTOCOL)

    size_kb = path.stat().st_size / 1024
    logger.info("Classifier saved → %s  (%.0f KB)", path, size_kb)
    return DeformationClassifier(rf)


def train_default_anomaly_detector(
    features: np.ndarray,
    contamination: float = 0.05,
    random_state: int = 42,
) -> AnomalyDetector:
    """
    Convenience: fit an IsolationForest on the provided feature matrix.

    Parameters
    ----------
    features:
        ``(n_pixels, n_features)`` float32 array from ``extract_features()``.
    contamination:
        Expected fraction of anomalous pixels.
    random_state:
        Random seed for IsolationForest.

    Returns
    -------
    Fitted ``AnomalyDetector``.
    """
    detector = AnomalyDetector(
        contamination=contamination,
        random_state=random_state,
    )
    return detector.fit(features)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    clf = train_default_classifier()
    logger.info("Done.  Model class counts per class available via clf._model.classes_")
