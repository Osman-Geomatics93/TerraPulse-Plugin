"""
TerraPulse QGIS Plugin — entry point required by QGIS plugin loader.

QGIS calls classFactory(iface) to instantiate the plugin.
All plugin logic lives in TerraPulsePlugin (plugin.py).

Path bootstrap
--------------
This module adds two directories to sys.path so that terrapulse_core
and any bundled dependency wheels are importable from QGIS Python:

1. ``<repo>/packages/terrapulse_core/src``   — the core pure-Python library
2. ``<plugin_dir>/deps/``                    — pip-installed missing packages
"""

from __future__ import annotations

import os
import pathlib
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qgis.gui import QgisInterface

# ---------------------------------------------------------------------------
# Bootstrap: add terrapulse_core and deps to sys.path
#
# terrapulse_core can live in one of three places, checked in order:
#   1. Bundled inside the installed plugin directory (production zip install):
#        <plugin_dir>/terrapulse_core/__init__.py
#      → add <plugin_dir> itself to sys.path
#   2. Repo source tree (developer running from a clone):
#        <repo>/packages/terrapulse_core/src/terrapulse_core/__init__.py
#      → add <repo>/packages/terrapulse_core/src to sys.path
#   3. Already installed via pip into QGIS Python (no action needed).
# ---------------------------------------------------------------------------
_PLUGIN_DIR = pathlib.Path(__file__).parent.resolve()

_CORE_BUNDLED = _PLUGIN_DIR                                                   # production: vendored inside plugin
_CORE_SRC = (_PLUGIN_DIR.parent.parent / "packages" / "terrapulse_core" / "src").resolve()  # dev: repo tree

# Local deps directory (pip install --target)
_DEPS_DIR = _PLUGIN_DIR / "deps"

# Pick the first location that actually contains terrapulse_core/
_core_root: pathlib.Path | None = None
for _candidate in (_CORE_BUNDLED, _CORE_SRC):
    if (_candidate / "terrapulse_core" / "__init__.py").is_file():
        _core_root = _candidate
        break

for _path in (_core_root, _DEPS_DIR):
    if _path is None:
        continue
    _s = str(_path)
    if _path.exists() and _s not in sys.path:
        sys.path.insert(0, _s)

# ---------------------------------------------------------------------------
# Dev mode: inject TERRAPULSE_DEV_SRC into os.environ so that engine_ipc.py
# mounts the local terrapulse_core source into Docker even when QGIS was
# launched before the env var was set in the shell.
# Only meaningful when running from the repo source tree.
# ---------------------------------------------------------------------------
if _CORE_SRC.exists() and not os.environ.get("TERRAPULSE_DEV_SRC"):
    os.environ["TERRAPULSE_DEV_SRC"] = str(_CORE_SRC)
    # (already set via system env → leave that value; only fill the gap)

# ---------------------------------------------------------------------------
# Module-cache flush — purge stale terrapulse.* and terrapulse_core.* modules
# so that uncheck→recheck in the Plugin Manager always loads fresh code.
# Without this, Python returns the old in-memory version of every submodule.
# ---------------------------------------------------------------------------
_RELOAD_PREFIXES = ("terrapulse.", "terrapulse_core.")
for _mod_name in list(sys.modules.keys()):
    if any(_mod_name.startswith(p) for p in _RELOAD_PREFIXES):
        del sys.modules[_mod_name]

# ---------------------------------------------------------------------------


def classFactory(iface: "QgisInterface") -> object:  # noqa: N802
    """
    QGIS plugin factory function.

    Parameters
    ----------
    iface:
        QgisInterface instance provided by QGIS at load time.

    Returns
    -------
    Instantiated TerraPulsePlugin.
    """
    from terrapulse.plugin import TerraPulsePlugin

    return TerraPulsePlugin(iface)
