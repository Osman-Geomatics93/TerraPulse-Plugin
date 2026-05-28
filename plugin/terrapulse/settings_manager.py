"""
TerraPulse settings manager.

Single point of access for all persisted settings (QgsSettings).
All QGIS imports are deferred to avoid import-time failures outside QGIS.

Settings keys (all prefixed with "terrapulse/"):
    cdse_username      — Copernicus Data Space username
    cdse_credential    — Copernicus Data Space password (stored in OS keystore via QgsSettings)
    anthropic_key      — Anthropic API key for LLM narrative (optional)
    output_dir         — custom output directory (empty = system temp)
    max_scenes         — maximum scenes per run (default 30)
    preferred_orbit    — "ascending" or "descending" (default "ascending")
    docker_image       — Docker image tag (default "osmanos93/terrapulse-pygmtsar:latest")
    default_mode       — processing mode (default "standard")
    generate_pdf       — bool, whether to generate PDF reports (default False)
"""

from __future__ import annotations

SETTINGS_PREFIX = "terrapulse/"

# Setting key constants — these are QgsSettings key *names*, not secrets.
# The pragma comments suppress detect-secrets false positives.
KEY_CDSE_USERNAME = "cdse_username"
KEY_CDSE_PASSWORD = "cdse_credential"  # pragma: allowlist secret  # nosec B105
KEY_ANTHROPIC_KEY = "anthropic_key"  # pragma: allowlist secret  # nosec B105
KEY_OUTPUT_DIR = "output_dir"
KEY_MAX_SCENES = "max_scenes"
KEY_PREFERRED_ORBIT = "preferred_orbit"
KEY_DOCKER_IMAGE = "docker_image"
KEY_DEFAULT_MODE = "default_mode"
KEY_GENERATE_PDF = "generate_pdf"

_DEFAULT_DOCKER_IMAGE = "osmanos93/terrapulse-pygmtsar:latest"

# Default values
_DEFAULTS: dict[str, object] = {
    KEY_CDSE_USERNAME: "",
    KEY_CDSE_PASSWORD: "",  # pragma: allowlist secret
    KEY_ANTHROPIC_KEY: "",  # pragma: allowlist secret
    KEY_OUTPUT_DIR: "",
    KEY_MAX_SCENES: 30,
    KEY_PREFERRED_ORBIT: "ascending",
    KEY_DOCKER_IMAGE: _DEFAULT_DOCKER_IMAGE,
    KEY_DEFAULT_MODE: "standard",
    KEY_GENERATE_PDF: False,
}


class SettingsManager:
    """
    Typed accessors for TerraPulse QgsSettings.

    All methods are static so they can be called without instantiation:
        user = SettingsManager.cdse_username()
    """

    # ------------------------------------------------------------------
    # Generic get/set
    # ------------------------------------------------------------------

    @staticmethod
    def get(key: str, default: str = "") -> str:
        from qgis.core import QgsSettings  # type: ignore[import]
        return str(QgsSettings().value(f"{SETTINGS_PREFIX}{key}", default))

    @staticmethod
    def set(key: str, value: object) -> None:
        from qgis.core import QgsSettings  # type: ignore[import]
        QgsSettings().setValue(f"{SETTINGS_PREFIX}{key}", value)

    @staticmethod
    def get_int(key: str, default: int = 0) -> int:
        from qgis.core import QgsSettings  # type: ignore[import]
        raw = QgsSettings().value(f"{SETTINGS_PREFIX}{key}", default)
        try:
            return int(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default

    @staticmethod
    def get_bool(key: str, default: bool = False) -> bool:
        from qgis.core import QgsSettings  # type: ignore[import]
        raw = QgsSettings().value(f"{SETTINGS_PREFIX}{key}", default)
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.lower() in ("true", "1", "yes")
        return bool(raw)

    # ------------------------------------------------------------------
    # Typed accessors
    # ------------------------------------------------------------------

    @staticmethod
    def cdse_username() -> str:
        return SettingsManager.get(KEY_CDSE_USERNAME)

    @staticmethod
    def cdse_password() -> str:
        return SettingsManager.get(KEY_CDSE_PASSWORD)

    @staticmethod
    def anthropic_api_key() -> str:
        return SettingsManager.get(KEY_ANTHROPIC_KEY)

    @staticmethod
    def output_dir() -> str:
        return SettingsManager.get(KEY_OUTPUT_DIR)

    @staticmethod
    def max_scenes() -> int:
        return SettingsManager.get_int(KEY_MAX_SCENES, 30)

    @staticmethod
    def preferred_orbit() -> str:
        return SettingsManager.get(KEY_PREFERRED_ORBIT, "ascending")

    @staticmethod
    def docker_image() -> str:
        return SettingsManager.get(KEY_DOCKER_IMAGE, _DEFAULT_DOCKER_IMAGE)

    @staticmethod
    def default_mode() -> str:
        return SettingsManager.get(KEY_DEFAULT_MODE, "standard")

    @staticmethod
    def generate_pdf() -> bool:
        return SettingsManager.get_bool(KEY_GENERATE_PDF, False)

    # ------------------------------------------------------------------
    # Batch save (used by SettingsDialog)
    # ------------------------------------------------------------------

    @staticmethod
    def save_all(
        *,
        cdse_username: str = "",
        cdse_password: str = "",  # pragma: allowlist secret
        anthropic_api_key: str = "",  # pragma: allowlist secret
        output_dir: str = "",
        max_scenes: int = 30,
        preferred_orbit: str = "ascending",
        docker_image: str = _DEFAULT_DOCKER_IMAGE,
        default_mode: str = "standard",
        generate_pdf: bool = False,
    ) -> None:
        """Persist all settings in a single batch."""
        sm = SettingsManager
        sm.set(KEY_CDSE_USERNAME, cdse_username)
        sm.set(KEY_CDSE_PASSWORD, cdse_password)
        sm.set(KEY_ANTHROPIC_KEY, anthropic_api_key)
        sm.set(KEY_OUTPUT_DIR, output_dir)
        sm.set(KEY_MAX_SCENES, max_scenes)
        sm.set(KEY_PREFERRED_ORBIT, preferred_orbit)
        sm.set(KEY_DOCKER_IMAGE, docker_image)
        sm.set(KEY_DEFAULT_MODE, default_mode)
        sm.set(KEY_GENERATE_PDF, generate_pdf)

    @staticmethod
    def has_cdse_credentials() -> bool:
        """Return True if both CDSE username and password are non-empty."""
        return bool(SettingsManager.cdse_username() and SettingsManager.cdse_password())
