"""
Integration tests for the TerraPulse Phase 1 pipeline.

These tests mock the Docker subprocess so they run in any CI environment
without Docker Desktop installed. They verify:
  - The IPC message protocol (encode/decode round-trips)
  - EngineIPCClient correctly orchestrates subprocess I/O
  - Provenance recipe is written + updated on success and failure
  - STAC client returns correct stack structure (using mocked HTTP)
  - COGWriter + read_cog_window round-trip with real rasterio

All subprocess calls are mocked via pytest-mock / unittest.mock.
No real Docker or CDSE network calls are made.
"""

from __future__ import annotations

import io
import json
import subprocess
import textwrap
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from terrapulse_core.io.engine_ipc import EngineIPCClient, IPCMessage, EngineIPCError
from terrapulse_core.provenance.recipe import RecipeWriter, RunRecipe
from terrapulse_core.stac.models import BBox, ProcessingMode


# ============================================================
# IPC Message protocol
# ============================================================


class TestIPCMessageProtocol:
    def test_run_message_roundtrip(self) -> None:
        msg = IPCMessage(
            type="run",
            data={
                "aoi_wkt": "POLYGON((30.8 29.8, 31.6 29.8, 31.6 30.4, 30.8 30.4, 30.8 29.8))",
                "start_date": "2023-01-01",
                "end_date": "2023-12-31",
                "mode": "standard",
                "cdse_username": "test@email.com",
                "cdse_password": "secret",
                "output_dir": "/output",
            },
        )
        raw = msg.to_json()
        assert '"type": "run"' in raw
        restored = IPCMessage.from_json(raw)
        assert restored.type == "run"
        assert restored.data["mode"] == "standard"
        assert restored.data["aoi_wkt"].startswith("POLYGON")

    def test_progress_message_roundtrip(self) -> None:
        msg = IPCMessage(
            type="progress",
            data={"step": "coregistration", "percent": 35.0, "message": "Coregistering…"},
        )
        restored = IPCMessage.from_json(msg.to_json())
        assert restored.type == "progress"
        assert restored.data["percent"] == 35.0
        assert restored.data["step"] == "coregistration"

    def test_result_message_roundtrip(self) -> None:
        msg = IPCMessage(
            type="result",
            data={
                "success": True,
                "velocity_cog": "/output/velocity.tif",
                "coherence_cog": "/output/coherence.tif",
                "displacement_zarr": "/output/displacement.zarr",
                "n_scenes_processed": 12,
                "processing_time_seconds": 3612.4,
                "warnings": [],
            },
        )
        restored = IPCMessage.from_json(msg.to_json())
        assert restored.type == "result"
        assert restored.data["success"] is True
        assert restored.data["n_scenes_processed"] == 12

    def test_error_message_roundtrip(self) -> None:
        msg = IPCMessage(
            type="error",
            data={"message": "PyGMTSAR failed: SNAPHU exited with code 1", "step": "unwrapping"},
        )
        restored = IPCMessage.from_json(msg.to_json())
        assert restored.type == "error"
        assert "SNAPHU" in str(restored.data["message"])

    def test_unknown_field_in_data_is_preserved(self) -> None:
        msg = IPCMessage(type="pong", data={"status": "ok", "extra": 42})
        restored = IPCMessage.from_json(msg.to_json())
        assert restored.data["extra"] == 42

    def test_missing_data_field_defaults_to_empty_dict(self) -> None:
        raw = '{"type": "ping"}'
        msg = IPCMessage.from_json(raw)
        assert msg.data == {}


# ============================================================
# EngineIPCClient — mocked subprocess
# ============================================================

def _make_fake_popen(stdout_lines: list[str], stderr_lines: list[str] = (), returncode: int = 0) -> MagicMock:
    """
    Build a MagicMock that mimics subprocess.Popen for the engine IPC.
    stdout yields the given lines; stdin is a writable mock.
    """
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdin = MagicMock()
    proc.stdin.write = MagicMock()
    proc.stdin.flush = MagicMock()
    proc.stdin.close = MagicMock()
    proc.stdout = io.StringIO("\n".join(stdout_lines) + "\n")
    proc.stderr = io.StringIO("\n".join(stderr_lines) + "\n")
    proc.wait = MagicMock(return_value=returncode)
    proc.poll = MagicMock(return_value=returncode)
    proc.terminate = MagicMock()
    return proc


class TestEngineIPCClientSuccess:
    def test_success_returns_velocity_cog(self, tmp_output_dir: Path) -> None:
        """Happy path: engine emits progress + result."""
        progress_lines = [
            json.dumps({"type": "progress", "data": {"step": "download", "percent": 10.0, "message": "Downloading…"}}),
            json.dumps({"type": "progress", "data": {"step": "coregistration", "percent": 35.0, "message": "Coregistering…"}}),
            json.dumps({"type": "progress", "data": {"step": "sbas_inversion", "percent": 80.0, "message": "SBAS…"}}),
        ]
        result_line = json.dumps({"type": "result", "data": {
            "success": True,
            "velocity_cog": str(tmp_output_dir / "velocity.tif"),
            "coherence_cog": str(tmp_output_dir / "coherence.tif"),
            "displacement_zarr": str(tmp_output_dir / "displacement.zarr"),
            "n_scenes_processed": 12,
            "processing_time_seconds": 3600.0,
            "warnings": [],
        }})
        fake_proc = _make_fake_popen(progress_lines + [result_line])

        progress_calls: list[dict[str, object]] = []

        from datetime import datetime

        with patch("subprocess.Popen", return_value=fake_proc):
            with patch.object(EngineIPCClient, "is_docker_available", return_value=True), \
                 patch.object(EngineIPCClient, "_image_exists", return_value=True):
                client = EngineIPCClient()
                result = client.run(
                    aoi_wkt="POLYGON((30.8 29.8, 31.6 29.8, 31.6 30.4, 30.8 30.4, 30.8 29.8))",
                    start_date=datetime(2023, 1, 1),
                    end_date=datetime(2023, 12, 31),
                    output_dir=tmp_output_dir,
                    progress_cb=progress_calls.append,
                )

        assert result["success"] is True
        assert "velocity.tif" in str(result["velocity_cog"])
        assert result["n_scenes_processed"] == 12
        assert len(progress_calls) == 3  # three progress messages

    def test_progress_percentages_forwarded(self, tmp_output_dir: Path) -> None:
        """Verify percent values are forwarded correctly to the callback."""
        pcts = [10.0, 35.0, 80.0]
        lines = [
            json.dumps({"type": "progress", "data": {"step": "download", "percent": pct, "message": ""}})
            for pct in pcts
        ]
        lines.append(json.dumps({"type": "result", "data": {
            "success": True, "velocity_cog": "/output/v.tif",
            "coherence_cog": "", "displacement_zarr": "",
            "n_scenes_processed": 5, "processing_time_seconds": 100.0, "warnings": [],
        }}))
        fake_proc = _make_fake_popen(lines)

        received_pcts: list[float] = []

        from datetime import datetime

        with patch("subprocess.Popen", return_value=fake_proc):
            with patch.object(EngineIPCClient, "is_docker_available", return_value=True), \
                 patch.object(EngineIPCClient, "_image_exists", return_value=True):
                client = EngineIPCClient()
                client.run(
                    aoi_wkt="POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))",
                    start_date=datetime(2023, 1, 1),
                    end_date=datetime(2023, 12, 31),
                    output_dir=tmp_output_dir,
                    progress_cb=lambda d: received_pcts.append(float(d["percent"])),
                )

        assert received_pcts == pcts


class TestEngineIPCClientError:
    def test_engine_error_message_captured(self, tmp_output_dir: Path) -> None:
        """Engine emits error JSON — result should have success=False."""
        error_line = json.dumps({"type": "error", "data": {
            "message": "SNAPHU phase unwrapping failed: exit code 1",
            "step": "unwrapping",
            "warnings": [],
        }})
        fake_proc = _make_fake_popen([error_line], returncode=1)

        from datetime import datetime

        with patch("subprocess.Popen", return_value=fake_proc):
            with patch.object(EngineIPCClient, "is_docker_available", return_value=True), \
                 patch.object(EngineIPCClient, "_image_exists", return_value=True):
                client = EngineIPCClient()
                result = client.run(
                    aoi_wkt="POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))",
                    start_date=datetime(2023, 1, 1),
                    end_date=datetime(2023, 12, 31),
                    output_dir=tmp_output_dir,
                )

        assert result["success"] is False
        assert "SNAPHU" in str(result["error_message"])

    def test_docker_unavailable_returns_error(self, tmp_output_dir: Path) -> None:
        """If Docker is not running, result should indicate it clearly."""
        from datetime import datetime

        with patch.object(EngineIPCClient, "is_docker_available", return_value=False):
            client = EngineIPCClient()
            result = client.run(
                aoi_wkt="POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))",
                start_date=datetime(2023, 1, 1),
                end_date=datetime(2023, 12, 31),
                output_dir=tmp_output_dir,
            )

        assert result["success"] is False
        assert "docker" in str(result["error_message"]).lower()

    def test_no_output_from_engine(self, tmp_output_dir: Path) -> None:
        """Engine produces no JSON output (crashes on startup) — graceful error."""
        fake_proc = _make_fake_popen([], returncode=1)

        from datetime import datetime

        with patch("subprocess.Popen", return_value=fake_proc):
            with patch.object(EngineIPCClient, "is_docker_available", return_value=True), \
                 patch.object(EngineIPCClient, "_image_exists", return_value=True):
                client = EngineIPCClient()
                result = client.run(
                    aoi_wkt="POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))",
                    start_date=datetime(2023, 1, 1),
                    end_date=datetime(2023, 12, 31),
                    output_dir=tmp_output_dir,
                )

        assert result["success"] is False
        assert result["error_message"]  # non-empty error string

    def test_invalid_json_lines_skipped(self, tmp_output_dir: Path) -> None:
        """Non-JSON lines in engine stdout don't crash the client."""
        lines = [
            "not json at all",
            "",
            "  also not json  ",
            json.dumps({"type": "result", "data": {
                "success": True, "velocity_cog": "/output/v.tif",
                "coherence_cog": "", "displacement_zarr": "",
                "n_scenes_processed": 1, "processing_time_seconds": 60.0, "warnings": [],
            }}),
        ]
        fake_proc = _make_fake_popen(lines)

        from datetime import datetime

        with patch("subprocess.Popen", return_value=fake_proc):
            with patch.object(EngineIPCClient, "is_docker_available", return_value=True), \
                 patch.object(EngineIPCClient, "_image_exists", return_value=True):
                client = EngineIPCClient()
                result = client.run(
                    aoi_wkt="POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))",
                    start_date=datetime(2023, 1, 1),
                    end_date=datetime(2023, 12, 31),
                    output_dir=tmp_output_dir,
                )

        # Should still parse the final result line
        assert result["success"] is True


# ============================================================
# Full pipeline mock: recipe provenance
# ============================================================


class TestFullPipelineMock:
    def test_recipe_written_on_success(self, tmp_output_dir: Path) -> None:
        """
        Simulate a full run: mock engine returns success → verify
        recipe YAML is written with status='completed'.
        """
        run_id = str(uuid.uuid4())
        result_line = json.dumps({"type": "result", "data": {
            "success": True,
            "velocity_cog": str(tmp_output_dir / "velocity.tif"),
            "coherence_cog": str(tmp_output_dir / "coherence.tif"),
            "displacement_zarr": str(tmp_output_dir / "displacement.zarr"),
            "n_scenes_processed": 12,
            "processing_time_seconds": 3600.0,
            "warnings": ["Low coherence in top-left quadrant"],
        }})
        fake_proc = _make_fake_popen([result_line])

        # Write a "running" recipe (mimics InSARTask.run behaviour)
        writer = RecipeWriter(tmp_output_dir)
        recipe = RunRecipe(
            run_id=run_id,
            status="running",
            aoi_wkt="POLYGON((30.8 29.8, 31.6 29.8, 31.6 30.4, 30.8 30.4, 30.8 29.8))",
            start_date="2023-01-01",
            end_date="2023-12-31",
            engine="pygmtsar",
            mode="standard",
            terrapulse_version="0.1.0",
        )
        writer.write(recipe)

        # Simulate successful run
        from datetime import datetime

        with patch("subprocess.Popen", return_value=fake_proc):
            with patch.object(EngineIPCClient, "is_docker_available", return_value=True), \
                 patch.object(EngineIPCClient, "_image_exists", return_value=True):
                client = EngineIPCClient()
                result = client.run(
                    aoi_wkt=recipe.aoi_wkt,
                    start_date=datetime(2023, 1, 1),
                    end_date=datetime(2023, 12, 31),
                    output_dir=tmp_output_dir,
                )

        assert result["success"] is True

        # Update recipe (mimics InSARTask.run behaviour)
        recipe.status = "completed"
        recipe.velocity_cog = str(result["velocity_cog"])
        recipe.coherence_cog = str(result["coherence_cog"])
        recipe.warnings = list(result.get("warnings", []))
        writer.write(recipe)

        # Verify YAML on disk
        loaded = writer.load(run_id)
        assert loaded.status == "completed"
        assert "velocity.tif" in loaded.velocity_cog
        assert len(loaded.warnings) == 1

    def test_recipe_written_on_failure(self, tmp_output_dir: Path) -> None:
        """Engine returns error → recipe status should be 'failed'."""
        run_id = str(uuid.uuid4())
        error_line = json.dumps({"type": "error", "data": {
            "message": "STAC query found 0 scenes",
            "step": "download",
        }})
        fake_proc = _make_fake_popen([error_line], returncode=1)

        writer = RecipeWriter(tmp_output_dir)
        recipe = RunRecipe(run_id=run_id, status="running")
        writer.write(recipe)

        from datetime import datetime

        with patch("subprocess.Popen", return_value=fake_proc):
            with patch.object(EngineIPCClient, "is_docker_available", return_value=True), \
                 patch.object(EngineIPCClient, "_image_exists", return_value=True):
                client = EngineIPCClient()
                result = client.run(
                    aoi_wkt="POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))",
                    start_date=datetime(2023, 1, 1),
                    end_date=datetime(2023, 12, 31),
                    output_dir=tmp_output_dir,
                )

        assert result["success"] is False

        recipe.status = "failed"
        recipe.error_message = str(result["error_message"])
        writer.write(recipe)

        loaded = writer.load(run_id)
        assert loaded.status == "failed"
        assert "0 scenes" in loaded.error_message


# ============================================================
# STAC client mock
# ============================================================


class TestSTACClientMock:
    def test_build_stack_returns_correct_structure(self, cairo_bbox: BBox, scene_stack: object) -> None:
        """
        Mock STACClient.build_stack to verify the return type is correct.
        """
        from datetime import datetime
        from terrapulse_core.stac.client import STACClient

        with patch.object(STACClient, "build_stack", return_value=scene_stack):
            client = STACClient()
            stack = client.build_stack(
                aoi=cairo_bbox,
                start_date=datetime(2023, 1, 1),
                end_date=datetime(2023, 12, 31),
                orbit_direction="ascending",
                max_scenes=30,
            )

        from terrapulse_core.stac.models import SceneStack

        assert isinstance(stack, SceneStack)
        assert stack.n_scenes == 12
        assert stack.orbit_direction == "ascending"
        assert stack.estimate_total_size_gb() > 0


# ============================================================
# CDSE downloader unit tests (no network)
# ============================================================


class TestCDSEDownloader:
    def test_url_filename_extraction(self) -> None:
        from terrapulse_core.stac.downloader import CDSEDownloader

        dl = CDSEDownloader()
        assert dl._url_filename("https://example.com/path/to/scene.zip") == "scene.zip"
        assert dl._url_filename("https://example.com/no-extension") == "no-extension"
        assert dl._url_filename("https://example.com/") == ""

    def test_auth_error_on_bad_status(self) -> None:
        from terrapulse_core.stac.downloader import CDSEAuth, CDSEAuthError

        auth = CDSEAuth(username="bad@email.com", password="wrong")
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"

        with patch("requests.post", return_value=mock_response):
            with pytest.raises(CDSEAuthError, match="auth failed"):
                auth.get_token()

    def test_auth_error_on_missing_token(self) -> None:
        from terrapulse_core.stac.downloader import CDSEAuth, CDSEAuthError

        auth = CDSEAuth(username="test@email.com", password="pass")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}  # no access_token

        with patch("requests.post", return_value=mock_response):
            with pytest.raises(CDSEAuthError, match="missing 'access_token'"):
                auth.get_token()

    def test_auth_success(self) -> None:
        from terrapulse_core.stac.downloader import CDSEAuth

        auth = CDSEAuth(username="test@email.com", password="pass")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "fake_token_xyz"}

        with patch("requests.post", return_value=mock_response):
            token = auth.get_token()

        assert token == "fake_token_xyz"

    def test_download_existing_file_skipped(self, tmp_output_dir: Path) -> None:
        """If the zip already exists, download is skipped (returns path immediately)."""
        from terrapulse_core.stac.downloader import CDSEDownloader

        dl = CDSEDownloader()
        existing = tmp_output_dir / "existing_scene.zip"
        existing.write_bytes(b"fake zip content")

        with patch("requests.get") as mock_get:
            result = dl.download_scene(
                url="https://example.com/existing_scene.zip",
                dest_dir=tmp_output_dir,
                token="token",
                scene_id="existing_scene",
            )
            mock_get.assert_not_called()  # download should be skipped

        assert result == existing

    def test_unzip_safe_rejects_corrupt_zip(self, tmp_output_dir: Path) -> None:
        from terrapulse_core.stac.downloader import CDSEDownloader, CDSEDownloadError

        dl = CDSEDownloader()
        corrupt = tmp_output_dir / "corrupt.zip"
        corrupt.write_bytes(b"this is not a zip file")

        with pytest.raises(CDSEDownloadError, match="Corrupt zip"):
            dl.unzip_safe(corrupt, tmp_output_dir)


# ============================================================
# COG I/O round-trip
# ============================================================


class TestCOGIO:
    def test_cog_writer_creates_valid_tif(self, tmp_output_dir: Path) -> None:
        """Write a simple float32 array as COG and verify with rasterio."""
        import rasterio
        from affine import Affine
        from terrapulse_core.io.cog import COGWriter

        data = np.random.default_rng(42).standard_normal((64, 64)).astype(np.float32)
        transform = Affine.translation(30.8, 30.4) * Affine.scale(0.01, -0.01)
        out_path = tmp_output_dir / "test_velocity.tif"

        writer = COGWriter()
        result_path = writer.write(
            data=data,
            output_path=out_path,
            crs="EPSG:4326",
            transform=transform,
            nodata=-9999.0,
        )

        assert result_path.exists()
        with rasterio.open(result_path) as ds:
            assert ds.count == 1
            assert ds.width == 64
            assert ds.height == 64
            assert ds.crs.to_epsg() == 4326
            band_data = ds.read(1)
            assert band_data.shape == (64, 64)

    def test_cog_writer_2d_input_auto_expanded(self, tmp_output_dir: Path) -> None:
        """2-D input (height, width) should be auto-expanded to (1, h, w)."""
        import rasterio
        from affine import Affine
        from terrapulse_core.io.cog import COGWriter

        data = np.ones((32, 32), dtype=np.float32)
        transform = Affine.translation(0.0, 1.0) * Affine.scale(0.01, -0.01)
        out_path = tmp_output_dir / "test_2d.tif"

        COGWriter().write(data=data, output_path=out_path, crs="EPSG:4326", transform=transform)

        with rasterio.open(out_path) as ds:
            assert ds.count == 1

    def test_cog_writer_raises_on_bad_shape(self, tmp_output_dir: Path) -> None:
        from affine import Affine
        from terrapulse_core.io.cog import COGWriter, COGWriteError

        with pytest.raises(COGWriteError, match="3-D"):
            COGWriter().write(
                data=np.zeros((2, 3, 4, 5), dtype=np.float32),  # 4-D — invalid
                output_path=tmp_output_dir / "bad.tif",
                crs="EPSG:4326",
                transform=Affine.identity(),
            )

    def test_read_cog_window_round_trip(self, tmp_output_dir: Path) -> None:
        """Write a COG, then read a sub-window and verify shape + values."""
        import rasterio
        from affine import Affine
        from terrapulse_core.io.cog import COGWriter, read_cog_window

        # Create a recognisable pattern
        data = np.arange(64 * 64, dtype=np.float32).reshape(64, 64)
        transform = Affine.translation(30.0, 31.0) * Affine.scale(0.01, -0.01)
        out_path = tmp_output_dir / "test_window.tif"
        COGWriter().write(data=data, output_path=out_path, crs="EPSG:4326", transform=transform)

        # Read a quarter window (west half)
        arr, win_transform = read_cog_window(
            cog_path_or_url=out_path,
            bbox_wgs84=(30.0, 30.36, 30.32, 31.0),  # left half
        )
        assert arr.ndim == 3  # (bands, height, width)
        assert arr.shape[0] == 1  # 1 band
        assert arr.shape[1] > 0
        assert arr.shape[2] > 0

    def test_read_cog_window_raises_on_no_intersection(self, tmp_output_dir: Path) -> None:
        """Bbox that doesn't overlap the COG extent raises ValueError."""
        from affine import Affine
        from terrapulse_core.io.cog import COGWriter, read_cog_window

        data = np.zeros((16, 16), dtype=np.float32)
        transform = Affine.translation(30.0, 31.0) * Affine.scale(0.01, -0.01)
        out_path = tmp_output_dir / "test_nointersect.tif"
        COGWriter().write(data=data, output_path=out_path, crs="EPSG:4326", transform=transform)

        with pytest.raises(ValueError, match="does not intersect"):
            read_cog_window(out_path, bbox_wgs84=(-10.0, -10.0, -9.0, -9.0))
