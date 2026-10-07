"""Mouse gestures on the ROIs tab with no tool armed (2026-10-07, maintainer's spec):

- **Left-drag**: a rubber-band rectangle that selects every ROI whose
  *centre* is inside it (Ctrl/Shift adds to the current selection).
- **Right-drag**: moves the selected ROIs together, live. Starting on an
  unselected ROI selects just that one first.

Both only turn a gesture into commands (`SelectionModule.set_roi_selection`,
`RoiToolbox.translate_rois`); the panel owns no ROI state. The move is written
to the toolbox *during* the drag, so the overlay, the table and the sensorgram
follow the cursor, and the whole drag is one undo step (`begin_batch` /
`end_batch`, the same pattern the Rotate and Crop tools use).

**Which space a drag edits:** ROIs are stored in the reference frame, but drawn
at the *display* position (the chromatic affine applied). The cursor moves in
display space, so the display delta is pulled back through the affine's linear
part (``L``) before it is added to the stored centres: a ROI then stays under
the cursor even where the correction has scale or rotation in it.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QObject, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QPen
from PyQt6.QtWidgets import QGraphicsRectItem

from ...roi import RoiToolbox
from ...selection import SelectionModule
from ...undo import undo_manager

_BAND_COLOR = "#38bdf8"
_Z_VALUE = 60  # above the ROI curves, as the area-selection outline is


class RoiGestures(QObject):
    def __init__(
        self,
        plot: pg.PlotItem,
        roi_toolbox: RoiToolbox,
        selection: SelectionModule,
        *,
        rois_in_rect: Callable[[float, float, float, float], set[int]],
        display_linear: Callable[[], np.ndarray],
        redraw_overlay: Callable[[], None],
        parent: QObject | None = None,
    ) -> None:
        """``rois_in_rect(x0, y0, x1, y1)``: ids of the ROIs whose display centre
        is inside the rectangle. ``display_linear()``: the 2x2 linear part of
        the current frame's affine. ``redraw_overlay()``: redraw the ROI circles
        *now* (see `update_move`)."""
        super().__init__(parent)
        self._toolbox = roi_toolbox
        self._selection = selection
        self._rois_in_rect = rois_in_rect
        self._display_linear = display_linear
        self._redraw_overlay = redraw_overlay
        self._band_start: tuple[float, float] | None = None
        self._move_ids: tuple[int, ...] = ()
        self._move_start: tuple[float, float] | None = None
        self._move_applied = np.zeros(2)
        self._move_inverse: np.ndarray | None = None

        pen = QPen(QColor(_BAND_COLOR))
        pen.setCosmetic(True)
        pen.setWidthF(1.0)
        pen.setStyle(Qt.PenStyle.DashLine)
        fill = QColor(_BAND_COLOR)
        fill.setAlphaF(0.15)
        self._band = QGraphicsRectItem()
        self._band.setPen(pen)
        self._band.setBrush(QBrush(fill))
        self._band.setZValue(_Z_VALUE)
        self._band.setAcceptedMouseButtons(Qt.MouseButton.NoButton)  # purely visual
        self._band.hide()
        plot.addItem(self._band, ignoreBounds=True)

    # -- rubber-band selection (left drag) ---------------------------------------

    def begin_marquee(self, x: float, y: float) -> bool:
        self._band_start = (x, y)
        self._band.setRect(QRectF(x, y, 0.0, 0.0))
        self._band.show()
        return True

    def update_marquee(self, x: float, y: float) -> None:
        if self._band_start is None:
            return
        x0, y0 = self._band_start
        self._band.setRect(QRectF(min(x0, x), min(y0, y), abs(x - x0), abs(y - y0)))

    def end_marquee(self, x: float, y: float, *, additive: bool) -> None:
        start = self._band_start
        self._band_start = None
        self._band.hide()
        if start is None:
            return
        x0, y0 = start
        inside = self._rois_in_rect(min(x0, x), min(y0, y), max(x0, x), max(y0, y))
        self._selection.set_roi_selection((set(self._selection.selected_roi_ids()) | inside) if additive else inside)

    # -- moving the selection (right drag) ---------------------------------------

    def begin_move(self, roi_id: int | None, x: float, y: float) -> bool:
        """Start moving the selected ROIs. ``roi_id`` is the ROI under the
        cursor: unselected, it replaces the selection; ``None`` (empty image)
        declines the drag. Returns whether a move started."""
        if roi_id is None or self._move_start is not None:
            return False
        try:
            inverse = np.linalg.inv(np.asarray(self._display_linear(), dtype=np.float64)[:2, :2])
        except np.linalg.LinAlgError:
            return False  # a degenerate affine cannot be dragged through
        if roi_id not in self._selection.selected_roi_ids():
            self._selection.set_roi_selection({roi_id})
        self._move_ids = tuple(sorted(self._selection.selected_roi_ids()))
        self._move_start = (x, y)
        self._move_applied = np.zeros(2)
        self._move_inverse = inverse
        undo_manager.begin_batch("Move ROIs")
        return True

    def update_move(self, x: float, y: float) -> None:
        if self._move_start is None or self._move_inverse is None:
            return
        display_delta = np.array([x - self._move_start[0], y - self._move_start[1]])
        reference_delta = self._move_inverse @ display_delta
        step = reference_delta - self._move_applied
        if not step.any():
            return
        self._toolbox.translate_rois(self._move_ids, float(step[0]), float(step[1]))
        self._move_applied = reference_delta
        # The panel's own redraw is debounced (a timer restarted by every change), so
        # during a continuous drag it would not fire until the mouse paused and the
        # ROIs would jump at the end. Draw the circles directly instead; the
        # debounced redraw still runs once afterwards for the image.
        self._redraw_overlay()

    def end_move(self) -> None:
        if self._move_start is None:
            return
        self._move_start = None
        self._move_inverse = None
        undo_manager.end_batch()
