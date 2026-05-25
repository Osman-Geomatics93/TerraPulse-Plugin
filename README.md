<div align="center">

# 🌍 TerraPulse

### AI-powered ground deformation & subsidence intelligence for QGIS

*Draw an AOI. Pick a date range. Get a deformation map.*  
*No SAR expertise required.*

<br/>

[![CI](https://github.com/Osman-Geomatics93/TerraPulse-Plugin/actions/workflows/ci.yml/badge.svg)](https://github.com/Osman-Geomatics93/TerraPulse-Plugin/actions/workflows/ci.yml)
[![Release](https://github.com/Osman-Geomatics93/TerraPulse-Plugin/actions/workflows/release.yml/badge.svg)](https://github.com/Osman-Geomatics93/TerraPulse-Plugin/releases)
[![QGIS Plugin](https://img.shields.io/badge/QGIS-Plugin%20Repository-589632?logo=qgis&logoColor=white)](https://plugins.qgis.org/plugins/terrapulse/)
[![Docker Hub](https://img.shields.io/docker/v/osmanos93/terrapulse-pygmtsar?label=Docker%20Hub&color=2496ED&logo=docker&logoColor=white)](https://hub.docker.com/r/osmanos93/terrapulse-pygmtsar)
[![License: GPL-2.0](https://img.shields.io/badge/License-GPL--2.0-blue.svg)](plugin/terrapulse/LICENSE)
[![Tests](https://img.shields.io/badge/tests-241%2B%20passing-brightgreen?logo=pytest&logoColor=white)](packages/terrapulse_core/tests)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://python.org)
[![QGIS](https://img.shields.io/badge/QGIS-3.34%20LTR-589632?logo=qgis&logoColor=white)](https://qgis.org)

<br/>

[**📦 Install Plugin**](#-installation) · [**🐳 Docker Image**](#-docker-engine) · [**📖 Docs**](#-first-run-walkthrough) · [**🤝 Contribute**](#-contributing)

</div>

---

## ✨ What TerraPulse Does

TerraPulse turns **Sentinel-1 SAR time-series** into actionable ground deformation intelligence — directly inside QGIS, with zero satellite imagery expertise required.

<table>
<tr>
<td width="50%">

**🗺️ Draw your Area of Interest**  
Use the built-in rubber-band polygon tool or import any vector layer as your AOI.

**🛰️ Automatic STAC Discovery**  
Queries the Copernicus Data Space Ecosystem catalog and builds an optimal Sentinel-1 SLC stack for your time window.

**⚙️ SBAS-InSAR Processing**  
Runs a full Small Baseline Subset pipeline via PyGMTSAR inside a local Docker container — no cloud subscription needed.

</td>
<td width="50%">

**🤖 ML Deformation Classification**  
Random Forest classifier labels every pixel: Stable / Linear / Seasonal / Accelerating / Anomalous.

**⚠️ Infrastructure Risk Overlay**  
Cross-references OSM buildings, roads, pipelines and critical nodes with deformation rates to produce a composite risk score.

**📄 AI-Written Reports**  
Generates HTML + PDF reports with plain-language narratives powered by Anthropic Claude, plus a YAML provenance recipe and STAC 1.0 item.

</td>
</tr>
</table>

---

## 🔄 Processing Pipeline

```
  ┌──────────────┐     ┌─────────────────┐     ┌──────────────────────────────────┐
  │  QGIS Plugin │     │  Copernicus STAC │     │       Docker Container           │
  │              │     │  (free account)  │     │  osmanos93/terrapulse-pygmtsar   │
  │  1. Draw AOI │────▶│  2. Discover     │────▶│  3. Download SLC scenes          │
  │  2. Set dates│     │     Sentinel-1   │     │  4. Coregistration (PyGMTSAR)    │
  │  3. Click Run│     │     SLC stack    │     │  5. SBAS interferogram stack     │
  └──────────────┘     └─────────────────┘     │  6. Phase unwrapping (SNAPHU)    │
          │                                     │  7. SBAS inversion → velocity    │
          │            ┌─────────────────┐      └──────────────────┬───────────────┘
          │            │  QGIS Results   │                         │
          └───────────▶│                 │◀────── COG velocity ────┘
                       │  8. ML classify │        + coherence raster
                       │  9. Risk overlay│
                       │  10. Report PDF │
                       └─────────────────┘
```

**IPC protocol:** JSON over stdin/stdout — the plugin speaks to the Docker engine via a lightweight pipe, throttled to prevent buffer saturation on large scenes.

---

## ⚡ Performance

| Mode | Typical Time | Scenes | Resolution | Best For |
|------|-------------|--------|-----------|----------|
| 🟢 **Quick** | ~30 min | up to 10 | Coarse | Rapid assessment, preview |
| 🟡 **Standard** | ~2 hours | up to 24 | Medium | Default — balanced quality |
| 🔴 **High Precision** | ~6 hours | up to 60 | Maximum | Final analysis, reporting |

> **4× faster downloads** via parallel HTTP Range requests — a 8 GB SLC scene downloads in minutes, not hours.

---

## 📦 Installation

### Option 1 — QGIS Plugin Repository *(recommended)*

```
QGIS → Plugins → Manage and Install Plugins → Search: "TerraPulse" → Install
```

### Option 2 — Install from ZIP

Download the latest release ZIP and install via **Plugins → Install from ZIP**:

[![Download ZIP](https://img.shields.io/github/v/release/Osman-Geomatics93/TerraPulse-Plugin?label=Download%20ZIP&logo=github&color=181717)](https://github.com/Osman-Geomatics93/TerraPulse-Plugin/releases/latest)

### Option 3 — Clone & symlink *(development)*

```bash
git clone https://github.com/Osman-Geomatics93/TerraPulse-Plugin.git
cd TerraPulse-Plugin
```

**Windows:**
```powershell
$PluginDir = "$env:APPDATA\QGIS\QGIS3\profiles\default\python\plugins"
New-Item -ItemType SymbolicLink -Path "$PluginDir\terrapulse" -Target (Resolve-Path plugin\terrapulse)
```

**Linux / macOS:**
```bash
ln -s $(pwd)/plugin/terrapulse \
      ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/terrapulse
```

---

## 🐳 Docker Engine

The InSAR processing engine runs inside a pre-built Docker image — no GMTSAR or PyGMTSAR installation required on your machine.

```bash
# Pull from Docker Hub (recommended)
docker pull osmanos93/terrapulse-pygmtsar:latest

# Verify it works
docker run --rm osmanos93/terrapulse-pygmtsar:latest \
    python -c "import pygmtsar, terrapulse_core; print('Engine ready ✓')"
```

| Image | Size | Contents |
|-------|------|----------|
| `osmanos93/terrapulse-pygmtsar:latest` | 928 MB | Ubuntu 22.04 · GMT 6.3 · SNAPHU · PyGMTSAR · Python 3.11 |

> 🔗 **Docker Hub:** https://hub.docker.com/r/osmanos93/terrapulse-pygmtsar

---

## 🚀 First Run Walkthrough

After installing the plugin and pulling the Docker image:

| Step | Action |
|------|--------|
| **1** | Open QGIS → click the 🌍 TerraPulse toolbar icon |
| **2** | Go to **Settings** → enter your [free CDSE account](https://dataspace.copernicus.eu/) credentials |
| **3** | Click **✏ Draw on Map** → draw a polygon over your study area |
| **4** | Set **Start date** and **End date** (e.g. 2023-01-01 → 2023-12-31) |
| **5** | Click **🔍 Discover Scenes** → preview the available Sentinel-1 stack |
| **6** | Select processing mode → click **🚀 Run Analysis** |
| **7** | Results dialog opens → **Add to Map** · **Classify** · **Generate Report** |

### Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| QGIS | ≥ 3.34 LTR | [Download](https://qgis.org/download/) |
| Docker Desktop | ≥ 4.x | [Download](https://www.docker.com/products/docker-desktop/) |
| CDSE account | — | [Free registration](https://dataspace.copernicus.eu/) |
| Anthropic API key | — | [Optional](https://console.anthropic.com/) — AI narrative in reports |

---

## 🏗️ Architecture

```
┌──────────────────────────── QGIS process ────────────────────────────────┐
│                                                                           │
│   MainDialog ──► AOIMapTool          SettingsManager ──► QgsSettings     │
│       │                                                                   │
│       ├──► STACDiscoveryTask ────────────────► terrapulse_core.stac      │
│       │         (QgsTask, non-blocking)                                   │
│       │                                                                   │
│       └──► InSARTask ──► EngineIPCClient ──► docker run -i engine_server │
│                 │              │                      │                   │
│                 │              └── JSON/stdin ────────┘                   │
│                 │              ◄── JSON/stdout (progress + result)        │
│                 │                                                         │
│   ResultsDialog ◄──────────────────────────────────────────────────────  │
│       ├──► ClassifyTask ────────► terrapulse_core.ml                     │
│       ├──► ReportTask ──────────► terrapulse_core.{risk, reporting}      │
│       └──► Layer loaders ───────► QgsRasterLayer (COG)                   │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘

         ┌─────────────────── Docker container ──────────────────────┐
         │  engine_server.py                                          │
         │    ├── STACClient → Copernicus STAC API                    │
         │    ├── CDSEDownloader → parallel HTTP Range (4× speed)     │
         │    └── PyGMTSAREngine → coregister → unwrap → invert      │
         └────────────────────────────────────────────────────────────┘
```

**Design principle:** `terrapulse_core` is a pure Python package with **zero QGIS dependency**. It runs inside Docker and is fully tested without QGIS. The plugin is a thin Qt wrapper that only manages UI state and task orchestration.

---

## 🔬 Technology Stack

<table>
<tr><th>Layer</th><th>Technology</th><th>Version</th></tr>
<tr><td>🛰️ InSAR engine (local)</td><td>PyGMTSAR + GMTSAR + SNAPHU</td><td>2024.1.21+</td></tr>
<tr><td>☁️ InSAR engine (cloud)</td><td>OpenEO via CDSE</td><td>≥ 0.31</td></tr>
<tr><td>🗂️ STAC catalog</td><td>pystac-client + pystac</td><td>≥ 0.8</td></tr>
<tr><td>🗺️ Raster I/O</td><td>rasterio + odc-stac + xarray + zarr</td><td>≥ 1.3</td></tr>
<tr><td>🤖 ML classification</td><td>scikit-learn (Random Forest)</td><td>≥ 1.5</td></tr>
<tr><td>🌍 Risk analysis</td><td>overpy + GeoPandas + shapely</td><td>≥ 0.7 / ≥ 1.0</td></tr>
<tr><td>📄 Reporting</td><td>Jinja2 + WeasyPrint + Plotly</td><td>≥ 3.1 / ≥ 62</td></tr>
<tr><td>🧠 LLM narrative</td><td>Anthropic SDK (Claude)</td><td>≥ 0.28</td></tr>
<tr><td>🔌 QGIS plugin CI</td><td>qgis-plugin-ci</td><td>latest</td></tr>
</table>

---

## 🗂️ Repository Structure

```
TerraPulse-Plugin/
│
├── packages/terrapulse_core/          # Pure Python engine (no QGIS dependency)
│   └── src/terrapulse_core/
│       ├── stac/                      # Sentinel-1 STAC discovery + models
│       ├── insar/                     # PyGMTSAR / OpenEO engine wrappers
│       ├── io/                        # COG writer · Docker IPC client
│       ├── ml/                        # Feature extraction · RF classifier
│       ├── risk/                      # OSM querier · asset risk ranker
│       ├── reporting/                 # Jinja2/WeasyPrint · LLM client
│       └── provenance/                # YAML recipe · STAC 1.0 item
│
├── plugin/terrapulse/                 # QGIS plugin (thin PyQt5 wrapper)
│   ├── dialogs/                       # Main · Settings · Results dialogs
│   ├── tasks/                         # QgsTask: STAC · InSAR · Classify · Report
│   ├── layers/                        # Velocity · coherence · classification loaders
│   ├── map_tools/                     # Rubber-band AOI polygon tool
│   └── settings_manager.py           # Typed QgsSettings accessors
│
├── docker/
│   ├── Dockerfile.pygmtsar            # SBAS-InSAR engine image
│   └── engine_server.py              # JSON-over-stdin/stdout IPC server
│
└── .github/workflows/
    ├── ci.yml                         # pytest · ruff · mypy on every push
    └── release.yml                    # ZIP · wheel · GitHub Release · QGIS repo
```

---

## 🛠️ Development Setup

```bash
# 1. Clone
git clone https://github.com/Osman-Geomatics93/TerraPulse-Plugin.git
cd TerraPulse-Plugin

# 2. Install core package in editable mode
cd packages/terrapulse_core
pip install -e ".[dev]"

# 3. Run the full test suite (no QGIS required)
pytest -v                          # 241+ tests
pytest -k "not integration" -v     # unit tests only
pytest tests/test_ml.py -v         # ML module only

# 4. Lint + type-check
ruff check src/
mypy src/ --strict
```

### Release a new version

```bash
# 1. Bump version in __version__.py, pyproject.toml, metadata.txt, CHANGELOG.md
# 2. Tag WITHOUT a "v" prefix (required by qgis-plugin-ci)
git tag 0.2.3
git push origin 0.2.3
# → CI: packages ZIP + wheel → GitHub Release → plugins.qgis.org → Docker Hub
```

---

## ⚙️ Configuration

All settings persist via QGIS settings (Windows registry / macOS plist / Linux ini):

| Setting | Default | Notes |
|---------|---------|-------|
| CDSE username | *(empty)* | Required — [register free](https://dataspace.copernicus.eu/) |
| CDSE password | *(empty)* | Required |
| Anthropic API key | *(empty)* | Optional — enables AI report narrative |
| Docker image | `osmanos93/terrapulse-pygmtsar:latest` | Override for custom builds |
| Output directory | System temp | Leave empty for auto |
| Max scenes | `30` | Range: 6–60 |
| Processing mode | `standard` | quick / standard / high_precision |
| Generate PDF | `false` | Requires WeasyPrint |

---

## 🗺️ Roadmap

| Phase | Status | Deliverable |
|-------|--------|-------------|
| 0 — Scaffold | ✅ Complete | Plugin loads in QGIS · 61 tests · Docker builds |
| 1 — MVP | ✅ Complete | Draw AOI → SBAS → velocity COG in QGIS |
| 2 — ML | ✅ Complete | 5-class pixel classification + anomaly detection |
| 3 — Reporting | ✅ Complete | Risk overlay · HTML/PDF report · STAC 1.0 item |
| 4 — Release | ✅ Complete | QGIS Plugin Repository · Docker Hub · CI/CD |
| 5 — UX Polish | 🔄 Planned | Progress animations · dark theme · tutorial wizard |
| 6 — Cloud Mode | 🔄 Planned | OpenEO / CDSE cloud processing (no Docker required) |

---

## 🤝 Contributing

Contributions are welcome! Please:

1. **Fork** the repository and branch from `main`
2. Ensure all checks pass: `ruff check` · `mypy --strict` · `pytest` (241+ tests)
3. New features must include tests in `packages/terrapulse_core/tests/`
4. Keep QGIS imports out of `terrapulse_core` — it must stay QGIS-free

```bash
# Set up pre-commit hooks
pip install pre-commit
pre-commit install
pre-commit run --all-files
```

**Issues and feature requests** → [GitHub Issues](https://github.com/Osman-Geomatics93/TerraPulse-Plugin/issues)

---

## 📜 License & Credits

**GPL-2.0** — required for QGIS plugin compatibility.

| Component | License | Notes |
|-----------|---------|-------|
| QGIS | GPL-2.0+ | [qgis.org](https://qgis.org) |
| PyGMTSAR | MIT | [github.com/mobigroup/PyGMTSAR](https://github.com/mobigroup/PyGMTSAR) |
| Sentinel-1 data | Copernicus open access | ESA / EU Copernicus Programme |
| scikit-learn | BSD-3 | [scikit-learn.org](https://scikit-learn.org) |
| rasterio | BSD-3 | [rasterio.readthedocs.io](https://rasterio.readthedocs.io) |
| OSM data | ODbL | © OpenStreetMap contributors |

---

<div align="center">

Made with ❤️ by **OSMAN IBRAHIM**  
[plugins.qgis.org/plugins/terrapulse](https://plugins.qgis.org/plugins/terrapulse/) · [Docker Hub](https://hub.docker.com/r/osmanos93/terrapulse-pygmtsar) · [GitHub Releases](https://github.com/Osman-Geomatics93/TerraPulse-Plugin/releases)

</div>
