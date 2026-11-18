"""The handles of the selected rectangle ROIs (phase 3 of ``docs/roi_shapes_and_linking_plan_2026-11-18.md``).

Small white squares on the corners and the middles of the edges, and a round **knob** on a short stem beyond the top edge: what `roi/shape_hits.py`'s
`rectangle_handle_zone` answers to (drag an edge or corner to change the width / height, drag the knob to turn the rectangle; `roi_gestures.py`).
The positions come from the same function the hit test uses (`rectangle_handle_points`), so what is drawn is what can be grabbed. The marks keep a constant
size on screen at any zoom, and the knob a constant distance (`roi_gestures.KNOB_PX`).
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QPainter, QPen

from ...domain.roi_shapes import Rectangle
from ...roi.shape_hits import rectangle_handle_points
from .roi_gestures import KNOB_PX

HANDLE_PX = 7.0
"""Side of a handle's square, in screen pixels."""
_FILL = "#f8fafc"
_EDGE = "#0f172a"
_SQUARES = ("ne", "nw", "se", "sw", "n", "s", "e", "w")


class RectangleHandlesItem(pg.GraphicsObject):
    def __init__(self) -> None:
        super().__init__()
        self._rects: list[tuple[Rectangle, tuple[float, float]]] = []
        self._linear = np.eye(2)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)  # the gestures underneath do the grabbing

    def set_rectangles(self, rectangles: Sequence[tuple[Rectangle, tuple[float, float]]], linear: np.ndarray) -> None:
        """``(shape, display centre)`` of every selected rectangle, and the frame's 2 x 2 linear part."""
        self.prepareGeometryChange()
        self._rects = [(shape, (float(centre[0]), float(centre[1]))) for shape, centre in rectangles]
        self._linear = np.asarray(linear, dtype=np.float64)[:2, :2]
        self.update()

    def clear(self) -> None:
        self.set_rectangles([], np.eye(2))

    def count(self) -> int:
        return len(self._rects)

    def handle_points(self, knob_display: float = 0.0) -> list[dict[str, tuple[float, float]]]:
        """The handle positions of every rectangle (tests; the knob is ``knob_display`` display units out)."""
        return [rectangle_handle_points(shape, centre, self._linear, knob_display) for shape, centre in self._rects]

    def boundingRect(self) -> QRectF:
        if not self._rects:
            return QRectF()
        pad = (KNOB_PX + 2.0 * HANDLE_PX) * max(self.pixelWidth(), self.pixelHeight())
        points = [p for found in self.handle_points(KNOB_PX * self.pixelWidth()) for p in found.values()]
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        return QRectF(min(xs) - pad, min(ys) - pad, max(xs) - min(xs) + 2.0 * pad, max(ys) - min(ys) + 2.0 * pad)

    def viewTransformChanged(self) -> None:
        self.prepareGeometryChange()  # the marks' size in data units depends on the zoom
        super().viewTransformChanged()

    def paint(self, painter: QPainter, *_args) -> None:
        if not self._rects:
            return
        pixel_x, pixel_y = self.pixelWidth(), self.pixelHeight()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(_EDGE), 1.0)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(QBrush(QColor(_FILL)))
        half_x, half_y = HANDLE_PX / 2.0 * pixel_x, HANDLE_PX / 2.0 * pixel_y
        for shape, centre in self._rects:
            points = rectangle_handle_points(shape, centre, self._linear, KNOB_PX * pixel_x)
            painter.drawLine(QPointF(*points["n"]), QPointF(*points["knob"]))
            for name in _SQUARES:
                x, y = points[name]
                painter.drawRect(QRectF(x - half_x, y - half_y, 2.0 * half_x, 2.0 * half_y))
            kx, ky = points["knob"]
            painter.drawEllipse(QPointF(kx, ky), half_x * 1.2, half_y * 1.2)
