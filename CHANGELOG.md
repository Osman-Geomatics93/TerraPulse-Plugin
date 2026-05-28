# TerraPulse Changelog

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
