"""
Tests for terrapulse_core.insar — engine protocol + availability checks.

Phase 0: tests cover the abstract protocol and error handling.
Phase 1: add integration tests with a mock Docker container.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from terrapulse_core.insar.base import (
    BaseInSAREngine,
    EngineResult,
    ProcessingProgress,
)
from terrapulse_core.insar.mintpy_engine import MintPyEngine
from terrapulse_core.insar.openeo_engine import OpenEOEngine
from terrapulse_core.insar.pygmtsar_engine import PyGMTSAREngine
from terrapulse_core.io.engine_ipc import EngineIPCClient, IPCMessage


# ---------------------------------------------------------------------------
# EngineResult
# ---------------------------------------------------------------------------

class TestEngineResult:
    def test_default_state(self, tmp_output_dir: Path) -> None:
        result = EngineResult(output_dir=tmp_output_dir)
        assert result.success is False
        assert result.error_message == ""
        assert result.n_scenes_processed == 0


# ---------------------------------------------------------------------------
# ProcessingProgress
# ---------------------------------------------------------------------------

class TestProcessingProgress:
    def test_fields(self) -> None:
        p = ProcessingProgress(step="coregistration", percent=25.0, message="Coregistering")
        assert p.step == "coregistration"
        assert p.percent == 25.0


# ---------------------------------------------------------------------------
# Concrete engines — phase 0 checks
# ---------------------------------------------------------------------------

class TestMintPyEngine:
    def test_run_raises_not_implemented(self, tmp_output_dir: Path) -> None:
        engine = MintPyEngine()
        with pytest.raises(NotImplementedError, match="Phase 1"):
            engine.run([], tmp_output_dir, "POLYGON((0 0,1 0,1 1,0 1,0 0))")

    def test_estimate_runtime(self) -> None:
        engine = MintPyEngine()
        t = engine.estimate_runtime_minutes(20, 2500.0, "standard")
        assert t > 0


class TestOpenEOEngine:
    def test_run_raises_not_implemented(self, tmp_output_dir: Path) -> None:
        engine = OpenEOEngine()
        with pytest.raises(NotImplementedError, match="Phase 1"):
            engine.run([], tmp_output_dir, "POLYGON((0 0,1 0,1 1,0 1,0 0))")

    def test_estimate_credits(self) -> None:
        engine = OpenEOEngine()
        credits = engine.estimate_credits(24, 2500.0, "standard")
        assert credits > 0

    def test_estimate_runtime(self) -> None:
        engine = OpenEOEngine()
        t = engine.estimate_runtime_minutes(24, 2500.0, "standard")
        assert t > 0


class TestPyGMTSAREngine:
    def test_availability_without_pygmtsar(self) -> None:
        """PyGMTSAR not installed in test env → is_available() returns False."""
        engine = PyGMTSAREngine()
        # In CI (no Docker/PyGMTSAR), this should be False without crashing
        result = engine.is_available()
        assert isinstance(result, bool)

    def test_run_raises_when_unavailable(self, tmp_output_dir: Path) -> None:
        engine = PyGMTSAREngine()
        if engine.is_available():
            pytest.skip("PyGMTSAR is installed — skip unavailability test")
        result = engine.run(
            scene_paths=[],
            output_dir=tmp_output_dir,
            aoi_wkt="POLYGON((0 0,1 0,1 1,0 1,0 0))",
        )
        assert result.success is False
        assert "not installed" in result.error_message.lower()

    def test_estimate_runtime(self) -> None:
        engine = PyGMTSAREngine()
        t = engine.estimate_runtime_minutes(12, 2500.0, "quick")
        assert t > 0


# ---------------------------------------------------------------------------
# Engine IPC
# ---------------------------------------------------------------------------

class TestIPCMessage:
    def test_roundtrip(self) -> None:
        msg = IPCMessage(type="run", data={"scene_count": 12})
        raw = msg.to_json()
        restored = IPCMessage.from_json(raw)
        assert restored.type == "run"
        assert restored.data["scene_count"] == 12

    def test_docker_detection(self) -> None:
        # Should not raise even if Docker is absent
        result = EngineIPCClient.is_docker_available()
        assert isinstance(result, bool)
