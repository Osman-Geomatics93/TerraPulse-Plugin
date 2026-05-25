"""
Classification uncertainty quantification for TerraPulse.

Three complementary metrics computed from the class probability matrix
returned by ``DeformationClassifier.predict()``:

entropy
    Shannon entropy (bits) — higher = classifier is less certain.
    Maximum value for 5 classes = log2(5) ≈ 2.32 bits.
confidence
    Maximum class probability — higher = more confident.
margin
    Difference between the top-two class probabilities.
    Low margin → the classifier is torn between two classes.

Typical usage::

    labels, proba = clf.predict(features)
    uc = compute_uncertainty(proba)
    label_raster, entropy_raster, confidence_raster = uncertainty_to_raster(
        labels, proba, valid_mask, shape=(ny, nx)
    )
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class ClassificationUncertainty:
    """Per-pixel uncertainty metrics derived from the probability matrix."""

    entropy: np.ndarray     # (n_pixels,) float32 — Shannon entropy in bits
    confidence: np.ndarray  # (n_pixels,) float32 — max class probability [0, 1]
    margin: np.ndarray      # (n_pixels,) float32 — top-1 minus top-2 probability


def compute_uncertainty(probabilities: np.ndarray) -> ClassificationUncertainty:
    """
    Compute three uncertainty metrics from the class probability matrix.

    Parameters
    ----------
    probabilities:
        Shape ``(n_pixels, n_classes)`` float32 or float64.
        Each row should sum to approximately 1.

    Returns
    -------
    ``ClassificationUncertainty`` with three ``(n_pixels,)`` float32 arrays.

    Raises
    ------
    ValueError
        If ``probabilities`` is not 2-D or has fewer than 2 classes.
    """
    proba = np.asarray(probabilities, dtype=np.float64)

    if proba.ndim != 2:
        raise ValueError(
            f"probabilities must be 2-D (n_pixels, n_classes), got {proba.ndim}D"
        )
    if proba.shape[1] < 2:
        raise ValueError(
            f"At least 2 classes required, got {proba.shape[1]}"
        )

    # Clip to avoid log2(0) = -inf
    proba_safe = np.clip(proba, 1e-12, 1.0)

    # Shannon entropy H = -Σ p·log2(p) in bits
    entropy = (-np.sum(proba_safe * np.log2(proba_safe), axis=1)).astype(np.float32)

    # Confidence: max probability
    confidence = np.max(proba_safe, axis=1).astype(np.float32)

    # Margin: difference between top-two probabilities
    # Sort each row descending, take first two columns
    sorted_desc = np.sort(proba_safe, axis=1)[:, ::-1]
    margin = (sorted_desc[:, 0] - sorted_desc[:, 1]).astype(np.float32)

    return ClassificationUncertainty(
        entropy=entropy,
        confidence=confidence,
        margin=margin,
    )


def uncertainty_to_raster(
    labels: np.ndarray,
    probabilities: np.ndarray,
    valid_mask: np.ndarray,
    shape: tuple[int, int],
    nodata_label: int = -1,
    nodata_float: float = float("nan"),
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Reshape flat ML outputs back into 2-D raster arrays.

    Parameters
    ----------
    labels:
        ``(n_valid,)`` int array from ``DeformationClassifier.predict()``.
    probabilities:
        ``(n_valid, n_classes)`` float32 from ``DeformationClassifier.predict()``.
    valid_mask:
        Flat boolean array of shape ``(ny * nx,)`` — the same mask produced by
        ``pixel_valid_mask()`` or the internal mask of ``extract_features()``.
    shape:
        ``(ny, nx)`` spatial dimensions of the original raster.
    nodata_label:
        Integer fill value for invalid pixels in the label raster. Default ``-1``.
    nodata_float:
        Float fill value for invalid pixels in entropy / confidence rasters. Default NaN.

    Returns
    -------
    label_raster:
        ``(ny, nx)`` int32 — ``DeformationClass`` value, ``nodata_label`` where invalid.
    entropy_raster:
        ``(ny, nx)`` float32 — Shannon entropy in bits, ``nodata_float`` where invalid.
    confidence_raster:
        ``(ny, nx)`` float32 — max class probability, ``nodata_float`` where invalid.

    Raises
    ------
    ValueError
        If ``valid_mask`` length doesn't equal ``ny * nx``, or ``labels`` length
        doesn't equal ``np.sum(valid_mask)``.
    """
    ny, nx = shape
    n_total = ny * nx

    valid_mask = np.asarray(valid_mask, dtype=bool)
    if valid_mask.shape != (n_total,):
        raise ValueError(
            f"valid_mask must be flat with shape ({n_total},), got {valid_mask.shape}"
        )

    n_valid = int(valid_mask.sum())
    if len(labels) != n_valid:
        raise ValueError(
            f"labels length {len(labels)} != n_valid_pixels {n_valid}"
        )

    uc = compute_uncertainty(probabilities)

    # --- label raster ---
    label_flat = np.full(n_total, nodata_label, dtype=np.int32)
    label_flat[valid_mask] = labels.astype(np.int32)
    label_raster = label_flat.reshape(ny, nx)

    # --- entropy raster ---
    entropy_flat = np.full(n_total, nodata_float, dtype=np.float32)
    entropy_flat[valid_mask] = uc.entropy
    entropy_raster = entropy_flat.reshape(ny, nx)

    # --- confidence raster ---
    conf_flat = np.full(n_total, nodata_float, dtype=np.float32)
    conf_flat[valid_mask] = uc.confidence
    confidence_raster = conf_flat.reshape(ny, nx)

    logger.debug(
        "uncertainty_to_raster: shape=%s, valid=%d/%d, "
        "entropy range=[%.3f, %.3f]",
        shape, n_valid, n_total,
        float(uc.entropy.min()), float(uc.entropy.max()),
    )
    return label_raster, entropy_raster, confidence_raster
