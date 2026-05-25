"""Machine learning layer: pixel classification, anomaly detection, and uncertainty."""

from terrapulse_core.ml.anomaly import AnomalyDetector
from terrapulse_core.ml.classifier import DeformationClass, DeformationClassifier
from terrapulse_core.ml.features import (
    FEATURE_NAMES,
    N_FEATURES,
    extract_features,
    pixel_valid_mask,
)
from terrapulse_core.ml.uncertainty import ClassificationUncertainty, compute_uncertainty

__all__ = [
    "DeformationClassifier",
    "DeformationClass",
    "AnomalyDetector",
    "ClassificationUncertainty",
    "compute_uncertainty",
    "extract_features",
    "pixel_valid_mask",
    "N_FEATURES",
    "FEATURE_NAMES",
]
