"""
TerraPulse settings dialog — Phase 4 full implementation.

Persists settings via QgsSettings (stored in the QGIS user profile,
OS keychain where available). Opens at startup when credentials are missing.
"""

from __future__ import annotations

import logging
from pathlib import Path

from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
)

logger = logging.getLogger(__name__)


class SettingsDialog(QDialog):
    """
    Plugin settings dialog.

    Sections:
    - CDSE credentials (username + password)
    - Anthropic API key (optional, for LLM reports)
    - Output directory (custom or system temp)
    - Processing defaults (max scenes, default mode, orbit preference)
    - Docker image tag
    - PDF generation toggle
    """

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)  # type: ignore[call-overload]
        self.setWindowTitle("TerraPulse Settings")
        self.setMinimumWidth(520)
        self._setup_ui()
        self._load_settings()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # ---- CDSE credentials ----
        cdse_group = QGroupBox("Copernicus Data Space (CDSE) Credentials")
        cdse_form = QFormLayout(cdse_group)
        self._cdse_username = QLineEdit()
        self._cdse_username.setPlaceholderText("your@email.com")
        self._cdse_password = QLineEdit()
        self._cdse_password.setEchoMode(QLineEdit.Password)
        self._cdse_password.setPlaceholderText("CDSE password")
        cdse_form.addRow("Username:", self._cdse_username)
        cdse_form.addRow("Password:", self._cdse_password)
        cdse_form.addRow(QLabel(
            "<small>Register free at "
            "<a href='https://dataspace.copernicus.eu/'>dataspace.copernicus.eu</a>. "
            "Required for Sentinel-1 downloads.</small>"
        ))
        layout.addWidget(cdse_group)

        # ---- Anthropic API key ----
        llm_group = QGroupBox("LLM Report Narrative (Optional)")
        llm_form = QFormLayout(llm_group)
        self._anthropic_key = QLineEdit()
        self._anthropic_key.setEchoMode(QLineEdit.Password)
        self._anthropic_key.setPlaceholderText("sk-ant-… (leave blank for template fallback)")
        llm_form.addRow("Anthropic API key:", self._anthropic_key)
        llm_form.addRow(QLabel(
            "<small>If blank, reports use a deterministic template. "
            "Key is stored in QGIS profile settings.</small>"
        ))
        layout.addWidget(llm_group)

        # ---- Output directory ----
        out_group = QGroupBox("Output")
        out_form = QFormLayout(out_group)
        out_row = QHBoxLayout()
        self._output_dir = QLineEdit()
        self._output_dir.setPlaceholderText("Leave blank for system temp directory")
        browse_btn = _small_btn("Browse…")
        browse_btn.clicked.connect(self._on_browse_output)
        out_row.addWidget(self._output_dir)
        out_row.addWidget(browse_btn)
        out_form.addRow("Output directory:", out_row)
        layout.addWidget(out_group)

        # ---- Processing defaults ----
        proc_group = QGroupBox("Processing Defaults")
        proc_form = QFormLayout(proc_group)

        self._max_scenes = QSpinBox()
        self._max_scenes.setRange(6, 60)
        self._max_scenes.setValue(30)
        self._max_scenes.setToolTip("Maximum Sentinel-1 scenes per processing run")
        proc_form.addRow("Max scenes per run:", self._max_scenes)

        self._docker_image = QLineEdit()
        self._docker_image.setPlaceholderText("terrapulse-pygmtsar:latest")
        self._docker_image.setToolTip("Docker image for local processing engine")
        proc_form.addRow("Docker image tag:", self._docker_image)

        self._generate_pdf = QCheckBox("Generate PDF report (requires WeasyPrint)")
        self._generate_pdf.setToolTip(
            "When enabled, a print-ready PDF is produced alongside the HTML report.\n"
            "Requires WeasyPrint: pip install weasyprint"
        )
        proc_form.addRow("", self._generate_pdf)
        layout.addWidget(proc_group)

        # ---- Buttons ----
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save_and_close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------

    def _load_settings(self) -> None:
        """Populate UI from persisted QgsSettings."""
        from terrapulse.settings_manager import SettingsManager

        self._cdse_username.setText(SettingsManager.cdse_username())
        self._cdse_password.setText(SettingsManager.cdse_password())
        self._anthropic_key.setText(SettingsManager.anthropic_api_key())
        self._output_dir.setText(SettingsManager.output_dir())
        self._max_scenes.setValue(SettingsManager.max_scenes())
        self._docker_image.setText(SettingsManager.docker_image())
        self._generate_pdf.setChecked(SettingsManager.generate_pdf())
        logger.debug("TerraPulse settings loaded from QgsSettings.")

    def _save_and_close(self) -> None:
        """Persist settings to QgsSettings and close."""
        from terrapulse.settings_manager import SettingsManager

        SettingsManager.save_all(
            cdse_username=self._cdse_username.text().strip(),
            cdse_password=self._cdse_password.text(),
            anthropic_api_key=self._anthropic_key.text().strip(),
            output_dir=self._output_dir.text().strip(),
            max_scenes=self._max_scenes.value(),
            docker_image=self._docker_image.text().strip() or "terrapulse-pygmtsar:latest",
            generate_pdf=self._generate_pdf.isChecked(),
        )
        logger.info("TerraPulse settings saved.")
        self.accept()

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_browse_output(self) -> None:
        current = self._output_dir.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(
            self,  # type: ignore[arg-type]
            "Select Output Directory",
            current,
        )
        if chosen:
            self._output_dir.setText(chosen)


def _small_btn(label: str):
    from qgis.PyQt.QtWidgets import QPushButton
    btn = QPushButton(label)
    btn.setFixedWidth(80)
    return btn
