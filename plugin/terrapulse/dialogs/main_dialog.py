"""
TerraPulse main analysis dialog — full Phase 1 implementation.

Responsibilities:
- AOI drawing (delegates to AOIMapTool) and display
- Time window and mode selection
- Launching STACDiscoveryTask (STAC preview) and InSARTask (processing)
- Displaying live progress from QgsTask signals
- Opening ResultsDialog on success

All heavy work runs on QgsTask background threads. This dialog only
manages UI state and wires signals — no blocking calls.
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from qgis.core import QgsApplication, QgsSettings
from qgis.PyQt.QtCore import QDate, Qt, QTimer, pyqtSlot
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

if TYPE_CHECKING:
    from qgis.gui import QgisInterface
    from terrapulse_core.stac.models import SceneStack

logger = logging.getLogger(__name__)

_SETTINGS_PREFIX = "terrapulse/"


class MainDialog(QDialog):
    """
    TerraPulse main analysis dialog.

    Non-blocking: all processing is delegated to QgsTask subclasses.
    This dialog only manages UI state and communicates with tasks via Qt signals.
    """

    def __init__(
        self,
        iface: "QgisInterface",
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)  # type: ignore[call-overload]
        self.iface = iface
        self._aoi_wkt: str | None = None
        self._aoi_tool: object | None = None   # AOIMapTool
        self._active_stac_task: object | None = None
        self._active_insar_task: object | None = None
        self._scene_stack: object | None = None  # SceneStack
        self._setup_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        self.setWindowTitle("TerraPulse — Ground Deformation Analysis")
        self.setMinimumWidth(520)
        self.setWindowFlags(self.windowFlags() | Qt.WindowMaximizeButtonHint)

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(12)

        # --- AOI group ---
        aoi_group = QGroupBox("Area of Interest")
        aoi_layout = QHBoxLayout(aoi_group)
        self._draw_aoi_btn = QPushButton("✏  Draw on Map")
        self._draw_aoi_btn.setToolTip(
            "Click then draw a polygon on the map canvas.\n"
            "Left-click = add vertex | Right-click or Enter = finish | Esc = cancel"
        )
        self._draw_aoi_btn.clicked.connect(self._on_draw_aoi)

        self._import_aoi_btn = QPushButton("📂  Import Layer…")
        self._import_aoi_btn.setToolTip("Use the bounding box of an existing vector layer as AOI")
        self._import_aoi_btn.clicked.connect(self._on_import_aoi)

        self._clear_aoi_btn = QPushButton("✕")
        self._clear_aoi_btn.setFixedWidth(28)
        self._clear_aoi_btn.setToolTip("Clear AOI")
        self._clear_aoi_btn.setEnabled(False)
        self._clear_aoi_btn.clicked.connect(self._on_clear_aoi)

        self._aoi_label = QLabel("No AOI selected")
        self._aoi_label.setStyleSheet("color: #888;")
        aoi_layout.addWidget(self._draw_aoi_btn)
        aoi_layout.addWidget(self._import_aoi_btn)
        aoi_layout.addWidget(self._clear_aoi_btn)
        aoi_layout.addWidget(self._aoi_label, stretch=1)
        main_layout.addWidget(aoi_group)

        # --- Time window group ---
        time_group = QGroupBox("Time Window")
        form = QFormLayout(time_group)
        today = date.today()
        one_year_ago = today - timedelta(days=365)

        self._start_date = QDateEdit()
        self._start_date.setCalendarPopup(True)
        self._start_date.setDate(
            QDate(one_year_ago.year, one_year_ago.month, one_year_ago.day)
        )
        self._end_date = QDateEdit()
        self._end_date.setCalendarPopup(True)
        self._end_date.setDate(QDate(today.year, today.month, today.day))

        form.addRow("Start date:", self._start_date)
        form.addRow("End date:", self._end_date)
        main_layout.addWidget(time_group)

        # --- Processing options group ---
        options_group = QGroupBox("Processing Options")
        options_form = QFormLayout(options_group)

        self._mode_combo = QComboBox()
        self._mode_combo.addItems([
            "Quick (~30 min)",
            "Standard (~2 hr)",
            "High precision (~6 hr)",
        ])
        self._mode_combo.setCurrentIndex(1)
        self._mode_combo.setToolTip(
            "Quick: fewer looks, smaller baseline graph\n"
            "Standard: default SBAS settings\n"
            "High precision: full coherence matrix, maximum resolution"
        )

        self._engine_combo = QComboBox()
        self._engine_combo.addItems([
            "Local (Docker / PyGMTSAR)",
            "Remote (OpenEO / CDSE cloud)",
        ])
        self._engine_combo.setToolTip(
            "Local: requires Docker Desktop with the terrapulse-pygmtsar image\n"
            "Remote: requires a CDSE account with processing credits"
        )

        self._orbit_combo = QComboBox()
        self._orbit_combo.addItems(["Ascending", "Descending"])
        self._orbit_combo.setToolTip(
            "Sentinel-1 orbit direction. Use the same orbit for the entire stack.\n"
            "Ascending covers most of Europe, North America, and East Africa.\n"
            "Descending provides better coverage in some areas — check before running."
        )

        options_form.addRow("Mode:", self._mode_combo)
        options_form.addRow("Engine:", self._engine_combo)
        options_form.addRow("Orbit:", self._orbit_combo)
        main_layout.addWidget(options_group)

        # --- Scene preview ---
        self._scene_info_label = QLabel("STAC scene count: (run discovery first)")
        self._scene_info_label.setStyleSheet("color: #555; font-size: 11px;")
        self._discover_btn = QPushButton("🔍  Discover Scenes")
        self._discover_btn.setToolTip(
            "Query the Copernicus STAC catalog for available Sentinel-1 scenes.\n"
            "Shows scene count and download estimate before the full run."
        )
        self._discover_btn.clicked.connect(self._on_discover)

        discover_row = QHBoxLayout()
        discover_row.addWidget(self._discover_btn)
        discover_row.addWidget(self._scene_info_label, stretch=1)
        main_layout.addLayout(discover_row)

        # --- Progress + log ---
        log_group = QGroupBox("Processing Log")
        log_layout = QVBoxLayout(log_group)
        self._progress_bar = QProgressBar()
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFormat("Ready")
        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setMaximumHeight(140)
        self._log_text.setPlaceholderText("Processing log will appear here…")
        log_layout.addWidget(self._progress_bar)
        log_layout.addWidget(self._log_text)
        main_layout.addWidget(log_group)

        # --- Settings shortcut button (top-right of button row) ---
        self._settings_btn = QPushButton("⚙  Settings")
        self._settings_btn.setToolTip("Open TerraPulse Settings (CDSE credentials, output dir, …)")
        self._settings_btn.setStyleSheet(
            "QPushButton { padding: 6px 14px; }"
        )
        self._settings_btn.clicked.connect(self._on_open_settings)

        # --- Buttons ---
        self._run_btn = QPushButton("🚀  Run Analysis")
        self._run_btn.setDefault(True)
        self._run_btn.setStyleSheet(
            "QPushButton { background-color: #1A6B4A; color: white; "
            "font-weight: bold; padding: 8px 20px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #145A3C; }"
            "QPushButton:disabled { background-color: #95A5A6; }"
        )
        self._run_btn.clicked.connect(self._on_run)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self._on_cancel)

        bottom_row = QHBoxLayout()
        bottom_row.addWidget(self._settings_btn)
        bottom_row.addStretch()
        button_box = QDialogButtonBox()
        button_box.addButton(self._run_btn, QDialogButtonBox.AcceptRole)
        button_box.addButton(self._cancel_btn, QDialogButtonBox.RejectRole)
        bottom_row.addWidget(button_box)
        main_layout.addLayout(bottom_row)

    # ------------------------------------------------------------------
    # AOI handlers
    # ------------------------------------------------------------------

    @pyqtSlot()
    def _on_draw_aoi(self) -> None:
        """Activate the rubber-band polygon tool on the map canvas."""
        from terrapulse.map_tools.aoi_tool import AOIMapTool

        canvas = self.iface.mapCanvas()
        self._aoi_tool = AOIMapTool(canvas)
        self._aoi_tool.aoi_captured.connect(self._on_aoi_captured)  # type: ignore[union-attr]
        self._aoi_tool.aoi_cancelled.connect(self._on_aoi_cancelled)  # type: ignore[union-attr]
        canvas.setMapTool(self._aoi_tool)  # type: ignore[arg-type]

        self._draw_aoi_btn.setText("✏  Drawing… (click map)")
        self._draw_aoi_btn.setEnabled(False)
        self._log("Draw a polygon on the map. Left-click = vertex | Right-click/Enter = finish | Esc = cancel")

        # Minimise the dialog so the canvas is visible
        self.showMinimized()

    @pyqtSlot(str)
    def _on_aoi_captured(self, wkt: str) -> None:
        """Receive the finished AOI WKT from AOIMapTool."""
        self._aoi_wkt = wkt
        # Show a compact description in the label
        self._aoi_label.setText(f"AOI: {wkt[:60]}…" if len(wkt) > 60 else f"AOI: {wkt}")
        self._aoi_label.setStyleSheet("color: #1A6B4A; font-weight: bold;")
        self._clear_aoi_btn.setEnabled(True)
        self._draw_aoi_btn.setText("✏  Draw on Map")
        self._draw_aoi_btn.setEnabled(True)
        self._scene_info_label.setText("STAC scene count: (click Discover Scenes)")
        self._log(f"AOI captured ({len(wkt)} chars WKT)")

        # Restore the dialog
        self.showNormal()
        self.raise_()
        self.activateWindow()

    @pyqtSlot()
    def _on_aoi_cancelled(self) -> None:
        self._draw_aoi_btn.setText("✏  Draw on Map")
        self._draw_aoi_btn.setEnabled(True)
        self.showNormal()
        self.raise_()
        self._log("AOI drawing cancelled.")

    @pyqtSlot()
    def _on_import_aoi(self) -> None:
        """Use the bounding box of a selected vector layer as AOI."""
        from qgis.core import QgsProject, QgsWkbTypes

        layers = [
            lyr for lyr in QgsProject.instance().mapLayers().values()
            if hasattr(lyr, "wkbType") and lyr.wkbType() != QgsWkbTypes.Unknown
        ]
        if not layers:
            QMessageBox.information(
                self, "No vector layers",
                "No vector layers are loaded. Add a layer or use 'Draw on Map'."
            )
            return

        # Simple: use the first selected layer's bounding box
        selected = self.iface.activeLayer()
        if selected is None or selected not in layers:
            selected = layers[0]

        bbox = selected.extent()
        if bbox.isEmpty():
            QMessageBox.warning(self, "Empty extent", "The selected layer has an empty extent.")
            return

        # Convert to WGS-84 if needed
        from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform

        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        if selected.crs() != wgs84:
            xform = QgsCoordinateTransform(selected.crs(), wgs84, QgsProject.instance())
            bbox = xform.transformBoundingBox(bbox)

        w, s, e, n = bbox.xMinimum(), bbox.yMinimum(), bbox.xMaximum(), bbox.yMaximum()
        wkt = (
            f"POLYGON(({w:.6f} {s:.6f}, {e:.6f} {s:.6f}, "
            f"{e:.6f} {n:.6f}, {w:.6f} {n:.6f}, {w:.6f} {s:.6f}))"
        )
        self._on_aoi_captured(wkt)

    @pyqtSlot()
    def _on_clear_aoi(self) -> None:
        self._aoi_wkt = None
        self._aoi_label.setText("No AOI selected")
        self._aoi_label.setStyleSheet("color: #888;")
        self._clear_aoi_btn.setEnabled(False)
        self._scene_info_label.setText("STAC scene count: (run discovery first)")
        self._log("AOI cleared.")

    # ------------------------------------------------------------------
    # STAC discovery
    # ------------------------------------------------------------------

    @pyqtSlot()
    def _on_discover(self) -> None:
        """Run STACDiscoveryTask to preview available scenes."""
        if not self._aoi_wkt:
            QMessageBox.warning(self, "No AOI", "Please draw or import an Area of Interest first.")
            return

        from terrapulse_core.stac.models import BBox
        from terrapulse.tasks.stac_task import STACDiscoveryTask

        try:
            aoi = self._parse_bbox_from_wkt(self._aoi_wkt)
        except ValueError as exc:
            QMessageBox.warning(self, "AOI Error", str(exc))
            return

        start_qdate = self._start_date.date()
        end_qdate = self._end_date.date()

        from datetime import datetime as dt

        start = dt(start_qdate.year(), start_qdate.month(), start_qdate.day())
        end = dt(end_qdate.year(), end_qdate.month(), end_qdate.day())

        orbit = self._orbit_combo.currentText().lower()

        self._discover_btn.setEnabled(False)
        self._scene_info_label.setText("Querying STAC catalog…")
        self._log("Starting STAC scene discovery…")

        task = STACDiscoveryTask(
            aoi=aoi,
            start_date=start,
            end_date=end,
            orbit_direction=orbit,
            max_scenes=30,
        )
        task.stack_ready.connect(self._on_stac_ready)
        task.stack_failed.connect(self._on_stac_failed)
        task.taskCompleted.connect(lambda: self._discover_btn.setEnabled(True))
        task.taskTerminated.connect(lambda: self._discover_btn.setEnabled(True))

        self._active_stac_task = task
        QgsApplication.taskManager().addTask(task)

    @pyqtSlot(object)
    def _on_stac_ready(self, stack: "SceneStack") -> None:
        self._scene_stack = stack
        size_gb = stack.estimate_total_size_gb()
        info = (
            f"Found {stack.n_scenes} scenes  |  "
            f"~{size_gb:.0f} GB download  |  "
            f"Orbit {stack.relative_orbit} {stack.orbit_direction}"
        )
        self._scene_info_label.setText(info)
        self._scene_info_label.setStyleSheet("color: #1A6B4A;")
        self._log(f"STAC: {info}")

    @pyqtSlot(str)
    def _on_stac_failed(self, error: str) -> None:
        self._scene_info_label.setText(f"Discovery failed: {error[:60]}")
        self._scene_info_label.setStyleSheet("color: #E74C3C;")
        self._log(f"STAC error: {error}")

    # ------------------------------------------------------------------
    # Run analysis
    # ------------------------------------------------------------------

    @pyqtSlot()
    def _on_run(self) -> None:
        """Validate inputs and launch InSAR processing via InSARTask."""
        if not self._aoi_wkt:
            QMessageBox.warning(self, "No AOI", "Please draw or import an Area of Interest first.")
            return

        # Read CDSE credentials from QgsSettings
        settings = QgsSettings()
        cdse_user = settings.value(f"{_SETTINGS_PREFIX}cdse_username", "", type=str)
        cdse_pass = settings.value(f"{_SETTINGS_PREFIX}cdse_password", "", type=str)

        if not cdse_user or not cdse_pass:
            reply = QMessageBox.question(
                self,
                "No CDSE Credentials",
                "No CDSE credentials found in settings.\n\n"
                "Without credentials the engine cannot download Sentinel-1 data.\n"
                "Open Settings now?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                from terrapulse.dialogs.settings_dialog import SettingsDialog

                dlg = SettingsDialog(parent=self)
                dlg.exec()
                # Re-read after settings saved
                cdse_user = settings.value(f"{_SETTINGS_PREFIX}cdse_username", "", type=str)
                cdse_pass = settings.value(f"{_SETTINGS_PREFIX}cdse_password", "", type=str)

        # Output directory
        output_dir_str = settings.value(f"{_SETTINGS_PREFIX}output_dir", "", type=str)
        if output_dir_str:
            output_dir = Path(output_dir_str)
        else:
            output_dir = Path(tempfile.gettempdir()) / "terrapulse"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Date range
        sq = self._start_date.date()
        eq = self._end_date.date()
        from datetime import datetime as dt

        start = dt(sq.year(), sq.month(), sq.day())
        end = dt(eq.year(), eq.month(), eq.day())

        if start >= end:
            QMessageBox.warning(self, "Invalid dates", "Start date must be before end date.")
            return

        # Mode
        mode_map = {0: "quick", 1: "standard", 2: "high_precision"}
        mode = mode_map[self._mode_combo.currentIndex()]
        orbit = self._orbit_combo.currentText().lower()

        # Import task
        from terrapulse.tasks.insar_task import InSARTask
        from terrapulse.settings_manager import SettingsManager

        task = InSARTask(
            aoi_wkt=self._aoi_wkt,
            start_date=start,
            end_date=end,
            output_dir=output_dir,
            mode=mode,
            orbit_direction=orbit,
            max_scenes=SettingsManager.max_scenes(),
            cdse_username=cdse_user,
            cdse_password=cdse_pass,
            docker_image=SettingsManager.docker_image(),
        )
        task.run_complete.connect(self._on_run_complete)
        task.progress_message.connect(self._on_progress_message)
        task.progressChanged.connect(self._on_progress_changed)
        task.taskCompleted.connect(lambda: self._run_btn.setEnabled(True))
        task.taskTerminated.connect(lambda: self._run_btn.setEnabled(True))

        self._active_insar_task = task
        self._run_btn.setEnabled(False)
        self._progress_bar.setValue(0)
        self._progress_bar.setFormat("Launching engine…")
        self._log(f"Starting analysis. Run ID: {task.run_id[:8]}…")
        self._log(f"Mode: {mode} | Orbit: {orbit} | Output: {output_dir}")

        QgsApplication.taskManager().addTask(task)

    @pyqtSlot(bool, str)
    def _on_run_complete(self, success: bool, run_id: str) -> None:
        """Called on main thread when InSARTask finishes."""
        if success:
            self._progress_bar.setValue(100)
            self._progress_bar.setFormat("Complete ✓")
            self._log(f"Run {run_id[:8]} completed successfully!")

            # Open results dialog
            task = self._active_insar_task
            if task is not None:
                from terrapulse.dialogs.results_dialog import ResultsDialog

                dlg = ResultsDialog(
                    run_id=run_id,
                    output_dir=task.result_dir or Path(tempfile.gettempdir()) / "terrapulse",  # type: ignore[union-attr]
                    velocity_cog=task.velocity_cog,  # type: ignore[union-attr]
                    parent=self,
                )
                dlg.exec()
        else:
            task = self._active_insar_task
            err = task.error_message if task else ""  # type: ignore[union-attr]
            if not err or not err.strip():
                err = (
                    "No error details captured.\n\n"
                    "Possible causes:\n"
                    "  • Docker Desktop is not installed or not running\n"
                    "  • The engine image is not built yet\n"
                    "  • The task was cancelled by QGIS internally\n\n"
                    "Open Plugins → Python Console and look for logged warnings."
                )
            self._progress_bar.setFormat("Failed ✗")
            self._log(f"Run {run_id[:8]} FAILED: {err.splitlines()[0]}")
            QMessageBox.critical(
                self,
                "TerraPulse — Processing Failed",
                f"The InSAR processing run failed:\n\n{err}\n\n"
                "Check the processing log above for details.\n"
                "A partial YAML recipe has been saved to the output directory.",
            )

    @pyqtSlot(str)
    def _on_progress_message(self, message: str) -> None:
        self._log(message)

    @pyqtSlot(float)
    def _on_progress_changed(self, pct: float) -> None:
        self._progress_bar.setValue(int(pct))
        self._progress_bar.setFormat(f"{pct:.0f}%")

    @pyqtSlot()
    def _on_open_settings(self) -> None:
        """Open the TerraPulse settings dialog."""
        from terrapulse.dialogs.settings_dialog import SettingsDialog

        dlg = SettingsDialog(parent=self)
        dlg.exec()

    @pyqtSlot()
    def _on_cancel(self) -> None:
        """Cancel any running task and close the dialog."""
        if self._active_insar_task is not None:
            try:
                self._active_insar_task.cancel()  # type: ignore[union-attr]
            except Exception:
                pass
        if self._active_stac_task is not None:
            try:
                self._active_stac_task.cancel()  # type: ignore[union-attr]
            except Exception:
                pass
        self.reject()

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _log(self, message: str) -> None:
        self._log_text.append(message)
        logger.info("[TerraPulse UI] %s", message)

    @staticmethod
    def _parse_bbox_from_wkt(wkt: str) -> "BBox":
        """Extract a BBox from a WKT polygon string (WGS-84 assumed)."""
        from terrapulse_core.stac.models import BBox

        inner = wkt.strip()
        for prefix in ("POLYGON((", "POLYGON (( "):
            if inner.upper().startswith(prefix.upper()):
                inner = inner[len(prefix):]
                break
        inner = inner.rstrip(") ")

        coords = []
        for pair in inner.split(","):
            parts = pair.strip().split()
            if len(parts) >= 2:
                try:
                    coords.append((float(parts[0]), float(parts[1])))
                except ValueError:
                    continue

        if len(coords) < 3:
            raise ValueError(f"Cannot parse AOI coordinates from WKT: {wkt[:80]}")

        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        return BBox(west=min(xs), south=min(ys), east=max(xs), north=max(ys))
