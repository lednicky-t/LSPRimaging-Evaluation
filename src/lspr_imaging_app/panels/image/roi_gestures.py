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

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QObject, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QPen
from PyQt6.QtWidgets import QGraphicsRectItem

from ...applog.events import emit_timing
from ...roi import RoiToolbox
from ...domain.roi_model import SCOPE_PERSISTENT
from ...roi.scope import RoiEditTarget
from ...domain.roi_shapes import Border, MaskShape, Rectangle, effective_border, whole_pixels
from ...roi.rasterize import effective_reference_diameters
from ...roi.shape_hits import (
    BORDER_ZONES,
    RECTANGLE_ZONES,
    ZONE_BORDER_INNER,
    ZONE_ROTATE,
    FrameMap,
    rectangle_signed_distance,
    rectangle_size_at,
    rectangle_zone_direction,
)
from ...roi.toolbox import MIN_SAMPLE_DIAMETER_PX
from ...selection import SelectionModule
from ...undo import undo_manager

logger = logging.getLogger(__name__)

_BAND_COLOR = "#38bdf8"
_Z_VALUE = 60  # above the ROI curves, as the area-selection outline is

MIN_RING_GAP_PX = 1.0
"""Smallest ring thickness (diameter difference, reference px) a drag of the outer border can leave. A rectangle's border band keeps half of it as its
thickness (a rectangle's band is measured outwards from the edge, so a thickness is half the difference of two diameters)."""

HIT_TOLERANCE_PX = 6.0
"""How far (screen pixels) from a selected ROI's border still counts as on it."""

KNOB_PX = 22.0
"""How far (screen pixels) a selected rectangle's rotation knob sits beyond its top edge."""


@dataclass(frozen=True)
class SelectedApertures:
    """The selected ROIs as the hit test needs them. Display space throughout.

    ``resizable`` is False for a ROI that is not a circle: it has no diameter to drag. ``body[i]`` (``None`` for a circle) tells whether a display
    point is inside that ROI's own region, so a rectangle or a mask can still be grabbed by its inside and moved."""

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
    body: tuple[Callable[[float, float], bool] | None, ...] | None = None
    # per selected ROI, ``None`` unless it is a rectangle: ``handle(x, y, tolerance, knob)`` -> the handle zone under the display point (`shape_hits`), or ``None``
    handles: tuple[Callable[[float, float, float, float], str | None] | None, ...] | None = None
    # per selected ROI, ``None`` unless it is a rectangle whose border band can be dragged (it has a border, is not a reference ROI, and the reference rings are
    # shown): ``border(x, y, tolerance)`` -> ``(zone, distance from that band edge, distance from the rectangle's own edge)`` or ``None`` (`rectangle_border_zone`)
    borders: tuple[Callable[[float, float, float], tuple[str, float, float] | None] | None, ...] | None = None


@dataclass(frozen=True)
class RoiHit:
    roi_id: int
    zone: str  # "edge" (sample border), "ring_inner", "ring_outer", a rectangle's `RECTANGLE_ZONES` / `BORDER_ZONES`, or "body"
    center: tuple[float, float]  # display space


def hit_test(
    x: float, y: float, selected: SelectedApertures, linear: np.ndarray, tolerance: float, knob: float = 0.0
) -> RoiHit | None:
    """What the display point (x, y) is over among the *selected* ROIs.

    The border of any resizable ROI wins over the inside of another (the
    nearest border if several). Otherwise the ROI whose centre is nearest wins,
    so a small ROI inside a big one stays reachable. ``tolerance`` is in display
    units; ``knob`` is how far (display units) a selected rectangle's rotation knob sits beyond its edge. The border zone reaches ``tolerance`` outward but at most half a
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
    if selected.handles is not None or selected.borders is not None:
        # A selected rectangle's knob, edges, corners and border band (after the circles' borders). The knob wins outright; between the rectangle's own edge
        # and an edge of its border band the nearer one wins (as the nearest border wins for a circle), so a thin gap does not hide the band.
        for index in range(len(selected.ids)):
            handle = None if selected.handles is None else selected.handles[index]
            border = None if selected.borders is None else selected.borders[index]
            zone = None if handle is None else handle(x, y, tolerance, knob)
            if zone != ZONE_ROTATE and border is not None:
                found = border(x, y, tolerance)
                if found is not None and (zone is None or found[1] < found[2]):
                    zone = found[0]
            if zone is not None:
                return RoiHit(int(selected.ids[index]), zone, (float(selected.centers[index, 0]), float(selected.centers[index, 1])))
    inside = distance <= radius
    if selected.body is not None:
        for index, test in enumerate(selected.body):
            if test is not None:
                inside[index] = bool(test(x, y))
    if inside.any():
        index = int(np.argmin(np.where(inside, distance, np.inf)))
        return RoiHit(int(selected.ids[index]), "body", (float(selected.centers[index, 0]), float(selected.centers[index, 1])))
    return None


_EDIT_MODES = ("move", "resize", "rectangle", "border")
"""The gestures that change ROIs (the rubber band only selects, a refused drag does nothing): `RoiGestures.edit_running` is True while one of them runs."""


class RoiGestures(QObject):
    refused = pyqtSignal(str)  # a drag that cannot edit here (the scope does not fit the cube); the reason, for the status bar
    edit_running = pyqtSignal(bool)
    """True when a drag that edits ROIs starts (a move, a resize, a rectangle handle or a border edge), False when it ends (2026-11-19). Other panels hold their
    refresh in between (`HiddenPanelGate.set_held`): the table's rebuild ran when the mouse paused and the next move waited behind it."""

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
        self._mode: str | None = None  # "marquee" | "move" | "resize" | "rectangle" | "border" while a gesture runs
        self._border_start: dict[int, tuple[Border, float]] = {}  # rectangle border drag: roi id -> (its border, half its shorter side) at drag start
        self._border_anchor: Rectangle | None = None  # the rectangle whose border edge was grabbed (the distance is measured from its edge)
        self._rect_angle = 0.0  # the rectangle's angle when a handle drag began (an edge drag measures along these axes)
        self._band_start: tuple[float, float] | None = None
        self._edit_ids: tuple[int, ...] = ()
        self._map_ids: tuple[int, ...] = ()  # a move drag: the pixel maps among the selected ROIs (moved by whole pixels; `_edit_ids` then holds the others)
        self._map_applied = np.zeros(2, dtype=np.int64)  # whole pixels the maps have been moved so far in this drag
        self._move_start: tuple[float, float] | None = None
        self._move_applied = np.zeros(2)
        self._resize_center: tuple[float, float] = (0.0, 0.0)
        self._resize_zone = "edge"
        self._grab_roi_id: int | None = None  # the ROI whose edge / handle / border the running drag grabbed (for `held`)
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

    @property
    def rotating(self) -> bool:
        """The drag in progress is turning rectangles (the knob was grabbed): the canvas shows the rotate cursor for all of it."""
        return self._mode == "rectangle" and self._resize_zone == ZONE_ROTATE

    @property
    def held(self) -> RoiHit | None:
        """What a running resize drag has hold of (a circle's edge or ring, a rectangle's edge / corner, an edge of its border band), so the arrow cursor can follow
        the pointer: the direction it lies along changes during the drag (round a circle, or as a rectangle's corner changes its proportions). ``None`` for no drag,
        a move, a rubber band, or the turning knob (that one keeps the rotate cursor)."""
        if self._mode in ("resize", "rectangle", "border") and not self.rotating and self._grab_roi_id is not None:
            return RoiHit(self._grab_roi_id, self._resize_zone, self._resize_center)
        return None

    def resize_direction(self, hit: RoiHit, x: float, y: float) -> tuple[float, float] | None:
        """The direction on the screen (display units) along which dragging ``hit`` moves, for the two-headed arrow cursor, when ``hit`` is on a rectangle's
        edge, corner or border band; ``None`` otherwise (a circle's border points along its radius, and the knob has its own cursor)."""
        if hit.zone == ZONE_ROTATE or hit.zone not in RECTANGLE_ZONES + BORDER_ZONES:
            return None
        rect = next((roi.shape for roi in self._toolbox.rois_at(self._selection.current_cube()) if roi.area_roi_id == hit.roi_id), None)
        if not isinstance(rect, Rectangle):
            return None
        try:
            inverse = np.linalg.inv(np.asarray(self._display_linear(), dtype=np.float64)[:2, :2])
        except np.linalg.LinAlgError:
            return None
        frame = FrameMap(np.linalg.inv(inverse), inverse, np.zeros(2))
        return rectangle_zone_direction(hit.zone, rect, hit.center, frame, x, y)

    def hit(self, x: float, y: float) -> RoiHit | None:
        """What display point (x, y) is over among the selected ROIs (see `hit_test`)."""
        selected = self._selected_apertures()
        if selected is None:
            return None
        pixel = float(self._view_pixel_size())
        return hit_test(x, y, selected, self._display_linear(), HIT_TOLERANCE_PX * pixel, KNOB_PX * pixel)

    # -- one gesture: begin / update / end -------------------------------------------

    def begin(self, x: float, y: float, *, additive: bool) -> bool:
        """Start a gesture at the display point (x, y) where the button went down.
        Always claims the drag: it is a resize, a move or a rubber band."""
        if self._mode is not None:
            return False
        hit = None if additive else self.hit(x, y)
        if hit is not None and self._begin_edit(hit, x, y):
            if self._mode in _EDIT_MODES:
                self.edit_running.emit(True)
            return True
        self._mode = "marquee"
        self._band_start = (x, y)
        self._band.setRect(QRectF(x, y, 0.0, 0.0))
        self._band.show()
        return True

    def _begin_edit(self, hit: RoiHit, x: float, y: float) -> bool:
        try:
            edit_kwargs = {} if self._edit_target is None else self._edit_target.kwargs()
            refusal = self._toolbox.edit_refusal(edit_kwargs.get("cube"), edit_kwargs.get("scope", SCOPE_PERSISTENT))
            if refusal is not None:
                raise ValueError(refusal)
        except ValueError as exc:
            # Checked here, once, because a refusal in the middle of a drag would surface from inside a mouse-move handler.
            # The drag is still claimed (it started on a ROI), it just does nothing.
            self.refused.emit(str(exc))
            self._mode = "refused"
            return True
        try:
            self._inverse = np.linalg.inv(np.asarray(self._display_linear(), dtype=np.float64)[:2, :2])
        except np.linalg.LinAlgError:
            return False  # a degenerate affine cannot be dragged through
        selected = self._selection.selected_roi_ids()
        self._edit_kwargs = edit_kwargs
        self._edit_cube = None if self._edit_target is None else self._edit_target.cube()
        self._grab_roi_id = hit.roi_id
        if hit.zone in RECTANGLE_ZONES:
            apertures = self._selected_apertures()
            rectangles = set() if apertures is None or apertures.handles is None else {
                int(i) for i, handle in zip(apertures.ids, apertures.handles, strict=True) if handle is not None
            }
            self._edit_ids = tuple(sorted(selected & rectangles))
            self._resize_center = hit.center
            self._resize_zone = hit.zone
            anchor = next((r for r in self._toolbox.rois_at(self._edit_cube) if r.area_roi_id == hit.roi_id), None)
            self._rect_angle = float(anchor.shape.angle_deg) if anchor is not None and isinstance(anchor.shape, Rectangle) else 0.0
            self._mode = "rectangle"
            undo_manager.begin_batch("Resize rectangles" if hit.zone != ZONE_ROTATE else "Turn rectangles")
        elif hit.zone in BORDER_ZONES:
            apertures = self._selected_apertures()
            grabbable = set() if apertures is None or apertures.borders is None else {
                int(i) for i, border in zip(apertures.ids, apertures.borders, strict=True) if border is not None
            }
            self._edit_ids = tuple(sorted(selected & grabbable))
            self._resize_center = hit.center
            self._resize_zone = hit.zone
            default_border = self._toolbox.detection_settings().default_border()
            by_id = {roi.area_roi_id: roi for roi in self._toolbox.rois_at(self._edit_cube)}
            anchor = by_id.get(hit.roi_id)
            if anchor is None or not isinstance(anchor.shape, Rectangle) or hit.roi_id not in self._edit_ids:
                return False
            self._border_anchor = anchor.shape
            # Each rectangle keeps its own thickness (inner edge dragged) or gap (outer edge dragged); remember where they started.
            self._border_start = {
                i: (effective_border(by_id[i].border, default_border), min(by_id[i].shape.width_px, by_id[i].shape.height_px) / 2.0)
                for i in self._edit_ids
                if isinstance(by_id[i].shape, Rectangle)
            }
            self._mode = "border"
            undo_manager.begin_batch("Resize rims")
        elif hit.zone in ("edge", "ring_inner", "ring_outer"):
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
                    i: effective_reference_diameters(by_id[i], defaults.default_border())
                    for i in self._edit_ids
                }
            self._mode = "resize"
            undo_manager.begin_batch("Resize ROIs")
        else:
            # A pixel map moves by whole pixels (`RoiToolbox._at_position`), so it keeps a count of its own: the circles and rectangles of the selection follow the
            # pointer smoothly, the maps are given whole-pixel steps worked out from the whole distance dragged (a step of the pointer under half a pixel is not lost).
            shapes = {roi.area_roi_id: roi.shape for roi in self._toolbox.rois_at(self._edit_cube)}
            maps = {i for i in selected if isinstance(shapes.get(i), MaskShape)}
            self._edit_ids = tuple(sorted(selected - maps))
            self._map_ids = tuple(sorted(maps))
            self._map_applied = np.zeros(2, dtype=np.int64)
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
        elif self._mode == "rectangle":
            self._update_rectangle(x, y)
        elif self._mode == "border":
            self._update_border(x, y)

    def end(self, x: float, y: float, *, additive: bool) -> None:
        mode, self._mode = self._mode, None
        self._grab_roi_id = None
        if mode == "marquee":
            self._end_marquee(x, y, additive=additive)
        elif mode in _EDIT_MODES:
            self._inverse = None
            self._move_start = None
            undo_manager.end_batch()
            self.edit_running.emit(False)  # after the batch is closed: whoever catches up now sees the finished edit

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
        map_step = np.array([whole_pixels(v) for v in reference_delta], dtype=np.int64) - self._map_applied  # whole pixels still owed to the maps
        moves_free = bool(self._edit_ids) and bool(step.any())
        moves_maps = bool(self._map_ids) and bool(map_step.any())
        if not (moves_free or moves_maps):
            return
        # Stage timing, DEBUG only, one line per mouse step: the 2026-10-21 "GUI slow while moving ROIs" report
        # needed the toolbox write and the overlay draw told apart (and a drag step is the hot path here).
        started = time.perf_counter()
        if moves_free:
            self._toolbox.translate_rois(self._edit_ids, float(step[0]), float(step[1]), **self._edit_kwargs)
            self._move_applied = reference_delta
        if moves_maps:
            self._toolbox.translate_rois(self._map_ids, float(map_step[0]), float(map_step[1]), **self._edit_kwargs)
            self._map_applied = self._map_applied + map_step
        written = time.perf_counter()
        self._redraw_now()
        done = time.perf_counter()
        emit_timing(
            logger, "roi.drag_step", (done - started) * 1000.0,
            write_ms=round((written - started) * 1000.0, 2),
            overlay_ms=round((done - written) * 1000.0, 2),
            n=len(self._edit_ids) + len(self._map_ids),
        )

    def _update_rectangle(self, x: float, y: float) -> None:
        """Dragging a rectangle's handle: every selected rectangle gets the width / height / angle under the cursor (the same value for all, as a circle's
        diameter is), about its own centre, one undo step for the whole drag."""
        if self._inverse is None or not self._edit_ids:
            return
        sizes = rectangle_size_at(self._resize_zone, self._rect_angle, self._resize_center, FrameMap(np.linalg.inv(self._inverse), self._inverse, np.zeros(2)), x, y)
        sizes = {name: (max(MIN_SAMPLE_DIAMETER_PX, value) if name != "angle_deg" else value) for name, value in sizes.items()}
        if sizes:
            self._toolbox.resize_rectangles(self._edit_ids, **sizes, **self._edit_kwargs)
            self._redraw_now()

    def _update_border(self, x: float, y: float) -> None:
        """Dragging an edge of a rectangle's border band. The distance from the grabbed rectangle's edge to the pointer is the value, in reference pixels: the
        inner edge sets the **gap** of every selected rectangle to it (each keeps its thickness, as a circle's ring keeps its width), the outer edge sets the
        outer distance, i.e. each rectangle's **thickness** (never below `MIN_RING_GAP_PX` / 2; the gap stays). The inner edge cannot go past the middle of a
        rectangle's shorter side. One undo step for the whole drag."""
        if self._inverse is None or not self._edit_ids or self._border_anchor is None or not self._border_start:
            return
        frame = FrameMap(np.linalg.inv(self._inverse), self._inverse, np.zeros(2))
        distance = rectangle_signed_distance(self._border_anchor, self._resize_center, frame, x, y)
        if distance is None:
            return
        borders: dict[int, Border] = {}
        for roi_id, (start, half_side) in self._border_start.items():
            if self._resize_zone == ZONE_BORDER_INNER:
                borders[roi_id] = Border(max(distance, -half_side), start.thickness_px, start.enabled)
            else:
                borders[roi_id] = Border(start.gap_px, max(distance - start.gap_px, MIN_RING_GAP_PX / 2.0), start.enabled)
        self._toolbox.set_borders(borders, **self._edit_kwargs)
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
