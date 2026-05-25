# TerraPulse Changelog

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
