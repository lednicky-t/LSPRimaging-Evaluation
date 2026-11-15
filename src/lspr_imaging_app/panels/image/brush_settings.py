"""The selection brush's size, in the pop-up of its toolbar button (2026-11-15; right-click the brush button, like the wand's settings).

The number is `MaskSettings.brush_size_px` (the Mask tab's old Draw tool had the same field), so it is saved with the session and a
session that had a brush size keeps it. The brush paints into the *selection*, not the mask: turning the selection into mask pixels is
the Mask tab's Add / Replace selection.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFormLayout, QSpinBox, QWidget

from ...image_tools import MaskModule
from ..mask_tool_settings import push_tool_settings


class BrushSettingsForm(QWidget):
    def __init__(self, mask: MaskModule, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mask = mask
        self._syncing = False
        form = QFormLayout(self)
        form.setContentsMargins(10, 8, 10, 8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.size_spin = QSpinBox(self)
        self.size_spin.setRange(1, 200)
        self.size_spin.setSuffix(" px")
        self.size_spin.setKeyboardTracking(False)
        self.size_spin.setToolTip("Brush diameter in image pixels. A pixel is painted when its centre is within half of it of the stroke.")
        form.addRow("Brush size", self.size_spin)
        self._sync(mask.settings().brush_size_px)
        self.size_spin.valueChanged.connect(self._on_size_changed)
        mask.cosmetic_changed.connect(self._on_model_changed)
        mask.mask_changed.connect(self._on_model_changed)

    def diameter(self) -> float:
        return float(self._mask.settings().brush_size_px)

    def _sync(self, size: int) -> None:
        self._syncing = True
        try:
            self.size_spin.setValue(int(size))
        finally:
            self._syncing = False

    def _on_model_changed(self, _change: object) -> None:
        self._sync(self._mask.settings().brush_size_px)

    def _on_size_changed(self, value: int) -> None:
        if not self._syncing:
            push_tool_settings(self._mask, brush_size_px=int(value))
