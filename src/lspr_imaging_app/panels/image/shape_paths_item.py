"""``ShapePathsItem`` and the mask outlines it draws (phase 2.4 of ``docs/roi_shapes_and_linking_plan_2026-11-18.md``).

A circle or a rectangle has the same number of vertices as every other one, so the overlay keeps them as two arrays in `SampleCirclesItem`. A **mask**
ROI is a set of pixels: its outline has as many corners as its boundary has, and it can have holes and several pieces. This item holds one
`QPainterPath` per ROI, each with its own colour, and paints them like the circles are painted: a translucent fill (even-odd, so a hole stays empty)
under a cosmetic outline of constant screen width. There are few mask ROIs next to circles, so one path each is cheap.

**The outline is the pixels as they are shown.** The image's pixel ``(row, col)`` covers ``[col, col + 1) x [row, row + 1)`` in the view, so a mask's
outline is traced along those cell edges (`image_tools`' selection outline tracer, `region_outline`). A mask made from a selection is therefore drawn exactly
where the selection was. Under a chromatic affine the traced outline is carried the way the measurement carries the mask: through the frame's affine, whatever
the ROI's centre is (`placed`), so the drawn outline follows the measured pixels. Circles and rectangles reach the same result by another route: they are drawn
around their stored centre plus the same half cell (`overlay_geometry.CELL_OFFSET`, since 2026-11-18), so all three shapes outline the pixels they measure.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Sequence

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QTransform

from ...domain.roi_shapes import Border, RoiMask
from ...roi.overlay_geometry import CELL_OFFSET
from ...roi.rasterize import mask_border_pixels
from .selection_outlines import region_outline



class MaskOutlines:
    """Traced outlines of masks and of their borders, remembered by content (a mask that is not edited is traced once)."""

    LIMIT = 512

    def __init__(self) -> None:
        self._sample: OrderedDict[tuple, QPainterPath] = OrderedDict()
        self._border: OrderedDict[tuple, QPainterPath] = OrderedDict()
        # The same outlines as traced at the mask's own corner (0, 0), remembered by pixels only: a pixel map that is dragged keeps its pixels and only changes
        # place, so a new position costs a translated copy of this path, not a new trace (`placed_at`).
        self._traced: OrderedDict[tuple, QPainterPath] = OrderedDict()

    @staticmethod
    def _content(mask: RoiMask) -> tuple:
        """What the pixels are, without where they sit."""
        return (mask.mask.shape, np.ascontiguousarray(mask.mask, dtype=bool).tobytes())

    @classmethod
    def _key(cls, mask: RoiMask) -> tuple:
        return (int(mask.x0), int(mask.y0), *cls._content(mask))

    def _remember(self, store: OrderedDict, key: tuple, make) -> QPainterPath:
        path = store.get(key)
        if path is None:
            path = store[key] = make()
            while len(store) > self.LIMIT:
                store.popitem(last=False)
        else:
            store.move_to_end(key)
        return path

    def sample(self, mask: RoiMask) -> QPainterPath:
        """The outline of the mask's pixels, in view cells of the unwarped image (a copy is never needed: callers map it, which returns a new path)."""
        def make() -> QPainterPath:
            traced = self._remember(self._traced, ("sample", *self._content(mask)), lambda: region_outline(mask.mask))
            return traced.translated(float(mask.x0), float(mask.y0))

        return self._remember(self._sample, self._key(mask), make)

    def border(self, mask: RoiMask, border: Border) -> QPainterPath:
        """The outline of the mask's border band (`rasterize.mask_border_pixels`), in view cells of the unwarped image."""
        key = (*self._key(mask), float(border.gap_px), float(border.thickness_px), border.enabled)

        def trace() -> QPainterPath:
            x0, y0, band = mask_border_pixels(RoiMask(0, 0, mask.mask), border)  # the band does not depend on where the mask sits: found at the corner (0, 0)
            return region_outline(band).translated(float(x0), float(y0)) if band.any() else QPainterPath()

        def make() -> QPainterPath:
            traced = self._remember(self._traced, ("border", float(border.gap_px), float(border.thickness_px), border.enabled, *self._content(mask)), trace)
            return traced.translated(float(mask.x0), float(mask.y0))

        return self._remember(self._border, key, make)


def placed(path: QPainterPath, affine: np.ndarray) -> QPainterPath:
    """``path`` (view cells of the unwarped image) as it is drawn in the frame on screen: the mask's pixels go through the frame's affine, as the scan
    warps them (the pixel index is the view coordinate minus half a cell, so a view point ``v`` is drawn at ``A (v - 0.5) + 0.5``). **Independent of the
    ROI's centre**, which is only the point the ROI is labelled by: the drawn outline is always where the measured pixels are. The identity affine
    returns the path as it is."""
    matrix = np.asarray(affine, dtype=np.float64)
    a, b, c, d = (float(v) for v in matrix[:2, :2].ravel())
    tx, ty = (float(matrix[0, 2]), float(matrix[1, 2])) if matrix.shape[1] > 2 else (0.0, 0.0)
    half = CELL_OFFSET
    return QTransform(a, c, b, d, tx + half - (a * half + b * half), ty + half - (c * half + d * half)).map(path)


class ShapePathsItem(pg.GraphicsObject):
    """One path per ROI, one colour per path; see the module docstring. ``filled=False`` draws outlines only (the selection highlight)."""

    def __init__(self, width: float, *, filled: bool = True) -> None:
        super().__init__()
        self._width = float(width)
        self._filled = filled
        self._paths: list[QPainterPath] = []
        self._colors: list[str] = []
        self._rect = QRectF()
        self._outline_alpha = 1.0
        self._fill_alpha = 1.0
        self._pens: dict[str, QPen] = {}
        self._brushes: dict[str, QBrush] = {}
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)  # clicks go through to the ROI gestures underneath

    def set_paths(self, paths: Sequence[QPainterPath], colors: Sequence[str]) -> None:
        self.prepareGeometryChange()
        self._paths, self._colors = list(paths), list(colors)
        rect = QRectF()
        for path in self._paths:
            rect = rect.united(path.boundingRect())
        self._rect = rect
        self.update()

    def clear(self) -> None:
        self.set_paths([], [])

    def set_width(self, width: float) -> None:
        if float(width) != self._width:
            self._width = float(width)
            self._pens.clear()
            self.update()

    def set_style(self, outline_alpha: float, fill_alpha: float) -> None:
        self._outline_alpha = max(0.0, min(1.0, float(outline_alpha)))
        self._fill_alpha = max(0.0, min(1.0, float(fill_alpha)))
        self._pens.clear()
        self._brushes.clear()
        self.update()

    def path_count(self) -> int:
        return len(self._paths)

    def paths(self) -> list[QPainterPath]:
        return list(self._paths)

    def colors(self) -> list[str]:
        return list(self._colors)

    def _pen(self, color: str) -> QPen:
        pen = self._pens.get(color)
        if pen is None:
            qcolor = QColor(color)
            qcolor.setAlphaF(self._outline_alpha)
            pen = self._pens[color] = QPen(qcolor, self._width)
            pen.setCosmetic(True)  # a constant width in screen pixels at any zoom
        return pen

    def _brush(self, color: str) -> QBrush:
        brush = self._brushes.get(color)
        if brush is None:
            qcolor = QColor(color)
            qcolor.setAlphaF(self._fill_alpha)
            brush = self._brushes[color] = QBrush(qcolor)
        return brush

    def boundingRect(self) -> QRectF:
        if self._rect.isNull():
            return QRectF()
        pad_x, pad_y = 3.0 * self.pixelWidth(), 3.0 * self.pixelHeight()  # a cosmetic stroke is not part of the data rectangle
        return self._rect.adjusted(-pad_x, -pad_y, pad_x, pad_y)

    def viewTransformChanged(self) -> None:
        self.prepareGeometryChange()  # the pixel padding depends on the zoom
        super().viewTransformChanged()

    def paint(self, painter: QPainter, *_args) -> None:
        if not self._paths:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, bool(pg.getConfigOption("antialias")))
        if self._filled and self._fill_alpha > 0.0:
            painter.setPen(Qt.PenStyle.NoPen)
            for path, color in zip(self._paths, self._colors, strict=True):
                painter.setBrush(self._brush(color))
                painter.drawPath(path)
        if self._outline_alpha > 0.0:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for path, color in zip(self._paths, self._colors, strict=True):
                painter.setPen(self._pen(color))
                painter.drawPath(path)
