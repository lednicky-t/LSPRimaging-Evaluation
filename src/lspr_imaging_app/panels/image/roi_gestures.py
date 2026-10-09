"""Left-button gestures on the ROIs tab with no tool armed (maintainer's spec, 2026-10-07).

What a left press-and-drag does depends on where it starts:

- on the **border** of a *selected* ROI: **resize**. Every selected ROI is set to
  the diameter under the cursor, the same as editing the Sample cell with several
  rows selected. The same on a visible reference ring: dragging its *inner* border
  moves the outer one with it (thickness stays), dragging its *outer* border changes
  only the thickness;
- inside a *selected* ROI: **move** the whole selection together;
- anywhere else (empty image, an unselected ROI), or with Ctrl/Shift held: a
  **rubber-band** rectangle that selects every ROI whose *centre* is inside it
  (Ctrl/Shift adds to the current selection).

A plain click (no drag) still selects, in `CanvasInteraction`. The panel owns no
ROI state: this only turns gestures into commands (`SelectionModule.set_roi_selection`,
`RoiToolbox.translate_rois`, `RoiToolbox.resize_rois`). Move and resize are written
to the toolbox *during* the drag, so the overlay, the table and the sensorgram follow
the cursor, and a whole drag is one undo step (`begin_batch` / `end_batch`, the same
pattern the Rotate and Crop tools use).

**Which space a drag edits:** ROIs are stored in the reference frame, but drawn at the
*display* position (the chromatic affine applied). The cursor moves in display space,
so every display vector is pulled back through the affine's linear part (``L``) before
it is used: a ROI then stays under the cursor even where the correction has scale or
rotation in it, and a dragged border sets the stored radius that puts the border under
the cursor.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QObject, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QPen
from PyQt6.QtWidgets import QGraphicsRectItem

from ...roi import RoiToolbox
from ...roi.scope import RoiEditTarget
from ...roi.rasterize import effective_reference_diameters
from ...roi.toolbox import MIN_SAMPLE_DIAMETER_PX
from ...selection import SelectionModule
from ...undo import undo_manager

_BAND_COLOR = "#38bdf8"
_Z_VALUE = 60  # above the ROI curves, as the area-selection outline is

MIN_RING_GAP_PX = 1.0
"""Smallest ring thickness (diameter difference, reference px) a drag of the outer border can leave."""

HIT_TOLERANCE_PX = 6.0
"""How far (screen pixels) from a selected ROI's border still counts as on it."""


@dataclass(frozen=True)
class SelectedApertures:
    """The selected ROIs as the hit test needs them. Display space throughout.

    ``resizable`` is False for a mask ROI: it has no diameter to drag."""

    ids: np.ndarray  # (N,) int
    centers: np.ndarray  # (N, 2)
    diameters: np.ndarray  # (N,) stored diameters, reference-frame px
    resizable: np.ndarray  # (N,) bool
    # Reference ring (inner, outer) diameters actually used, reference-frame px. Both
    # None when no ring can be dragged; ``ring_resizable`` is False for a mask ROI or
    # while the reference rings are hidden (an invisible border must not be grabbable).
    ring_inner: np.ndarray | None = None  # (N,)
    ring_outer: np.ndarray | None = None  # (N,)
    ring_resizable: np.ndarray | None = None  # (N,) bool


@dataclass(frozen=True)
class RoiHit:
    roi_id: int
    zone: str  # "edge" (sample border), "ring_inner", "ring_outer" or "body"
    center: tuple[float, float]  # display space


def hit_test(
    x: float, y: float, selected: SelectedApertures, linear: np.ndarray, tolerance: float
) -> RoiHit | None:
    """What the display point (x, y) is over among the *selected* ROIs.

    The border of any resizable ROI wins over the inside of another (the
    nearest border if several). Otherwise the ROI whose centre is nearest wins,
    so a small ROI inside a big one stays reachable. ``tolerance`` is in display
    units. The border zone reaches ``tolerance`` outward but at most half a
    radius inward, so a small ROI always keeps a middle to grab it by. Pure."""
    if len(selected.ids) == 0:
        return None
    try:
        inverse = np.linalg.inv(np.asarray(linear, dtype=np.float64)[:2, :2])
    except np.linalg.LinAlgError:
        return None  # a degenerate affine cannot be hit-tested
    scale = math.sqrt(abs(float(np.linalg.det(np.asarray(linear, dtype=np.float64)[:2, :2]))))
    tolerance_ref = tolerance / scale
    offsets = np.array([x, y]) - selected.centers
    distance = np.hypot(*(offsets @ inverse.T).T)  # distance from each centre in stored (reference) px
    radius = selected.diameters / 2.0
    inner_band = np.minimum(tolerance_ref, radius / 2.0)
    on_edge = selected.resizable & (distance >= radius - inner_band) & (distance <= radius + tolerance_ref)
    # One row of gaps per draggable border (inf = not on it); the nearest border wins.
    gaps = [np.where(on_edge, np.abs(distance - radius), np.inf)]
    zones = ["edge"]
    if selected.ring_resizable is not None and selected.ring_inner is not None and selected.ring_outer is not None:
        half_thickness = np.maximum((selected.ring_outer - selected.ring_inner) / 4.0, 0.0)  # half the ring width, as a radius
        band = np.minimum(tolerance_ref, half_thickness)
        for zone, diameters in (("ring_inner", selected.ring_inner), ("ring_outer", selected.ring_outer)):
            ring_radius = diameters / 2.0
            near = selected.ring_resizable & (np.abs(distance - ring_radius) <= band)
            gaps.append(np.where(near, np.abs(distance - ring_radius), np.inf))
            zones.append(zone)
    stacked = np.vstack(gaps)
    if np.isfinite(stacked).any():
        zone_index, index = np.unravel_index(int(np.argmin(stacked)), stacked.shape)
        return RoiHit(
            int(selected.ids[index]), zones[int(zone_index)], (float(selected.centers[index, 0]), float(selected.centers[index, 1]))
        )
    inside = distance <= radius
    if inside.any():
        index = int(np.argmin(np.where(inside, distance, np.inf)))
        return RoiHit(int(selected.ids[index]), "body", (float(selected.centers[index, 0]), float(selected.centers[index, 1])))
    return None


class RoiGestures(QObject):
    def __init__(
        self,
        plot: pg.PlotItem,
        roi_toolbox: RoiToolbox,
        selection: SelectionModule,
        *,
        rois_in_rect: Callable[[float, float, float, float], set[int]],
        selected_apertures: Callable[[], SelectedApertures | None],
        view_pixel_size: Callable[[], float],
        display_linear: Callable[[], np.ndarray],
        redraw_overlay: Callable[[], None],
        edit_target: RoiEditTarget | None = None,
        parent: QObject | None = None,
    ) -> None:
        """``rois_in_rect(x0, y0, x1, y1)``: ids of the ROIs whose display centre
        is inside the rectangle. ``selected_apertures()``: the selected ROIs, or
        ``None`` with no selection. ``edit_target``: where an edit lands (scope + cube; ``None`` = the base geometry,
        as before the geometry timeline). ``view_pixel_size()``: display units per screen
        pixel (sets how thick the border zone is). ``display_linear()``: the 2x2
        linear part of the current frame's affine. ``redraw_overlay()``: redraw the
        ROI circles *now* (see `_update_edit`)."""
        super().__init__(parent)
        self._toolbox = roi_toolbox
        self._selection = selection
        self._rois_in_rect = rois_in_rect
        self._selected_apertures = selected_apertures
        self._view_pixel_size = view_pixel_size
        self._display_linear = display_linear
        self._redraw_overlay = redraw_overlay
        self._edit_target = edit_target
        self._edit_kwargs: dict = {}  # fixed at the start of a drag: the scope or cube must not change under it
        self._edit_cube: int | None = None
        self._mode: str | None = None  # "marquee" | "move" | "resize" while a gesture runs
        self._band_start: tuple[float, float] | None = None
        self._edit_ids: tuple[int, ...] = ()
        self._move_start: tuple[float, float] | None = None
        self._move_applied = np.zeros(2)
        self._resize_center: tuple[float, float] = (0.0, 0.0)
        self._resize_zone = "edge"
        self._ring_start: dict[int, tuple[float, float]] = {}  # roi id -> (inner, outer) at drag start
        self._inverse: np.ndarray | None = None

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

    @property
    def busy(self) -> bool:
        """A drag is in progress (the hover cursor must not change under it)."""
        return self._mode is not None

    def hit(self, x: float, y: float) -> RoiHit | None:
        """What display point (x, y) is over among the selected ROIs (see `hit_test`)."""
        selected = self._selected_apertures()
        if selected is None:
            return None
        return hit_test(x, y, selected, self._display_linear(), HIT_TOLERANCE_PX * float(self._view_pixel_size()))

    # -- one gesture: begin / update / end -------------------------------------------

    def begin(self, x: float, y: float, *, additive: bool) -> bool:
        """Start a gesture at the display point (x, y) where the button went down.
        Always claims the drag: it is a resize, a move or a rubber band."""
        if self._mode is not None:
            return False
        hit = None if additive else self.hit(x, y)
        if hit is not None and self._begin_edit(hit, x, y):
            return True
        self._mode = "marquee"
        self._band_start = (x, y)
        self._band.setRect(QRectF(x, y, 0.0, 0.0))
        self._band.show()
        return True

    def _begin_edit(self, hit: RoiHit, x: float, y: float) -> bool:
        try:
            self._inverse = np.linalg.inv(np.asarray(self._display_linear(), dtype=np.float64)[:2, :2])
        except np.linalg.LinAlgError:
            return False  # a degenerate affine cannot be dragged through
        selected = self._selection.selected_roi_ids()
        self._edit_kwargs = {} if self._edit_target is None else self._edit_target.kwargs()
        self._edit_cube = None if self._edit_target is None else self._edit_target.cube()
        if hit.zone in ("edge", "ring_inner", "ring_outer"):
            apertures = self._selected_apertures()
            resizable = set() if apertures is None else {int(i) for i, ok in zip(apertures.ids, apertures.resizable, strict=True) if ok}
            self._edit_ids = tuple(sorted(selected & resizable))
            self._resize_center = hit.center
            self._resize_zone = hit.zone
            self._ring_start = {}
            if hit.zone != "edge":
                # Each ROI keeps its own ring thickness, so remember where every ring started.
                defaults = self._toolbox.detection_settings()
                by_id = {roi.area_roi_id: roi for roi in self._toolbox.rois_at(self._edit_cube)}
                self._ring_start = {
                    i: effective_reference_diameters(by_id[i], defaults.reference_inner_diameter_px, defaults.reference_outer_diameter_px)
                    for i in self._edit_ids
                }
            self._mode = "resize"
            undo_manager.begin_batch("Resize ROIs")
        else:
            self._edit_ids = tuple(sorted(selected))
            self._move_start = (x, y)
            self._move_applied = np.zeros(2)
            self._mode = "move"
            undo_manager.begin_batch("Move ROIs")
        return True

    def update(self, x: float, y: float) -> None:
        if self._mode == "marquee":
            if self._band_start is not None:
                x0, y0 = self._band_start
                self._band.setRect(QRectF(min(x0, x), min(y0, y), abs(x - x0), abs(y - y0)))
        elif self._mode == "move":
            self._update_move(x, y)
        elif self._mode == "resize":
            self._update_resize(x, y)

    def end(self, x: float, y: float, *, additive: bool) -> None:
        mode, self._mode = self._mode, None
        if mode == "marquee":
            self._end_marquee(x, y, additive=additive)
        elif mode in ("move", "resize"):
            self._inverse = None
            self._move_start = None
            undo_manager.end_batch()

    # -- the three gestures ------------------------------------------------------------

    def _end_marquee(self, x: float, y: float, *, additive: bool) -> None:
        start, self._band_start = self._band_start, None
        self._band.hide()
        if start is None:
            return
        x0, y0 = start
        inside = self._rois_in_rect(min(x0, x), min(y0, y), max(x0, x), max(y0, y))
        self._selection.set_roi_selection((set(self._selection.selected_roi_ids()) | inside) if additive else inside)

    def _update_move(self, x: float, y: float) -> None:
        if self._move_start is None or self._inverse is None:
            return
        display_delta = np.array([x - self._move_start[0], y - self._move_start[1]])
        reference_delta = self._inverse @ display_delta
        step = reference_delta - self._move_applied
        if not step.any():
            return
        self._toolbox.translate_rois(self._edit_ids, float(step[0]), float(step[1]), **self._edit_kwargs)
        self._move_applied = reference_delta
        self._redraw_now()

    def _update_resize(self, x: float, y: float) -> None:
        if self._inverse is None or not self._edit_ids:
            return
        offset = np.array([x - self._resize_center[0], y - self._resize_center[1]])
        cursor_diameter = 2.0 * float(np.hypot(*(self._inverse @ offset)))
        if self._resize_zone == "edge":
            self._toolbox.resize_rois(
                self._edit_ids, sample_diameter_px=max(MIN_SAMPLE_DIAMETER_PX, cursor_diameter), **self._edit_kwargs
            )
        elif self._resize_zone == "ring_inner":
            # Inner border: the outer border moves with it, so the ring keeps its thickness.
            inner = max(0.0, cursor_diameter)
            for roi_id, (start_inner, start_outer) in self._ring_start.items():
                self._toolbox.resize_roi(
                    roi_id,
                    reference_inner_diameter_px=inner,
                    reference_outer_diameter_px=inner + (start_outer - start_inner),
                    **self._edit_kwargs,
                )
        else:
            # Outer border alone: the ring gets thicker or thinner (never thinner than a gap).
            for roi_id, (start_inner, _start_outer) in self._ring_start.items():
                self._toolbox.resize_roi(
                    roi_id, reference_outer_diameter_px=max(cursor_diameter, start_inner + MIN_RING_GAP_PX), **self._edit_kwargs
                )
        self._redraw_now()

    def _redraw_now(self) -> None:
        # The panel's own redraw is debounced (a timer restarted by every change), so
        # during a continuous drag it would not fire until the mouse paused and the
        # ROIs would jump at the end. Draw the circles directly instead; the
        # debounced redraw still runs once afterwards for the image.
        self._redraw_overlay()
