from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lspr_ui import get_active_theme

# Line-style settings dialogs opened from the small settings icon in the
# Spectra/Sensogram plots' corner overlay (see plot_overlay_controller.py).
# Modeled on this app's existing dialog shape (preferences_dialog.py:
# QDialog + Ok/Apply/Cancel QDialogButtonBox + a module-level
# show_..._dialog_for(window) factory), and on singleLSPR Acquisition's
# SensorgramPlotSettingsDialog for the kind of controls to expose (line
# width/style), see apps/sLSPR/acq/src/lspr_app/gui/main_window_plot_settings.py.
#
# Series colors are deliberately NOT exposed for the spectrum's data curves
# or the sensogram's raw curve: those colors already carry meaning (each
# ROI's own color, or the "which ROI is selected" highlight in
# AnalysisController.update_selection_highlight) and overriding them here
# would fight that. Colors ARE exposed for the sensogram's processed/group
# statistics overlays, whose colors are fixed decorative literals today.

_LINE_STYLE_OPTIONS = [
    ("Solid", Qt.PenStyle.SolidLine),
    ("Dashed", Qt.PenStyle.DashLine),
    ("Dotted", Qt.PenStyle.DotLine),
    ("Dash-dot", Qt.PenStyle.DashDotLine),
]


def _line_style_combo(current: Qt.PenStyle) -> QComboBox:
    combo = QComboBox()
    for label, style in _LINE_STYLE_OPTIONS:
        combo.addItem(label, style)
    index = combo.findData(current)
    combo.setCurrentIndex(index if index >= 0 else 0)
    return combo


def _line_width_spin(current: float, *, minimum: float = 0.5, maximum: float = 10.0) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(minimum, maximum)
    spin.setSingleStep(0.1)
    spin.setDecimals(1)
    spin.setSuffix(" px")
    spin.setValue(float(current))
    return spin


def _note_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {get_active_theme().text_dim}; font-size: 9pt;")
    return label


class _ColorSwatchButton(QToolButton):
    def __init__(self, color: QColor, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = QColor(color)
        self.setFixedSize(22, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Choose a color.")
        self.clicked.connect(self._pick_color)
        self._refresh_style()

    def color(self) -> QColor:
        return QColor(self._color)

    def set_color(self, color: QColor) -> None:
        self._color = QColor(color)
        self._refresh_style()

    def _refresh_style(self) -> None:
        theme = get_active_theme()
        self.setStyleSheet(
            f"QToolButton {{ background-color: {self._color.name()}; "
            f"border: 1px solid {theme.control_border}; border-radius: 4px; }}"
        )

    def _pick_color(self) -> None:
        chosen = QColorDialog.getColor(self._color, self, "Choose color")
        if chosen.isValid():
            self.set_color(chosen)


class _PlotStyleDialogBase(QDialog):
    def __init__(self, window, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent or window)
        self._window = window
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(340)

    def _finish_ui(self, content_layout: QVBoxLayout) -> None:
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._on_ok)
        button_box.rejected.connect(self.reject)
        apply_button = button_box.button(QDialogButtonBox.StandardButton.Apply)
        if apply_button is not None:
            apply_button.clicked.connect(self.apply_changes)
        content_layout.addWidget(button_box)

    def _on_ok(self) -> None:
        self.apply_changes()
        self.accept()

    def apply_changes(self) -> None:
        raise NotImplementedError


class SpectrumPlotSettingsDialog(_PlotStyleDialogBase):
    def __init__(self, window, parent: QWidget | None = None) -> None:
        super().__init__(window, "Spectra plot settings", parent)

        self.fit_width_spin = _line_width_spin(window._spectrum_fit_line_width_px)
        self.fit_style_combo = _line_style_combo(window._spectrum_fit_line_style)
        self.symbol_size_spin = _line_width_spin(window._spectrum_symbol_size_px, minimum=2.0, maximum=20.0)

        layout = QVBoxLayout(self)

        fit_box = QGroupBox("Fit line")
        fit_layout = QFormLayout(fit_box)
        fit_layout.addRow("Width", self.fit_width_spin)
        fit_layout.addRow("Style", self.fit_style_combo)

        points_box = QGroupBox("Data points")
        points_layout = QFormLayout(points_box)
        points_layout.addRow("Symbol size", self.symbol_size_spin)

        layout.addWidget(fit_box)
        layout.addWidget(points_box)
        layout.addWidget(_note_label("Series colors follow each ROI's own color, set from the ROI list."))
        self._finish_ui(layout)

    def apply_changes(self) -> None:
        window = self._window
        window._spectrum_fit_line_width_px = float(self.fit_width_spin.value())
        window._spectrum_fit_line_style = self.fit_style_combo.currentData()
        window._spectrum_symbol_size_px = float(self.symbol_size_spin.value())
        if hasattr(window, "_refresh_absorbance_spectrum"):
            window._refresh_absorbance_spectrum()


class SensorgramPlotSettingsDialog(_PlotStyleDialogBase):
    def __init__(self, window, parent: QWidget | None = None) -> None:
        super().__init__(window, "Sensogram plot settings", parent)

        self.raw_width_spin = _line_width_spin(window._sensorgram_line_width_px)
        self.raw_style_combo = _line_style_combo(window._sensorgram_line_style)

        self.processed_width_spin = _line_width_spin(window._sensorgram_processed_line_width_px)
        self.processed_style_combo = _line_style_combo(window._sensorgram_processed_line_style)
        self.processed_color_button = _ColorSwatchButton(window._sensorgram_processed_color)

        self.group_width_spin = _line_width_spin(window._sensorgram_group_line_width_px)
        self.group_style_combo = _line_style_combo(window._sensorgram_group_line_style)
        self.group_color_button = _ColorSwatchButton(window._sensorgram_group_color)

        layout = QVBoxLayout(self)

        raw_box = QGroupBox("Raw trace")
        raw_layout = QFormLayout(raw_box)
        raw_layout.addRow("Width", self.raw_width_spin)
        raw_layout.addRow("Style", self.raw_style_combo)

        processed_box = QGroupBox("Statistics: processed trace")
        processed_layout = QFormLayout(processed_box)
        processed_layout.addRow("Width", self.processed_width_spin)
        processed_layout.addRow("Style", self.processed_style_combo)
        processed_layout.addRow("Color", self.processed_color_button)

        group_box = QGroupBox("Statistics: group trace")
        group_layout = QFormLayout(group_box)
        group_layout.addRow("Width", self.group_width_spin)
        group_layout.addRow("Style", self.group_style_combo)
        group_layout.addRow("Color", self.group_color_button)

        layout.addWidget(raw_box)
        layout.addWidget(processed_box)
        layout.addWidget(group_box)
        layout.addWidget(_note_label(
            "The raw trace's color follows ROI selection (see the ROI list); only its "
            "width and style are adjustable here."
        ))
        self._finish_ui(layout)

    def apply_changes(self) -> None:
        window = self._window
        window._sensorgram_line_width_px = float(self.raw_width_spin.value())
        window._sensorgram_line_style = self.raw_style_combo.currentData()
        window._sensorgram_processed_line_width_px = float(self.processed_width_spin.value())
        window._sensorgram_processed_line_style = self.processed_style_combo.currentData()
        window._sensorgram_processed_color = self.processed_color_button.color()
        window._sensorgram_group_line_width_px = float(self.group_width_spin.value())
        window._sensorgram_group_line_style = self.group_style_combo.currentData()
        window._sensorgram_group_color = self.group_color_button.color()
        if hasattr(window, "_analysis_controller"):
            window._analysis_controller.apply_sensorgram_style_settings()


def show_spectrum_plot_settings_dialog_for(window) -> None:
    dialog = SpectrumPlotSettingsDialog(window)
    dialog.exec()


def show_sensorgram_plot_settings_dialog_for(window) -> None:
    dialog = SensorgramPlotSettingsDialog(window)
    dialog.exec()
