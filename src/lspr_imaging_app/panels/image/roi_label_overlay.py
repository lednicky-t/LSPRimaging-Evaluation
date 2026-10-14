"""ROI labels on the image: each ROI's number (and name, if it has one) beside
its circle.

A child of the plot's `ViewBox`, like the scale bar (`scale_bar_overlay.py`):
it paints in screen pixels, so the text stays one size while you zoom and only
its position follows the ROI. Painted by one item for all ROIs, not one text
item per ROI, so a few hundred labels cost one paint. Only the centre of each
circle is mapped through the view; nothing here changes any ROI.

**Where a label sits** is one of nine places around its circle (`label_placement.py`, picked with the 3 x 3
grid in the ROIs tab's Display menu): centred on the circle, or just outside it on a side or corner. The
default, "right", is where the labels were always drawn.

**Painting is cached** (2026-10-14): each distinct label text (halo + text, antialiased) is rendered once
into a small transparent bitmap and then only blitted, and every anchor is mapped through the view with one
vectorised transform. The first version drew each label with five `drawText` calls and a `mapFromView`
call (~1100 Qt calls for 160 labels, 10-16 ms of GUI time per repaint, i.e. per Play frame).
"""

from __future__ import annotations

import math

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPixmap

from .label_placement import DEFAULT_ROI_PLACEMENT, PLACEMENTS, placement_origin

_GAP_PX = 4.0  # between the circle's edge and the text
_MARGIN_X_PX = 200.0  # a label whose circle is this far (plus its radius) off the view is skipped; a longer name may be cut
_MARGIN_Y_PX = 40.0
_TEXT_COLOR = QColor(255, 255, 255)
_HALO_COLOR = QColor(0, 0, 0, 210)
_HALO_OFFSETS = ((-1, 0), (1, 0), (0, -1), (0, 1))  # a dark outline keeps the text readable on any background
_BITMAP_PAD = 2.0  # logical px around the text in its bitmap (room for the halo)
_BITMAP_CACHE_LIMIT = 2000


def label_text(roi_id: int, name: str | None) -> str:
    """``"12"``, or ``"12 spot A"`` when the ROI has a name."""
    return f"{roi_id} {name}" if name else str(roi_id)


class RoiLabelItem(pg.GraphicsObject):
    def __init__(self, view_box: pg.ViewBox) -> None:
        super().__init__()
        self._vb = view_box
        self.setParentItem(view_box)
        self.setZValue(900)
        self._entries: list[tuple[float, float, float, str]] = []
        self._font = QFont()
        self._font.setPointSizeF(9.0)
        self._font.setBold(True)
        self._bitmaps: dict[tuple[str, float], QPixmap] = {}  # (text, device pixel ratio) -> halo + text, transparent
        self._advances: dict[str, float] = {}  # text -> its width in logical px (no halo padding)
        self._placement = DEFAULT_ROI_PLACEMENT
        self.setVisible(False)

    def placement(self) -> str:
        return self._placement

    def set_placement(self, placement: str) -> None:
        """Where the labels sit around their circles: one of `label_placement.PLACEMENTS`."""
        if placement not in PLACEMENTS:
            raise ValueError(f"placement must be one of {PLACEMENTS}, got {placement!r}")
        if placement != self._placement:
            self._placement = placement
            self.update()

    def _advance(self, text: str) -> float:
        advance = self._advances.get(text)
        if advance is None:
            if len(self._advances) >= _BITMAP_CACHE_LIMIT:
                self._advances.clear()
            advance = self._advances[text] = QFontMetricsF(self._font).horizontalAdvance(text)
        return advance

    def _bitmap(self, text: str, dpr: float) -> QPixmap:
        key = (text, dpr)
        bitmap = self._bitmaps.get(key)
        if bitmap is None:
            if len(self._bitmaps) >= _BITMAP_CACHE_LIMIT:
                self._bitmaps.clear()
            metrics = QFontMetricsF(self._font)
            width = metrics.horizontalAdvance(text) + 2.0 * _BITMAP_PAD
            height = metrics.ascent() + metrics.descent() + 2.0 * _BITMAP_PAD
            bitmap = QPixmap(math.ceil(width * dpr), math.ceil(height * dpr))
            bitmap.setDevicePixelRatio(dpr)
            bitmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(bitmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            painter.setFont(self._font)
            baseline = QPointF(_BITMAP_PAD, _BITMAP_PAD + metrics.ascent())
            painter.setPen(_HALO_COLOR)
            for dx, dy in _HALO_OFFSETS:
                painter.drawText(QPointF(baseline.x() + dx, baseline.y() + dy), text)
            painter.setPen(_TEXT_COLOR)
            painter.drawText(baseline, text)
            painter.end()
            self._bitmaps[key] = bitmap
        return bitmap

    def set_labels(self, entries: list[tuple[float, float, float, str]]) -> None:
        """``(x, y, radius, text)`` per ROI, in image (data) coordinates;
        empty hides the item."""
        self._entries = list(entries)
        self.setVisible(bool(self._entries))
        self.update()

    def labels(self) -> list[tuple[float, float, float, str]]:
        return list(self._entries)

    def boundingRect(self) -> QRectF:  # noqa: N802 - Qt naming
        return QRectF(self._vb.boundingRect())

    def paint(self, painter: QPainter, *_args: object) -> None:
        if not self._entries:
            return
        view = self._vb.boundingRect()
        # All circle centres (and their radii, in screen px) through the view transform in one go.
        transform = self._vb.childTransform()
        data = np.asarray([(x, y) for x, y, _radius, _text in self._entries], dtype=np.float64)
        screen_x = transform.m11() * data[:, 0] + transform.m21() * data[:, 1] + transform.dx()
        screen_y = transform.m12() * data[:, 0] + transform.m22() * data[:, 1] + transform.dy()
        pixels_per_unit = math.hypot(transform.m11(), transform.m12())
        radius_px = np.asarray([radius for _x, _y, radius, _text in self._entries], dtype=np.float64) * pixels_per_unit
        # A label may reach in from beyond the edge (its text can be long), so the cull margin is generous.
        inside = (
            (screen_x >= view.left() - _MARGIN_X_PX - radius_px) & (screen_x <= view.right() + _MARGIN_X_PX + radius_px)
            & (screen_y >= view.top() - _MARGIN_Y_PX - radius_px) & (screen_y <= view.bottom() + _MARGIN_Y_PX + radius_px)
        )
        dpr = painter.device().devicePixelRatioF()
        ascent = QFontMetricsF(self._font).ascent()
        placement = self._placement
        for index in np.flatnonzero(inside):
            text = self._entries[int(index)][3]
            # The text box is `advance` wide and `ascent` tall (digits and capitals; descenders hang into the gap).
            left, top = placement_origin(
                float(screen_x[index]), float(screen_y[index]), float(radius_px[index]), placement, self._advance(text), ascent, _GAP_PX
            )
            # The baseline sits 1 px above the box's bottom (what keeps digits optically centred beside the circle);
            # the bitmap's own baseline is (pad, pad + ascent) from its top left.
            painter.drawPixmap(QPointF(left - _BITMAP_PAD, top - 1.0 - _BITMAP_PAD), self._bitmap(text, dpr))
