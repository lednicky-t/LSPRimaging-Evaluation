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
        self.item.setImage(rgba_layer(mask, rgba), autoLevels=False)
        self.item.show()


# Above this share of tinted pixels a whole-array `np.where` beats writing only the selected pixels through a
# boolean index (measured 2026-10-14 on 1.2 MP frames: 13.5 -> 7.5 ms at 40-60 % selected, 15.3 -> 7.2 ms at
# 60-95 %; below ~10 % the indexed write wins, 3.8 vs 8.5 ms). Both give the same bytes.
_DENSE_SHARE = 0.10


def rgba_layer(mask: np.ndarray, rgba: np.ndarray) -> np.ndarray:
    """The (H, W, 4) uint8 layer that is `rgba` (4 bytes) where *mask* is True and fully transparent (0, 0, 0, 0)
    elsewhere. Pure; `OverlayTint.paint` shows it."""
    value = rgba.view(np.uint32)[0]
    if np.count_nonzero(mask) > _DENSE_SHARE * mask.size:
        packed = np.where(mask, value, np.uint32(0)).astype(np.uint32, copy=False)
    else:
        packed = np.zeros(mask.shape, dtype=np.uint32)
        packed[mask] = value
    return packed.view(np.uint8).reshape(*mask.shape, 4)
