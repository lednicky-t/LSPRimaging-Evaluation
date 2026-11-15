"""The selection's outlines on the image: "marching ants" in the three colours of a gesture (2026-11-16, moved out of `area_selection_tool.py`,
which keeps the drag gesture; the colours and the table of what a drag shows are in `selection_palette.py`).

**An outline** is two ``QGraphicsPathItem``s tracing the same path: a solid white line underneath and a dashed *coloured* one on top, so it is
visible on any image brightness. Both use *cosmetic* pens (constant screen width however far you zoom). A ~6 fps ``QTimer`` slides the dash
offset two pixels per tick to make the dashes crawl; it runs only while an outline is visible, so an idle panel pays nothing.

**Three outlines**, one per role (past, present, result):

* *past* = the committed selection (the module's). Built when the selection changes (`invalidate`), never per mouse move: a wand region's
  outline can take hundreds of ms. Pink, also while nothing is dragged.
* *present* = the shape being dragged (the tool hands it in as a path).
* *result* = what the drag would leave selected, for **Subtract** (the old selection with the shape cut out) and **Intersect** (the overlap).
  Made with Qt's boolean path operations on every mouse move, so it is a *preview* (vector edges; the committed result is the pixels whose
  centres are inside the shape and the outline then jumps to pixel edges). Which of the three are drawn in each mode: `OUTLINES_FOR_MODE`.

**Cost of the result** (measured 2026-11-16 on a 1275 x 800 image, a 120-point lasso, whole outline through the operation: 40 particles 3 ms,
300 particles 22 ms, 1500 particles 156 ms). Two measures keep a drag smooth: the outline is split once into its closed pieces and only the
pieces whose bounding box meets the shape's bounding box go through the operation (the rest is copied for Subtract, dropped for Intersect;
a piece inside another one has a bounding box inside it, so a hole or island is never separated from the outline that holds it), and a
computation is followed by a pause of twice its own length (`_PAUSE_FACTOR`), after which one trailing update catches the picture up with the
pointer, so the preview never takes more than a third of the GUI thread however big the selection is.

The outline is always the boundary of the *editable region*: the shape clipped to the image, or - inverted - the image minus the shape. So it
follows the shape's edge inside the image and the image border only where the region actually touches it (never along an edge the shape covers,
when inverted). A **pixel-map selection** (the wand) is outlined along the pixel edges themselves (`region_outline`: one rectangle per run of
selected pixels in a row, merged by `QPainterPath.simplified`).
"""

from __future__ import annotations

import time

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QObject, QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QPainterPath, QPen, QPolygonF
from PyQt6.QtWidgets import QGraphicsPathItem

from ...selection import AreaSelectionModule
from ...selection.area_selection_module import SUBTRACT
from .selection_palette import COLORS, OUTLINES_FOR_MODE, PAST, PRESENT, RESULT

# The dashes crawl 12.5 px/s: 2 px every 160 ms (it was 1 px every 80 ms: the same speed with half the repaints; each tick repaints everything
# under the outline's bounding box, 4 ms for 300 particles and 12 ms for 1500, measured 2026-11-16). A dash period is 2 * _DASH = 8 px, so
# the crawl still has four positions per period.
_ANT_INTERVAL_MS = 160
_ANT_STEP = 2.0
_DASH = 4.0  # dash/gap length, in screen pixels (a cosmetic pen)
_Z_VALUE = 60  # above ROI curves and overlay tints; the result is drawn over the present shape, that over the past
_Z_STEP = {PAST: 0.0, PRESENT: 0.1, RESULT: 0.2}
_PAUSE_FACTOR = 2.0  # after a result took T, the next one waits 2 T


def closed_path(path: QPainterPath) -> QPainterPath:
    """*path* with every subpath explicitly closed. Qt's boolean operations (`intersected` / `subtracted`) return subpaths that are *not*
    closed, and a stroke skips an unclosed subpath's closing edge, which is exactly the edge lying on the image border."""
    closed = QPainterPath()
    for polygon in path.toSubpathPolygons():
        closed.addPolygon(polygon)
        closed.closeSubpath()
    return closed


def polygon_path(points: np.ndarray | list[tuple[float, float]]) -> QPainterPath:
    path = QPainterPath()
    path.addPolygon(QPolygonF([QPointF(float(x), float(y)) for x, y in points]))
    path.closeSubpath()
    return path


def _runs(flags: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The runs of ``True`` along the rows of the 2-D *flags* (``False`` in its first and last column): arrays ``(row, start, stop)``, a run
    covering columns ``[start, stop)`` of its row, in the coordinates of *flags* minus 1 (the padding column). Flat indices, not 2-D
    ``np.nonzero``: that was half of the time of an outline (50 of 103 ms)."""
    step = np.diff(flags.view(np.int8), axis=1)
    width = step.shape[1]
    row, start = np.divmod(np.flatnonzero(step == 1), width)
    stop = np.flatnonzero(step == -1) % width  # row-major like the starts, so they pair up one to one
    return row, start, stop


def _boundaries(padded: np.ndarray) -> tuple[tuple[np.ndarray, ...], tuple[np.ndarray, ...]]:
    """The boundaries between consecutive rows of the int8 *padded* mask (zero border) as runs along the row: first those where the lower row
    is selected and the upper is not (the top edges of selected pixels), then the reverse (the bottom edges). The ``row`` of a run is the
    boundary's y in the unpadded pixel grid."""
    change = padded[1:] - padded[:-1]
    return _runs(change == 1), _runs(change == -1)


# Directions of an outline edge in view coordinates (x right, y down), clockwise on the screen, the selected pixel on the right-hand side.
_EAST, _SOUTH, _WEST, _NORTH = 0, 1, 2, 3


def region_outline(editable: np.ndarray) -> QPainterPath:
    """The outline of the ``True`` pixels of *editable* along pixel edges, in view units (pixel (row, col) covers [col, col+1) x [row, row+1)):
    one closed polygon per boundary, the outer ones and the holes with opposite orientation (so the even-odd fill is the region). An empty
    region gives an empty path; a region touching the image border is closed along the border.

    **Traced, not filled** (2026-11-16, measured on 1275 x 800 with 300 particles: the old way, one rectangle per run of pixels merged by
    `QPainterPath.simplified`, took 201 ms, 178 of them in `simplified`, on the GUI thread at every release of a selection stroke; this takes
    about a tenth). The boundary edges are found for the whole mask at once with numpy, joined into straight runs, and chained corner to corner
    through a lookup table; only the chaining is a Python loop (one step per corner). Cost follows the boundary, not the area, so a mostly
    selected image needs no special case. Where two selected pixels touch only at a corner, the outline keeps them as separate polygons.
    """
    padded = np.pad(np.asarray(editable, dtype=bool), 1).view(np.int8)
    width = padded.shape[1] - 2
    # Boundary edges of the selected pixels, merged into maximal straight runs, as (start x, start y, end x, end y, direction):
    # horizontal edges from the rows of the mask, vertical edges from the rows of its transpose (columns of the mask).
    (top_y, top_from, top_to), (bottom_y, bottom_from, bottom_to) = _boundaries(padded)
    (left_x, left_from, left_to), (right_x, right_from, right_to) = _boundaries(np.ascontiguousarray(padded.T))
    start_x = np.concatenate((top_from, right_x, bottom_to, left_x))
    start_y = np.concatenate((top_y, right_from, bottom_y, left_to))
    end_x = np.concatenate((top_to, right_x, bottom_from, left_x))
    end_y = np.concatenate((top_y, right_to, bottom_y, left_from))
    direction = np.concatenate(
        (np.full(top_y.size, _EAST), np.full(right_x.size, _SOUTH), np.full(bottom_y.size, _WEST), np.full(left_x.size, _NORTH))
    )
    path = QPainterPath()
    count = direction.size
    if count == 0:
        return path

    # The edge that follows each edge: the one starting where it ends, turning right if it can (so pixels that meet only at a corner stay
    # separate polygons), else left. Runs are maximal, so an edge never continues straight.
    stride = width + 1
    start_key = (start_y * stride + start_x) * 4 + direction
    order = np.argsort(start_key)
    sorted_keys = start_key[order]
    end_vertex = (end_y * stride + end_x) * 4
    following = np.full(count, -1, dtype=np.int64)
    for turn in (1, 3):  # right, then left
        wanted = end_vertex + (direction + turn) % 4
        at = np.minimum(np.searchsorted(sorted_keys, wanted), count - 1)
        found = (sorted_keys[at] == wanted) & (following < 0)
        following[found] = order[at[found]]
    if (following < 0).any():  # cannot happen for a pixel mask (every boundary corner has as many edges in as out)
        raise ValueError("selection outline does not close")

    # Walk the loops.
    chain = following.tolist()
    seen = bytearray(count)
    loops: list[list[int]] = []
    for first in range(count):
        if seen[first]:
            continue
        loop = []
        edge = first
        while not seen[edge]:
            seen[edge] = 1
            loop.append(edge)
            edge = chain[edge]
        loops.append(loop)
    for loop in loops:
        edges = np.asarray(loop)
        path.addPolygon(pg.functions.arrayToQPolygonF(start_x[edges].astype(float), start_y[edges].astype(float)))
        path.closeSubpath()
    return path


def _ant_pen(color: str, *, dashed: bool) -> QPen:
    # Width 0 is Qt's cosmetic hairline (one screen pixel at any zoom), the same line as a cosmetic 1.0 pen. It is not a style choice: with any
    # other width a QGraphicsPathItem measures its bounding rect by running a stroker over the *whole* path every time its pen changes (6.5 ms
    # per ants tick on a 300-particle outline, measured 2026-11-16); with width 0 it takes the path's control rect (0.01 ms).
    pen = QPen(QColor(color))
    pen.setWidth(0)
    pen.setCosmetic(True)
    if dashed:
        pen.setStyle(Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([_DASH, _DASH])
    return pen


class SelectionOutlines(QObject):
    """Draws the past / present / result outlines on *plot*. The tool says what is being dragged (`update`); this decides what shows."""

    def __init__(self, plot: pg.PlotItem, selection: AreaSelectionModule, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._selection = selection
        self._image_shape: tuple[int, int] | None = None
        self._pens = {role: _ant_pen(COLORS[role], dashed=True) for role in COLORS}
        self._items: dict[str, tuple[QGraphicsPathItem, QGraphicsPathItem]] = {}
        for role in COLORS:
            base = QGraphicsPathItem()
            base.setPen(_ant_pen("#ffffff", dashed=False))
            ants = QGraphicsPathItem()
            ants.setPen(self._pens[role])
            for item in (base, ants):
                item.setZValue(_Z_VALUE + _Z_STEP[role])
                item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)  # purely visual, never eats clicks
                item.hide()
                plot.addItem(item, ignoreBounds=True)
            self._items[role] = (base, ants)

        self._committed: QPainterPath | None = None  # the past outline as last built
        self._stale = True  # the selection or the image size changed since it was built
        self._parts: list[tuple[QRectF, QPolygonF]] | None = None  # the past outline's closed pieces (built when a result needs them)
        self._drag: tuple[QPainterPath | None, str | None] = (None, None)  # (present shape, combine mode) of the drag in progress
        self._ready_at = 0.0  # monotonic time before which no new result is made
        self._catch_up = QTimer(self)
        self._catch_up.setSingleShot(True)
        self._catch_up.timeout.connect(self._redraw)
        self._dash_offset = 0.0
        self._ants = QTimer(self)
        self._ants.setInterval(_ANT_INTERVAL_MS)
        self._ants.timeout.connect(self._advance_ants)

    # -- what the tool and the tests ask -------------------------------------------------------------------

    def past_items(self) -> tuple[QGraphicsPathItem, QGraphicsPathItem]:
        """(solid, dashed) of the committed selection's outline."""
        return self._items[PAST]

    def present_items(self) -> tuple[QGraphicsPathItem, QGraphicsPathItem]:
        return self._items[PRESENT]

    def result_items(self) -> tuple[QGraphicsPathItem, QGraphicsPathItem]:
        return self._items[RESULT]

    def is_animating(self) -> bool:
        return self._ants.isActive()

    def set_image_shape(self, shape: tuple[int, int] | None) -> None:
        self._image_shape = shape
        self.invalidate()

    def invalidate(self) -> None:
        """The selection (or the image size) changed: the committed outline is rebuilt at the next `update`."""
        self._stale = True

    def update(self, present: QPainterPath | None = None, how: str | None = None) -> None:
        """Redraw. *present* is the shape being dragged (``None`` with no drag) and *how* the drag's combine mode."""
        self._drag = (present, how)
        self._redraw()

    # -- drawing ------------------------------------------------------------------------------------------

    def _redraw(self) -> None:
        present, how = self._drag
        if self._stale:
            self._committed = self._committed_path()
            self._parts = None
            self._stale = False
            self._put(PAST, self._committed)
        dragging = present is not None and how in OUTLINES_FOR_MODE
        shown = OUTLINES_FOR_MODE[how] if dragging else (PAST,)  # type: ignore[index]
        self._set_visible(PAST, self._committed is not None and PAST in shown)
        self._put(PRESENT, present if PRESENT in shown else None)
        self._update_result(present, how, RESULT in shown)
        if self._committed is None and present is None:
            self._ants.stop()
        elif not self._ants.isActive():
            self._ants.start()

    def _put(self, role: str, path: QPainterPath | None) -> None:
        base, ants = self._items[role]
        if path is None:
            base.hide()
            ants.hide()
            return
        base.setPath(path)
        ants.setPath(path)
        base.show()
        ants.show()

    def _set_visible(self, role: str, visible: bool) -> None:
        for item in self._items[role]:
            item.setVisible(visible)

    def _advance_ants(self) -> None:
        self._dash_offset = (self._dash_offset + _ANT_STEP) % (2 * _DASH)
        for role, pen in self._pens.items():
            pen.setDashOffset(self._dash_offset)
            ants = self._items[role][1]
            if ants.isVisible():  # a hidden outline needs no new pen: it gets the current one the next time it is shown
                ants.setPen(pen)

    # -- the result of a Subtract / Intersect drag ---------------------------------------------------------

    def _update_result(self, present: QPainterPath | None, how: str | None, wanted: bool) -> None:
        if not wanted or present is None or self._committed is None:
            self._catch_up.stop()
            self._put(RESULT, None)
            return
        wait = self._ready_at - time.monotonic()
        if wait > 0 and self._items[RESULT][1].isVisible():
            self._catch_up.start(int(wait * 1000) + 1)  # keep the last picture; one more update once the pause is over
            return
        started = time.monotonic()
        self._put(RESULT, self._result_path(present, how))
        self._ready_at = time.monotonic() + _PAUSE_FACTOR * (time.monotonic() - started)

    def _committed_parts(self) -> list[tuple[QRectF, QPolygonF]]:
        if self._parts is None:
            polygons = self._committed.toSubpathPolygons() if self._committed is not None else []
            self._parts = [(polygon.boundingRect(), polygon) for polygon in polygons]
        return self._parts

    def _result_path(self, present: QPainterPath, how: str | None) -> QPainterPath:
        """The committed outline with *present* cut out (Subtract) or cut down to it (Intersect), see the module docstring for the pieces."""
        box = present.boundingRect()
        touched = QPainterPath()
        rest = QPainterPath()
        for rect, polygon in self._committed_parts():
            target = touched if rect.intersects(box) else rest
            target.addPolygon(polygon)
            target.closeSubpath()
        if how == SUBTRACT:
            result = closed_path(touched.subtracted(present))
            result.addPath(rest)
            return result
        return closed_path(touched.intersected(present))

    # -- the committed outline -----------------------------------------------------------------------------

    def _committed_path(self) -> QPainterPath | None:
        selection = self._selection
        if selection.region() is not None or selection.has_lock():
            # A pixel map, or any selection made under a lock (its pixels are clipped to the lock): the outline of the
            # selection itself. A lock alone selects nothing, so it has no ants (the Image panel dims what is outside it).
            if self._image_shape is None:
                return None
            editable = selection.selected_mask(self._image_shape)
            if editable is None or not editable.any():
                return None
            path = region_outline(editable)
            return None if path.isEmpty() else path
        vertices = selection.vertices()
        if vertices is None:
            return None
        path = polygon_path(vertices)
        if self._image_shape is not None:
            # Always work with the part of the shape that is inside the image (the module may hold a shape reaching past it - only the
            # inside is ever editable). The editable region is the clipped shape, or - inverted - "image minus shape": its outline follows
            # the shape's edge where that is inside the image, and the image border wherever the region touches it.
            height, width = self._image_shape
            image_path = QPainterPath()
            image_path.addRect(0.0, 0.0, float(width), float(height))
            path = image_path.subtracted(path) if selection.is_inverted() else image_path.intersected(path)
        if path.isEmpty():
            return None
        return closed_path(path)
