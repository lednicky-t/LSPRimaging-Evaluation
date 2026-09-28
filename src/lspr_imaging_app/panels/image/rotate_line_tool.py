"""Two-click "rotate by line" tool for the Image panel (IrfanView-style).

Migrated/rewritten from the stable app's rotate tool (arrow keys only,
`gui/shortcut_manager.py`); the arrow keys are kept, the primary workflow is
now: activate the tool, click two points that should lie on a horizontal
line, and the image rotates so they do.

**Gestures** (also listed in `image_controls.py`, the single controls table):

- Left-click #1 places point 1. From then on a dashed line follows the
  cursor from point 1, with a live readout of the angle it would apply.
- Left-click #2 places point 2 and applies the rotation (one undo step),
  then the tool is ready for another pair, so a second pass can refine.
- Right-click (or Esc) cancels point 1; the next left-click starts over.
- Arrow keys: 0.1 deg per press, Ctrl 1 deg, Shift 5 deg - Left/Down turn one
  way, Right/Up the other, exactly the stable app's convention.
- No button is ever held or dragged (panning is the middle button, handled
  by `ImageViewBox`).

**What it owns**: only the transient click state (`_first`) and two purely
visual items (the rubber-band line and the point-1 marker). The rotation
itself lives in `GeometryModule` - this class only calls
`GeometryModule.set_rotation(...)`, so the change is undoable and the panel
redraws through the module's usual signal, like every other edit. The angle
math is `image_tools/geometry/alignment.py` (pure, tested against the real
transform).

**Rounding** (maintainer's decision, 2026-09-28): the two-click result is
rounded to 0.01 deg. The rounding error is at most 0.005 deg, i.e. ~0.2px at
2500px from the image centre - far below the method's own uncertainty (one
pixel of click error over a 1000px baseline is already 0.057 deg), and the
stored angle stays a short, meaningful number. Arrow-key steps are not
rounded that way (they are 0.1/1/5 deg steps added to the current angle);
they are only cleaned to 1e-6 deg so repeated steps don't store float noise
like 0.30000000000000004.
"""

from __future__ import annotations

import pyqtgraph as pg
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QColor

from ...image_tools import GeometryModule
from ...image_tools.geometry.alignment import line_tilt_deg, rotation_correction_deg
from ...undo import undo_manager

_TOOL_COLOR = "#fbbf24"  # same amber as the rotate button's active state
_CLICK_ANGLE_DECIMALS = 2  # two-click result: 0.01 deg
_STEP_ANGLE_DECIMALS = 6  # arrow steps: float-noise cleanup only
_ARROW_KEYS = {
    Qt.Key.Key_Left: -1.0,
    Qt.Key.Key_Down: -1.0,
    Qt.Key.Key_Right: 1.0,
    Qt.Key.Key_Up: 1.0,
}


class RotateLineTool(QObject):
    # A one-line dynamic status ("" = nothing to say), shown under the
    # controls hint - the live angle while placing, the result afterwards.
    status_changed = pyqtSignal(str)

    def __init__(self, plot: pg.PlotItem, geometry: GeometryModule, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._geometry = geometry
        self._active = False
        self._first: tuple[float, float] | None = None

        pen = pg.mkPen(QColor(_TOOL_COLOR), width=1.5)
        pen.setStyle(Qt.PenStyle.DashLine)
        self._band = pg.PlotCurveItem(pen=pen)
        self._marker = pg.ScatterPlotItem(size=9, brush=pg.mkBrush(QColor(_TOOL_COLOR)), pen=pg.mkPen(None))
        for item in (self._band, self._marker):
            item.setZValue(20)
            item.setVisible(False)
            # ignoreBounds: a tool overlay must never move the view's auto-range.
            plot.addItem(item, ignoreBounds=True)

    # -- state --------------------------------------------------------------

    def is_active(self) -> bool:
        return self._active

    def first_point(self) -> tuple[float, float] | None:
        return self._first

    def set_active(self, active: bool) -> None:
        if active == self._active:
            return
        self._active = active
        self._clear_first_point()
        self.status_changed.emit("")

    # -- gestures -----------------------------------------------------------

    def on_left_click(self, x: float, y: float) -> None:
        if not self._active:
            return
        if self._first is None:
            self._first = (float(x), float(y))
            self._marker.setData([x], [y])
            self._marker.setVisible(True)
            self.status_changed.emit("Point 1 set - click point 2 (right-click or Esc cancels).")
            return
        first, self._first = self._first, None
        self._hide_band()
        settings = self._geometry.settings()
        correction = rotation_correction_deg(
            first,
            (float(x), float(y)),
            flip_horizontal=settings.flip_horizontal,
            flip_vertical=settings.flip_vertical,
        )
        if correction is None:
            self.status_changed.emit("The two points are too close together - click point 1 again.")
            return
        self._rotate_by(correction, prefix="Aligned", decimals=_CLICK_ANGLE_DECIMALS)

    def on_right_click(self) -> None:
        self.cancel()

    def cancel(self) -> bool:
        """Drop point 1 if there is one. Returns whether anything was
        cancelled (so Esc knows whether it consumed the key)."""
        if not self._active or self._first is None:
            return False
        self._clear_first_point()
        self.status_changed.emit("Cancelled - click point 1.")
        return True

    def on_mouse_moved(self, x: float, y: float) -> None:
        """Stretch the rubber-band line from point 1 to the cursor and show
        the angle a click here would apply. A no-op until point 1 exists."""
        if not self._active or self._first is None:
            return
        x0, y0 = self._first
        self._band.setData([x0, float(x)], [y0, float(y)])
        self._band.setVisible(True)
        settings = self._geometry.settings()
        tilt = line_tilt_deg(self._first, (float(x), float(y)))
        correction = rotation_correction_deg(
            self._first,
            (float(x), float(y)),
            flip_horizontal=settings.flip_horizontal,
            flip_vertical=settings.flip_vertical,
        )
        if tilt is None or correction is None:
            self.status_changed.emit("Move further from point 1 (points too close).")
        else:
            self.status_changed.emit(f"Line is {tilt:+.2f} deg from horizontal - clicking here rotates by {correction:+.2f} deg.")

    def handle_key(self, key: int, modifiers: Qt.KeyboardModifier) -> bool:
        """Arrow-key steps and Esc. Returns True if the key was consumed."""
        if not self._active:
            return False
        if key == Qt.Key.Key_Escape:
            return self.cancel()
        sign = _ARROW_KEYS.get(key)
        if sign is None:
            return False
        step = 0.1
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            step = 5.0
        elif modifiers & Qt.KeyboardModifier.ControlModifier:
            step = 1.0
        self._rotate_by(sign * step, prefix="Rotated", decimals=_STEP_ANGLE_DECIMALS)
        return True

    # -- internals ----------------------------------------------------------

    def _rotate_by(self, delta_deg: float, *, prefix: str, decimals: int) -> None:
        settings = self._geometry.settings()
        if not settings.image_tools_enabled:
            self.status_changed.emit("Image tools are switched off - turn them on to rotate.")
            return
        new_angle = round(settings.rotation_angle_deg + delta_deg, decimals)
        # One undo step for the rotation *and* whatever it moves along with
        # it (RoiGeometrySync's ROI/mask remap, reacting synchronously to
        # GeometryModule's own signal) - a rotation and the ROI shift it
        # causes are one user gesture, not two. Batching (rather than relying
        # on push order) is what makes this correct regardless of whether the
        # remap's own undo push lands on the stack before or after this
        # command's - `undo.manager.UndoManager._BatchCommand.undo()` always
        # reverses in the order things were pushed, whichever call pushed
        # first.
        undo_manager.begin_batch(prefix)
        try:
            self._geometry.set_rotation(new_angle)
        finally:
            undo_manager.end_batch()
        applied = round(new_angle - settings.rotation_angle_deg, _STEP_ANGLE_DECIMALS)
        self.status_changed.emit(f"{prefix} by {applied:+.2f} deg - rotation is now {new_angle:.2f} deg.")

    def _clear_first_point(self) -> None:
        self._first = None
        self._marker.setVisible(False)
        self._hide_band()

    def _hide_band(self) -> None:
        self._band.setVisible(False)
