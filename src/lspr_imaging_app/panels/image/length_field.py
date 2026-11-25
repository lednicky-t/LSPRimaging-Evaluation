"""A length in pixels shown as px or um (the app-wide unit): the field of the Array pop-out's pitches.

Moved here 2026-11-25 from the retired Array group's `array_controls.py`. It always keeps the value in **pixels** (resolution 0.1 px); the small text button behind the
field switches the shown unit for the whole app (the same switch as the scale bar's), and is disabled without a calibration.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QDoubleSpinBox, QHBoxLayout, QToolButton, QWidget

from lspr_ui import get_active_theme

from ...image_tools.geometry.module import GeometryModule


class LengthField(QWidget):
    """A length in pixels shown as px or um (the app-wide unit), edited with 0.1 px resolution.

    ``value_px()`` is always pixels."""

    value_changed = pyqtSignal(float)

    def __init__(self, geometry: GeometryModule, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._geometry = geometry
        self._px = 0.0
        self._updating = False
        self._spin = QDoubleSpinBox(self)
        self._spin.setRange(0.0, 1_000_000.0)
        self._spin.setKeyboardTracking(False)
        self._spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._unit = QToolButton(self)
        self._unit.setCheckable(True)  # checked = micrometers
        self._unit.setAutoRaise(True)
        self._unit.setFixedWidth(34)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._spin, 1)
        layout.addWidget(self._unit)
        self._spin.valueChanged.connect(self._on_edited)
        self._unit.toggled.connect(self._on_unit_toggled)
        geometry.cosmetic_changed.connect(self._refresh)
        geometry.geometry_changed.connect(self._refresh)
        self._refresh()

    def value_px(self) -> float:
        return self._px

    def set_value_px(self, value: float) -> None:
        self._px = max(float(value), 0.0)
        self._refresh()

    def _factor(self) -> float:
        settings = self._geometry.settings()
        if settings.display_units == "um" and self._geometry.can_display_micrometers():
            return float(self._geometry.microns_per_pixel_scalar())
        return 1.0

    def _refresh(self, *_args: object) -> None:
        um = self._factor() != 1.0
        self._updating = True
        try:
            self._spin.setDecimals(2 if um else 1)
            self._spin.setSingleStep(0.1 * self._factor())
            self._spin.setValue(self._px * self._factor())
            self._unit.setChecked(um)
            self._unit.setText("µm" if um else "px")
            self._unit.setEnabled(self._geometry.can_display_micrometers())
            theme = get_active_theme()  # the switch's text is always drawn (also while it cannot be clicked, without a calibration)
            self._unit.setStyleSheet(
                f"QToolButton {{ border: none; background: transparent; padding: 0px 2px; font-weight: 600; color: {theme.text_secondary}; }}"
                f"QToolButton:disabled {{ color: {theme.text_dim}; background: transparent; }}"
                f"QToolButton:hover:enabled {{ color: {theme.accent_blue}; }}"
            )
            self._unit.setToolTip(
                ("Micrometers (click for pixels)" if um else "Pixels (click for micrometers)")
                if self._geometry.can_display_micrometers()
                else "Micrometers need a calibration (Image tools > Measure)"
            )
        finally:
            self._updating = False

    def _on_edited(self, shown: float) -> None:
        if self._updating:
            return
        self._px = float(shown) / self._factor()
        self.value_changed.emit(self._px)

    def _on_unit_toggled(self, checked: bool) -> None:
        if self._updating:
            return
        try:
            self._geometry.set_display_units("um" if checked else "px")
        except ValueError:  # the calibration was lost meanwhile
            pass
        self._refresh()
