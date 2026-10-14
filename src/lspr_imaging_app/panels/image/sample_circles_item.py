"""``SampleCirclesItem`` - every ROI's sample circle as ONE painted item (2026-10-14).

Why: the overlay used to keep one `PlotDataItem` (outline) and one `QGraphicsPathItem`
(fill) *per distinct ROI colour*. With ROIs that each have their own colour (a group
tints its members) that is hundreds of graphics items, and every wavelength step during
Play updated all of them: 60-80 ms of GUI thread per frame at 160 colours (measured on
the maintainer's data, 2026-10-14; `PlotDataItem.setData` ~0.4 ms each, a fill path
~0.1 ms each). Their data is the same circles every time, so this item stores the
circle vertices as two arrays and a colour per circle, and paints them itself:

- **update** = keep two arrays and one big `QPolygonF` (~0.1 ms for 160 ROIs, any number of colours);
- **paint** = two passes (all fills, then all outlines) over `QPolygonF.mid` slices of that
  polygon, one pen / brush switch per colour run. Outlines stay above fills, as before.

Look is unchanged: 1.5 px cosmetic outline in the ROI's colour at the style's transparency,
fill at that transparency times the Options-menu maximum fill opacity. (Qt strokes a
1.5 px pen ~5x slower than a 1.0 px one: 8 ms vs 1.6 ms for 160 circles; `SAMPLE_WIDTH` in
`roi_overlay.py` is the one number to change if that is ever worth a thinner line.)

Queries (`circle_count`, `colors`, `outline_xy`, `outline_color`, `fill_color`) exist for
tests and diagnostics; nothing in the app reads them.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF


class SampleCirclesItem(pg.GraphicsObject):
    def __init__(self, width: float) -> None:
        super().__init__()
        self._width = float(width)
        self._points = 0  # vertices per circle
        self._xs = np.empty((0, 0))
        self._ys = np.empty((0, 0))
        self._colors: list[str] = []
        self._groups: dict[str, list[int]] = {}
        self._polygon: QPolygonF | None = None
        self._rect = QRectF()
        self._outline_alpha = 1.0
        self._fill_alpha = 1.0  # already multiplied by the maximum fill opacity
        self._pens: dict[str, QPen] = {}
        self._brushes: dict[str, QBrush] = {}
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)  # clicks go through to the ROI gestures underneath

    # -- data ------------------------------------------------------------------------

    def set_circles(self, xs: np.ndarray, ys: np.ndarray, colors: Sequence[str]) -> None:
        """`xs` / `ys`: (N, P) vertices of N circles (each closed: last point = first); `colors`: N hex strings."""
        count, points = xs.shape if xs.ndim == 2 else (0, 0)
        self.prepareGeometryChange()
        self._xs, self._ys, self._points = xs, ys, points
        self._colors = list(colors)
        groups: dict[str, list[int]] = {}
        for index, color in enumerate(self._colors):
            groups.setdefault(color, []).append(index)
        self._groups = groups
        if count:
            polygon = pg.functions.create_qpolygonf(count * points)
            vertices = pg.functions.ndarray_from_qpolygonf(polygon)
            vertices[:, 0] = xs.ravel()
            vertices[:, 1] = ys.ravel()
            self._polygon = polygon
            self._rect = QRectF(float(xs.min()), float(ys.min()), float(np.ptp(xs)), float(np.ptp(ys)))
        else:
            self._polygon = None
            self._rect = QRectF()
        self.update()

    def clear(self) -> None:
        self.set_circles(np.empty((0, 0)), np.empty((0, 0)), [])

    def set_width(self, width: float) -> None:
        """Outline width in screen pixels (Qt strokes 1.0 px ~5x faster than 1.5 px)."""
        if float(width) != self._width:
            self._width = float(width)
            self._pens.clear()
            self.update()

    @property
    def width(self) -> float:
        return self._width

    def set_style(self, outline_alpha: float, fill_alpha: float) -> None:
        """Transparency of the outlines, and of the fills (the caller multiplies in the maximum fill opacity)."""
        self._outline_alpha = max(0.0, min(1.0, float(outline_alpha)))
        self._fill_alpha = max(0.0, min(1.0, float(fill_alpha)))
        self._pens.clear()
        self._brushes.clear()
        self.update()

    # -- queries (tests, diagnostics) -----------------------------------------------------

    def colors(self) -> list[str]:
        """The distinct colours in use, in order of first appearance."""
        return list(self._groups)

    def circle_count(self, color: str | None = None) -> int:
        return len(self._colors) if color is None else len(self._groups.get(color, ()))

    def outline_xy(self, color: str | None = None) -> tuple[np.ndarray, np.ndarray]:
        """The circles of one colour (or all) as two flat arrays, NaN between circles (the old curve format)."""
        indices = list(range(len(self._colors))) if color is None else self._groups.get(color, [])
        if not indices:
            return np.empty(0), np.empty(0)
        gap = np.full((len(indices), 1), np.nan)
        flat_x = np.hstack((self._xs[indices], gap)).ravel()[:-1]
        flat_y = np.hstack((self._ys[indices], gap)).ravel()[:-1]
        return flat_x, flat_y

    def outline_color(self, color: str) -> QColor:
        return self._pen(color).color()

    def fill_color(self, color: str) -> QColor:
        return self._brush(color).color()

    # -- painting ---------------------------------------------------------------------------

    def _pen(self, color: str) -> QPen:
        pen = self._pens.get(color)
        if pen is None:
            qcolor = QColor(color)
            qcolor.setAlphaF(self._outline_alpha)
            pen = QPen(qcolor, self._width)
            pen.setCosmetic(True)  # a constant width in screen pixels at any zoom
            self._pens[color] = pen
        return pen

    def _brush(self, color: str) -> QBrush:
        brush = self._brushes.get(color)
        if brush is None:
            qcolor = QColor(color)
            qcolor.setAlphaF(self._fill_alpha)
            brush = self._brushes[color] = QBrush(qcolor)
        return brush

    def boundingRect(self) -> QRectF:  # noqa: N802 - Qt naming
        if self._rect.isNull() and self._polygon is None:
            return QRectF()
        # A cosmetic stroke is not part of the data rectangle: pad by a few screen pixels.
        pad_x, pad_y = 3.0 * self.pixelWidth(), 3.0 * self.pixelHeight()
        return self._rect.adjusted(-pad_x, -pad_y, pad_x, pad_y)

    def viewTransformChanged(self) -> None:  # noqa: N802 - pyqtgraph naming
        self.prepareGeometryChange()  # the pixel padding depends on the zoom
        super().viewTransformChanged()

    def paint(self, painter: QPainter, *_args) -> None:
        polygon = self._polygon
        if polygon is None:
            return
        points = self._points
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, bool(pg.getConfigOption("antialias")))
        if self._fill_alpha > 0.0:
            painter.setPen(Qt.PenStyle.NoPen)
            for color, indices in self._groups.items():
                painter.setBrush(self._brush(color))
                for index in indices:
                    painter.drawPolygon(polygon.mid(index * points, points))
        if self._outline_alpha > 0.0:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for color, indices in self._groups.items():
                painter.setPen(self._pen(color))
                for index in indices:
                    painter.drawPolyline(polygon.mid(index * points, points))
