"""Analysis stage - the scan row: Run, Stop and what the last scan did (2026-10-23).

A display and a pair of buttons, nothing more: pressing Run emits ``run_requested``; ``app_rewrite`` starts ``analysis/scan_task.ScanTask``
(reads every frame once and saves the mean, spread and pixel counts of every ROI to ``analysis/scan.h5`` of the session). Progress, elapsed
time and Cancel are in the status bar's task indicator like every other long task; Stop here does the same as its Cancel button.

Normally only cubes whose inputs changed since the last scan are read again (``analysis/scan_fingerprint.py``); the checkbox forces all of
them. The checkbox is remembered across restarts like every control (``ui_state.bind`` in ``app_rewrite``).

Does not touch a module or a file: the owner tells it what is running (``set_running``) and what to say (``set_status``).
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from lspr_ui import get_active_theme


class AnalysisScanRow(QWidget):
    run_requested = pyqtSignal()
    stop_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._run_button = QPushButton("Run scan", self)
        self._run_button.setToolTip(
            "Read every frame once and save the mean, spread and pixel counts of each ROI (all ROIs, both sides, all wavelengths) "
            "to analysis/scan.h5 of this session.\n"
            "Only cubes whose inputs changed since the last scan are read again. Progress and Cancel are in the status bar."
        )
        self._stop_button = QPushButton("Stop", self)
        self._stop_button.setToolTip("Stop the scan. Cubes that are already finished stay saved; run again to continue.")
        self._stop_button.setEnabled(False)
        self.rescan_all_checkbox = QCheckBox("Scan every cube again", self)
        self.rescan_all_checkbox.setToolTip(
            "Read all cubes even if their saved results are still current (nothing changed since the last scan)."
        )
        self._status = QLabel("No scan yet.", self)
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f"color: {get_active_theme().text_dim};")

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(6)
        buttons.addWidget(self._run_button)
        buttons.addWidget(self._stop_button)
        buttons.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(3)
        layout.addLayout(buttons)
        layout.addWidget(self.rescan_all_checkbox)
        layout.addWidget(self._status)

        self._run_button.clicked.connect(lambda _checked=False: self.run_requested.emit())
        self._stop_button.clicked.connect(lambda _checked=False: self.stop_requested.emit())

    # -- driven by the owner -------------------------------------------------------------------------------------------------

    def set_running(self, running: bool) -> None:
        self._run_button.setEnabled(not running)
        self._stop_button.setEnabled(running)
        self.rescan_all_checkbox.setEnabled(not running)

    def set_status(self, text: str) -> None:
        self._status.setText(text)

    def status_text(self) -> str:
        return self._status.text()

    def rescan_all(self) -> bool:
        return self.rescan_all_checkbox.isChecked()
