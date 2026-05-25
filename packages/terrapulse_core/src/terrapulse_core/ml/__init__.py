"""Machine learning layer: pixel classification, anomaly detection, and uncertainty."""

from terrapulse_core.ml.classifier import DeformationClassifier, DeformationClass
from terrapulse_core.ml.anomaly import AnomalyDetector
from terrapulse_core.ml.uncertainty import ClassificationUncertainty, compute_uncertainty
from terrapulse_core.ml.features import extract_features, pixel_valid_mask, N_FEATURES, FEATURE_NAMES

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
