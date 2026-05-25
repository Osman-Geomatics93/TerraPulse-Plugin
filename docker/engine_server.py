"""
TerraPulse Engine Server — runs INSIDE the Docker container.

Entry point for the subprocess IPC protocol. Reads one JSON command from
stdin, executes the full InSAR pipeline (STAC query → download → PyGMTSAR),
and writes JSON progress/result lines to stdout.

Protocol:
  stdin  ← one {"type":"run","data":{...}} JSON line from the QGIS plugin
  stdout → {"type":"progress","data":{...}} lines during processing
  stdout → {"type":"result","data":{...}} on success
  stdout → {"type":"error","data":{...}} on failure (then exit 1)

Run inside Docker:
  docker run --rm -i -v /host/output:/output osmanos93/terrapulse-pygmtsar:latest \
      python /home/terrapulse/engine_server.py

NEVER call this script directly in the QGIS plugin process.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from terrapulse_core.insar.base import ProcessingProgress

# Configure logging to stderr so it doesn't pollute the stdout IPC channel
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="[engine_server] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def _emit(msg_type: str, data: dict[str, object]) -> None:
    """Write one JSON IPC message to stdout and flush immediately."""
    print(json.dumps({"type": msg_type, "data": data}), flush=True)


def _emit_progress(
    step: str,
    percent: float,
    message: str = "",
    scene_index: int = 0,
    total_scenes: int = 0,
) -> None:
    _emit("progress", {
        "step": step,
        "percent": percent,
        "message": message,
        "scene_index": scene_index,
        "total_scenes": total_scenes,
    })


def handle_run(data: dict[str, object]) -> None:
    """
    Execute the full InSAR pipeline based on the ``run`` command payload.

    Parameters (all from ``data`` dict):
      aoi_wkt         - WKT polygon in WGS-84
      start_date      - ISO date string "YYYY-MM-DD"
      end_date        - ISO date string "YYYY-MM-DD"
      mode            - "quick" | "standard" | "high_precision"
      orbit_direction - "ascending" | "descending"
      max_scenes      - int (default 30)
      cdse_username   - CDSE account email
      cdse_password   - CDSE account password
      output_dir      - path inside container (e.g. "/output")
    """
    # ----------------------------------------------------------------
    # Parse inputs
    # ----------------------------------------------------------------
    aoi_wkt: str = str(data["aoi_wkt"])
    start_date = datetime.fromisoformat(str(data["start_date"]))
    end_date = datetime.fromisoformat(str(data["end_date"]))
    mode: str = str(data.get("mode", "standard"))
    orbit_direction: str = str(data.get("orbit_direction", "ascending"))
    max_scenes: int = int(data.get("max_scenes", 30))
    cdse_username: str = str(data.get("cdse_username", ""))
    cdse_password: str = str(data.get("cdse_password", ""))
    output_dir = Path(str(data.get("output_dir", "/output")))
    output_dir.mkdir(parents=True, exist_ok=True)

    slc_dir = output_dir / ".cache" / "slc"
    slc_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Run: AOI=%s… mode=%s orbit=%s scenes≤%d",
        aoi_wkt[:60], mode, orbit_direction, max_scenes,
    )

    # ----------------------------------------------------------------
    # Step 1: STAC discovery
    # ----------------------------------------------------------------
    _emit_progress("download", 2.0, "Querying Copernicus STAC catalog…")

    try:
        from terrapulse_core.stac.client import STACClient

        # Extract BBox from WKT (simple approach: parse first/last coords)
        aoi_bbox = _wkt_to_bbox(aoi_wkt)
        client = STACClient()
        stack = client.build_stack(
            aoi=aoi_bbox,
            start_date=start_date,
            end_date=end_date,
            orbit_direction=orbit_direction,
            max_scenes=max_scenes,
        )
        n_scenes = stack.n_scenes
        logger.info("STAC: found %d scenes (%.1f GB)", n_scenes, stack.estimate_total_size_gb())
        _emit_progress(
            "download", 5.0,
            f"Found {n_scenes} scenes ({stack.estimate_total_size_gb():.1f} GB). Starting download…",
            total_scenes=n_scenes,
        )
    except Exception as exc:
        logger.exception("STAC discovery failed")
        _emit("error", {"message": f"STAC query failed: {exc}", "step": "download"})
        sys.exit(1)

    # ----------------------------------------------------------------
    # Step 2: Authenticate with CDSE
    # ----------------------------------------------------------------
    token: str | None = None
    if cdse_username and cdse_password:
        try:
            from terrapulse_core.stac.downloader import CDSEAuth

            auth = CDSEAuth(username=cdse_username, password=cdse_password)
            token = auth.get_token()
            logger.info("CDSE authentication successful.")
        except Exception as exc:
            logger.error("CDSE auth failed: %s", exc)
            _emit("error", {
                "message": f"CDSE authentication failed: {exc}. Check username/password.",
                "step": "download",
            })
            sys.exit(1)
    else:
        logger.warning("No CDSE credentials provided — assuming SLCs are pre-cached.")

    # ----------------------------------------------------------------
    # Step 3: Download SLC scenes
    # ----------------------------------------------------------------
    scene_paths: list[Path] = []
    dl_base_pct = 5.0
    dl_range_pct = 45.0  # download occupies 5–50% of overall progress

    from terrapulse_core.stac.downloader import CDSEDownloader, CDSEDownloadError

    downloader = CDSEDownloader()

    for i, scene in enumerate(stack.scenes):
        scene_pct = dl_base_pct + (i / n_scenes) * dl_range_pct
        _emit_progress(
            "download", scene_pct,
            f"Downloading scene {i + 1}/{n_scenes}: {scene.scene_id}",
            scene_index=i + 1,
            total_scenes=n_scenes,
        )

        # CDSE STAC uses lowercase "product" key for the OData download URL
        product_url = (
            scene.assets.get("product")
            or scene.assets.get("PRODUCT")
            or ""
        )
        if not product_url:
            logger.warning("Scene %s has no download URL — skipping.", scene.scene_id)
            continue

        # Check if SAFE dir already exists in cache
        expected_safe = slc_dir / f"{scene.scene_id}.SAFE"
        if expected_safe.exists():
            logger.info("Cache hit: %s", expected_safe)
            scene_paths.append(expected_safe)
            continue

        if not token:
            logger.warning(
                "No auth token; cannot download %s. Skipping.", scene.scene_id
            )
            continue

        # Throttle progress messages: emit at most once per 1% of scene download.
        # Without throttling a 8 GB scene generates ~2 000 messages at 4 MB/chunk,
        # which fills the 64 KB pipe buffer and deadlocks the Docker container.
        _last_scene_pct: list[float] = [-1.0]

        def _dl_progress(
            done: int, total: int,
            _i: int = i,
            _last: list[float] = _last_scene_pct,
        ) -> None:
            if total <= 0:
                return
            scene_pct_now = done / total * 100.0
            if scene_pct_now - _last[0] < 1.0:   # emit only on each full 1% step
                return
            _last[0] = scene_pct_now
            sub_pct = (done / total) * (dl_range_pct / n_scenes)
            overall = dl_base_pct + (_i / n_scenes) * dl_range_pct + sub_pct
            _emit_progress(
                "download",
                min(overall, dl_base_pct + dl_range_pct - 0.1),
                f"Scene {_i + 1}/{n_scenes}: {done / 1e6:.0f} / {total / 1e6:.0f} MB",
                scene_index=_i + 1,
                total_scenes=n_scenes,
            )

        try:
            safe_dir = downloader.download_and_unzip(
                url=product_url,
                dest_dir=slc_dir,
                token=token,
                scene_id=scene.scene_id,
                progress_cb=_dl_progress,
            )
            scene_paths.append(safe_dir)
        except CDSEDownloadError as exc:
            logger.warning("Failed to download %s: %s", scene.scene_id, exc)
            _emit("error", {
                "message": f"Download failed for {scene.scene_id}: {exc}",
                "step": "download",
            })
            sys.exit(1)

    if not scene_paths:
        _emit("error", {
            "message": "No SLC scenes available for processing after download.",
            "step": "download",
        })
        sys.exit(1)

    _emit_progress("coregistration", 50.0, f"Download complete. Processing {len(scene_paths)} scenes…")
    logger.info("Downloaded %d scenes to %s", len(scene_paths), slc_dir)

    # ----------------------------------------------------------------
    # Step 4: Run PyGMTSAR engine
    # ----------------------------------------------------------------
    try:
        from terrapulse_core.insar.pygmtsar_engine import PyGMTSAREngine

        engine = PyGMTSAREngine()
        if not engine.is_available():
            _emit("error", {
                "message": "PyGMTSAR is not installed in this container. "
                           "Rebuild the Docker image: docker build -f docker/Dockerfile.pygmtsar .",
                "step": "coregistration",
            })
            sys.exit(1)

        def _engine_progress_cb(p: ProcessingProgress) -> None:
            # Map engine's 0–100% to overall 50–95%
            overall = 50.0 + p.percent * 0.45
            _emit_progress(
                p.step, overall, p.message,
                scene_index=p.scene_index,
                total_scenes=p.total_scenes,
            )

        result = engine.run(
            scene_paths=scene_paths,
            output_dir=output_dir,
            aoi_wkt=aoi_wkt,
            mode=mode,  # type: ignore[arg-type]
            progress_cb=_engine_progress_cb,
        )

    except Exception as exc:
        logger.exception("PyGMTSAR engine crashed")
        _emit("error", {
            "message": f"Engine crashed unexpectedly: {exc}",
            "step": "sbas_inversion",
        })
        sys.exit(1)

    # ----------------------------------------------------------------
    # Step 5: Emit final result or error
    # ----------------------------------------------------------------
    if result.success:
        _emit_progress("done", 100.0, "Processing complete.")
        _emit("result", {
            "success": True,
            "velocity_cog": str(result.velocity_cog) if result.velocity_cog else "",
            "coherence_cog": str(result.coherence_cog) if result.coherence_cog else "",
            "displacement_zarr": str(result.displacement_stack) if result.displacement_stack else "",
            "n_scenes_processed": result.n_scenes_processed,
            "processing_time_seconds": result.processing_time_seconds,
            "warnings": result.warnings,
            "error_message": "",
        })
        logger.info("Engine completed successfully in %.1f s", result.processing_time_seconds)
    else:
        _emit("error", {
            "message": result.error_message or "PyGMTSAR processing failed (unknown error).",
            "step": "sbas_inversion",
            "warnings": result.warnings,
        })
        sys.exit(1)


def _wkt_to_bbox(wkt: str) -> Any:  # BBox imported lazily inside the function body
    """
    Parse the bounding box of a WKT polygon string.

    Supports POLYGON((x1 y1, x2 y2, ...)) format.
    Falls back to parsing all coordinate pairs.
    """
    from terrapulse_core.stac.models import BBox

    # Strip "POLYGON((" prefix and "))" suffix
    inner = wkt.strip()
    for prefix in ("POLYGON((", "POLYGON (( "):
        if inner.upper().startswith(prefix.upper()):
            inner = inner[len(prefix):]
            break
    inner = inner.rstrip(") ")

    coords = []
    for pair in inner.split(","):
        pair = pair.strip()
        if pair:
            parts = pair.split()
            if len(parts) >= 2:
                try:
                    coords.append((float(parts[0]), float(parts[1])))
                except ValueError:
                    continue

    if not coords:
        raise ValueError(f"Cannot parse coordinates from WKT: {wkt[:100]}")

    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    return BBox(
        west=min(xs), south=min(ys),
        east=max(xs), north=max(ys),
    )


def main() -> None:
    """Read one JSON command from stdin and dispatch to the handler."""
    logger.info("Engine server started. Waiting for command on stdin…")

    raw_line = sys.stdin.readline()
    if not raw_line.strip():
        _emit("error", {"message": "Empty input received on stdin.", "step": "download"})
        sys.exit(1)

    try:
        msg = json.loads(raw_line)
    except json.JSONDecodeError as exc:
        _emit("error", {"message": f"Invalid JSON input: {exc}", "step": "download"})
        sys.exit(1)

    msg_type = msg.get("type")
    if msg_type == "run":
        handle_run(msg.get("data", {}))
    elif msg_type == "ping":
        _emit("pong", {"status": "ok"})
    else:
        _emit("error", {
            "message": f"Unknown command type: '{msg_type}'. Expected 'run' or 'ping'.",
            "step": "download",
        })
        sys.exit(1)


if __name__ == "__main__":
    main()
