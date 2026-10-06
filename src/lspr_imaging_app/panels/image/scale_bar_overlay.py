"""Scale bar for the image canvas (2026-10-06).

Two parts:

- `nice_length` / `choose_bar`: pure functions (no Qt) that pick a readable bar
  length and label for the current zoom. Lengths are rounded to 1, 2 or 5
  times a power of ten; in micrometers the unit steps through nm / µm / mm so
  the number stays small (e.g. "500 nm", "20 µm", "2 mm").
- `ScaleBarItem`: one painted item (not three separate lines like the stable
  app, which left a seam between the bar and its end ticks). The bar and both
  end ticks are a single "H" path, stroked once with a contrasting halo
  underneath, so the outline runs around the whole shape. The length label
  sits centred above it on a small chip. The item is a child of the plot's
  `ViewBox`, so it is drawn in screen pixels: line width and text stay the
  same size while zooming, only the bar length follows the zoom.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
import pyqtgraph as pg

# Target bar length as a fraction of the visible image width. Rounding to
# 1/2/5 moves it at most about +/-50 %, so the bar stays between roughly 14 %
# and 35 % of the view.
TARGET_FRACTION = 0.2

_UNITS_UM = (("nm", 1e-3), ("µm", 1.0), ("mm", 1e3))


def nice_length(target: float) -> float:
    """Round *target* (> 0) to 1, 2 or 5 times a power of ten (nearest in a
    log sense, so the result is within about 1.5x of the target)."""
    if not target > 0.0 or not math.isfinite(target):
        return 1.0
    exponent = math.floor(math.log10(target))
    base = target / 10.0**exponent
    if base < 1.5:
        nice = 1.0
    elif base < 3.5:
        nice = 2.0
    elif base < 7.5:
        nice = 5.0
    else:
        nice = 10.0
    return nice * 10.0**exponent


def choose_bar(visible_width_px: float, um_per_px: float | None) -> tuple[float, str]:
    """Return ``(bar_length_in_image_px, label)`` for a view that shows
    *visible_width_px* image pixels across. *um_per_px* None means "show
    pixels" (no calibration, or the user chose px)."""
    visible_width_px = max(float(visible_width_px), 1.0)
    target_px = visible_width_px * TARGET_FRACTION
    if um_per_px is None or not um_per_px > 0.0:
        value = nice_length(target_px)
        return value, f"{value:g} px"
    target_um = target_px * um_per_px
    # Largest unit whose scale does not exceed the target, so the number is >= 1.
    name, factor = _UNITS_UM[0]
    for unit_name, unit_factor in _UNITS_UM:
        if target_um >= unit_factor:
            name, factor = unit_name, unit_factor
    value = nice_length(target_um / factor)
    if name != "mm" and value >= 1000.0:  # rounding pushed it over the next unit
        name, factor = _UNITS_UM[_UNITS_UM.index((name, factor)) + 1]
        value = nice_length(target_um / factor)
    return value * factor / um_per_px, f"{value:g} {name}"


def contrast_color(color: QColor) -> QColor:
    """Black or white, whichever stands out against *color* (for the halo)."""
    luminance = 0.299 * color.redF() + 0.587 * color.greenF() + 0.114 * color.blueF()
    return QColor(0, 0, 0) if luminance > 0.5 else QColor(255, 255, 255)


class ScaleBarItem(pg.GraphicsObject):
    """See the module docstring. Create with the plot's `ViewBox`; call
    `update_view(...)` whenever the view range, size, units or visibility
    change (`ImagePanel._draw_scale_bar`)."""

    _MARGIN = 16.0  # px from the view's bottom-right corner
    _BAR_WIDTH = 3.0
    _HALO_EXTRA = 3.0  # halo is this much wider than the bar
    _TICK_HALF = 6.0
    _LABEL_GAP = 4.0

    def __init__(self, view_box: pg.ViewBox) -> None:
        super().__init__()
        self._vb = view_box
        self.setParentItem(view_box)
        self.setZValue(1000)
        self._color = QColor(255, 255, 255)
        self._bar_px = 0.0  # length on screen
        self._label = ""
        self._font = QFont()
        self._font.setPointSizeF(9.5)
        self._font.setBold(True)
        self.setVisible(False)

    def color(self) -> QColor:
        return QColor(self._color)

    def set_color(self, color: QColor) -> None:
        self._color = QColor(color)
        self.update()

    def update_view(self, visible: bool, um_per_px: float | None) -> None:
        """Re-layout for the current view. *um_per_px* None = pixels."""
        x_range, y_range = self._vb.viewRange()
        visible_width = abs(float(x_range[1]) - float(x_range[0]))
        screen_width = float(self._vb.width())
        if not visible or visible_width <= 0.0 or screen_width <= 0.0:
            self.setVisible(False)
            return
        length_image_px, label = choose_bar(visible_width, um_per_px)
        self.prepareGeometryChange()
        self._bar_px = length_image_px * screen_width / visible_width
        self._label = label
        self.setVisible(True)
        self.update()

    # -- painting ---------------------------------------------------------

    def _layout(self) -> tuple[QPainterPath, QRectF]:
        """The "H" path and the label chip, in the view box's pixel coordinates."""
        rect = self._vb.boundingRect()
        right = rect.right() - self._MARGIN
        bottom = rect.bottom() - self._MARGIN
        left = right - self._bar_px
        h = QPainterPath()
        h.moveTo(left, bottom)
        h.lineTo(right, bottom)
        h.moveTo(left, bottom - self._TICK_HALF)
        h.lineTo(left, bottom + self._TICK_HALF)
        h.moveTo(right, bottom - self._TICK_HALF)
        h.lineTo(right, bottom + self._TICK_HALF)
        metrics = QFontMetricsF(self._font)
        text_w = metrics.horizontalAdvance(self._label)
        chip_w, chip_h = text_w + 10.0, metrics.height() + 2.0
        chip = QRectF(
            (left + right) / 2.0 - chip_w / 2.0,
            bottom - self._TICK_HALF - self._LABEL_GAP - chip_h,
            chip_w,
            chip_h,
        )
        return h, chip

    def boundingRect(self) -> QRectF:  # noqa: N802 - Qt naming
        rect = self._vb.boundingRect()
        return QRectF(rect)

    def paint(self, painter: QPainter, *_args: object) -> None:
        if not self._label or self._bar_px <= 0.0:
            return
        path, chip = self._layout()
        halo = contrast_color(self._color)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # One path, two strokes: the wider halo first, then the colour on top.
        # Flat caps + miter joins keep the bar and ticks one clean shape.
        for width, color in ((self._BAR_WIDTH + 2 * self._HALO_EXTRA, halo), (self._BAR_WIDTH, self._color)):
            pen = QPen(color, width)
            pen.setCapStyle(Qt.PenCapStyle.SquareCap)
            pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
            painter.setPen(pen)
            painter.drawPath(path)
        chip_fill = QColor(halo)
        chip_fill.setAlpha(150)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(chip_fill)
        painter.drawRoundedRect(chip, 4.0, 4.0)
        painter.setPen(QPen(self._color))
        painter.setFont(self._font)
        painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, self._label)
