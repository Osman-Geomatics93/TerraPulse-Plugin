"""
terrapulse_core — QGIS-independent processing engine for TerraPulse.

This package must never import PyQGIS or Qt. It is designed to run inside
a Docker container, a conda environment, or any plain Python 3.11+ env.
"""

from terrapulse_core.__version__ import (
    __version__,
    QGIS_MIN_VERSION,
    QGIS_MAX_VERSION,
    PYTHON_MIN_VERSION,
    DEFAULT_DOCKER_IMAGE,
    STAC_EXTENSION_URL,
    RISK_V_MAX,
    N_ML_FEATURES,
)

__all__ = [
    "__version__",
    "QGIS_MIN_VERSION",
    "QGIS_MAX_VERSION",
    "PYTHON_MIN_VERSION",
    "DEFAULT_DOCKER_IMAGE",
    "STAC_EXTENSION_URL",
    "RISK_V_MAX",
    "N_ML_FEATURES",
]
