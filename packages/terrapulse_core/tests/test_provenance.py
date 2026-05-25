"""
Tests for terrapulse_core.provenance — recipe writing and STAC stubs.

Phase 0: full RecipeWriter coverage; STACItemWriter stub check.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import yaml

from terrapulse_core.provenance.recipe import RecipeWriter, RunRecipe, SceneRef
from terrapulse_core.provenance.stac_writer import STACItemWriter
from terrapulse_core.stac.models import BBox


class TestRunRecipe:
    def test_default_status(self, run_recipe: RunRecipe) -> None:
        assert run_recipe.status == "planned"

    def test_run_id_is_string(self, run_recipe: RunRecipe) -> None:
        assert isinstance(run_recipe.run_id, str)
        assert len(run_recipe.run_id) > 0

    def test_created_at_is_set(self, run_recipe: RunRecipe) -> None:
        assert run_recipe.created_at != ""


class TestRecipeWriter:
    def test_write_and_load_roundtrip(
        self, run_recipe: RunRecipe, tmp_output_dir: Path
    ) -> None:
        writer = RecipeWriter(tmp_output_dir)
        path = writer.write(run_recipe)
        assert path.exists()
        loaded = writer.load(run_recipe.run_id)
        assert loaded.run_id == run_recipe.run_id
        assert loaded.status == "planned"
        assert loaded.engine == "pygmtsar"

    def test_yaml_is_human_readable(
        self, run_recipe: RunRecipe, tmp_output_dir: Path
    ) -> None:
        writer = RecipeWriter(tmp_output_dir)
        path = writer.write(run_recipe)
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data["status"] == "planned"
        assert data["engine"] == "pygmtsar"
        assert data["mode"] == "standard"

    def test_status_update(
        self, run_recipe: RunRecipe, tmp_output_dir: Path
    ) -> None:
        writer = RecipeWriter(tmp_output_dir)
        writer.write(run_recipe)
        run_recipe.status = "completed"
        run_recipe.velocity_cog = "/tmp/velocity.tif"
        writer.write(run_recipe)
        loaded = writer.load(run_recipe.run_id)
        assert loaded.status == "completed"
        assert loaded.velocity_cog == "/tmp/velocity.tif"

    def test_recipe_with_scenes(
        self, tmp_output_dir: Path
    ) -> None:
        recipe = RunRecipe(
            run_id=str(uuid.uuid4()),
            scenes=[
                SceneRef(
                    scene_id="S1A_001",
                    datetime="2023-01-01T04:00:00+00:00",
                    download_url="https://example.com/slc.zip",
                    sha256="abc123",
                )
            ],
        )
        writer = RecipeWriter(tmp_output_dir)
        path = writer.write(recipe)
        loaded = writer.load(recipe.run_id)
        assert len(loaded.scenes) == 1
        assert loaded.scenes[0]["scene_id"] == "S1A_001"  # type: ignore[index]

    def test_sha256_file(self, tmp_output_dir: Path) -> None:
        test_file = tmp_output_dir / "test.bin"
        test_file.write_bytes(b"terrapulse test data")
        sha = RecipeWriter.sha256_file(test_file)
        assert len(sha) == 64  # SHA-256 hex digest
        assert sha == RecipeWriter.sha256_file(test_file)  # deterministic


class TestSTACItemWriterBasic:
    def test_write_returns_path(
        self, run_recipe: RunRecipe, cairo_bbox: BBox, tmp_output_dir: Path
    ) -> None:
        """STACItemWriter.write() returns a Path that exists."""
        writer = STACItemWriter()
        path = writer.write(run_recipe, cairo_bbox, tmp_output_dir)
        assert path.exists()
        assert path.suffix == ".json"
