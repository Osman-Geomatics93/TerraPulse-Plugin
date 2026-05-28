"""
Engine IPC (inter-process communication) transport layer.

The QGIS plugin process communicates with the processing engine
(running in a Docker container) via JSON messages over subprocess pipes:

  Plugin spawns:
    docker run --rm -i -v <output_dir>:/output <image> python engine_server.py

  Protocol:
    Plugin → Docker (stdin):  one {"type":"run","data":{...}} JSON line
    Docker → Plugin (stdout): {"type":"progress","data":{...}} lines
                              {"type":"result",  "data":{...}} on success
                              {"type":"error",   "data":{...}} on failure

  Docker stderr is captured and forwarded to logger.warning — it never
  appears on the plugin's stdout or interferes with the IPC channel.

Thread safety:
  ``EngineIPCClient.run()`` is called from ``InSARTask.run()`` which runs
  on a QgsTask background thread.  ``setProgress()`` is Qt-thread-safe.
  Do NOT call any Qt widget methods from within ``EngineIPCClient``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from datetime import datetime

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Docker executable discovery
# ---------------------------------------------------------------------------

# Common Windows Docker Desktop installation paths (tried when docker is not on PATH)
_DOCKER_FALLBACK_PATHS_WIN = [
    r"C:\Program Files\Docker\Docker\resources\bin\docker.exe",
    r"C:\Program Files\Docker\Docker\resources\docker.exe",
]


def _find_docker_exe() -> str:
    """
    Locate the docker executable.

    1. Try ``shutil.which("docker")`` (uses current PATH).
    2. On Windows, also probe common Docker Desktop install locations.

    Returns the executable name/path to use in subprocess calls.
    Falls back to ``"docker"`` so the OS error message is informative.
    """
    found = shutil.which("docker")
    if found:
        return found

    if sys.platform == "win32":
        for candidate in _DOCKER_FALLBACK_PATHS_WIN:
            if os.path.isfile(candidate):
                logger.info("docker not on PATH — using fallback path: %s", candidate)
                return candidate

    return "docker"  # let subprocess raise FileNotFoundError with a clear message


# Resolved once at import time; cached for the process lifetime.
_DOCKER_EXE = _find_docker_exe()

MessageType = Literal["run", "ping", "pong", "progress", "result", "error"]
ProgressCallback = Callable[[dict[str, object]], None]

# How long (seconds) to wait for Docker to start and emit the first message
_STARTUP_TIMEOUT_S = 60
# Maximum wall-clock time for a single run (6 h)
_MAX_RUN_TIMEOUT_S = 6 * 3600


@dataclass
class IPCMessage:
    """A single JSON message in the engine IPC protocol."""

    type: MessageType
    data: dict[str, object]

    def to_json(self) -> str:
        return json.dumps({"type": self.type, "data": self.data})

    @classmethod
    def from_json(cls, raw: str) -> IPCMessage:
        parsed = json.loads(raw)
        return cls(
            type=parsed["type"],  # type: ignore[arg-type]
            data=parsed.get("data", {}),
        )


class EngineIPCError(Exception):
    """Raised when the engine subprocess exits unexpectedly."""


class EngineIPCClient:
    """
    Client for communicating with the TerraPulse processing engine
    running inside a Docker container via subprocess stdin/stdout pipes.

    Usage (from InSARTask.run())::

        client = EngineIPCClient(docker_image="osmanos93/terrapulse-pygmtsar:latest")
        result = client.run(
            aoi_wkt="POLYGON(...)",
            start_date=datetime(2023, 1, 1),
            end_date=datetime(2023, 12, 31),
            mode="standard",
            orbit_direction="ascending",
            max_scenes=24,
            cdse_username="user@email.com",
            cdse_password="secret",
            output_dir=Path("/data/output/run_001"),
            progress_cb=lambda p: task.setProgress(p["percent"]),
        )
        if result["success"]:
            velocity_path = Path(result["velocity_cog"])
    """

    def __init__(
        self,
        docker_image: str = "osmanos93/terrapulse-pygmtsar:latest",
        engine_script: str = "/home/terrapulse/engine_server.py",
    ) -> None:
        self._image = docker_image
        self._engine_script = engine_script
        self._proc: subprocess.Popen[str] | None = None
        self._cancel_requested = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        aoi_wkt: str,
        start_date: datetime,
        end_date: datetime,
        output_dir: Path,
        mode: str = "standard",
        orbit_direction: str = "ascending",
        max_scenes: int = 30,
        cdse_username: str = "",
        cdse_password: str = "",
        progress_cb: ProgressCallback | None = None,
    ) -> dict[str, object]:
        """
        Launch the Docker engine and run the full InSAR pipeline.

        Blocks until the engine emits a ``result`` or ``error`` message,
        or until the process exits unexpectedly.

        Parameters
        ----------
        aoi_wkt:
            WKT polygon in WGS-84 (EPSG:4326).
        start_date / end_date:
            Inclusive time window for Sentinel-1 data selection.
        output_dir:
            Host-side directory. Mounted as ``/output`` inside Docker.
            The engine writes all output files here.
        mode:
            ``"quick"`` | ``"standard"`` | ``"high_precision"``
        orbit_direction:
            ``"ascending"`` or ``"descending"``
        max_scenes:
            Maximum number of SLC scenes to process.
        cdse_username / cdse_password:
            CDSE account credentials for SLC download.
        progress_cb:
            Called on the calling thread for each progress message.
            Receives the ``data`` dict of the progress IPCMessage.
            Must be thread-safe (use ``QgsTask.setProgress()``).

        Returns
        -------
        dict with keys:
          ``success``                (bool)
          ``velocity_cog``           (str path, on success)
          ``coherence_cog``          (str path, on success)
          ``displacement_zarr``      (str path, on success)
          ``n_scenes_processed``     (int)
          ``processing_time_seconds`` (float)
          ``warnings``               (list[str])
          ``error_message``          (str, on failure)

        Never raises — errors are returned in the dict with ``success=False``.
        """
        self._cancel_requested = False
        output_dir.mkdir(parents=True, exist_ok=True)

        if not self.is_docker_available():
            return self._error_result(
                "Docker is not available. Install Docker Desktop and ensure "
                "the Docker daemon is running."
            )

        if not self._image_exists():
            return self._error_result(
                f"Docker image '{self._image}' not found locally.\n"
                "Build it first by running from the TerraPulse repo root:\n"
                f"  docker build -f docker/Dockerfile.pygmtsar -t {self._image} .\n"
                "This takes 5–10 minutes on first build."
            )

        cmd = self._build_docker_cmd(output_dir)
        logger.info("Launching engine: %s", " ".join(cmd))

        run_msg = IPCMessage(
            type="run",
            data={
                "aoi_wkt": aoi_wkt,
                "start_date": start_date.date().isoformat(),
                "end_date": end_date.date().isoformat(),
                "mode": mode,
                "orbit_direction": orbit_direction,
                "max_scenes": max_scenes,
                "cdse_username": cdse_username,
                "cdse_password": cdse_password,
                "output_dir": "/output",
            },
        )

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,  # line-buffered
            )
        except FileNotFoundError:
            return {
                "success": False,
                "error_message": (
                    "docker executable not found. "
                    "Install Docker Desktop and ensure it is on PATH."
                ),
                "velocity_cog": "",
                "coherence_cog": "",
                "displacement_zarr": "",
                "n_scenes_processed": 0,
                "processing_time_seconds": 0.0,
                "warnings": [],
            }

        # Start stderr reader thread (logs Docker daemon messages)
        stderr_thread = threading.Thread(
            target=self._drain_stderr,
            args=(self._proc,),
            daemon=True,
            name="terrapulse-engine-stderr",
        )
        stderr_thread.start()

        # Send the run command and close stdin
        try:
            assert self._proc.stdin is not None
            self._proc.stdin.write(run_msg.to_json() + "\n")
            self._proc.stdin.flush()
            self._proc.stdin.close()
        except BrokenPipeError as exc:
            logger.error("Engine process died before receiving input: %s", exc)
            self._proc.wait()
            return self._error_result(
                "Engine process died before receiving the run command. "
                "Check Docker image is valid: "
                f"`docker run --rm {self._image} python -c 'import terrapulse_core'`"
            )

        # Read progress + result from stdout
        result = self._error_result("Engine produced no output.")
        try:
            assert self._proc.stdout is not None
            for msg in self._iter_messages(self._proc):
                if self._cancel_requested:
                    logger.info("Cancel requested — terminating engine.")
                    self._proc.terminate()
                    return self._error_result("Cancelled by user.")

                if msg.type == "progress":
                    logger.debug(
                        "Engine progress: %.0f%% — %s",
                        msg.data.get("percent", 0),
                        msg.data.get("message", ""),
                    )
                    if progress_cb:
                        progress_cb(msg.data)

                elif msg.type == "result":
                    result = dict(msg.data)
                    if "success" not in result:
                        result["success"] = True
                    break

                elif msg.type == "error":
                    result = {
                        "success": False,
                        "error_message": str(msg.data.get("message", "Unknown error")),
                        "velocity_cog": "",
                        "coherence_cog": "",
                        "displacement_zarr": "",
                        "n_scenes_processed": 0,
                        "processing_time_seconds": 0.0,
                        "warnings": list(msg.data.get("warnings", [])),
                    }
                    break

                elif msg.type == "pong":
                    logger.debug("Engine ping/pong OK")

        except Exception as exc:
            logger.exception("Error reading engine output")
            result = self._error_result(f"Error reading engine output: {exc}")

        finally:
            # Wait for the process to terminate; capture exit code.
            # Wrap in try/except so TimeoutExpired never escapes run() —
            # the docstring guarantees this method never raises.
            try:
                ret = self._proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                logger.warning(
                    "Engine process did not exit within 30 s — sending SIGTERM."
                )
                self._proc.terminate()
                try:
                    ret = self._proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    logger.warning(
                        "Engine process still alive after SIGTERM — sending SIGKILL."
                    )
                    self._proc.kill()
                    ret = self._proc.wait()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error waiting for engine process: %s", exc)
                ret = -1

            stderr_thread.join(timeout=5)
            # Explicitly close subprocess pipes so the GC doesn't have to.
            # Avoids ResourceWarning: unclosed file <_io.TextIOWrapper ...>
            # in the QGIS log after a failed or early-exit run.
            for _pipe in (self._proc.stdin, self._proc.stdout, self._proc.stderr):
                if _pipe is not None:
                    with contextlib.suppress(Exception):
                        _pipe.close()
            if ret not in (0, None) and result.get("success"):
                # Process failed after emitting a result — treat as warning
                result["warnings"] = list(result.get("warnings", [])) + [
                    f"Engine process exited with code {ret}"
                ]
            logger.info("Engine process exited with code %s", ret)

        return result

    def ping(self) -> bool:
        """
        Check if the engine image is reachable by sending a ping command.

        Returns True if the engine responds with ``pong``.
        Returns False on any error (Docker not running, image not found, etc.).
        """
        if not self.is_docker_available():
            return False

        # Ping doesn't write any output; use the platform temp dir as a
        # harmless mount target. Avoids hardcoding "/tmp" (Bandit B108).
        cmd = self._build_docker_cmd(Path(tempfile.gettempdir()))
        ping_msg = IPCMessage(type="ping", data={})

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            stdout, _ = proc.communicate(
                input=ping_msg.to_json() + "\n",
                timeout=30,
            )
            for line in stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = IPCMessage.from_json(line)
                    if msg.type == "pong":
                        return True
                except (json.JSONDecodeError, KeyError):
                    continue
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
        return False

    def cancel(self) -> None:
        """
        Request cancellation of a running job.
        The engine process will be terminated on the next progress read loop.
        """
        self._cancel_requested = True
        if self._proc and self._proc.poll() is None:
            logger.info("Sending SIGTERM to engine process.")
            self._proc.terminate()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_docker_cmd(self, output_dir: Path) -> list[str]:
        """
        Build the ``docker run`` command list.

        If the environment variable ``TERRAPULSE_DEV_SRC`` is set to the
        absolute path of the ``packages/terrapulse_core/src`` directory,
        that directory is mounted into the container as a read-only overlay.
        This lets you iterate on Python code without rebuilding the image.
        """
        cmd = [
            _DOCKER_EXE, "run",
            "--rm",           # remove container on exit
            "-i",             # keep stdin open for IPC
            "-v", f"{output_dir.resolve()}:/output",
            "--env", "TERRAPULSE_CACHE=/output/.cache",
            "--env", "PYTHONUNBUFFERED=1",  # ensure stdout is line-buffered
        ]

        # Dev-mode: live-mount the local terrapulse_core source into the container.
        # Set TERRAPULSE_DEV_SRC=<repo>/packages/terrapulse_core/src to use this.
        dev_src = os.environ.get("TERRAPULSE_DEV_SRC", "").strip()
        if dev_src and os.path.isdir(dev_src):
            cmd += [
                "-v", f"{dev_src}:/dev_src:ro",
                "--env", "PYTHONPATH=/dev_src",  # takes priority over installed pkg
            ]
            logger.info("DEV MODE: mounting host terrapulse_core from %s", dev_src)

        cmd += [self._image, "python", self._engine_script]
        return cmd

    @staticmethod
    def _iter_messages(proc: subprocess.Popen[str]) -> Iterator[IPCMessage]:
        """
        Yield parsed IPCMessages from the process stdout, line by line.
        Skips blank lines and lines that are not valid JSON objects.
        """
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            line = raw_line.strip()
            if not line:
                continue
            try:
                yield IPCMessage.from_json(line)
            except (json.JSONDecodeError, KeyError) as exc:
                logger.warning("Unparseable engine output (skipped): %r — %s", line[:120], exc)

    @staticmethod
    def _drain_stderr(proc: subprocess.Popen[str]) -> None:
        """Read stderr on a background thread and log each line as WARNING."""
        assert proc.stderr is not None
        for line in proc.stderr:
            line = line.rstrip()
            if line:
                logger.warning("[engine stderr] %s", line)

    @staticmethod
    def _error_result(message: str) -> dict[str, object]:
        return {
            "success": False,
            "error_message": message,
            "velocity_cog": "",
            "coherence_cog": "",
            "displacement_zarr": "",
            "n_scenes_processed": 0,
            "processing_time_seconds": 0.0,
            "warnings": [],
        }

    def _image_exists(self) -> bool:
        """
        Return True if the Docker image is available locally.

        Runs ``docker image inspect <image>`` which succeeds instantly if the
        image is already pulled and fails (rc=1) if it is not present locally.
        Does NOT attempt to pull — that would hang indefinitely.
        """
        try:
            result = subprocess.run(
                [_DOCKER_EXE, "image", "inspect", self._image],
                capture_output=True,
                timeout=10,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False

    @staticmethod
    def is_docker_available() -> bool:
        """Return True if ``docker`` CLI is on PATH (or a known location) and the daemon is running."""
        try:
            result = subprocess.run(
                [_DOCKER_EXE, "info"],
                capture_output=True,
                timeout=10,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False
