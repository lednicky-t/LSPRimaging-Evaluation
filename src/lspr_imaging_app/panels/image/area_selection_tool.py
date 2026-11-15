"""Canvas side of the area selection (2026-10-03): the drag gesture that draws a rectangle, a lasso or a brush stroke.

The *state* lives in ``AreaSelectionModule`` (``selection/``); this class only turns a left-drag into a
``set_rectangle`` / ``set_polygon`` / ``combine_region`` command. Panels display and forward gestures, they own no domain state (LSPRi CLAUDE.md).
**Drawing** the outlines is `SelectionOutlines` (`selection_outlines.py`): this class hands it the shape being dragged and the drag's combine
mode, and it decides what shows (the selection as it stands, the shape, and for Subtract / Intersect the result: `selection_palette.py`).

A rectangle / lasso / brush stroke in progress is committed to the module only when the drag finishes. A drag may start and wander
outside the image (maintainer, 2026-10-03: starting exactly on the image edge is too fiddly); the live preview follows the cursor unclamped, and
the finished shape is simply clipped to the image wherever it is used (`AreaSelectionModule.mask`), so only the part inside the image is selected.
The wand and the select-by-value tools have no drag: `begin_gesture` declines while one of them is the picked mode.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QObject, Qt
from PyQt6.QtGui import QColor, QPainterPath, QPainterPathStroker, QPen
from PyQt6.QtWidgets import QGraphicsEllipseItem, QGraphicsPathItem

from ...image_tools.brush_stroke import stroke_region
from ...selection import AreaSelectionMode, AreaSelectionModule
from ...selection.area_selection_module import ADD, CLICK_MODES, INTERSECT, REPLACE, SUBTRACT, rasterize_polygon
from .selection_outlines import SelectionOutlines, polygon_path

_Z_VALUE = 60  # the brush's ring (above the outlines)
_MIN_LASSO_STEP = 0.5  # view units; drops sub-pixel jitter from the trail
_MAX_PREVIEW_POINTS = 200  # a long brush stroke is outlined from at most this many points while it is dragged (the stroke itself uses all)


class AreaSelectionTool(QObject):
    """Runs the drag gesture on *plot* and keeps the outlines (`SelectionOutlines`) up to date."""

    def __init__(
        self,
        plot: pg.PlotItem,
        selection: AreaSelectionModule,
        parent: QObject | None = None,
        notify: Callable[[str], None] | None = None,
        brush_diameter: Callable[[], float] | None = None,
    ) -> None:
        super().__init__(parent)
        self._plot = plot
        self._selection = selection
        self._brush_diameter = brush_diameter or (lambda: 10.0)  # image pixels; the selection brush's size (`brush_settings.py`)
        self._notify = notify or (lambda _text: None)  # the status bar: says why a drag selected nothing
        self._image_shape: tuple[int, int] | None = None
        self._gesture_start: tuple[float, float] | None = None
        self._gesture_points: list[tuple[float, float]] = []
        self._gesture_how = REPLACE  # the combine mode of the drag in progress (the sticky mode or a Shift / Alt override)

        self._outlines = SelectionOutlines(plot, selection, self)

        # The brush's cursor: a white ring under a black one, the size of the stamp, following the mouse while the brush is armed.
        self._cursor_items: list[QGraphicsEllipseItem] = []
        for color, width in (("#ffffff", 3.0), ("#000000", 1.0)):
            ring = QGraphicsEllipseItem()
            pen = QPen(QColor(color))
            pen.setWidthF(width)
            pen.setCosmetic(True)
            ring.setPen(pen)
            ring.setZValue(_Z_VALUE + 1)
            ring.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            ring.hide()
            plot.addItem(ring, ignoreBounds=True)
            self._cursor_items.append(ring)

        selection.selection_changed.connect(self._on_selection_changed)

    # -- display --------------------------------------------------------------

    @property
    def _ants_item(self) -> QGraphicsPathItem:
        """The dashed line of the committed selection's outline (tests read its path)."""
        return self._outlines.past_items()[1]

    def set_image_shape(self, shape: tuple[int, int] | None) -> None:
        """The displayed image's ``(height, width)``; needed to clamp drags
        and to draw an inverted selection's border."""
        self._image_shape = shape
        self._outlines.set_image_shape(shape)
        self.refresh()

    def _on_selection_changed(self) -> None:
        self._outlines.invalidate()
        self.refresh()

    def refresh(self) -> None:
        """Redraw the outlines: the committed one is rebuilt only when the selection changed, the dragged shape (and its result) every call."""
        dragging = self._gesture_start is not None
        self._outlines.update(self._preview_path() if dragging else None, self._gesture_how if dragging else None)

    def is_animating(self) -> bool:
        return self._outlines.is_animating()

    def gesture_how(self) -> str | None:
        """How the drag in progress will combine with the selection (fixed when the drag began), or ``None`` with no drag."""
        return self._gesture_how if self._gesture_start is not None else None

    def _brush_path(self, points: list[tuple[float, float]]) -> QPainterPath:
        """The outline of a brush stroke through `points` (round ends and joins, merged into one shape)."""
        if len(points) > _MAX_PREVIEW_POINTS:
            step = len(points) // _MAX_PREVIEW_POINTS + 1
            points = points[::step] + [points[-1]]
        path = QPainterPath()
        path.moveTo(*points[0])
        for x, y in points[1:]:
            path.lineTo(x, y)
        if len(points) == 1:
            path.lineTo(points[0][0] + 1e-3, points[0][1])  # a click: one round stamp
        stroker = QPainterPathStroker()
        stroker.setWidth(max(float(self._brush_diameter()), 1e-3))
        stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
        stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        return stroker.createStroke(path).simplified()

    def _preview_path(self) -> QPainterPath | None:
        if not self._gesture_points:
            return None
        if self._selection.mode() is AreaSelectionMode.BRUSH:
            return self._brush_path(self._gesture_points)
        if self._selection.mode() is AreaSelectionMode.RECTANGLE:
            (x0, y0), (x1, y1) = self._gesture_points[0], self._gesture_points[-1]
            return polygon_path([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
        if len(self._gesture_points) < 2:
            return None
        return polygon_path(self._gesture_points)

    # -- gesture --------------------------------------------------------------

    def begin_gesture(self, x: float, y: float, how: str = REPLACE) -> bool:
        """Starts a drag at view point ``(x, y)``, which may be outside the
        image. Declines (``False``) when there is no image at all, and while the
        wand is picked (a click, not a drag, makes its selection). ``how`` is how the
        finished shape combines with the selection (Replace / Add / Subtract / Intersect)."""
        if self._image_shape is None or self._selection.mode() in CLICK_MODES:
            return False
        self._gesture_how = how
        self._gesture_start = (x, y)
        self._gesture_points = [(x, y)]
        self.refresh()
        return True

    def update_gesture(self, x: float, y: float) -> None:
        if self._gesture_start is None:
            return
        point = (x, y)  # unclamped while previewing; clamped on commit
        if self._selection.mode() is AreaSelectionMode.BRUSH:
            last = self._gesture_points[-1]
            if np.hypot(point[0] - last[0], point[1] - last[1]) >= _MIN_LASSO_STEP:
                self._gesture_points.append(point)
        elif self._selection.mode() is AreaSelectionMode.RECTANGLE:
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
        if self._selection.mode() is AreaSelectionMode.BRUSH:
            self._commit_stroke(points, self._gesture_how)
            self.refresh()
            return
        # Committed *unclamped* (maintainer, 2026-10-03): clamping each point
        # onto the image edge would turn an excursion outside the image into
        # spurious outline along the edge, and could change the selected area.
        # The true shape is simply clipped to the image wherever it is used
        # (`AreaSelectionModule.mask`, the outline), so only the part really
        # inside the image counts.
        if self._image_shape is not None and self._reaches_image(points):
            how, shape = self._gesture_how, self._image_shape
            if self._selection.mode() is AreaSelectionMode.RECTANGLE:
                if len(points) == 2:
                    (x0, y0), (x1, y1) = points
                    self._selection.set_rectangle(x0, y0, x1, y1, how, shape)
            else:
                self._selection.set_polygon(points, how, shape)
        self.refresh()  # also covers the "nothing committed" case (restores the old outline)

    def _reaches_image(self, points: list[tuple[float, float]]) -> bool:
        """Whether the dragged shape covers any image pixel - a drag wholly
        outside the image selects nothing, so it must not replace (or create)
        a selection. With a lock on it must also cover a pixel *inside the lock*; the status bar says when it does not."""
        assert self._image_shape is not None
        if self._selection.mode() is AreaSelectionMode.RECTANGLE:
            if len(points) != 2:
                return False
            (x0, y0), (x1, y1) = points
            height, width = self._image_shape
            if not (
                max(min(x0, x1), 0.0) < min(max(x0, x1), float(width)) and max(min(y0, y1), 0.0) < min(max(y0, y1), float(height))
            ):
                return False
            vertices = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=float)
        else:
            vertices = np.asarray(points, dtype=float)
        covered = rasterize_polygon(vertices, self._image_shape)
        if not covered.any():
            return False
        lock = self._selection.lock_mask(self._image_shape)
        if lock is not None and not (covered & lock).any():
            self._notify("Selection: the shape is outside the lock (Unlock to select there).")
            return False
        return True

    # -- the brush ------------------------------------------------------------

    def stamp_at(self, x: float, y: float, how: str = ADD) -> None:
        """A click with the brush: one stamp at view point ``(x, y)``."""
        self._commit_stroke([(x, y)], how)

    def _commit_stroke(self, points: list[tuple[float, float]], how: str) -> None:
        """Combine the stroke through `points` with the selection as `how` says: ``REPLACE`` (the stroke becomes the selection), ``ADD`` (paint),
        ``SUBTRACT`` (erase) or ``INTERSECT`` (keep only what the stroke covers): one undo step. Like every selection tool, the brush does what the
        picked mode says (until 2026-11-16 a Replace painted; the maintainer: "I select replace, and when I start drawing it switches to add").
        A stroke that reaches no pixel of the lock selects nothing and says so."""
        if self._image_shape is None or not points:
            return
        stroke = stroke_region(points, float(self._brush_diameter()), self._image_shape)
        if not stroke.any():
            return
        lock = self._selection.lock_mask(self._image_shape)
        if lock is not None and not (stroke & lock).any():
            self._notify("Brush: the stroke is outside the lock (Unlock to paint there).")
            return
        label = {REPLACE: "Brush stroke", ADD: "Brush stroke", SUBTRACT: "Brush erase", INTERSECT: "Brush intersect"}[how]
        self._selection.combine_region(stroke, how, label)

    def show_brush_cursor(self, x: float, y: float) -> None:
        """Put the brush ring (the stamp's size) at view point ``(x, y)``."""
        radius = max(float(self._brush_diameter()), 1.0) / 2.0
        for ring in self._cursor_items:
            ring.setRect(x - radius, y - radius, 2.0 * radius, 2.0 * radius)
            ring.show()

    def hide_brush_cursor(self) -> None:
        for ring in self._cursor_items:
            if ring.isVisible():
                ring.hide()

    def cancel_gesture(self) -> None:
        if self._gesture_start is None:
            return
        self._gesture_start = None
        self._gesture_points = []
        self.refresh()
