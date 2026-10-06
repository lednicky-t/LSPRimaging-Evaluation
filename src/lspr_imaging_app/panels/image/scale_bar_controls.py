"""The three View-tab icons for the canvas scale bar (2026-10-06): show/hide,
px <-> µm units, and colour. Show/hide and units live in `GeometryModule`
(`set_scale_bar_visible` / `set_display_units`, saved with the session); the
colour is plain UI state, owned by `ImagePanel`. See `scale_bar_overlay.py`."""

from __future__ import annotations

from PyQt6.QtCore import QSize, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QColorDialog, QHBoxLayout, QToolButton, QWidget

from lspr_ui import GuiTheme, get_active_theme, load_tabler_icon

from ...image_tools.geometry.module import GeometryModule
from .general_group import ICON_SIZE, style_general_icon_button

_SWATCH_SIZE = 14


class ScaleBarControls(QWidget):
    color_changed = pyqtSignal(QColor)

    def __init__(self, geometry: GeometryModule, color: QColor, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._geometry = geometry
        self._color = QColor(color)

        self._toggle = QToolButton(self)
        self._toggle.setCheckable(True)
        self._toggle.toggled.connect(self._on_toggled)

        self._units = QToolButton(self)  # checked = micrometers, unchecked = pixels
        self._units.setCheckable(True)
        self._units.toggled.connect(self._on_units_toggled)

        self._swatch = QToolButton(self)
        self._swatch.setFixedSize(_SWATCH_SIZE, _SWATCH_SIZE)
        self._swatch.setToolTip("Choose the scale bar colour.")
        self._swatch.clicked.connect(self._on_choose_color)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._toggle)
        layout.addWidget(self._units)
        layout.addWidget(self._swatch)

        geometry.cosmetic_changed.connect(self.sync)
        geometry.geometry_changed.connect(self.sync)
        self.refresh_theme(get_active_theme())

    def color(self) -> QColor:
        return QColor(self._color)

    def set_color(self, color: QColor) -> None:
        """Set the swatch without emitting `color_changed` (startup restore)."""
        self._color = QColor(color)
        self._refresh_swatch()

    def sync(self, *_args: object) -> None:
        """Follow the geometry settings (also after a session restore)."""
        settings = self._geometry.settings()
        can_um = self._geometry.can_display_micrometers()
        theme = get_active_theme()
        shown = bool(settings.scale_bar_visible)
        um = bool(settings.display_units == "um" and can_um)
        for button, checked in ((self._toggle, shown), (self._units, um)):
            blocked = button.blockSignals(True)
            button.setChecked(checked)
            button.blockSignals(blocked)
        self._toggle.setIcon(
            load_tabler_icon("ruler-measure", color=theme.accent_blue if shown else theme.text_muted,
                             size=ICON_SIZE * 2, stroke_width=2.1)
        )
        self._toggle.setToolTip("Scale bar shown (click to hide)" if shown else "Scale bar hidden (click to show)")
        self._units.setText("µm" if um else "px")
        self._units.setEnabled(can_um)
        self._units.setToolTip(
            ("Scale bar in micrometers (click for pixels)" if um else "Scale bar in pixels (click for micrometers)")
            if can_um
            else "Micrometers need a calibration (Image tools > Measure)"
        )

    def refresh_theme(self, _theme: GuiTheme) -> None:
        for button in (self._toggle, self._units):
            style_general_icon_button(button)
            button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self._refresh_swatch()
        self.sync()

    def _on_toggled(self, checked: bool) -> None:
        self._geometry.set_scale_bar_visible(bool(checked))

    def _on_units_toggled(self, checked: bool) -> None:
        try:
            self._geometry.set_display_units("um" if checked else "px")
        except ValueError:  # calibration lost meanwhile; the button is re-synced below
            pass
        self.sync()

    def _on_choose_color(self) -> None:
        color = QColorDialog.getColor(self._color, self, "Choose scale bar colour")
        if not color.isValid():
            return
        self._color = color
        self._refresh_swatch()
        self.color_changed.emit(QColor(color))

    def _refresh_swatch(self) -> None:
        theme = get_active_theme()
        self._swatch.setStyleSheet(
            f"QToolButton {{ background-color: {self._color.name()}; min-width: {_SWATCH_SIZE}px; "
            f"max-width: {_SWATCH_SIZE}px; min-height: {_SWATCH_SIZE}px; max-height: {_SWATCH_SIZE}px; "
            f"border: 1px solid {theme.control_border}; border-radius: 3px; padding: 0; }}"
        )
