"""ROI labels on the image: each ROI's number (and name, if it has one) beside
its circle.

A child of the plot's `ViewBox`, like the scale bar (`scale_bar_overlay.py`):
it paints in screen pixels, so the text stays one size while you zoom and only
its position follows the ROI. Painted by one item for all ROIs, not one text
item per ROI, so a few hundred labels cost one paint. Only the anchor of each
label (the right edge of its circle) is mapped through the view; nothing here
changes any ROI.
"""

from __future__ import annotations

import pyqtgraph as pg
from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QPainter

_GAP_PX = 4.0  # between the circle's edge and the text
_TEXT_COLOR = QColor(255, 255, 255)
_HALO_COLOR = QColor(0, 0, 0, 210)
_HALO_OFFSETS = ((-1, 0), (1, 0), (0, -1), (0, 1))  # a dark outline keeps the text readable on any background


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
        self.setVisible(False)

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
        visible = view.adjusted(-80.0, -20.0, 10.0, 20.0)  # a label just off the left edge may still reach in
        metrics = QFontMetricsF(self._font)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setFont(self._font)
        for x, y, radius, text in self._entries:
            anchor = self._vb.mapFromView(QPointF(x + radius, y))
            if not visible.contains(anchor):
                continue
            origin = QPointF(anchor.x() + _GAP_PX, anchor.y() + metrics.ascent() / 2.0 - 1.0)  # vertically centred
            painter.setPen(_HALO_COLOR)
            for dx, dy in _HALO_OFFSETS:
                painter.drawText(QPointF(origin.x() + dx, origin.y() + dy), text)
            painter.setPen(_TEXT_COLOR)
            painter.drawText(origin, text)
