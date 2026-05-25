"""
TerraPulse QGIS Plugin — main plugin class.

Responsibilities:
- Register toolbar icon and menu action
- Open the main dialog on user click
- Manage QgsTask instances (never block the UI thread)
- Clean up all resources on unload

Engineering rule: no terrapulse_core import at module level.
All core imports are deferred to task execution time inside the
Docker/conda subprocess boundary.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMessageBox

if TYPE_CHECKING:
    from qgis.gui import QgisInterface

logger = logging.getLogger(__name__)

# Plugin root directory (contains metadata.txt)
PLUGIN_DIR = Path(__file__).parent


class TerraPulsePlugin:
    """
    Main QGIS plugin class.

    QGIS calls:
    - ``__init__`` on load
    - ``initGui`` to add UI elements
    - ``unload`` on plugin disable/QGIS exit
    """

    def __init__(self, iface: "QgisInterface") -> None:
        self.iface = iface
        self._actions: list[QAction] = []
        self._main_dialog: object | None = None

        # Configure logging for the plugin's own log category
        logging.basicConfig(level=logging.INFO)
        logger.info("TerraPulse plugin initialised (QGIS %s)", self._qgis_version())

    # ------------------------------------------------------------------
    # QGIS lifecycle
    # ------------------------------------------------------------------

    def initGui(self) -> None:  # noqa: N802
        """Called by QGIS to add plugin's UI elements."""
        icon_path = str(PLUGIN_DIR / "resources" / "icons" / "terrapulse_32.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()

        # Main action: open TerraPulse dialog
        action = QAction(icon, "TerraPulse — Deformation Analysis", self.iface.mainWindow())
        action.setToolTip(
            "Open TerraPulse: AI-powered ground deformation and subsidence analysis"
        )
        action.triggered.connect(self._open_main_dialog)

        self.iface.addToolBarIcon(action)
        self.iface.addPluginToMenu("&TerraPulse", action)
        self._actions.append(action)

        # Settings action
        settings_action = QAction("TerraPulse Settings…", self.iface.mainWindow())
        settings_action.triggered.connect(self._open_settings_dialog)
        self.iface.addPluginToMenu("&TerraPulse", settings_action)
        self._actions.append(settings_action)

        # Status bar notification
        try:
            self.iface.statusBarIface().showMessage("TerraPulse 0.2.0 ready", 3000)
        except Exception:
            pass

        logger.info("TerraPulse UI elements added.")

    def unload(self) -> None:
        """Called by QGIS when the plugin is disabled or QGIS exits."""
        for action in self._actions:
            self.iface.removePluginMenu("&TerraPulse", action)
            self.iface.removeToolBarIcon(action)
        self._actions.clear()

        if self._main_dialog is not None:
            try:
                self._main_dialog.close()  # type: ignore[union-attr]
            except Exception:
                pass
            self._main_dialog = None

        logger.info("TerraPulse plugin unloaded cleanly.")

    # ------------------------------------------------------------------
    # Dialog launchers
    # ------------------------------------------------------------------

    def _open_main_dialog(self) -> None:
        """Open the main TerraPulse analysis dialog."""
        try:
            from terrapulse.dialogs.main_dialog import MainDialog

            if self._main_dialog is None or not self._main_dialog.isVisible():  # type: ignore[union-attr]
                self._main_dialog = MainDialog(self.iface, parent=self.iface.mainWindow())
            self._main_dialog.show()  # type: ignore[union-attr]
            self._main_dialog.raise_()  # type: ignore[union-attr]
            self._main_dialog.activateWindow()  # type: ignore[union-attr]
        except ImportError as exc:
            logger.exception("Failed to import MainDialog")
            QMessageBox.critical(
                self.iface.mainWindow(),
                "TerraPulse — Import Error",
                f"Could not load the TerraPulse dialog:\n\n{exc}\n\n"
                "Ensure the plugin is correctly installed.",
            )
        except Exception as exc:
            logger.exception("Unexpected error opening main dialog")
            try:
                from terrapulse.utils.message_bar import push_error
                push_error(self.iface, f"Dialog error: {exc}")
            except Exception:
                pass

    def _open_settings_dialog(self) -> None:
        """Open the TerraPulse settings dialog."""
        try:
            from terrapulse.dialogs.settings_dialog import SettingsDialog

            dlg = SettingsDialog(parent=self.iface.mainWindow())
            dlg.exec()
        except ImportError as exc:
            logger.exception("Failed to import SettingsDialog")
            QMessageBox.warning(
                self.iface.mainWindow(),
                "TerraPulse — Settings Unavailable",
                f"Settings dialog not available:\n{exc}",
            )

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _qgis_version() -> str:
        try:
            from qgis.core import Qgis

            return str(Qgis.QGIS_VERSION)
        except Exception:
            return "unknown"
