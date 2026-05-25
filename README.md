# TerraPulse 🌍

> AI-powered ground deformation and subsidence intelligence for QGIS

[![CI](https://github.com/terrapulse/terrapulse/actions/workflows/ci.yml/badge.svg)](https://github.com/terrapulse/terrapulse/actions/workflows/ci.yml)
[![Release](https://github.com/terrapulse/terrapulse/actions/workflows/release.yml/badge.svg)](https://github.com/terrapulse/terrapulse/releases)
[![License: GPL-3.0](https://img.shields.io/badge/License-GPL--3.0-blue.svg)](LICENSE)
[![QGIS: 3.34+](https://img.shields.io/badge/QGIS-3.34%20LTR-green.svg)](https://qgis.org)
[![Python: 3.11+](https://img.shields.io/badge/Python-3.11+-yellow.svg)](https://python.org)
[![Tests: 230+](https://img.shields.io/badge/tests-230%2B%20passing-brightgreen.svg)](packages/terrapulse_core/tests)

TerraPulse lets a **planner, engineer, or NGO worker draw an AOI in QGIS, pick a time window, and receive an interpreted deformation map with risk attribution** — no SAR or InSAR expertise required.

---

## What it does

| Step | What happens |
|------|-------------|
| **1. Draw AOI** | Rubber-band polygon on the QGIS canvas, or import from a vector layer |
| **2. STAC discovery** | Queries Sentinel-1 SLC stacks from [Copernicus Data Space](https://dataspace.copernicus.eu/) |
| **3. InSAR processing** | SBAS-InSAR via PyGMTSAR (local Docker) or OpenEO (CDSE cloud) |
| **4. ML classification** | Pixels labelled: Stable / Linear / Seasonal / Accelerating / Anomalous |
| **5. Risk overlay** | OSM buildings, roads, pipelines, and critical nodes ranked by deformation exposure |
| **6. Report** | HTML + optional PDF with LLM-written narrative; YAML recipe + STAC 1.0 item |

---

## Quick start

### Prerequisites

| Requirement | Minimum | Notes |
|-------------|---------|-------|
| QGIS | 3.34 LTR | Plugin interface |
| Python | 3.11 | Core package |
| Docker Desktop | 4.x | Local InSAR processing (optional for cloud mode) |
| CDSE account | — | [Free registration](https://dataspace.copernicus.eu/) for Sentinel-1 access |
| Anthropic API key | — | [Optional](https://console.anthropic.com/) — for AI-written report narrative |

### Install from QGIS Plugin Repository (recommended)

1. Open QGIS → **Plugins → Manage and Install Plugins**
2. Search **"TerraPulse"**
3. Click **Install Plugin**
4. Go to **Plugins → TerraPulse → TerraPulse Settings** and enter your CDSE credentials

### Install from ZIP (latest release)

```bash
# Download the latest release
curl -L https://github.com/terrapulse/terrapulse/releases/latest/download/terrapulse_0.2.0.zip \
     -o terrapulse_0.2.0.zip
```

Then in QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**

### Install Docker image (local processing)

```bash
docker pull terrapulse/pygmtsar:latest
# Or build from source:
docker build -f docker/Dockerfile.pygmtsar -t terrapulse-pygmtsar:latest .
```

---

## First run walkthrough

1. **Open TerraPulse** — toolbar icon or **Plugins → TerraPulse**
2. **Enter settings** — CDSE username + password (first run only)
3. **Draw AOI** — click "✏ Draw on Map", draw a polygon over your area
4. **Set dates** — e.g. 2023-01-01 → 2023-12-31
5. **Discover scenes** — click "🔍 Discover Scenes" to preview the data stack
6. **Run** — click "🚀 Run Analysis" (≈2 hr for Standard mode)
7. **Results dialog** → Add to Map / Classify / Generate Report

---

## Repository structure

```
terrapulse/
├── packages/
│   └── terrapulse_core/       ← Pure Python — QGIS-independent processing engine
│       ├── src/terrapulse_core/
│       │   ├── stac/          STAC discovery + models
│       │   ├── insar/         PyGMTSAR / OpenEO engine wrappers
│       │   ├── io/            COG writer, engine IPC (Docker subprocess)
│       │   ├── ml/            Feature extraction, RF classifier, anomaly detector
│       │   ├── risk/          OSM querier, asset risk ranker
│       │   ├── reporting/     Jinja2/WeasyPrint renderer, LLM client
│       │   └── provenance/    YAML recipe, STAC item writer
│       └── tests/             219+ pytest tests (no QGIS required)
│
├── plugin/
│   └── terrapulse/            ← QGIS plugin (thin PyQt5 wrapper)
│       ├── dialogs/           Main dialog, Settings dialog, Results dialog
│       ├── tasks/             QgsTask: STAC, InSAR, Classify, Report
│       ├── layers/            Layer loaders (velocity, coherence, classification)
│       ├── map_tools/         Rubber-band AOI polygon tool
│       ├── utils/             QGIS message bar helpers
│       └── settings_manager.py  Typed QgsSettings accessors
│
├── docker/
│   ├── Dockerfile.pygmtsar    Local SBAS-InSAR engine (PyGMTSAR + GMTSAR)
│   ├── Dockerfile.mintpy      MintPy alternative engine
│   └── engine_server.py       JSON-over-stdin/stdout IPC server
│
├── .github/workflows/
│   ├── ci.yml                 pytest + ruff + mypy on push/PR
│   └── release.yml            Package zip + GitHub Release + QGIS repo upload
│
└── .plugin-ci.yml             qgis-plugin-ci configuration
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  QGIS process                                                   │
│                                                                 │
│  MainDialog ──► AOIMapTool (rubber band polygon)               │
│      │                                                         │
│      ├──► STACDiscoveryTask ──► terrapulse_core.stac.client    │
│      │                                                         │
│      └──► InSARTask ──► EngineIPCClient ──►  Docker subprocess │
│                                              │                 │
│  ResultsDialog                               │  engine_server  │
│      ├──► ClassifyTask ──► terrapulse_core.ml                 │
│      └──► ReportTask ──► terrapulse_core.{risk,reporting}     │
│                                                                 │
│  SettingsManager ──► QgsSettings (OS user profile)             │
└─────────────────────────────────────────────────────────────────┘

IPC protocol (JSON over stdin/stdout):
  Plugin ──► {"type":"run","data":{...}} ──► docker run -i engine_server.py
  Plugin ◄── {"type":"progress","data":{...}} ◄── per processing step
  Plugin ◄── {"type":"result","data":{...}}  ◄── velocity + coherence COG paths
```

**Key design principle:** `terrapulse_core` is a pure Python package with zero QGIS dependency. It runs inside the Docker container and is tested without QGIS. The plugin is a thin Qt layer that only manages UI state.

---

## Development setup

### 1. Clone

```bash
git clone https://github.com/terrapulse/terrapulse.git
cd terrapulse
```

### 2. Install core package

```bash
cd packages/terrapulse_core
pip install -e ".[dev]"
```

### 3. Run tests

```bash
pytest -v                          # all 219+ tests
pytest tests/test_ml.py -v        # ML module only
pytest -k "not integration" -v    # exclude integration tests
```

### 4. Linting + type checking

```bash
ruff check src/          # PEP 8, imports, type annotations
mypy src/                # strict type checking
```

### 5. Install plugin in QGIS (development)

**Windows:**
```powershell
$PluginDir = "$env:APPDATA\QGIS\QGIS3\profiles\default\python\plugins"
New-Item -ItemType SymbolicLink -Path "$PluginDir\terrapulse" -Target (Resolve-Path plugin\terrapulse)
```

**Linux/macOS:**
```bash
PLUGIN_DIR=~/.local/share/QGIS/QGIS3/profiles/default/python/plugins
ln -s $(pwd)/plugin/terrapulse $PLUGIN_DIR/terrapulse
```

Then in QGIS: **Plugins → Manage and Install Plugins → Installed** → enable TerraPulse.

---

## Configuration

All settings are persisted via QGIS settings (Windows registry / macOS plist / Linux ini file):

| Setting | Key | Default | Notes |
|---------|-----|---------|-------|
| CDSE username | `terrapulse/cdse_username` | *(empty)* | Required |
| CDSE password | `terrapulse/cdse_password` | *(empty)* | Required |
| Anthropic API key | `terrapulse/anthropic_api_key` | *(empty)* | Optional |
| Output directory | `terrapulse/output_dir` | System temp | Empty = auto |
| Max scenes | `terrapulse/max_scenes` | 30 | 6–60 |
| Docker image | `terrapulse/docker_image` | `terrapulse-pygmtsar:latest` | |
| Default mode | `terrapulse/default_mode` | `standard` | |
| Generate PDF | `terrapulse/generate_pdf` | `false` | Requires WeasyPrint |

---

## Processing modes

| Mode | Time | Scene limit | Use case |
|------|------|-------------|----------|
| Quick | ~30 min | 10 | Rapid preview, coarse resolution |
| Standard | ~2 hr | 24 | Default — balanced quality/speed |
| High precision | ~6 hr | 60 | Maximum resolution for final analysis |

---

## Technology stack

| Layer | Library | Version |
|-------|---------|---------|
| InSAR (local) | PyGMTSAR + GMTSAR + SNAPHU | latest |
| InSAR (remote) | OpenEO via CDSE | ≥ 0.31 |
| STAC | pystac-client | ≥ 0.8 |
| Raster I/O | rasterio + odc-stac + xarray | ≥ 1.3 |
| ML | scikit-learn (default) | ≥ 1.5 |
| ML (optional) | PyTorch + Prithvi-EO (IBM/NASA) | — |
| Risk | overpy + GeoPandas | ≥ 0.7 / ≥ 1.0 |
| Reporting | Jinja2 + WeasyPrint + Plotly | ≥ 3.1 / ≥ 62 |
| LLM | anthropic SDK (claude-haiku) | ≥ 0.28 |
| Plugin CI | qgis-plugin-ci | latest |

---

## Release process

Releases are automated via GitHub Actions:

```bash
# 1. Update version in __version__.py and metadata.txt
# 2. Tag and push
git tag v0.2.0
git push origin v0.2.0
# → CI runs tests → release.yml packages zip → uploads to GitHub Releases + QGIS repo
```

Required GitHub secrets:
- `QGIS_PLUGIN_REPO_TOKEN` — from plugins.qgis.org
- `OSGEO_USERNAME` / `OSGEO_PASSWORD` — OSGeo account

---

## Phased roadmap

| Phase | Status | Deliverable |
|-------|--------|-------------|
| 0 — Scaffold | ✅ **Done** | Plugin loads in QGIS; 61 tests; Docker builds |
| 1 — MVP | ✅ **Done** | Draw AOI → SBAS → velocity COG layer in QGIS |
| 2 — ML | ✅ **Done** | 5-class pixel classification + anomaly detection |
| 3 — Reporting | ✅ **Done** | Risk overlay + HTML/PDF report + STAC item |
| 4 — Polish | ✅ **Done** | Settings persistence, qgis-plugin-ci, docs |

---

## Contributing

1. Fork and branch from `develop`
2. All PRs must pass: `ruff check`, `mypy --strict`, `pytest` (219+ tests)
3. New features need tests in `packages/terrapulse_core/tests/`
4. Plugin-only code goes in `plugin/terrapulse/` — no QGIS imports in `terrapulse_core`
5. See [docs/developer-guide.md](docs/developer-guide.md) for detailed guidelines

### Pre-commit hooks

```bash
pre-commit install
pre-commit run --all-files
```

---

## License

**GPL-3.0** — required for all plugins depending on QGIS core libraries.

- Sentinel-1 data: ESA / Copernicus Programme (free, open)
- Foundation model: Prithvi-EO-1.0 (Apache 2.0, IBM/NASA) — optional component
- OSM data: © OpenStreetMap contributors (ODbL)
