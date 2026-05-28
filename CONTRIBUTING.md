# Contributing to TerraPulse

Thanks for your interest in TerraPulse! Contributions of all kinds are welcome:
bug reports, feature requests, documentation fixes, new features, additional InSAR
engines, ML model improvements, and translations.

This document explains the conventions and processes used by the project. By
participating you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

---

## Table of contents

- [Ways to contribute](#ways-to-contribute)
- [Reporting bugs](#reporting-bugs)
- [Suggesting features](#suggesting-features)
- [Development setup](#development-setup)
- [Project layout](#project-layout)
- [Coding standards](#coding-standards)
- [Testing](#testing)
- [Pull request process](#pull-request-process)
- [Commit message style](#commit-message-style)
- [Release process](#release-process)
- [Where to get help](#where-to-get-help)

---

## Ways to contribute

| Type | Where |
|---|---|
| Bug reports | [Open a bug report issue](https://github.com/Osman-Geomatics93/TerraPulse-Plugin/issues/new?template=bug_report.yml) |
| Feature requests | [Open a feature request](https://github.com/Osman-Geomatics93/TerraPulse-Plugin/issues/new?template=feature_request.yml) |
| Questions / ideas | [GitHub Discussions](https://github.com/Osman-Geomatics93/TerraPulse-Plugin/discussions) |
| Security issues | See [SECURITY.md](SECURITY.md) — **do not file public issues for vulnerabilities** |
| Code / docs | Pull requests against `main` |
| Translations | Open an issue with the target locale before starting |

---

## Reporting bugs

Before filing a new bug:

1. Search [existing issues](https://github.com/Osman-Geomatics93/TerraPulse-Plugin/issues?q=is%3Aissue) to avoid duplicates.
2. Update to the latest released version and verify the bug still reproduces.
3. Collect:
   - QGIS version (`Help → About`)
   - TerraPulse version (`Plugins → Manage and Install Plugins`)
   - OS and Python version
   - Docker engine status (`docker info`)
   - Steps to reproduce — exact AOI, dates, mode
   - Full traceback from the QGIS Python Console (`Plugins → Python Console`)

Use the [bug report template](https://github.com/Osman-Geomatics93/TerraPulse-Plugin/issues/new?template=bug_report.yml).
Minimal, reproducible examples get fixed fastest.

---

## Suggesting features

We love feature ideas. To increase the chance your idea gets built:

- Explain the **user problem**, not just the requested solution.
- Describe the **workflow** you'd like to use end-to-end.
- Mention any **scientific references** for new InSAR or ML methods.
- Indicate if you're willing to **implement** it yourself.

Use the [feature request template](https://github.com/Osman-Geomatics93/TerraPulse-Plugin/issues/new?template=feature_request.yml).

---

## Development setup

### Prerequisites

- Python **3.11 or 3.12**
- Git
- Docker Desktop (only required for end-to-end InSAR tests)
- QGIS **3.34 LTR** or later (for plugin UI testing)

### Clone and bootstrap

```bash
git clone https://github.com/Osman-Geomatics93/TerraPulse-Plugin.git
cd TerraPulse-Plugin

# Create virtualenv
python -m venv .venv
.venv\Scripts\activate         # Windows PowerShell
# source .venv/bin/activate    # macOS/Linux

# Install the core library in editable mode with dev extras
pip install -e packages/terrapulse_core[dev,llm]
```

### Install pre-commit hooks

```bash
pre-commit install
```

The hooks run `ruff`, `mypy`, `detect-secrets`, and basic file checks on every
commit. Don't bypass them with `--no-verify` — if a hook is wrong, fix it.

### Symlink the QGIS plugin

To test inside QGIS, symlink (or junction on Windows) the plugin folder into
your QGIS profile:

| OS | Plugin directory |
|---|---|
| Linux | `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/` |
| macOS | `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/` |
| Windows | `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\` |

```powershell
# Windows (run in elevated PowerShell)
New-Item -ItemType Junction `
  -Path "$env:APPDATA\QGIS\QGIS3\profiles\default\python\plugins\terrapulse" `
  -Target "D:\path\to\TerraPulse-Plugin\plugin\terrapulse"
```

Then restart QGIS and enable **TerraPulse** in the Plugin Manager.

---

## Project layout

```
TerraPulse-Plugin/
├── packages/
│   └── terrapulse_core/            # QGIS-independent engine (PyPI-installable)
│       ├── src/terrapulse_core/
│       │   ├── stac/               # STAC search + CDSE downloader
│       │   ├── insar/              # PyGMTSAR engine + protocol
│       │   ├── io/                 # IPC client, COG writer
│       │   ├── ml/                 # RandomForest classifier
│       │   ├── reporting/          # PDF/HTML + LLM narrative
│       │   └── provenance/         # YAML recipe + STAC item
│       └── tests/                  # 241 pytest tests
├── plugin/
│   └── terrapulse/                 # QGIS plugin (PyQt5)
│       ├── dialogs/                # Main + settings + results dialogs
│       ├── tasks/                  # QgsTask background workers
│       ├── map_tools/              # AOI rubber-band tool
│       ├── layers/                 # Velocity/coherence layer loaders
│       └── metadata.txt            # Plugin manifest
├── docker/
│   ├── Dockerfile.pygmtsar
│   └── engine_server.py            # Runs inside the container
├── .github/                        # CI, issue templates, PR template
└── docs/                           # User docs (built with mkdocs)
```

---

## Coding standards

### Python

- **Style**: `ruff format` (Black-compatible). Run `ruff check --fix` before pushing.
- **Type hints**: required on all public functions. `mypy` runs in CI.
- **Docstrings**: Google style. Required on public modules, classes, and functions.
- **Imports**: sorted by `ruff` (isort-compatible). Standard library → third-party → local.
- **Line length**: 100 characters.

### Naming

- Modules / functions: `snake_case`
- Classes / exceptions: `PascalCase`
- Constants: `UPPER_SNAKE_CASE`
- Private members: prefix with `_`
- Test functions: `test_<unit>_<behavior>`

### Errors

- Raise specific, named exceptions. Don't catch broad `Exception` unless re-raising.
- Use `terrapulse_core.errors` for domain-specific errors.
- Never swallow tracebacks in CLI/IPC layers — they must reach the user.

### Logging

- Use `logging.getLogger(__name__)` at module top. **Never** `print()` in library code.
- `engine_server.py` is the one exception: it `print()`s JSON IPC messages to stdout.

### QGIS plugin code

- No blocking I/O on the main thread — use `QgsTask` subclasses.
- All long-running work goes through `tasks/`. UI code must remain responsive.
- Strings shown to users go through `self.tr(...)` for future translations.

---

## Testing

### Run all tests

```bash
cd packages/terrapulse_core
pytest -v
```

### Run with coverage

```bash
pytest --cov=terrapulse_core --cov-report=html
# Open htmlcov/index.html
```

### Categories

| Marker | Use |
|---|---|
| `unit` | Pure logic, no I/O. Run on every commit. |
| `integration` | Hits real STAC API or Docker. Skipped on free-tier CI. |
| `slow` | Full pipeline runs. Run before release. |

```bash
pytest -m "unit"             # fast, default in CI
pytest -m "integration"      # requires Docker + CDSE creds
pytest -m "not slow"         # everything except multi-minute tests
```

### Adding tests

- New code needs tests. Bug fixes need a regression test that fails on the old code.
- Use fixtures from `tests/conftest.py` (`synthetic_sar_stack`, `cairo_bbox`, etc.).
- Mock external services (`pystac_client`, `requests`, `subprocess.Popen`) — do not
  call the real CDSE API from unit tests.

---

## Pull request process

1. **Open an issue first** for anything non-trivial (>50 lines or behavior change).
   This avoids wasted work if the design needs to change.
2. **Fork** the repo and create a topic branch from `main`:
   ```bash
   git checkout -b feat/burst-mode-support
   ```
3. **Write code + tests + docs** in the same PR. PRs without tests will be asked to add them.
4. **Run locally** before pushing:
   ```bash
   pre-commit run --all-files
   pytest
   ```
5. **Push** and open a PR against `main`. Fill out the PR template completely.
6. **Address review feedback** by pushing additional commits to the same branch
   (do not force-push during review — it breaks reviewer context).
7. Once approved, a maintainer will squash-merge and the change ships in the next release.

### PR checklist (also in the template)

- [ ] Tests added or updated and passing locally
- [ ] `ruff check` and `mypy` clean
- [ ] `CHANGELOG.md` updated under the `Unreleased` heading
- [ ] Documentation updated if behavior changed
- [ ] No secrets committed (`detect-secrets` runs in pre-commit)
- [ ] PR title follows [Conventional Commits](#commit-message-style)

---

## Commit message style

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <short summary>

<body — what and why, not how>

<footer — issue refs, breaking-change notes>
```

**Types** used in this project:

| Type | Use for |
|---|---|
| `feat` | New user-visible functionality |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `refactor` | Code change that neither fixes a bug nor adds a feature |
| `perf` | Performance improvement |
| `test` | Adding or fixing tests |
| `build` | Build system, Docker, packaging |
| `ci` | CI configuration |
| `chore` | Maintenance, dependency bumps |

**Scopes** used in this project: `stac`, `insar`, `ml`, `report`, `ipc`, `plugin`, `docker`, `ci`, `docs`.

Examples:

```
feat(stac): add burst-level filtering for IW SLC scenes

Closes #42.

---

fix(ipc): prevent stdout pipe deadlock on long downloads

Throttle progress messages to one per second to avoid saturating the
subprocess pipe buffer on hosts with slow stdout drains.

Fixes #38.
```

---

## Release process

Releases are cut by maintainers. The flow is:

1. Bump version in:
   - `packages/terrapulse_core/pyproject.toml`
   - `packages/terrapulse_core/src/terrapulse_core/__version__.py`
   - `plugin/terrapulse/metadata.txt`
2. Update `CHANGELOG.md` (root) and `plugin/CHANGELOG.md` — move items from
   `Unreleased` under a new `## X.Y.Z (YYYY-MM-DD)` heading.
3. Commit: `chore(release): 0.X.Y`.
4. Tag: `git tag 0.X.Y` (no `v` prefix — qgis-plugin-ci requires this).
5. Push: `git push && git push --tags`.
6. CI publishes:
   - The QGIS plugin ZIP to plugins.qgis.org
   - The Docker image as `osmanos93/terrapulse-pygmtsar:0.X.Y` and `:latest`
   - The Python package to PyPI (if changes in `packages/`)
   - A GitHub Release with the changelog entry

---

## Where to get help

- **General questions / ideas** → [GitHub Discussions](https://github.com/Osman-Geomatics93/TerraPulse-Plugin/discussions)
- **Bugs** → [Issue tracker](https://github.com/Osman-Geomatics93/TerraPulse-Plugin/issues)
- **Security** → see [SECURITY.md](SECURITY.md)
- **Direct contact** → osmangeomatics93@gmail.com (response within ~3 business days)

Thanks for making TerraPulse better.
