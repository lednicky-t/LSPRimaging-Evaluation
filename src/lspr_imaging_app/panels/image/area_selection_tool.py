"""Canvas side of the area selection (2026-10-03): the drag gesture that
draws a rectangle or lasso, and the Photoshop-style "marching ants" outline.

The *state* lives in ``AreaSelectionModule`` (``selection/``); this class
only turns a left-drag into a ``set_rectangle``/``set_polygon`` command and
draws whatever the module currently holds. Panels display and forward
gestures, they own no domain state (LSPRi CLAUDE.md).

**Marching ants**: two ``QGraphicsPathItem``s trace the same outline - a solid
white one underneath and a black dashed one on top - so the line is visible on
any image brightness. Both use *cosmetic* pens (constant screen width however
far you zoom). A ~12 fps ``QTimer`` slides the dash offset one pixel per tick
to make the dashes crawl; it runs only while an outline is visible, so an
idle panel pays nothing. Changing a dash offset repaints just this item.

The outline is always the boundary of the *editable region*: the shape clipped
to the image, or - inverted - the image minus the shape. So it follows the
shape's edge inside the image and the image border only where the region
actually touches it (never along an edge the shape covers, when inverted).

A rectangle/lasso in progress is drawn as the same ants, but is only
committed to the module when the drag finishes. A drag may start and wander
outside the image (maintainer, 2026-10-03: starting exactly on the image
edge is too fiddly); the live preview follows the cursor unclamped, and on
commit the points are clamped to the image, so only the part inside the image
is selected and the outline runs along the image edge.
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QObject, QPointF, Qt, QTimer
from PyQt6.QtGui import QColor, QPainterPath, QPen, QPolygonF
from PyQt6.QtWidgets import QGraphicsPathItem

from ...selection import AreaSelectionMode, AreaSelectionModule
from ...selection.area_selection_module import rasterize_polygon

_ANT_INTERVAL_MS = 80
_DASH = 4.0  # dash/gap length, in pen widths (cosmetic width 1 -> screen pixels)
_Z_VALUE = 60  # above ROI curves and overlay tints
_MIN_LASSO_STEP = 0.5  # view units; drops sub-pixel jitter from the trail


def _ant_pen(color: str, *, dashed: bool) -> QPen:
    pen = QPen(QColor(color))
    pen.setWidthF(1.0)
    pen.setCosmetic(True)
    if dashed:
        pen.setStyle(Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([_DASH, _DASH])
    return pen


class AreaSelectionTool(QObject):
    """Draws the selection outline on *plot* and runs the drag gesture."""

    def __init__(self, plot: pg.PlotItem, selection: AreaSelectionModule, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._plot = plot
        self._selection = selection
        self._image_shape: tuple[int, int] | None = None
        self._gesture_start: tuple[float, float] | None = None
        self._gesture_points: list[tuple[float, float]] = []

        self._base_item = QGraphicsPathItem()
        self._base_item.setPen(_ant_pen("#ffffff", dashed=False))
        self._ants_pen = _ant_pen("#000000", dashed=True)
        self._ants_item = QGraphicsPathItem()
        self._ants_item.setPen(self._ants_pen)
        for item in (self._base_item, self._ants_item):
            item.setZValue(_Z_VALUE)
            item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)  # purely visual, never eats clicks
            item.hide()
            plot.addItem(item, ignoreBounds=True)

        self._dash_offset = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(_ANT_INTERVAL_MS)
        self._timer.timeout.connect(self._advance_ants)

        selection.selection_changed.connect(self.refresh)

    # -- display --------------------------------------------------------------

    def set_image_shape(self, shape: tuple[int, int] | None) -> None:
        """The displayed image's ``(height, width)``; needed to clamp drags
        and to draw an inverted selection's border."""
        self._image_shape = shape
        self.refresh()

    def refresh(self) -> None:
        path = self._preview_path() if self._gesture_start is not None else self._committed_path()
        if path is None:
            self._timer.stop()
            self._base_item.hide()
            self._ants_item.hide()
            return
        self._base_item.setPath(path)
        self._ants_item.setPath(path)
        self._base_item.show()
        self._ants_item.show()
        if not self._timer.isActive():
            self._timer.start()

    def is_animating(self) -> bool:
        return self._timer.isActive()

    def _advance_ants(self) -> None:
        self._dash_offset = (self._dash_offset + 1.0) % (2 * _DASH)
        self._ants_pen.setDashOffset(self._dash_offset)
        self._ants_item.setPen(self._ants_pen)

    @staticmethod
    def _polygon_path(points: np.ndarray | list[tuple[float, float]]) -> QPainterPath:
        path = QPainterPath()
        path.addPolygon(QPolygonF([QPointF(float(x), float(y)) for x, y in points]))
        path.closeSubpath()
        return path

    def _committed_path(self) -> QPainterPath | None:
        vertices = self._selection.vertices()
        if vertices is None:
            return None
        path = self._polygon_path(vertices)
        if self._image_shape is not None:
            # Always work with the part of the shape that is inside the image
            # (the module may hold a shape reaching past it - only the inside
            # is ever editable).
            height, width = self._image_shape
            image_path = QPainterPath()
            image_path.addRect(0.0, 0.0, float(width), float(height))
            # The editable region is the clipped shape, or - inverted - "image
            # minus shape". Its outline follows the shape's edge where that is
            # inside the image, and the image border wherever the region touches it.
            path = image_path.subtracted(path) if self._selection.is_inverted() else image_path.intersected(path)
        if path.isEmpty():
            return None
        # Qt's boolean ops (`intersected`/`subtracted`) return subpaths that
        # are *not* explicitly closed, and a stroke skips an unclosed
        # subpath's closing edge - which is exactly the edge lying on the image
        # border, so it silently vanished. Re-add each subpath closed.
        closed = QPainterPath()
        for polygon in path.toSubpathPolygons():
            closed.addPolygon(polygon)
            closed.closeSubpath()
        return closed

    def _preview_path(self) -> QPainterPath | None:
        if not self._gesture_points:
            return None
        if self._selection.mode() is AreaSelectionMode.RECTANGLE:
            (x0, y0), (x1, y1) = self._gesture_points[0], self._gesture_points[-1]
            return self._polygon_path([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
        if len(self._gesture_points) < 2:
            return None
        return self._polygon_path(self._gesture_points)

    # -- gesture --------------------------------------------------------------

    def begin_gesture(self, x: float, y: float) -> bool:
        """Starts a drag at view point ``(x, y)``, which may be outside the
        image. Declines (``False``) only when there is no image at all."""
        if self._image_shape is None:
            return False
        self._gesture_start = (x, y)
        self._gesture_points = [(x, y)]
        self.refresh()
        return True

    def update_gesture(self, x: float, y: float) -> None:
        if self._gesture_start is None:
            return
        point = (x, y)  # unclamped while previewing; clamped on commit
        if self._selection.mode() is AreaSelectionMode.RECTANGLE:
            self._gesture_points = [self._gesture_start, point]
        else:
            last = self._gesture_points[-1]
            if np.hypot(point[0] - last[0], point[1] - last[1]) >= _MIN_LASSO_STEP:
                self._gesture_points.append(point)
        self.refresh()

    def end_gesture(self) -> None:
        """Commits the drag. A bare click (rectangle with no area, lasso with
        under three points) commits nothing and leaves any previous selection
        as it was."""
        if self._gesture_start is None:
            return
        points = self._gesture_points
        self._gesture_start = None
        self._gesture_points = []
        # Committed *unclamped* (maintainer, 2026-10-03): clamping each point
        # onto the image edge would turn an excursion outside the image into
        # spurious outline along the edge, and could change the selected area.
        # The true shape is simply clipped to the image wherever it is used
        # (`AreaSelectionModule.mask`, the outline), so only the part really
        # inside the image counts.
        if self._image_shape is not None and self._reaches_image(points):
            if self._selection.mode() is AreaSelectionMode.RECTANGLE:
                if len(points) == 2:
                    (x0, y0), (x1, y1) = points
                    self._selection.set_rectangle(x0, y0, x1, y1)
            else:
                self._selection.set_polygon(points)
        self.refresh()  # also covers the "nothing committed" case (restores the old outline)

    def _reaches_image(self, points: list[tuple[float, float]]) -> bool:
        """Whether the dragged shape covers any image pixel - a drag wholly
        outside the image selects nothing, so it must not replace (or create)
        a selection."""
        assert self._image_shape is not None
        if self._selection.mode() is AreaSelectionMode.RECTANGLE:
            if len(points) != 2:
                return False
            (x0, y0), (x1, y1) = points
            height, width = self._image_shape
            return max(min(x0, x1), 0.0) < min(max(x0, x1), float(width)) and max(min(y0, y1), 0.0) < min(
                max(y0, y1), float(height)
            )
        return bool(rasterize_polygon(np.asarray(points, dtype=float), self._image_shape).any())

    def cancel_gesture(self) -> None:
        if self._gesture_start is None:
            return
        self._gesture_start = None
        self._gesture_points = []
        self.refresh()
