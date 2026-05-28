# TerraPulse Changelog

## 0.2.8 (2026-05-28)

### Added
- STAC client retries transient CDSE failures (HTTP 502/503/504, connection
  resets, gateway timeouts) with exponential backoff: 5s, 20s, 60s. Avoids
  spurious run failures during short CDSE outages.

### Fixed
- CDSE nginx HTML error pages are now parsed and replaced with a clean
  one-line message in the UI: instead of dumping
  `<html><head><title>502 Bad Gateway...` the dialog shows
  `Copernicus Data Space is temporarily unavailable (HTTP 502). ...`

## 0.2.7 (2026-05-28)

### Fixed
- `EngineIPCClient.run()` now explicitly closes the subprocess's stdin/stdout/stderr
  pipes in its `finally:` block, instead of leaving them for Python's garbage
  collector. Silences `ResourceWarning: unclosed file <_io.TextIOWrapper>`
  messages that appeared in QGIS logs after failed or early-exit InSAR runs.

## 0.2.6 (2026-05-28)

### Fixed
- **Release workflow:** plugins.qgis.org rejected the 0.2.5 upload with
  `Fault 1: 'For security reasons, zip file cannot contain .pyc file'`.
  The earlier `Validate __version__.py` workflow step imports `terrapulse_core`
  via `python -c "..."`, which writes `__pycache__/*.pyc` into the source tree.
  The subsequent `cp -r` step copies those `.pyc` files into the vendored
  directory, and qgis-plugin-ci then includes them in the upload.
  release.yml now scrubs `__pycache__/` and `*.pyc` from the vendored copy
  before staging it for git ls-files.

## 0.2.5 (2026-05-28)

### Fixed
- **Release workflow:** `qgis-plugin-ci` enumerates files via `git ls-files`,
  which skips untracked and gitignored paths. The vendored `terrapulse_core/`
  copy added in 0.2.4 is gitignored on purpose (canonical source lives under
  `packages/`), so the zip uploaded to plugins.qgis.org by qgis-plugin-ci did
  not include it — leading to the same `ModuleNotFoundError` 0.2.4 was meant
  to fix. The GitHub release zip was unaffected.
- `release.yml` now runs `git add -f plugin/terrapulse/terrapulse_core` after
  the vendor step so the files appear in `git ls-files` for the CI build only.
  Nothing is committed — the runner is ephemeral.

## 0.2.4 (2026-05-28)

### Fixed
- **Critical packaging bug:** the released QGIS plugin zip was missing `terrapulse_core`,
  causing `ModuleNotFoundError: No module named 'terrapulse_core'` (reported as
  "Docker pre-check failed") on every InSAR run after installing from the
  QGIS Plugin Repository. `release.yml` now vendors `terrapulse_core` into the
  plugin zip; `plugin/terrapulse/__init__.py` finds the bundled copy (production
  install) or repo source tree (dev mode).
- Default Docker image in `insar_task.py` and `settings_dialog.py` corrected
  from bare `terrapulse-pygmtsar:latest` to `osmanos93/terrapulse-pygmtsar:latest`,
  matching the public Docker Hub image.
- `main_dialog` now actually reads `docker_image` and `max_scenes` from
  `SettingsManager` instead of using hardcoded values (settings were ignored).
- "Image not found" error now suggests `docker pull osmanos93/terrapulse-pygmtsar:latest`
  in addition to the `docker build` instruction.
- `ModuleNotFoundError` during pre-check now produces a distinct, actionable
  error message instead of being mislabeled as "Docker pre-check failed".

## 0.2.3 (2026-05-26)

### Fixed
- Security scan: suppress `detect-secrets` false positives in `settings_manager.py`
  (settings key name strings are not credentials; `# pragma: allowlist secret` added)

## 0.2.2 (2026-05-26)

### Changed
- Default Docker image updated to `osmanos93/terrapulse-pygmtsar:latest` (Docker Hub)
- README badges and URLs corrected to Osman-Geomatics93/TerraPulse-Plugin

## 0.2.1 (2026-05-26)

### Changed
- Author name updated to OSMAN IBRAHIM

## 0.2.0 (2026-05-25)

### Fixed
- Download crash at ~16 pct progress (pipe deadlock + double disk usage in parallel HTTP Range download)
- Throttle IPC progress messages to prevent Docker stdout pipe buffer saturation

### Added
- Parallel HTTP Range download for 4x faster Sentinel-1 SLC acquisition
- Full IPC pipeline (Docker subprocess JSON protocol)
- PyGMTSAR engine server, CDSE OAuth2 downloader, COG writer
- STACDiscoveryTask and InSARTask background workers
- AOI rubber-band map tool, velocity/coherence layer loaders
- RandomForest deformation classifier (5 classes: stable/linear/seasonal/accelerating/anomalous)
- OSM infrastructure overlay with composite risk score
- PDF/HTML report renderer with optional AI narrative
- YAML provenance recipe + STAC 1.0 item
- Settings dialog (CDSE credentials, API keys, output directory)
- 241 automated tests, Python 3.11 and 3.12, Ubuntu and Windows CI
