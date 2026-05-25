# terrapulse-core

Core InSAR processing, ML classification, risk attribution, and reporting
engine for TerraPulse. **QGIS-independent** — installable in any Python 3.11+
environment, Docker container, or conda env.

## Architecture

```
terrapulse_core/
├── stac/        ← STAC catalog queries (pystac-client, CDSE)
├── insar/       ← InSAR engine abstraction (PyGMTSAR / MintPy / OpenEO)
├── ml/          ← Pixel classification + anomaly detection
├── risk/        ← OSM overlay + asset-at-risk ranking
├── reporting/   ← Jinja2 HTML → WeasyPrint PDF + LLM narrative
├── provenance/  ← YAML recipe + STAC item emission
└── io/          ← COG streaming + engine IPC transport
```

## Install

```bash
# Development
pip install -e ".[dev]"

# With optional LLM narrative
pip install -e ".[dev,llm]"

# With optional foundation model (GPU)
pip install -e ".[dev,ml-gpu]"

# With OpenEO remote engine
pip install -e ".[dev,openeo]"
```

## Run tests

```bash
pytest --cov=terrapulse_core --cov-report=term-missing
```

## Design rules

- No PyQGIS or Qt imports anywhere in this package.
- No `terrapulse_core` import inside the QGIS plugin process.
- All engine calls go through `io.engine_ipc.EngineIPCClient`.
- Every run must emit a `provenance/recipe.py` YAML before returning.
