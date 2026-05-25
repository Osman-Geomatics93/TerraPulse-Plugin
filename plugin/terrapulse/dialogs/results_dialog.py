"""
TerraPulse results dialog — Phase 3 implementation.

Shown after a successful InSAR run. Offers:
- "Add to Map"       → loads velocity + coherence COGs into QGIS
- "Classify"         → launches ClassifyTask (Phase 2 ML pipeline)
- "Generate Report"  → launches ReportTask (Phase 3 OSM + report)
- "Open Folder"      → opens the output directory in the OS file manager
- "View Recipe YAML" → opens the provenance YAML in a text editor
- "View Report"      → opens report.html in the default browser

Summary group shows run metadata; a live log area shows task progress.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import webbrowser
from pathlib import Path

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

logger = logging.getLogger(__name__)

_BTN_STYLE = (
    "QPushButton {{ background-color: {bg}; color: white; "
    "padding: 6px 14px; border-radius: 4px; font-size: 12px; }}"
    "QPushButton:hover {{ background-color: {hover}; }}"
    "QPushButton:disabled {{ background-color: #aaa; }}"
)


class ResultsDialog(QDialog):
    """
    Post-run results dialog.

    Shows run metadata and provides action buttons for:
    - Loading deformation layers into QGIS
    - Triggering ML classification (Phase 2)
    - Generating the risk + narrative report (Phase 3)
    - Opening output files / folders
    """

    def __init__(
        self,
        run_id: str,
        output_dir: Path,
        velocity_cog: Path | None = None,
        coherence_cog: Path | None = None,
        n_scenes: int = 0,
        anthropic_api_key: str | None = None,
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)  # type: ignore[call-overload]
        self.setWindowTitle(f"TerraPulse Results — {run_id[:8]}")
        self.setMinimumWidth(540)
        self._run_id = run_id
        self._output_dir = output_dir
        self._velocity_cog = velocity_cog
        self._coherence_cog = coherence_cog
        self._n_scenes = n_scenes
        self._anthropic_api_key = anthropic_api_key

        # Track running tasks
        self._classify_task: object | None = None
        self._report_task: object | None = None

        self._setup_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # ---- Summary ----
        summary_group = QGroupBox("Run Summary")
        summary_layout = QVBoxLayout(summary_group)
        summary_layout.addWidget(QLabel(f"<b>Run ID:</b> <code>{self._run_id}</code>"))
        summary_layout.addWidget(QLabel(f"<b>Output:</b> {self._output_dir}"))
        if self._n_scenes:
            summary_layout.addWidget(QLabel(f"<b>Scenes processed:</b> {self._n_scenes}"))
        if self._velocity_cog:
            summary_layout.addWidget(QLabel(f"<b>Velocity COG:</b> {self._velocity_cog.name}"))
        if self._coherence_cog:
            summary_layout.addWidget(QLabel(f"<b>Coherence COG:</b> {self._coherence_cog.name}"))
        layout.addWidget(summary_group)

        # ---- Primary actions ----
        primary_group = QGroupBox("InSAR Results")
        primary_layout = QHBoxLayout(primary_group)

        self._add_btn = self._make_btn(
            "🗺  Add to Map", "#1A6B4A", "#145A3C",
            "Load velocity and coherence layers into the current QGIS project",
            self._on_add_to_map,
        )
        has_vel = bool(self._velocity_cog and self._velocity_cog.exists())
        self._add_btn.setEnabled(has_vel)
        if not has_vel:
            self._add_btn.setToolTip("Velocity COG not found — processing may have failed.")

        folder_btn = self._make_btn(
            "📂  Open Folder", "#555555", "#333333",
            "Open the output directory in the OS file manager",
            self._on_open_folder,
        )
        recipe_btn = self._make_btn(
            "📄  Recipe YAML", "#555555", "#333333",
            "Open the provenance YAML for this run",
            self._on_view_recipe,
        )

        primary_layout.addWidget(self._add_btn)
        primary_layout.addWidget(folder_btn)
        primary_layout.addWidget(recipe_btn)
        layout.addWidget(primary_group)

        # ---- ML / Analysis actions ----
        ml_group = QGroupBox("Analysis Pipeline")
        ml_layout = QHBoxLayout(ml_group)

        self._classify_btn = self._make_btn(
            "🤖  Classify", "#8E44AD", "#6C3483",
            "Run ML deformation classification (Phase 2)",
            self._on_classify,
        )
        self._classify_btn.setEnabled(has_vel)

        self._report_btn = self._make_btn(
            "📊  Generate Report", "#E67E22", "#CA6F1E",
            "Query OSM assets, score risk, and render HTML/PDF report",
            self._on_generate_report,
        )

        self._view_report_btn = self._make_btn(
            "🌐  View Report", "#2980B9", "#1F6391",
            "Open the generated HTML report in the default browser",
            self._on_view_report,
        )
        # Enable "View Report" only if report.html already exists
        report_html = self._output_dir / "report.html"
        self._view_report_btn.setEnabled(report_html.exists())

        ml_layout.addWidget(self._classify_btn)
        ml_layout.addWidget(self._report_btn)
        ml_layout.addWidget(self._view_report_btn)
        layout.addWidget(ml_group)

        # ---- Progress log ----
        log_group = QGroupBox("Task Log")
        log_layout = QVBoxLayout(log_group)
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(90)
        self._log.setPlaceholderText("Task progress will appear here…")
        log_layout.addWidget(self._log)
        layout.addWidget(log_group)

        # ---- Close button ----
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.accept)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    # Button callbacks
    # ------------------------------------------------------------------

    def _on_add_to_map(self) -> None:
        """Load velocity (+ coherence) into the QGIS project."""
        try:
            from terrapulse.layers.deformation_layer import (
                DeformationLayerError,
                load_coherence_layer,
                load_velocity_layer,
            )

            group = f"TerraPulse — {self._run_id[:8]}"

            if self._velocity_cog and self._velocity_cog.exists():
                vel_layer = load_velocity_layer(
                    self._velocity_cog,
                    layer_name=f"LOS Velocity (mm/yr) [{self._run_id[:8]}]",
                    group_name=group,
                )
                logger.info("Velocity layer added: %s", vel_layer.name())

            if self._coherence_cog and self._coherence_cog.exists():
                coh_layer = load_coherence_layer(
                    self._coherence_cog,
                    layer_name=f"Temporal Coherence [{self._run_id[:8]}]",
                    group_name=group,
                )
                logger.info("Coherence layer added: %s", coh_layer.name())

            QMessageBox.information(
                self,
                "Layers Added",
                f"Deformation layers added to '{group}' in the Layers panel.",
            )
            self.accept()

        except Exception as exc:
            logger.exception("Failed to add layers")
            QMessageBox.critical(self, "Layer Load Error", str(exc))

    def _on_classify(self) -> None:
        """Launch ClassifyTask in background."""
        if self._velocity_cog is None or not self._velocity_cog.exists():
            QMessageBox.warning(self, "Missing COG", "Velocity COG not found.")
            return

        try:
            from terrapulse.tasks.classify_task import ClassifyTask

            task = ClassifyTask(
                velocity_cog=self._velocity_cog,
                coherence_cog=self._coherence_cog or self._velocity_cog,
                output_dir=self._output_dir,
                run_id=self._run_id,
            )
            task.progress_message.connect(self._append_log)
            task.classification_complete.connect(self._on_classify_complete)

            self._classify_task = task
            self._classify_btn.setEnabled(False)
            self._classify_btn.setText("🤖  Classifying…")

            QgsApplication.taskManager().addTask(task)
            self._append_log("ML classification started…")

        except Exception as exc:
            logger.exception("Failed to start ClassifyTask")
            QMessageBox.critical(self, "Classification Error", str(exc))

    def _on_classify_complete(self, success: bool, run_id: str) -> None:
        self._classify_btn.setEnabled(True)
        self._classify_btn.setText("🤖  Classify")
        if success:
            self._append_log("✅ Classification complete.")
            self._offer_load_classification()
        else:
            self._append_log("❌ Classification failed — see QGIS log.")

    def _offer_load_classification(self) -> None:
        label_cog = next(self._output_dir.glob("classification_*.tif"), None)
        if label_cog is None:
            return
        reply = QMessageBox.question(
            self,
            "Classification Ready",
            "Classification complete. Add the label and uncertainty layers to QGIS?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            try:
                from terrapulse.layers.classification_layer import (
                    load_classification_layer,
                    load_entropy_layer,
                )
                group = f"TerraPulse ML — {self._run_id[:8]}"
                load_classification_layer(label_cog, group_name=group)
                entropy_cog = next(self._output_dir.glob("entropy_*.tif"), None)
                if entropy_cog:
                    load_entropy_layer(entropy_cog, group_name=group)
            except Exception as exc:
                QMessageBox.warning(self, "Layer Load Warning", str(exc))

    def _on_generate_report(self) -> None:
        """Launch ReportTask in background."""
        try:
            from terrapulse.tasks.report_task import ReportTask

            task = ReportTask(
                run_id=self._run_id,
                output_dir=self._output_dir,
                velocity_cog=self._velocity_cog,
                coherence_cog=self._coherence_cog,
                anthropic_api_key=self._anthropic_api_key,
                generate_pdf=False,
            )
            task.progress_message.connect(self._append_log)
            task.report_complete.connect(self._on_report_complete)

            self._report_task = task
            self._report_btn.setEnabled(False)
            self._report_btn.setText("📊  Generating…")

            QgsApplication.taskManager().addTask(task)
            self._append_log("Report generation started…")

        except Exception as exc:
            logger.exception("Failed to start ReportTask")
            QMessageBox.critical(self, "Report Error", str(exc))

    def _on_report_complete(self, success: bool, run_id: str) -> None:
        self._report_btn.setEnabled(True)
        self._report_btn.setText("📊  Generate Report")
        if success:
            self._append_log("✅ Report generated.")
            self._view_report_btn.setEnabled(
                (self._output_dir / "report.html").exists()
            )
        else:
            self._append_log("❌ Report generation failed — see QGIS log.")

    def _on_view_report(self) -> None:
        report_html = self._output_dir / "report.html"
        if not report_html.exists():
            QMessageBox.information(self, "No Report", "No report.html found yet.")
            return
        try:
            webbrowser.open(report_html.as_uri())
        except Exception as exc:
            QMessageBox.warning(self, "Cannot Open Report", str(exc))

    def _on_open_folder(self) -> None:
        path = str(self._output_dir)
        try:
            if sys.platform == "win32":
                subprocess.run(["explorer", path], check=False)
            elif sys.platform == "darwin":
                subprocess.run(["open", path], check=False)
            else:
                subprocess.run(["xdg-open", path], check=False)
        except Exception as exc:
            QMessageBox.warning(self, "Cannot Open Folder", str(exc))

    def _on_view_recipe(self) -> None:
        recipe_path = self._output_dir / f"recipe_{self._run_id}.yaml"
        if not recipe_path.exists():
            yamls = list(self._output_dir.glob("recipe_*.yaml"))
            if yamls:
                recipe_path = yamls[0]
            else:
                QMessageBox.information(
                    self, "Recipe Not Found",
                    f"No recipe YAML found in {self._output_dir}",
                )
                return
        self._open_file(recipe_path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _append_log(self, message: str) -> None:
        self._log.appendPlainText(message)
        self._log.verticalScrollBar().setValue(
            self._log.verticalScrollBar().maximum()
        )

    @staticmethod
    def _make_btn(
        label: str,
        bg: str,
        hover: str,
        tooltip: str,
        slot: object,
    ) -> QPushButton:
        btn = QPushButton(label)
        btn.setToolTip(tooltip)
        btn.setStyleSheet(_BTN_STYLE.format(bg=bg, hover=hover))
        btn.clicked.connect(slot)
        return btn

    @staticmethod
    def _open_file(path: Path) -> None:
        try:
            if sys.platform == "win32":
                subprocess.run(["notepad", str(path)], check=False)
            elif sys.platform == "darwin":
                subprocess.run(["open", "-t", str(path)], check=False)
            else:
                subprocess.run(["xdg-open", str(path)], check=False)
        except Exception as exc:
            logger.warning("Cannot open file: %s", exc)
