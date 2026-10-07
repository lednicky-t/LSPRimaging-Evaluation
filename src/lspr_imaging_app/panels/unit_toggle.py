"""The px <-> µm toggle: a one-button control for the Geometry display unit.

The same control, with the same look and behaviour, as the units button in the
Image ribbon's View tab (`image/scale_bar_controls.py`): it shows the current
unit, flips it on click, is disabled until a calibration exists (micrometers
need one), and follows the unit when it is changed anywhere else (the ribbon,
a session restore). The unit itself lives in `GeometryModule`; this holds no
state of its own.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QToolButton, QWidget

from ..image_tools.geometry.module import GeometryModule
from .image.general_group import style_general_icon_button


class UnitToggle(QToolButton):
    def __init__(self, geometry: GeometryModule, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._geometry = geometry
        self.setCheckable(True)  # checked = micrometers, unchecked = pixels
        self.toggled.connect(self._on_toggled)
        geometry.cosmetic_changed.connect(self.sync)
        geometry.geometry_changed.connect(self.sync)
        self.refresh_theme()

    def sync(self, *_args: object) -> None:
        """Follow the geometry settings (also after a session restore)."""
        can_um = self._geometry.can_display_micrometers()
        um = bool(self._geometry.settings().display_units == "um" and can_um)
        blocked = self.blockSignals(True)
        try:
            self.setChecked(um)
        finally:
            self.blockSignals(blocked)
        self.setText("µm" if um else "px")
        self.setEnabled(can_um)
        self.setToolTip(
            ("Lengths in micrometers (click for pixels)" if um else "Lengths in pixels (click for micrometers)")
            if can_um
            else "Micrometers need a calibration (Image tools > Measure)"
        )

    def refresh_theme(self) -> None:
        style_general_icon_button(self)
        self.sync()

    def _on_toggled(self, checked: bool) -> None:
        try:
            self._geometry.set_display_units("um" if checked else "px")
        except ValueError:  # the calibration was lost meanwhile; the button is re-synced below
            pass
        self.sync()
