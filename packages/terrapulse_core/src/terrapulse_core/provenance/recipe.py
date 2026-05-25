"""
YAML recipe writer.

Every TerraPulse run produces a machine-readable YAML recipe that fully
describes the inputs, parameters, and outputs needed to reproduce the result.

Design: the recipe is written BEFORE processing starts (with a "planned" status)
and updated to "completed" or "failed" at the end. This ensures a recipe exists
even if processing crashes midway.
"""

from __future__ import annotations

import hashlib
import logging
import platform
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import yaml

logger = logging.getLogger(__name__)


@dataclass
class SceneRef:
    """Reference to a single input Sentinel-1 scene."""

    scene_id: str
    datetime: str           # ISO-8601
    download_url: str
    sha256: str = ""        # filled after download


@dataclass
class RunRecipe:
    """
    Complete provenance record for a single TerraPulse run.
    Serialisable to YAML with ``RecipeWriter.write``.
    """

    run_id: str                      # UUID4
    status: Literal["planned", "running", "completed", "failed"] = "planned"
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    completed_at: str = ""

    # Inputs
    aoi_wkt: str = ""
    start_date: str = ""
    end_date: str = ""
    scenes: list[SceneRef] = field(default_factory=list)

    # Processing config
    engine: str = "pygmtsar"
    mode: str = "standard"
    terrapulse_version: str = ""
    python_version: str = field(
        default_factory=lambda: platform.python_version()
    )
    platform: str = field(
        default_factory=lambda: platform.system()
    )

    # Outputs
    velocity_cog: str = ""
    coherence_cog: str = ""
    displacement_zarr: str = ""
    report_html: str = ""
    report_pdf: str = ""
    stac_item_json: str = ""

    # Error
    error_message: str = ""
    warnings: list[str] = field(default_factory=list)


class RecipeWriter:
    """Writes and updates RunRecipe YAML files."""

    def __init__(self, output_dir: Path) -> None:
        self._output_dir = output_dir
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def recipe_path(self, run_id: str) -> Path:
        return self._output_dir / f"recipe_{run_id}.yaml"

    def write(self, recipe: RunRecipe) -> Path:
        """Serialise recipe to YAML. Overwrites if exists (for status updates)."""
        path = self.recipe_path(recipe.run_id)
        data = asdict(recipe)
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=True)
        logger.debug("Recipe written: %s", path)
        return path

    def load(self, run_id: str) -> RunRecipe:
        """Load an existing recipe from disk."""
        path = self.recipe_path(run_id)
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return RunRecipe(**data)

    @staticmethod
    def sha256_file(path: Path) -> str:
        """Compute SHA-256 of a file. Used to verify downloaded scenes."""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
