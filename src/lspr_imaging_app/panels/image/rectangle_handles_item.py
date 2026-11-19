"""The handles of the selected rectangle ROIs (phase 3 of ``docs/roi_shapes_and_linking_plan_2026-11-18.md``).

Small white squares on the corners and the middles of the edges, and a round **knob** on a short stem beyond the top edge: what `roi/shape_hits.py`'s
`rectangle_handle_zone` answers to (drag an edge or corner to change the width / height, drag the knob to turn the rectangle; `roi_gestures.py`).
The positions come from the same numbers the hit test uses (`rectangle_handle_arrays`), so what is drawn is what can be grabbed. The marks keep a constant
size on screen at any zoom, and the knob a constant distance (`roi_gestures.KNOB_PX`).

**Speed (2026-11-18, maintainer: "rectangles redraw slowly, especially while resizing or rotating").** A resize or a turn applies to every selected rectangle, so
the usual case is many handle sets at once. Two things made it slow, measured with 150 and 500 selected rectangles (the same count of selected circles painted
in 10 and 34 ms):

1. The first version worked out the positions of every handle in Python inside ``boundingRect()`` (which Qt calls several times per paint) and again in
   ``paint()``. Now all handles are one numpy calculation made when the rectangles or the zoom change and kept until then (``_make``).
2. Painting 4,000 small antialiased squares as one painter path costs 43 ms and one ``drawRect`` per square 7.5 ms, but ``drawPoints`` with a square-capped
   *cosmetic* pen (a point is then a square of the pen's width in screen pixels, whatever the zoom) costs 2.7 ms: so a handle is a dark point with a smaller light
   point on top (the 1 px outline), the knob a round-capped one, and the stems one path. All points of a kind are one painter call.

Result, repaint of the view with every ROI selected (offscreen, 1200 x 1000 frame, runs of the old and the new item back to back): 150 rectangles 53 ms -> 12 ms,
500 rectangles 170 ms -> 37 ms, where the same number of selected circles takes 11 and 36 ms. Repeated on a busier machine the rectangles took 21-22 ms against 19 ms for
circles at 150, and 55-56 ms against 56-57 ms at 500: rectangles now paint like circles. (The first attempt, one antialiased path for all squares, was *slower* to paint
than the old per-square calls; the measurement is what chose ``drawPoints``.) The record is in the plan document, "Rectangle speed, border drag, rotate cursor".
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF

from ...domain.roi_shapes import Rectangle
from ...roi.shape_hits import HANDLE_NAMES, rectangle_handle_arrays
from .roi_gestures import KNOB_PX

HANDLE_PX = 7.0
"""Side of a handle's square, in screen pixels (outline included: 1 px of dark edge round 6 px of light)."""
KNOB_DIAMETER_PX = 8.4
"""Diameter of the rotation knob, in screen pixels (outline included)."""
_FILL = "#f8fafc"
_EDGE = "#0f172a"
_N_EDGE, _KNOB = HANDLE_NAMES.index("n"), HANDLE_NAMES.index("knob")


def _polygon(points: np.ndarray) -> QPolygonF:
    """The ``(n, 2)`` points as one `QPolygonF` (filled by array, not point by point)."""
    polygon = pg.functions.create_qpolygonf(len(points))
    if len(points):
        vertices = pg.functions.ndarray_from_qpolygonf(polygon)
        vertices[:, 0] = points[:, 0]
        vertices[:, 1] = points[:, 1]
    return polygon


def _mark_pen(color: str, width: float, cap: Qt.PenCapStyle) -> QPen:
    pen = QPen(QColor(color), width)
    pen.setCapStyle(cap)
    pen.setCosmetic(True)  # the width is in screen pixels at any zoom, so a point is a mark of constant size
    return pen


class RectangleHandlesItem(pg.GraphicsObject):
    def __init__(self) -> None:
        super().__init__()
        self._widths = np.empty(0)
        self._heights = np.empty(0)
        self._angles = np.empty(0)
        self._centres = np.empty((0, 2))
        self._linear = np.eye(2)
        # what is worked out for the current rectangles at one zoom, and the zoom (pixel size) it was worked out for
        self._made_for: tuple[float, float] | None = None
        self._stems = QPainterPath()
        self._squares = QPolygonF()
        self._knobs = QPolygonF()
        self._rect = QRectF()
        self._stem_pen = QPen(QColor(_EDGE), 1.0)
        self._stem_pen.setCosmetic(True)
        self._square_edge = _mark_pen(_EDGE, HANDLE_PX + 1.0, Qt.PenCapStyle.SquareCap)  # a dark square with a smaller light one on top of it
        self._square_fill = _mark_pen(_FILL, HANDLE_PX - 1.0, Qt.PenCapStyle.SquareCap)
        self._knob_edge = _mark_pen(_EDGE, KNOB_DIAMETER_PX + 1.0, Qt.PenCapStyle.RoundCap)
        self._knob_fill = _mark_pen(_FILL, KNOB_DIAMETER_PX - 1.0, Qt.PenCapStyle.RoundCap)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)  # the gestures underneath do the grabbing

    # -- data ------------------------------------------------------------------------

    def set_rectangles(self, rectangles: Sequence[tuple[Rectangle, tuple[float, float]]], linear: np.ndarray) -> None:
        """``(shape, display centre)`` of every selected rectangle, and the frame's 2 x 2 linear part."""
        self.prepareGeometryChange()
        self._widths = np.array([shape.width_px for shape, _centre in rectangles], dtype=np.float64)
        self._heights = np.array([shape.height_px for shape, _centre in rectangles], dtype=np.float64)
        self._angles = np.array([shape.angle_deg for shape, _centre in rectangles], dtype=np.float64)
        self._centres = np.array([(float(centre[0]), float(centre[1])) for _shape, centre in rectangles], dtype=np.float64).reshape(-1, 2)
        self._linear = np.asarray(linear, dtype=np.float64)[:2, :2]
        self._made_for = None  # worked out again at the next boundingRect() / paint()
        self.update()

    def clear(self) -> None:
        self.set_rectangles([], np.eye(2))

    def count(self) -> int:
        return int(self._widths.size)

    def handle_points(self, knob_display: float = 0.0) -> list[dict[str, tuple[float, float]]]:
        """The handle positions of every rectangle (tests; the knob is ``knob_display`` display units out)."""
        points = rectangle_handle_arrays(self._widths, self._heights, self._angles, self._centres, self._linear, knob_display)
        return [{name: (float(x), float(y)) for name, (x, y) in zip(HANDLE_NAMES, found, strict=True)} for found in points]

    # -- what is drawn, kept until the rectangles or the zoom change ----------------------------

    def _make(self) -> None:
        """Work out the handles for the current rectangles at the current zoom: the points to paint and the rectangle they lie in."""
        pixel_x, pixel_y = float(self.pixelWidth()), float(self.pixelHeight())
        if self._made_for == (pixel_x, pixel_y):
            return
        self._made_for = (pixel_x, pixel_y)
        count = self.count()
        if count == 0:
            self._stems, self._squares, self._knobs, self._rect = QPainterPath(), QPolygonF(), QPolygonF(), QRectF()
            return
        points = rectangle_handle_arrays(self._widths, self._heights, self._angles, self._centres, self._linear, KNOB_PX * pixel_x)  # (N, 9, 2)
        stem_x = np.column_stack((points[:, _N_EDGE, 0], points[:, _KNOB, 0], np.full(count, np.nan))).ravel()  # one two-point run per rectangle
        stem_y = np.column_stack((points[:, _N_EDGE, 1], points[:, _KNOB, 1], np.full(count, np.nan))).ravel()
        self._stems = pg.functions.arrayToQPath(stem_x, stem_y, connect="finite")
        self._squares = _polygon(points[:, :_KNOB, :].reshape(-1, 2))  # the corners and the edge middles
        self._knobs = _polygon(points[:, _KNOB, :])
        # the rectangle the marks lie in, with room for their size (a cosmetic mark is not part of the data)
        pad_x, pad_y = (HANDLE_PX + 2.0) * pixel_x, (HANDLE_PX + 2.0) * pixel_y
        low = points.reshape(-1, 2).min(axis=0)
        high = points.reshape(-1, 2).max(axis=0)
        self._rect = QRectF(float(low[0]) - pad_x, float(low[1]) - pad_y, float(high[0] - low[0]) + 2.0 * pad_x, float(high[1] - low[1]) + 2.0 * pad_y)

    def boundingRect(self) -> QRectF:
        if self.count() == 0:
            return QRectF()
        self._make()
        return self._rect

    def viewTransformChanged(self) -> None:
        self.prepareGeometryChange()  # the knob's distance and the marks' room in data units depend on the zoom
        super().viewTransformChanged()

    def paint(self, painter: QPainter, *_args) -> None:
        if self.count() == 0:
            return
        self._make()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(self._stem_pen)
        painter.drawPath(self._stems)  # first, so the marks cover the ends of the stems
        for pen, points in (
            (self._square_edge, self._squares), (self._square_fill, self._squares), (self._knob_edge, self._knobs), (self._knob_fill, self._knobs),
        ):
            painter.setPen(pen)
            painter.drawPoints(points)
