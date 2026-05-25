"""
Tests for terrapulse_core version constants.

Ensures that:
- __version__ is a valid SemVer string
- All constants are present and have correct types
- Values are self-consistent (e.g. QGIS_MIN <= QGIS_MAX)
- Constants match pyproject.toml where applicable
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import terrapulse_core
from terrapulse_core.__version__ import (
    DEFAULT_DOCKER_IMAGE,
    N_ML_FEATURES,
    PYTHON_MIN_VERSION,
    QGIS_MAX_VERSION,
    QGIS_MIN_VERSION,
    RISK_V_MAX,
    STAC_EXTENSION_URL,
    __version__,
)


# ---------------------------------------------------------------------------
# Version string format
# ---------------------------------------------------------------------------

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+].+)?$")


class TestVersionString:
    def test_version_is_string(self) -> None:
        assert isinstance(__version__, str)

    def test_version_is_semver(self) -> None:
        assert _SEMVER_RE.match(__version__), (
            f"__version__ {__version__!r} is not a valid SemVer string"
        )

    def test_version_major_is_non_negative(self) -> None:
        major = int(__version__.split(".")[0])
        assert major >= 0

    def test_version_exposed_on_package(self) -> None:
        """terrapulse_core.__version__ re-exports from __version__ module."""
        assert terrapulse_core.__version__ == __version__


# ---------------------------------------------------------------------------
# QGIS version constants
# ---------------------------------------------------------------------------

class TestQGISVersionConstants:
    def test_qgis_min_is_string(self) -> None:
        assert isinstance(QGIS_MIN_VERSION, str)

    def test_qgis_max_is_string(self) -> None:
        assert isinstance(QGIS_MAX_VERSION, str)

    def test_qgis_min_less_than_max(self) -> None:
        """Min version should sort before max version."""
        def _ver(s: str) -> tuple[int, ...]:
            return tuple(int(x) for x in s.split("."))
        assert _ver(QGIS_MIN_VERSION) <= _ver(QGIS_MAX_VERSION)

    def test_qgis_min_is_ltr_or_later(self) -> None:
        """Minimum QGIS is 3.34 (LTR)."""
        major, minor = int(QGIS_MIN_VERSION.split(".")[0]), int(QGIS_MIN_VERSION.split(".")[1])
        assert (major, minor) >= (3, 34)


# ---------------------------------------------------------------------------
# Python version constant
# ---------------------------------------------------------------------------

class TestPythonVersionConstant:
    def test_python_min_is_string(self) -> None:
        assert isinstance(PYTHON_MIN_VERSION, str)

    def test_python_min_is_311_or_later(self) -> None:
        major, minor = int(PYTHON_MIN_VERSION.split(".")[0]), int(PYTHON_MIN_VERSION.split(".")[1])
        assert (major, minor) >= (3, 11)


# ---------------------------------------------------------------------------
# Docker image constant
# ---------------------------------------------------------------------------

class TestDockerImageConstant:
    def test_docker_image_is_string(self) -> None:
        assert isinstance(DEFAULT_DOCKER_IMAGE, str)

    def test_docker_image_has_tag(self) -> None:
        assert ":" in DEFAULT_DOCKER_IMAGE, (
            "DEFAULT_DOCKER_IMAGE should include a tag, e.g. 'image:tag'"
        )

    def test_docker_image_contains_terrapulse(self) -> None:
        assert "terrapulse" in DEFAULT_DOCKER_IMAGE.lower()


# ---------------------------------------------------------------------------
# STAC extension URL
# ---------------------------------------------------------------------------

class TestSTACExtensionURL:
    def test_stac_extension_is_string(self) -> None:
        assert isinstance(STAC_EXTENSION_URL, str)

    def test_stac_extension_is_https(self) -> None:
        assert STAC_EXTENSION_URL.startswith("https://"), (
            "STAC extension URL should use HTTPS"
        )

    def test_stac_extension_contains_terrapulse(self) -> None:
        assert "terrapulse" in STAC_EXTENSION_URL.lower()


# ---------------------------------------------------------------------------
# Numeric constants
# ---------------------------------------------------------------------------

class TestNumericConstants:
    def test_risk_v_max_is_positive_float(self) -> None:
        assert isinstance(RISK_V_MAX, float)
        assert RISK_V_MAX > 0.0

    def test_risk_v_max_reasonable_value(self) -> None:
        """50 mm/yr is the design normalisation ceiling."""
        assert RISK_V_MAX == pytest.approx(50.0)

    def test_n_ml_features_is_int(self) -> None:
        assert isinstance(N_ML_FEATURES, int)

    def test_n_ml_features_equals_six(self) -> None:
        """6 features: velocity, acceleration, seasonal_amp, coherence, slope, aspect."""
        assert N_ML_FEATURES == 6

    def test_n_ml_features_matches_features_module(self) -> None:
        """N_ML_FEATURES must match ml.features.N_FEATURES."""
        from terrapulse_core.ml.features import N_FEATURES
        assert N_ML_FEATURES == N_FEATURES


# ---------------------------------------------------------------------------
# pyproject.toml consistency
# ---------------------------------------------------------------------------

class TestPyprojectConsistency:
    def test_version_matches_pyproject(self) -> None:
        """The version in __version__.py must match pyproject.toml."""
        pyproject = (
            Path(__file__).parent.parent / "pyproject.toml"
        )
        if not pyproject.exists():
            pytest.skip("pyproject.toml not found at expected location")

        content = pyproject.read_text(encoding="utf-8")
        # Find the version line in [project] section
        import re
        match = re.search(r'^\s*version\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
        if match is None:
            pytest.skip("Could not parse version from pyproject.toml")

        pyproject_version = match.group(1)
        assert __version__ == pyproject_version, (
            f"__version__={__version__!r} does not match "
            f"pyproject.toml version={pyproject_version!r}"
        )
