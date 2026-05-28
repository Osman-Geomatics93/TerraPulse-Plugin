"""
TerraPulse version constants — single source of truth.

All modules that need the version should import from here, never hardcode it.
The pyproject.toml ``version`` field should always match ``__version__``.
"""

from __future__ import annotations

#: Public release version (SemVer)
__version__: str = "0.2.10"

#: Minimum QGIS version required by the plugin
QGIS_MIN_VERSION: str = "3.34"

#: Maximum QGIS version tested against
QGIS_MAX_VERSION: str = "3.99"

#: Minimum Python version required
PYTHON_MIN_VERSION: str = "3.11"

#: Default Docker image for local processing
DEFAULT_DOCKER_IMAGE: str = "osmanos93/terrapulse-pygmtsar:latest"

#: STAC extension URL for TerraPulse custom properties
STAC_EXTENSION_URL: str = (
    "https://terrapulse.example.com/stac-extension/v0.1/schema.json"
)

#: Maximum subsidence velocity used for risk normalisation (mm/yr)
RISK_V_MAX: float = 50.0

#: Number of ML feature dimensions
N_ML_FEATURES: int = 6
