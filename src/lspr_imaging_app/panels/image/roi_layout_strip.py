"""``RoiLayoutStrip`` - the ROI row under the Cube slider (2026-10-16, maintainer request).

A thin timeline of the ROI *layout* (all ROIs together). It has the cube slider's axis: the same x position for every
cube (`data_axis_slider.axis_x`, one formula for both), so a mark sits right under the cube tick it belongs to. A mark is
a cube where the layout changes:

- ``>`` (persistent): the layout holds from this cube on, until the next persistent mark. The first cube always has one
  (the start layout).
- a plain thin line (individual): this cube only.

Both are a few pixels wide on purpose (hundreds of cubes share the row). The mark that governs the cube shown now is
drawn at full strength, the others softer, and a hairline shows the cube shown now.

Display only: marks, colour, axis and cursor are handed in; a click or drag on the row leaves as `index_requested`
(the panel moves the Cube slider), and the mouse wheel as `step_requested` (+1 = the next mark, -1 = the previous one;
one per wheel notch, wheel up = next, like the sliders), and a right click on a mark as `mark_menu_requested` (the
menu itself: `roi_layout_menu.py`).

**Zoom** (2026-10-16): the row draws through an `AxisWindow`, the Cube slider's own (`set_window`), so it always shows
the same stretch of cubes as the slider above it, and Ctrl + wheel, Shift + wheel, a side wheel, a middle-button drag and a
middle double-click work here exactly as on the slider (the window does the work). Which marks exist, and the text of each tooltip, is the panel's call (`RoiToolbox.layout_points`).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple

from PyQt6.QtCore import QEvent, QPointF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QSizePolicy, QToolTip, QWidget

from lspr_ui import get_active_theme

from ...roi.model import SCOPE_PERSISTENT
from .data_axis_slider import AxisWindow, axis_index, axis_track, axis_x

_HEIGHT = 14
_MARK_TOP = 2.0
_MARK_BOTTOM = 12.0
_CHEVRON_WIDTH = 4.0
_HIT_PX = 5.0  # how close (pixels) the pointer must be to a mark for its tooltip
_SOFT_ALPHA = 150
_WHEEL_NOTCH = 120  # Qt: one notch of a wheel mouse is 120 eighths of a degree
_MAX_WHEEL_STEPS = 5  # a free-spinning wheel can deliver a lot at once


class StripMark(NamedTuple):
    index: int  # tick position on the cube axis
    kind: str  # "persistent" | "individual"
    tip: str  # the tooltip, e.g. "Cube 79 - persistent: 3 ROIs changed"


def governing_mark(marks: Sequence[StripMark], cursor: int | None) -> int | None:
    """The index (into `marks`, sorted by tick index) of the mark that decides the layout at tick `cursor`: an individual
    mark exactly there, else the latest persistent mark at or before it. ``None`` when there is none."""
    if cursor is None:
        return None
    best: int | None = None
    for position, mark in enumerate(marks):
        if mark.index > cursor:
            break
        if mark.index == cursor and mark.kind != SCOPE_PERSISTENT:
            return position
        if mark.kind == SCOPE_PERSISTENT:
            best = position
    return best


class RoiLayoutStrip(QWidget):
    index_requested = pyqtSignal(int)  # the tick the user clicked or dragged to
    step_requested = pyqtSignal(int)  # +1 / -1: the wheel asks for the next / previous mark
    mark_menu_requested = pyqtSignal(object, object)  # a right click on a mark: the StripMark and the global QPoint

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._wheel_remainder = 0  # angle units not yet worth a step (a touchpad sends many small ones)
        self._count = 0
        self._window = AxisWindow(self)
        self._window.changed.connect(self.update)
        self._marks: tuple[StripMark, ...] = ()
        self._cursor: int | None = None
        self._color = "#38bdf8"
        self.setFixedHeight(_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    # -- what to show ------------------------------------------------------------------------

    def set_window(self, window: AxisWindow) -> None:
        """Draw through `window` (the Cube slider's) from now on."""
        if window is self._window:
            return
        self._window.changed.disconnect(self.update)
        self._window = window
        window.changed.connect(self.update)
        self.update()

    def _view(self) -> tuple[float, float]:
        return self._window.view_for(self._count)

    def set_axis_count(self, count: int) -> None:
        """How many ticks the cube axis has (the cube slider's `set_ticks` length)."""
        if count != self._count:
            self._count = int(count)
            self._window.set_count(self._count)
            self.update()

    def set_marks(self, marks: Sequence[StripMark]) -> None:
        ordered = tuple(sorted(marks, key=lambda mark: mark.index))
        if ordered != self._marks:
            self._marks = ordered
            self.update()

    def set_cursor(self, index: int | None) -> None:
        if index != self._cursor:
            self._cursor = index
            self.update()

    def set_color(self, color: str) -> None:
        if color != self._color:
            self._color = color
            self.update()

    def marks(self) -> tuple[StripMark, ...]:
        return self._marks

    def mark_near(self, x: float) -> StripMark | None:
        """The mark within a few pixels of the x pixel `x`, the nearest one if several."""
        best: StripMark | None = None
        best_distance = _HIT_PX + 1.0
        view = self._view()
        for mark in self._marks:
            mark_x = axis_x(mark.index, self._count, self.width(), view)
            distance = abs(mark_x - x)
            if self._on_screen(mark_x) and distance <= _HIT_PX and distance < best_distance:
                best, best_distance = mark, distance
        return best

    def _on_screen(self, x: float) -> bool:
        """Whether a mark at pixel `x` is drawn at all (a zoomed axis pushes the others off to the sides); a ``>`` just left
        of the track still reaches into it."""
        left, width = axis_track(self.width())
        return left - _CHEVRON_WIDTH <= x <= left + width + 0.5

    def sizeHint(self) -> QSize:
        return QSize(super().sizeHint().width(), _HEIGHT)

    # -- painting ------------------------------------------------------------------------------

    def paintEvent(self, _event) -> None:
        if self._count < 1 or not self.isEnabled():
            return
        theme = get_active_theme()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        left, width = axis_track(self.width())
        mid = (_MARK_TOP + _MARK_BOTTOM) / 2.0
        painter.setPen(QPen(QColor(theme.control_border), 1.0))
        painter.drawLine(QPointF(left, mid), QPointF(left + width, mid))

        view = self._view()
        governing = governing_mark(self._marks, self._cursor)
        for position, mark in enumerate(self._marks):
            x = axis_x(mark.index, self._count, self.width(), view)
            if not self._on_screen(x):
                continue  # off screen after a zoom
            color = QColor(self._color)
            if position != governing:
                color.setAlpha(_SOFT_ALPHA)
            pen = QPen(color, 1.5)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
            painter.setPen(pen)
            if mark.kind == SCOPE_PERSISTENT:
                painter.drawPolyline(
                    [QPointF(x, _MARK_TOP), QPointF(x + _CHEVRON_WIDTH, mid), QPointF(x, _MARK_BOTTOM)]
                )
            else:
                painter.drawLine(QPointF(x, _MARK_TOP), QPointF(x, _MARK_BOTTOM))

        if self._cursor is not None and 0 <= self._cursor < self._count:
            x = axis_x(self._cursor, self._count, self.width(), view)
            if left - 0.5 <= x <= left + width + 0.5:
                painter.setPen(QPen(QColor(theme.text_dim), 1.0))
                painter.drawLine(QPointF(x, 0.0), QPointF(x, float(_HEIGHT)))
        painter.end()

    # -- mouse -----------------------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled() and self._count > 0:
            self.index_requested.emit(axis_index(event.position().x(), self._count, self.width(), self._view()))
            event.accept()
            return
        if event.button() == Qt.MouseButton.MiddleButton and self.isEnabled() and self._count > 0:
            self._window.start_pan(event.position().x())
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if event.buttons() & Qt.MouseButton.LeftButton and self.isEnabled() and self._count > 0:
            self.index_requested.emit(axis_index(event.position().x(), self._count, self.width(), self._view()))
            event.accept()
            return
        if event.buttons() & Qt.MouseButton.MiddleButton and self._window.drag_pan(event.position().x(), self.width()):
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.MiddleButton and self._window.is_panning():
            self._window.end_pan()
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.MiddleButton and self.isEnabled():
            self._window.reset()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        mark = self.mark_near(event.pos().x()) if self.isEnabled() else None
        if mark is None:
            event.ignore()  # nothing under the pointer: no menu
            return
        self.mark_menu_requested.emit(mark, event.globalPos())
        event.accept()

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        if self.isEnabled() and self._window.handle_wheel(event, self.width()):  # Ctrl: zoom, Shift / side wheel: pan
            event.accept()
            return
        if event.modifiers() != Qt.KeyboardModifier.NoModifier or not self.isEnabled() or not self._marks:
            event.ignore()
            return
        self._wheel_remainder += event.angleDelta().y()
        notches = int(self._wheel_remainder / _WHEEL_NOTCH)  # towards zero: 119 units is not a notch yet
        if notches:
            self._wheel_remainder -= notches * _WHEEL_NOTCH
            for _ in range(min(abs(notches), _MAX_WHEEL_STEPS)):
                self.step_requested.emit(1 if notches > 0 else -1)
        event.accept()

    def event(self, event) -> bool:  # type: ignore[override]
        if event.type() == QEvent.Type.ToolTip:
            mark = self.mark_near(event.pos().x())
            if mark is None:
                QToolTip.hideText()
                event.ignore()
            else:
                QToolTip.showText(event.globalPos(), mark.tip, self)
            return True
        return super().event(event)
