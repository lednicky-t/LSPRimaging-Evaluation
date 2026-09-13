from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QPushButton,
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
# A literal per-series color override is deliberately NOT exposed for the
# spectrum's data curves or the sensogram's raw curve: those colors already
# carry meaning (each ROI's own color, or the "which ROI is selected"
# highlight in AnalysisController.update_selection_highlight) and
# overriding one directly here would fight that. What IS exposed, in the
# shared "ROI colors" section both dialogs add (_build_roi_color_section),
# is which PALETTE an unassigned ROI's/group's color is auto-picked from -
# see roi_color_palettes.py and roi_overlay_helpers.resolved_roi_plot_color.
# Colors ARE directly exposed for the sensogram's processed/group
# statistics overlays, whose colors are fixed decorative literals today.

_SEQUENTIAL_PALETTE_LABELS: list[tuple[str, str]] = [
    ("Viridis", "viridis"),
    ("Plasma", "plasma"),
    ("Cividis (colorblind-safe)", "cividis"),
    ("Turbo", "turbo"),
]
_CATEGORICAL_PALETTE_LABELS: list[tuple[str, str]] = [
    ("Tab10 (10 colors)", "tab10"),
    ("Tab20 (20 colors)", "tab20"),
    ("Set2 (8, soft)", "set2"),
    ("Dark2 (8, muted)", "dark2"),
]

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


def _palette_combo(options: list[tuple[str, str]], current: str) -> QComboBox:
    combo = QComboBox()
    for label, value in options:
        combo.addItem(label, value)
    index = combo.findData(current)
    combo.setCurrentIndex(index if index >= 0 else 0)
    return combo


def _build_roi_color_section(dialog: QDialog, window, *, gradient_attr: str) -> QGroupBox:
    """Shared "ROI colors" section, added identically to both the Spectra
    and Sensogram plot settings dialogs - see roi_color_palettes.py for the
    palette values and resolved_roi_plot_color for how they get used.

    - Ungrouped ROI gradient: which sequential palette an unassigned ROI's
      color is drawn from, by its position among the dataset's ROIs. Reads/
      writes `gradient_attr` on `window` - the two plots keep this setting
      separate (not shared) since the Sensogram can show a different
      number of simultaneous traces than the Spectra plot does (e.g. one
      averaged trace per group, rather than one per ROI).
    - Group palette: which categorical palette a newly-created group's own
      color is assigned from. This one IS shared between both dialogs
      (`window._roi_group_color_palette`) - a group's color means the same
      thing everywhere it's shown. "Apply to existing groups" is an
      explicit, opt-in re-color of every current group from this palette;
      changing the dropdown alone never touches an existing group's color.

    Widgets are stored on `dialog` as `roi_gradient_combo`/
    `roi_group_palette_combo`/`roi_group_apply_button` for `apply_changes`
    to read back.
    """
    box = QGroupBox("ROI colors")
    layout = QFormLayout(box)

    dialog.roi_gradient_combo = _palette_combo(_SEQUENTIAL_PALETTE_LABELS, getattr(window, gradient_attr))
    gradient_title = QLabel("Ungrouped ROI gradient")
    gradient_title.setToolTip(
        "An unassigned ROI's Spectra/Sensogram color comes from this gradient, ordered by the ROI's "
        "position among all of the dataset's ROIs - nearby indices read as nearby colors."
    )
    dialog.roi_gradient_combo.setToolTip(gradient_title.toolTip())
    layout.addRow(gradient_title, dialog.roi_gradient_combo)

    dialog.roi_group_palette_combo = _palette_combo(_CATEGORICAL_PALETTE_LABELS, window._roi_group_color_palette)
    group_title = QLabel("Group palette")
    group_title.setToolTip(
        "A newly-created group's own color comes from this palette, in creation order. Shared between "
        "the Spectra and Sensogram settings - a group's color is the same everywhere it's shown."
    )
    dialog.roi_group_palette_combo.setToolTip(group_title.toolTip())
    layout.addRow(group_title, dialog.roi_group_palette_combo)

    dialog.roi_group_apply_button = QPushButton("Apply to existing groups", box)
    dialog.roi_group_apply_button.setToolTip(
        "Re-color every existing group from the palette above, in their current order. Does not touch "
        "ungrouped ROIs. Changing the dropdown alone never does this on its own."
    )
    dialog.roi_group_apply_button.clicked.connect(lambda: _apply_group_palette_now(dialog, window))
    layout.addRow(dialog.roi_group_apply_button)

    return box


def _apply_group_palette_now(dialog: QDialog, window) -> None:
    window._roi_group_color_palette = str(
        dialog.roi_group_palette_combo.currentData() or window._roi_group_color_palette
    )
    window._save_visual_preferences()
    window._apply_group_color_palette()


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

        roi_color_box = _build_roi_color_section(self, window, gradient_attr="_spectrum_roi_gradient_palette")

        layout.addWidget(fit_box)
        layout.addWidget(points_box)
        layout.addWidget(roi_color_box)
        layout.addWidget(_note_label(
            "Series colors follow each ROI's own color, set from the ROI list, or the palettes above when "
            "a ROI has no color of its own."
        ))
        self._finish_ui(layout)

    def apply_changes(self) -> None:
        window = self._window
        window._spectrum_fit_line_width_px = float(self.fit_width_spin.value())
        window._spectrum_fit_line_style = self.fit_style_combo.currentData()
        window._spectrum_symbol_size_px = float(self.symbol_size_spin.value())
        window._spectrum_roi_gradient_palette = str(self.roi_gradient_combo.currentData() or "viridis")
        window._roi_group_color_palette = str(self.roi_group_palette_combo.currentData() or "tab10")
        if hasattr(window, "_refresh_formula_spectrum"):
            window._refresh_formula_spectrum()
        if hasattr(window, "_analysis_controller"):
            window._analysis_controller._render_sensorgram_display()
        window._save_visual_preferences()


class SensorgramPlotSettingsDialog(_PlotStyleDialogBase):
    def __init__(self, window, parent: QWidget | None = None) -> None:
        super().__init__(window, "Sensogram plot settings", parent)

        self.raw_width_spin = _line_width_spin(window._sensorgram_line_width_px)
        self.raw_style_combo = _line_style_combo(window._sensorgram_line_style)

        self.show_symbols_checkbox = QCheckBox("Show data point markers")
        self.show_symbols_checkbox.setChecked(bool(window._sensorgram_show_symbols))
        self.symbol_size_spin = _line_width_spin(window._sensorgram_symbol_size_px, minimum=2.0, maximum=20.0)
        self.symbol_size_spin.setEnabled(self.show_symbols_checkbox.isChecked())
        self.show_symbols_checkbox.toggled.connect(self.symbol_size_spin.setEnabled)

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

        points_box = QGroupBox("Raw trace data points")
        points_layout = QFormLayout(points_box)
        points_layout.addRow(self.show_symbols_checkbox)
        points_layout.addRow("Symbol size", self.symbol_size_spin)

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

        roi_color_box = _build_roi_color_section(self, window, gradient_attr="_sensorgram_roi_gradient_palette")

        layout.addWidget(raw_box)
        layout.addWidget(points_box)
        layout.addWidget(roi_color_box)
        layout.addWidget(processed_box)
        layout.addWidget(group_box)
        layout.addWidget(_note_label(
            "The raw trace's color follows ROI selection (see the ROI list) or the palettes above; only "
            "its width, style, and data point markers are adjustable here."
        ))
        self._finish_ui(layout)

    def apply_changes(self) -> None:
        window = self._window
        window._sensorgram_line_width_px = float(self.raw_width_spin.value())
        window._sensorgram_line_style = self.raw_style_combo.currentData()
        window._sensorgram_show_symbols = bool(self.show_symbols_checkbox.isChecked())
        window._sensorgram_symbol_size_px = float(self.symbol_size_spin.value())
        window._sensorgram_roi_gradient_palette = str(self.roi_gradient_combo.currentData() or "viridis")
        window._roi_group_color_palette = str(self.roi_group_palette_combo.currentData() or "tab10")
        window._sensorgram_processed_line_width_px = float(self.processed_width_spin.value())
        window._sensorgram_processed_line_style = self.processed_style_combo.currentData()
        window._sensorgram_processed_color = self.processed_color_button.color()
        window._sensorgram_group_line_width_px = float(self.group_width_spin.value())
        window._sensorgram_group_line_style = self.group_style_combo.currentData()
        window._sensorgram_group_color = self.group_color_button.color()
        if hasattr(window, "_analysis_controller"):
            window._analysis_controller.apply_sensorgram_style_settings()
        window._save_visual_preferences()


def show_spectrum_plot_settings_dialog_for(window) -> None:
    dialog = SpectrumPlotSettingsDialog(window)
    dialog.exec()


def show_sensorgram_plot_settings_dialog_for(window) -> None:
    dialog = SensorgramPlotSettingsDialog(window)
    dialog.exec()
