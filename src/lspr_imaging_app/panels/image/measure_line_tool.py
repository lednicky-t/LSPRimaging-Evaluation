"""Two-click "measure by line" tool for the Image panel - places a ruler
between two clicked points and hands the pixel delta to the calibration
controls (`measure_controls.py`) so the maintainer can type the real-world
distance and calibrate microns-per-pixel.

Placement is a near-twin of `rotate_line_tool.py`'s two-click state machine
(same rubber-band-line UX, same right-click-cancels / Esc-cancels-point-1
split). **Once placed, the two points are draggable** (2026-09-29, added
after the maintainer tried the click-twice-only version and asked for it) -
closer to the stable app's draggable ruler crosses
(`gui/measurement_calibration_mixin.py`'s `_on_measurement_marker_moved` on
`develop`) than the first cut was, but as a hover/drag on each point
individually rather than that app's always-visible crosses. Unlike Rotate
there are no arrow-key nudges (there is nothing to nudge - the tool does
not itself change any pixel, it only measures).

**What it owns**: the transient click state (`_first`) and four purely
visual items - the rubber-band line/point-1 marker while placing (identical
to `RotateLineTool`), plus a solid placed-line/placed-markers pair for the
current pair (`_point1`/`_point2`), which stays on screen once both points
are set - unlike Rotate (an immediate pixel change with nothing left to
look at), the whole point of Measure is comparing the line against image
features while typing a distance and deciding whether to Apply. It clears
only when a new first point is placed or the tool is deactivated.

**Live during placement, not just after point 2 commits** (2026-09-29,
maintainer's request - "add the fields also during the first drag before
the second point is placed"): `on_mouse_moved` treats the cursor as a
provisional point 2 while point 1 is pending, updating `GeometryModule`'s
anchors and the floating controls on every move - the exact same call
shape a drag of an already-placed point uses (`_geometry.set_measurement_
anchors` + `measured`). The rubber-band line is the only thing that
visually distinguishes "still placing" from "placed" - the numbers and
`GeometryModule`'s own state track the cursor continuously either way,
which is also what makes Apply already work correctly even if point 2 is
never actually clicked (the maintainer can watch the live numbers and hit
Apply directly).

The actual calibration state lives in `GeometryModule`: this class calls
`set_measurement_anchors` continuously while hovering toward point 2, on
the second click, and on every drag update thereafter (cosmetic, not
undo-tracked - matches the stable app's live-drag update) and nothing
else. Applying the calibration itself (`apply_measurement_calibration`,
the one undo-tracked step) is the floating controls widget's job, driven
by `measure_controls.py` - this tool only supplies the two points.

**Placing or dragging a pair stays in Measure mode** (matches Rotate's
"ready for another pair" behavior) - measuring is likely to need a second
look/refinement before the maintainer is happy with it, and there is no
destructive pixel change here the way Crop's apply has, so there is no
reason to force an exit just from moving a point. **Clicking Apply does
exit** (maintainer's spec, 2026-09-29, added after the first cut left
Measure the only Image Tools tool that didn't - `panel.py`'s
`_on_measure_apply_requested`, the same shape as Crop's own apply-exits
rule), matching the expectation that Apply is "done, not just try it".
"""

from __future__ import annotations

import pyqtgraph as pg
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QColor

from ...image_tools import GeometryModule

_TOOL_COLOR = "#38bdf8"  # same blue as CropTool's own _TOOL_COLOR - maintainer's preferred tool color
# (2026-09-29: was green, matching the stable app's icon literal - the maintainer asked for this
# blue instead, "similar like cropping rectangle... please keep it for most of the tools").
_HIT_MARGIN_PX = 8  # constant *screen*-pixel grab zone around a placed point, any zoom - matches crop_tool.py's

Point = tuple[float, float]


class MeasureLineTool(QObject):
    # A one-line dynamic status ("" = nothing to say) - same convention as
    # RotateLineTool.status_changed, relayed to the app's status bar.
    status_changed = pyqtSignal(str)

    # Emitted with (dx_px, dy_px, is_fresh_placement) on every change to the
    # current measurement - point 1 being placed (dx=dy=0, a reset),
    # hovering toward point 2 before it is clicked (2026-09-29 - "live
    # measurement" during placement, not just after), point 2's click
    # committing the pair, and any later drag of either point.
    # `is_fresh_placement` is True *only* for the point-1-placed reset -
    # every other emission is False. The floating controls widget resets
    # its editable um fields on a fresh placement (a new physical distance)
    # but not otherwise (dragging/hovering to fine-tune must not throw away
    # an already-typed target distance).
    measured = pyqtSignal(float, float, bool)

    def __init__(self, plot: pg.PlotItem, geometry: GeometryModule, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._plot = plot
        self._geometry = geometry
        self._active = False
        self._first: Point | None = None
        self._point1: Point | None = None
        self._point2: Point | None = None
        self._hover: Point | None = None  # live cursor position while placing point 2, pre-commit
        self._drag_point: str | None = None  # "point1" | "point2" | None

        pen = pg.mkPen(QColor(_TOOL_COLOR), width=1.5)
        pen.setStyle(Qt.PenStyle.DashLine)
        self._band = pg.PlotCurveItem(pen=pen)
        self._marker = pg.ScatterPlotItem(
            size=11, symbol="+", brush=pg.mkBrush(QColor(_TOOL_COLOR)), pen=pg.mkPen(None)
        )
        self._placed_line = pg.PlotCurveItem(pen=pg.mkPen(QColor(_TOOL_COLOR), width=1.5))
        self._placed_markers = pg.ScatterPlotItem(
            size=11, symbol="+", brush=pg.mkBrush(QColor(_TOOL_COLOR)), pen=pg.mkPen(None)
        )
        for item in (self._band, self._marker, self._placed_line, self._placed_markers):
            item.setZValue(20)
            item.setVisible(False)
            plot.addItem(item, ignoreBounds=True)  # a tool overlay must never move the view's auto-range

    # -- state --------------------------------------------------------------

    def is_active(self) -> bool:
        return self._active

    def first_point(self) -> Point | None:
        return self._first

    def current_anchor_point(self) -> Point | None:
        """Where the floating controls widget should anchor itself right
        now - the committed point 2 once a pair exists, otherwise the live
        cursor position while placing one (`None` before point 1 is even
        clicked, or once the tool goes fully idle again). Used by the panel
        to (re)position the floating controls after e.g. a pan/zoom/drag/
        hover, the same role `CropTool.rect()` plays for `_reposition_crop_
        controls`."""
        return self._point2 if self._point2 is not None else self._hover

    def set_active(self, active: bool) -> None:
        if active == self._active:
            return
        self._active = active
        self._clear_first_point()
        if not active:
            # Drop the placed ruler too, not just a pending point 1 - a
            # crop or rotation could happen while Measure is off, which
            # would make the old pixel coordinates meaningless. Re-activating
            # always starts from a clean slate (the floating controls hide
            # accordingly - see panel.py's `_reposition_measure_controls`).
            self._clear_placed()
        self.status_changed.emit("")

    # -- gestures: placement (from ImagePanel's click routing) --------------

    def on_left_click(self, x: float, y: float) -> None:
        if not self._active:
            return
        if self._first is None:
            self._clear_placed()  # a new pair starting - the old line is no longer the current measurement
            self._first = (float(x), float(y))
            self._marker.setData([x], [y])
            self._marker.setVisible(True)
            # The one moment that resets the floating controls (a brand-new
            # measurement session starting) - every subsequent emission for
            # this pair (hover, the point-2 click below, any later drag) is
            # `is_fresh_placement=False`, see the `measured` signal's docstring.
            self.measured.emit(0.0, 0.0, True)
            self.status_changed.emit("Point 1 set - click point 2 (right-click for options, Esc cancels point 1).")
            return
        first, self._first = self._first, None
        self._hide_band()
        self._marker.setVisible(False)
        self._hover = None
        self._point1, self._point2 = first, (float(x), float(y))
        self._redraw_placed()
        self._report_measurement()
        distance_px = self._distance_px()
        self.status_changed.emit(f"Ruler placed - {distance_px:.1f} px. Drag either point to fine-tune, or enter the real distance and Apply.")

    def cancel(self) -> bool:
        """Drop point 1 if there is one. Returns whether anything was
        cancelled (so Esc knows whether it consumed the key)."""
        if not self._active or self._first is None:
            return False
        self._clear_first_point()
        self.status_changed.emit("Cancelled - click point 1.")
        return True

    def on_mouse_moved(self, x: float, y: float) -> None:
        """Stretch the rubber-band line from point 1 to the cursor, show the
        distance a click here would measure, and - 2026-09-29, "live
        measurement" - live-update `GeometryModule`'s anchors and the
        floating controls exactly as a drag of an already-placed point
        would, using the cursor as a provisional point 2. This is why
        Apply already works correctly even before point 2 is ever clicked:
        whatever the controls currently show is always what `GeometryModule`
        currently holds. A no-op until point 1 exists (placement) - dragging
        an already-placed point is a different path, see `update_gesture`
        below."""
        if not self._active or self._first is None:
            return
        x0, y0 = self._first
        x, y = float(x), float(y)
        self._band.setData([x0, x], [y0, y])
        self._band.setVisible(True)
        self._hover = (x, y)
        self._geometry.set_measurement_anchors(x0, y0, x, y)
        self.measured.emit(x - x0, y - y0, False)
        distance_px = ((x - x0) ** 2 + (y - y0) ** 2) ** 0.5
        self.status_changed.emit(f"Line is {distance_px:.1f} px - click here to place point 2.")

    def handle_key(self, key: int) -> bool:
        """Esc only - no arrow-key nudge (there is nothing to nudge)."""
        if not self._active:
            return False
        if key == Qt.Key.Key_Escape:
            return self.cancel()
        return False

    # -- gestures: dragging an already-placed point (from ImageViewBox's
    # left-drag handler, same calling convention as CropTool's) -----------

    def hover_handle(self, x: float, y: float) -> str | None:
        """Which point (if any) a non-dragging hover is close enough to
        grab - `None` means "nothing to grab here". Only meaningful once a
        pair is placed and no new placement is in progress (a pending
        point 1 takes priority - there is nothing to drag yet)."""
        if not self._active or self._first is not None:
            return None
        return self._hit_test(x, y)

    def begin_gesture(self, x: float, y: float) -> bool:
        """Returns whether this tool wants the drag - `False` leaves it
        unclaimed (e.g. no pair placed yet, or the drag didn't start on
        either point - a fresh pair is placed by ordinary clicks, not by
        dragging empty space)."""
        handle = self.hover_handle(x, y)
        if handle is None:
            return False
        self._drag_point = handle
        return True

    def update_gesture(self, x: float, y: float) -> None:
        if self._drag_point is None:
            return
        moved = (float(x), float(y))
        if self._drag_point == "point1":
            self._point1 = moved
        else:
            self._point2 = moved
        self._redraw_placed()
        self._report_measurement()

    def end_gesture(self) -> None:
        self._drag_point = None

    # -- internals ------------------------------------------------------------

    def _hit_test(self, qx: float, qy: float) -> str | None:
        if self._point1 is None or self._point2 is None:
            return None
        margin_x, margin_y = self._hit_margin()
        # point2 checked first: placed last, so on an exact overlap (the two
        # points coincide) the one the maintainer can see on top wins.
        for name, point in (("point2", self._point2), ("point1", self._point1)):
            if abs(qx - point[0]) <= margin_x and abs(qy - point[1]) <= margin_y:
                return name
        return None

    def _hit_margin(self) -> tuple[float, float]:
        dx, dy = self._plot.vb.viewPixelSize()
        return abs(dx) * _HIT_MARGIN_PX, abs(dy) * _HIT_MARGIN_PX

    def _distance_px(self) -> float:
        dx, dy = self._point2[0] - self._point1[0], self._point2[1] - self._point1[1]
        return (dx * dx + dy * dy) ** 0.5

    def _report_measurement(self) -> None:
        """Called once a real pair exists (point 2 committed, or a drag of
        either point) - always `is_fresh_placement=False`. The only `True`
        emission is the direct one at point 1's placement, above - see the
        `measured` signal's own docstring for why that is the sole reset."""
        self._geometry.set_measurement_anchors(self._point1[0], self._point1[1], self._point2[0], self._point2[1])
        self.measured.emit(self._point2[0] - self._point1[0], self._point2[1] - self._point1[1], False)

    def _redraw_placed(self) -> None:
        x1, y1 = self._point1
        x2, y2 = self._point2
        self._placed_line.setData([x1, x2], [y1, y2])
        self._placed_markers.setData([x1, x2], [y1, y2])
        self._placed_line.setVisible(True)
        self._placed_markers.setVisible(True)

    def _clear_first_point(self) -> None:
        self._first = None
        self._hover = None
        self._marker.setVisible(False)
        self._hide_band()

    def _hide_band(self) -> None:
        self._band.setVisible(False)

    def _clear_placed(self) -> None:
        self._point1 = None
        self._point2 = None
        self._drag_point = None
        self._placed_line.setVisible(False)
        self._placed_markers.setVisible(False)
