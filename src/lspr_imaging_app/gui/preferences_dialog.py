from __future__ import annotations

from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

_ZARR_ADAPTIVE_BATCH_OPTIONS = [
    (256, "256 MB"),
    (512, "512 MB"),
    (1024, "1 GB (default)"),
    (2048, "2 GB"),
    (4096, "4 GB"),
]

_STARTUP_RESTORE_TIMEOUT_OPTIONS = [
    (5, "Prompt restore (5s)"),
    (0, "Auto restore (0s)"),
]


class PreferencesDialog(QDialog):
    def __init__(self, window, parent: QWidget | None = None) -> None:
        super().__init__(parent or window)
        self._window = window
        self.setWindowTitle("Preferences")
        self.setModal(True)
        self.setMinimumWidth(420)

        # Appearance
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Blue Dark Theme", "blue")
        self.theme_combo.addItem("Gray Dark Theme", "gray")

        # Startup
        self.startup_restore_combo = QComboBox()
        for seconds, label in _STARTUP_RESTORE_TIMEOUT_OPTIONS:
            self.startup_restore_combo.addItem(label, seconds)
        self.startup_restore_combo.setToolTip(
            "How long to show the \"restore previous session?\" prompt on launch before "
            "giving up (Prompt restore) or skip the prompt and restore immediately (Auto restore)."
        )
        self.log_panel_open_check = QCheckBox("Open log panel on startup")
        self.log_panel_open_check.setToolTip(
            "Start the app with the Workflow log console (Debug output, errors, and timing) "
            "expanded. Takes effect on the next launch; the log panel can always be expanded "
            "or collapsed manually from the Workflow tab."
        )

        # OME-Zarr export
        self.zarr_adaptive_enabled_check = QCheckBox("Adaptive worker tuning enabled")
        self.zarr_adaptive_enabled_check.setToolTip(
            "Periodically adjust the number of Stack-to-Zarr export worker processes based on "
            "whether time is being spent waiting on disk or on compression. Turn off for a "
            "fixed, predictable worker count equal to your CPU's core count."
        )
        self.zarr_adaptive_batch_combo = QComboBox()
        for mb, label in _ZARR_ADAPTIVE_BATCH_OPTIONS:
            self.zarr_adaptive_batch_combo.addItem(label, mb)
        self.zarr_adaptive_batch_combo.setToolTip(
            "How much data is processed between adaptive tuning checks. A larger sample avoids "
            "mistaking a fast drive's temporary write-cache burst for its true sustained speed."
        )
        self.zarr_adaptive_info_button = QPushButton("What is this?")
        self.zarr_adaptive_info_button.clicked.connect(self._show_zarr_adaptive_info)

        self._build_ui()
        self._load_from_window()
        self._fit_to_available_screen()

    def _build_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # A tall dialog gets a scroll area instead of growing past the
        # screen's available height (see _fit_to_available_screen), matching
        # the pattern used by singleLSPR Acquisition's preferences dialog.
        scroll_area = QScrollArea(self)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget(scroll_area)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        appearance_box = QGroupBox("Appearance")
        appearance_layout = QFormLayout(appearance_box)
        appearance_layout.setHorizontalSpacing(16)
        appearance_layout.setVerticalSpacing(8)
        appearance_layout.addRow("Theme", self.theme_combo)

        startup_box = QGroupBox("Startup")
        startup_layout = QFormLayout(startup_box)
        startup_layout.setHorizontalSpacing(16)
        startup_layout.setVerticalSpacing(8)
        startup_layout.addRow("Restore previous session", self.startup_restore_combo)
        startup_layout.addRow(self.log_panel_open_check)

        zarr_box = QGroupBox("OME-Zarr export: adaptive worker tuning")
        zarr_layout = QFormLayout(zarr_box)
        zarr_layout.setHorizontalSpacing(16)
        zarr_layout.setVerticalSpacing(8)
        zarr_layout.addRow(self.zarr_adaptive_enabled_check)
        zarr_layout.addRow("Sample size for tuning decisions", self.zarr_adaptive_batch_combo)
        info_row = QHBoxLayout()
        info_row.addStretch(1)
        info_row.addWidget(self.zarr_adaptive_info_button)
        zarr_layout.addRow(info_row)

        layout.addWidget(appearance_box)
        layout.addWidget(startup_box)
        layout.addWidget(zarr_box)

        scroll_area.setWidget(content)
        outer_layout.addWidget(scroll_area, 1)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._on_ok)
        button_box.rejected.connect(self.reject)
        apply_btn = button_box.button(QDialogButtonBox.StandardButton.Apply)
        if apply_btn is not None:
            apply_btn.clicked.connect(self.apply_changes)
        button_row = QWidget(self)
        button_row_layout = QVBoxLayout(button_row)
        button_row_layout.setContentsMargins(16, 10, 16, 16)
        button_row_layout.addWidget(button_box)
        outer_layout.addWidget(button_row, 0)

    def _fit_to_available_screen(self) -> None:
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        max_height = max(int(available.height() * 0.85), 320)
        target_height = min(self.sizeHint().height(), max_height)
        self.resize(self.width(), target_height)

    def _on_ok(self) -> None:
        self.apply_changes()
        self.accept()

    def _show_zarr_adaptive_info(self) -> None:
        if hasattr(self._window, "_dataset_controller"):
            self._window._dataset_controller._show_ome_zarr_adaptive_tuning_info()

    def _load_from_window(self) -> None:
        window = self._window

        theme = str(window._settings.value("ui/theme", "blue")) if hasattr(window, "_settings") else "blue"
        theme_index = self.theme_combo.findData("gray" if theme == "gray" else "blue")
        if theme_index >= 0:
            self.theme_combo.setCurrentIndex(theme_index)

        if hasattr(window, "_startup_restore_timeout_seconds"):
            timeout = window._startup_restore_timeout_seconds()
            index = self.startup_restore_combo.findData(timeout)
            self.startup_restore_combo.setCurrentIndex(index if index >= 0 else 0)

        if hasattr(window, "_startup_log_panel_open"):
            self.log_panel_open_check.setChecked(bool(window._startup_log_panel_open()))

        if hasattr(window, "_ome_zarr_adaptive_enabled"):
            self.zarr_adaptive_enabled_check.setChecked(bool(window._ome_zarr_adaptive_enabled()))

        if hasattr(window, "_ome_zarr_adaptive_batch_mb"):
            batch_mb = window._ome_zarr_adaptive_batch_mb()
            index = self.zarr_adaptive_batch_combo.findData(batch_mb)
            self.zarr_adaptive_batch_combo.setCurrentIndex(index if index >= 0 else 2)

    def apply_changes(self) -> None:
        window = self._window

        theme = str(self.theme_combo.currentData() or "blue")
        if hasattr(window, "_set_ui_theme"):
            window._set_ui_theme(theme)

        if hasattr(window, "_set_startup_restore_timeout_seconds"):
            timeout = self.startup_restore_combo.currentData()
            if timeout is not None:
                window._set_startup_restore_timeout_seconds(int(timeout))

        if hasattr(window, "_set_startup_log_panel_open"):
            window._set_startup_log_panel_open(self.log_panel_open_check.isChecked())

        if hasattr(window, "_set_ome_zarr_adaptive_enabled"):
            window._set_ome_zarr_adaptive_enabled(self.zarr_adaptive_enabled_check.isChecked())

        if hasattr(window, "_set_ome_zarr_adaptive_batch_mb"):
            batch_mb = self.zarr_adaptive_batch_combo.currentData()
            if batch_mb is not None:
                window._set_ome_zarr_adaptive_batch_mb(int(batch_mb))


def show_preferences_dialog_for(window) -> None:
    dialog = PreferencesDialog(window)
    dialog.exec()
