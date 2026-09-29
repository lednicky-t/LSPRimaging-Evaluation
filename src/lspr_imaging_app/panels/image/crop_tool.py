"""A simple click-drag rectangle selector for the Image panel's Crop tool
(2026-09-29, maintainer's spec).

Deliberately **not** a port of the old app's crop box
(`gui/image_tools_controller.py`'s `pg.RectROI`, with its scattered
per-fraction "scale handles" the maintainer called "strange"). This one has
no separate handle widgets at all: the rectangle's own border *is* the grab
zone - drag a corner to resize two sides, an edge to resize one, the
interior to move the whole thing, all via ordinary proximity hit-testing
against the rectangle itself (`_hit_test`), with the grab margin held to a
constant *screen*-pixel width via `ViewBox.viewPixelSize()` so it neither
vanishes at low zoom nor swallows half the image at high zoom.

**Two clamping rules, deliberately different** (both maintainer's spec):

- A resize-drag never moves the edge you are *not* dragging - drag the
  right edge to the frame boundary and it simply stops there
  (`_clamp_resize`).
- Editing the "x:"/"y:" size fields is anchored at the rectangle's current
  top-left corner, but if the requested size does not fit from there, the
  anchor itself is pushed back just enough to fit, up to the frame edge
  (`_clamp_reflecting`) - "if this will mean the crop will go outside the
  original image it will move as much to original direction and then rest
  do the opposite site."

**Stays live across repeated applies.** `apply()` commits the current
rectangle to `GeometryModule.set_crop(...)` (one undo step, batched with
the ROI remap it triggers - the same reasoning `rotate_line_tool.py`'s
`_rotate_by` documents) but does not end the editing session - the
rectangle remains exactly as resizable/movable afterward, so a second,
third, ... pass can refine and re-apply. Only `cancel()` (discards any
edit not yet applied, reverting to whatever `GeometryModule` currently
holds) or deactivating the tool ends a session.

**Pre-fills from the existing crop** (maintainer's explicit choice,
2026-09-29): activating the tool with a crop already applied starts the
rectangle right there, ready to nudge, rather than forcing a fresh drag
every time. Dragging in the darkened area outside it starts a brand new
rectangle instead, discarding the old one (`begin_gesture`'s `"new"` mode).
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QPainterPath, QPen

import pyqtgraph as pg

from ...image_tools import GeometryModule
from ...image_tools.geometry.model import GeometryComputationalChange
from ...undo import undo_manager

_TOOL_COLOR = "#38bdf8"  # the crop button's own active blue (transforms_settings.py)
_OVERLAY_COLOR = QColor(0, 0, 0, 140)  # semi-transparent dark - "illustrate it is cut out"
_MIN_SIZE = 4  # px - a resize/new-drag can never produce a smaller crop
_HIT_MARGIN_PX = 8  # constant *screen*-pixel grab zone around the border, any zoom

Rect = tuple[int, int, int, int]  # (x, y, width, height), post-rotate+flip pixel space

# (x-zone, y-zone) -> handle name. "inside"/"inside" is the whole rectangle
# (move); any zone pairing involving "outside" has no entry (nothing to grab).
_HANDLE_MAP: dict[tuple[str, str], str] = {
    ("near-lo", "near-lo"): "nw", ("near-hi", "near-lo"): "ne",
    ("near-lo", "near-hi"): "sw", ("near-hi", "near-hi"): "se",
    ("near-lo", "inside"): "w", ("near-hi", "inside"): "e",
    ("inside", "near-lo"): "n", ("inside", "near-hi"): "s",
    ("inside", "inside"): "move",
}


def _axis_zone(q: float, lo: float, hi: float, margin: float) -> str:
    if q < lo - margin or q > hi + margin:
        return "outside"
    if abs(q - lo) <= margin:
        return "near-lo"
    if abs(q - hi) <= margin:
        return "near-hi"
    if lo < q < hi:
        return "inside"
    return "outside"


class CropTool(QObject):
    # Fires on every rectangle change (drag, field edit, apply, cancel,
    # activate) - panel.py re-reads `rect()` to refresh/reposition the
    # floating size-controls widget (a real QWidget this class does not
    # own; see crop_size_controls.py's docstring for why).
    changed = pyqtSignal()
    # A one-line result message ("Crop applied.", "Image tools are switched
    # off...") - relayed to the app's status bar exactly like
    # RotateLineTool.status_changed (see panel.py's ImagePanel.tool_status_changed).
    status_changed = pyqtSignal(str)

    def __init__(self, plot: pg.PlotItem, geometry: GeometryModule, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._plot = plot
        self._geometry = geometry
        self._active = False
        self._rect: Rect | None = None
        self._frame_size: tuple[int, int] | None = None

        self._drag_mode: str | None = None
        self._drag_anchor_rect: Rect | None = None
        self._drag_start: tuple[float, float] | None = None
        self._pre_gesture_rect: Rect | None = None

        pen = pg.mkPen(QColor(_TOOL_COLOR), width=1.5)
        self._outline = pg.PlotCurveItem(pen=pen)
        self._outline.setZValue(20)
        self._outline.setVisible(False)
        plot.addItem(self._outline, ignoreBounds=True)

        self._overlay = pg.QtWidgets.QGraphicsPathItem()
        self._overlay.setPen(QPen(Qt.PenStyle.NoPen))
        self._overlay.setBrush(QBrush(_OVERLAY_COLOR))
        self._overlay.setZValue(10)
        self._overlay.setVisible(False)
        plot.addItem(self._overlay, ignoreBounds=True)

        geometry.geometry_changed.connect(self._on_geometry_changed)

    # -- state ----------------------------------------------------------

    def is_active(self) -> bool:
        return self._active

    def rect(self) -> Rect | None:
        return self._rect

    def frame_size(self) -> tuple[int, int] | None:
        return self._frame_size

    def has_pending_changes(self) -> bool:
        return self._active and self._rect != self._applied_rect()

    def set_active(self, active: bool) -> None:
        if active == self._active:
            return
        self._active = active
        self._drag_mode = None
        self._rect = self._applied_rect() if active else None
        self._redraw()
        self.changed.emit()

    def set_frame_size(self, width: int, height: int) -> None:
        """The displayed (uncropped) frame's pixel shape - the maintainer's
        clamp bound ("limited to max of the frame presented, after rotation
        if it is larger"). Re-clamps the current rectangle's *position*
        (never its size) if it no longer fits, e.g. a dataset switch to a
        differently-shaped frame."""
        new_size = (max(int(width), 1), max(int(height), 1))
        if new_size == self._frame_size:
            return
        self._frame_size = new_size
        if self._rect is not None:
            x, y, w, h = self._rect
            self._rect = self._clamp_move(float(x), float(y), float(w), float(h))
            self._redraw()
            self.changed.emit()

    def _applied_rect(self) -> Rect | None:
        crop = self._geometry.settings().crop
        if crop.enabled and crop.width > 0 and crop.height > 0:
            return (int(crop.x), int(crop.y), int(crop.width), int(crop.height))
        return None

    def _on_geometry_changed(self, change: GeometryComputationalChange) -> None:
        """An external crop change (the Transforms panel's Reset-crop
        button, or an undo/redo) while this tool is active and nothing is
        mid-drag: adopt it, dropping any not-yet-applied edit, rather than
        silently showing a stale rectangle."""
        if not self._active or self._drag_mode is not None or change.reason != "crop":
            return
        new_rect = self._applied_rect()
        if new_rect == self._rect:
            return
        self._rect = new_rect
        self._redraw()
        self.changed.emit()

    # -- gestures (called from ImageViewBox's left-drag handler) --------

    def begin_gesture(self, x: float, y: float) -> bool:
        """Returns whether this tool wants the drag - `False` (nothing
        active, or no frame to crop yet) leaves it unclaimed."""
        if not self._active or self._frame_size is None:
            return False
        self._pre_gesture_rect = self._rect
        self._drag_anchor_rect = self._rect
        self._drag_start = (float(x), float(y))
        self._drag_mode = (self._hit_test(x, y) if self._rect is not None else None) or "new"
        if self._drag_mode == "new":
            self._rect = self._clamp_new(x, y, x, y)
            self._redraw()
            self.changed.emit()
        return True

    def update_gesture(self, x: float, y: float) -> None:
        if self._drag_mode is None:
            return
        self._rect = self._compute_rect(self._drag_mode, float(x), float(y))
        self._redraw()
        self.changed.emit()

    def end_gesture(self) -> None:
        if self._drag_mode is None:
            return
        if self._drag_mode == "new" and self._rect is not None:
            _x, _y, w, h = self._rect
            if w < _MIN_SIZE or h < _MIN_SIZE:
                # Too small to be a deliberate selection (an accidental
                # click-drag) - back out rather than leave a sliver crop.
                self._rect = self._pre_gesture_rect
        self._drag_mode = None
        self._drag_anchor_rect = None
        self._pre_gesture_rect = None
        self._drag_start = None
        self._redraw()
        self.changed.emit()

    def hover_handle(self, x: float, y: float) -> str | None:
        """Which handle a (non-dragging) hover is over, for cursor hinting
        - `None` means "nothing to grab here", `"move"` the interior."""
        if not self._active or self._rect is None or self._drag_mode is not None:
            return None
        return self._hit_test(x, y)

    # -- editable size fields --------------------------------------------

    def set_size(self, width: int, height: int) -> None:
        if not self._active or self._rect is None:
            return
        x, y, _w, _h = self._rect
        self._rect = self._clamp_reflecting(float(x), float(y), float(width), float(height))
        self._redraw()
        self.changed.emit()

    # -- commit / discard --------------------------------------------------

    def apply(self) -> bool:
        if not self._active or self._rect is None:
            return False
        if not self._geometry.settings().image_tools_enabled:
            self.status_changed.emit("Image tools are switched off - turn them on to crop.")
            return False
        x, y, w, h = self._rect
        # One undo step for the crop *and* the ROI remap it triggers
        # (RoiGeometrySync reacting synchronously to GeometryModule's own
        # signal) - see rotate_line_tool.py's `_rotate_by` for why batching,
        # not push order, is what makes this correct.
        undo_manager.begin_batch("Crop")
        try:
            self._geometry.set_crop(x, y, w, h)
        finally:
            undo_manager.end_batch()
        self.status_changed.emit(f"Crop applied - {w}x{h} px at ({x}, {y}).")
        return True

    def cancel(self) -> bool:
        """Revert to whatever `GeometryModule` currently holds (not
        necessarily "no crop" - `reset_crop`/the Transforms panel's Reset
        button already owns clearing the crop entirely; this only discards
        edits this session hasn't applied yet, the same way Rotate's cancel
        only drops an in-progress gesture, never a past rotation)."""
        if not self.has_pending_changes():
            return False
        self._rect = self._applied_rect()
        self._redraw()
        self.changed.emit()
        self.status_changed.emit("Crop reverted to the last applied crop." if self._rect else "Crop cancelled.")
        return True

    # -- hit-testing ---------------------------------------------------

    def _hit_test(self, qx: float, qy: float) -> str | None:
        if self._rect is None:
            return None
        x, y, w, h = self._rect
        margin_x, margin_y = self._hit_margin()
        zx = _axis_zone(float(qx), float(x), float(x + w), margin_x)
        zy = _axis_zone(float(qy), float(y), float(y + h), margin_y)
        return _HANDLE_MAP.get((zx, zy))

    def _hit_margin(self) -> tuple[float, float]:
        dx, dy = self._plot.vb.viewPixelSize()
        return abs(dx) * _HIT_MARGIN_PX, abs(dy) * _HIT_MARGIN_PX

    # -- rectangle math ---------------------------------------------------

    def _compute_rect(self, mode: str, qx: float, qy: float) -> Rect:
        if mode == "new":
            ax, ay = self._drag_start
            return self._clamp_new(ax, ay, qx, qy)
        if mode == "move":
            ax, ay, aw, ah = self._drag_anchor_rect
            sx, sy = self._drag_start
            return self._clamp_move(ax + (qx - sx), ay + (qy - sy), float(aw), float(ah))
        return self._clamp_resize(mode, qx, qy)

    def _clamp_new(self, x0: float, y0: float, x1: float, y1: float) -> Rect:
        frame_w, frame_h = self._frame_size or (10**9, 10**9)
        x0 = max(0.0, min(x0, float(frame_w)))
        y0 = max(0.0, min(y0, float(frame_h)))
        x1 = max(0.0, min(x1, float(frame_w)))
        y1 = max(0.0, min(y1, float(frame_h)))
        x, y = min(x0, x1), min(y0, y1)
        w, h = max(abs(x1 - x0), 1.0), max(abs(y1 - y0), 1.0)
        return int(round(x)), int(round(y)), int(round(w)), int(round(h))

    def _clamp_move(self, x: float, y: float, w: float, h: float) -> Rect:
        """Position-only clamp: `w`/`h` never change here - a move (or a
        frame-size shrink) can push the rectangle back inside the frame,
        never resize it."""
        frame_w, frame_h = self._frame_size or (10**9, 10**9)
        w = min(w, float(frame_w))
        h = min(h, float(frame_h))
        x = max(0.0, min(x, float(frame_w) - w))
        y = max(0.0, min(y, float(frame_h) - h))
        return int(round(x)), int(round(y)), int(round(w)), int(round(h))

    def _clamp_resize(self, mode: str, qx: float, qy: float) -> Rect:
        """Edge/corner drag: whichever edge *isn't* named in *mode* never
        moves - dragging the right edge into the frame boundary simply
        stops there, unlike `_clamp_reflecting`'s field-edit behavior."""
        ax, ay, aw, ah = self._drag_anchor_rect
        frame_w, frame_h = self._frame_size or (10**9, 10**9)
        left, top, right, bottom = float(ax), float(ay), float(ax + aw), float(ay + ah)

        if "w" in mode:
            left = max(0.0, min(qx, right - _MIN_SIZE))
        if "e" in mode:
            right = min(float(frame_w), max(qx, left + _MIN_SIZE))
        if "n" in mode:
            top = max(0.0, min(qy, bottom - _MIN_SIZE))
        if "s" in mode:
            bottom = min(float(frame_h), max(qy, top + _MIN_SIZE))

        return int(round(left)), int(round(top)), int(round(right - left)), int(round(bottom - top))

    def _clamp_reflecting(self, x: float, y: float, w: float, h: float) -> Rect:
        """Anchored at (x, y) (the rectangle's current top-left); if the
        requested size does not fit from there, the anchor itself is pushed
        back just enough to fit, up to the frame edge - see the module
        docstring's "two clamping rules" note."""
        frame_w, frame_h = self._frame_size or (10**9, 10**9)
        w = max(1.0, min(w, float(frame_w)))
        h = max(1.0, min(h, float(frame_h)))
        x = max(0.0, min(x, float(frame_w) - w))
        y = max(0.0, min(y, float(frame_h) - h))
        return int(round(x)), int(round(y)), int(round(w)), int(round(h))

    # -- drawing ---------------------------------------------------------

    def _redraw(self) -> None:
        if not self._active or self._rect is None:
            self._outline.setVisible(False)
            self._overlay.setVisible(False)
            return
        x, y, w, h = self._rect
        x0, y0 = float(x), float(y)
        x1, y1 = x0 + float(w), y0 + float(h)
        self._outline.setData([x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0])
        self._outline.setVisible(True)

        path = QPainterPath()
        frame_w, frame_h = self._frame_size or (x1, y1)
        path.addRect(QRectF(0.0, 0.0, float(frame_w), float(frame_h)))
        path.addRect(QRectF(x0, y0, float(w), float(h)))
        path.setFillRule(Qt.FillRule.OddEvenFill)
        self._overlay.setPath(path)
        self._overlay.setVisible(True)
