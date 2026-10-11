"""One display-only tint overlay on the Image panel (mask or histogram highlight).

Holds the cosmetic state (shown/hidden, colour, opacity) and the row-major
`pg.ImageItem` it paints into. Split out of `panel.py` 2026-10-06, where the two
tints each kept these as four separate fields. The panel still decides *what* to
tint (which pixels) and *when*; this only turns a boolean mask into a coloured
RGBA layer."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PyQt6.QtGui import QColor

from .overlay_style import OverlayStyle


class OverlayTint:
    def __init__(self, color: QColor, alpha: float, style: OverlayStyle | None = None) -> None:
        self.visible = True
        self.color = QColor(color)
        self.alpha = float(alpha)
        if style is not None:
            self.visible = bool(style.visible)
            self.color = QColor(style.color)
            self.alpha = float(style.alpha)
        self.item = pg.ImageItem(axisOrder="row-major")
        self.item.hide()

    def attach(self, plot: pg.PlotItem) -> None:
        plot.addItem(self.item)

    def style(self) -> OverlayStyle:
        return OverlayStyle(self.visible, self.color.name(), self.alpha)

    def hide(self) -> None:
        self.item.hide()

    def paint(self, mask: np.ndarray) -> None:
        """Tint the True pixels of *mask* in the current colour and opacity."""
        # One 32-bit write per tinted pixel instead of four byte writes
        # (~3x faster on a full frame); viewing the bytes as RGBA is
        # byte-order safe because the packed value is built from the same bytes.
        rgba = np.array(
            [self.color.red(), self.color.green(), self.color.blue(), int(round(self.alpha * 255.0))], dtype=np.uint8
        )
        packed = np.zeros(mask.shape, dtype=np.uint32)
        packed[mask] = rgba.view(np.uint32)[0]
        self.item.setImage(packed.view(np.uint8).reshape(*mask.shape, 4), autoLevels=False)
        self.item.show()
