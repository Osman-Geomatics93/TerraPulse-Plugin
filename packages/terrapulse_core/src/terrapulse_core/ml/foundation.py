"""
Optional foundation model integration: Prithvi-EO-1.0.

Prithvi is a ViT-based geospatial foundation model from IBM/NASA,
pre-trained on HLS (Harmonized Landsat Sentinel-2) + SAR data.
License: Apache 2.0.
HuggingFace: ibm-nasa-geospatial/Prithvi-EO-1.0

This module is OPTIONAL. If torch / huggingface_hub are not installed,
the classifier falls back to the RandomForest path automatically.

Phase 0 stub — implementation in Phase 2.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PRITHVI_REPO_ID = "ibm-nasa-geospatial/Prithvi-EO-1.0"
PRITHVI_EXPECTED_SHA256 = ""  # filled in Phase 2 after pinning weights version


def is_foundation_model_available() -> bool:
    """Return True if torch and huggingface_hub are importable."""
    try:
        import torch  # noqa: F401  # type: ignore[import]
        import huggingface_hub  # noqa: F401  # type: ignore[import]

        return True
    except ImportError:
        return False


def download_prithvi_weights(
    local_dir: Path,
    progress_callback: object | None = None,  # type: ignore[type-arg]
) -> Path:
    """
    Download Prithvi-EO-1.0 weights from HuggingFace Hub.

    Parameters
    ----------
    local_dir:
        Directory to save weights (e.g. ~/.terrapulse/models/prithvi).
    progress_callback:
        Optional callable(bytes_downloaded, total_bytes) for progress reporting.

    Returns
    -------
    Path to the downloaded weights directory.

    Phase 0 stub.
    """
    raise NotImplementedError("Foundation model download is a Phase 2 deliverable.")


class PrithviClassifier:
    """
    Deformation pixel classifier backed by Prithvi-EO-1.0 fine-tuned features.

    Phase 0 stub.
    """

    def __init__(self, weights_dir: Path) -> None:
        raise NotImplementedError("PrithviClassifier is a Phase 2 deliverable.")
