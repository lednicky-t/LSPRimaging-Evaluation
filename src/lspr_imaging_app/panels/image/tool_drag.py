"""Dragging with the right mouse button on the image changes the armed tool's numbers (2026-11-15, maintainer: "implementing the tool gestures
changing parameter when RMB hold and move horizontally or vertically"). The wand had it first (`wand_drag.py`: threshold sideways, Grow up);
this is the general form the brush, Threshold and Local contrast use too.

Screen purpose: change how much the next click selects (or how big the brush is) with the hand already on the mouse.
Main user actions: hold the right button on the image and move. Each tool has up to two *axes*: one along the sideways movement, one along the
up-down movement. **Sideways to the right always means "bigger region"** (a bigger brush; for Threshold and Local contrast a lower threshold);
**up** raises the tool's second number (the blur size). A small readout beside the cursor names the numbers while the button is down.
Layout: a label over the image (child of the view's viewport), next to the cursor, shown only during the drag.
Important states: the numbers are the tools' own spin boxes (the ones the settings pop-up shows, which are remembered), so they never differ from
what the next click uses. A number moves on a **log scale** (every `px_per_doubling` screen pixels double it), kept inside the spin box's range.
Error messages: none. A right *click* (no movement) is not a drag: it still opens the image's context menu (pyqtgraph tells the two apart).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QGraphicsView, QLabel

from lspr_ui import get_active_theme

_READOUT_OFFSET = QPoint(18, 18)


@dataclass(frozen=True)
class DragAxis:
    """One number the drag changes. `along` is ``"x"`` (sideways) or ``"y"`` (up-down, up = positive). With `positive` the number grows as the
    pointer moves right / up; without it, it falls (a lower threshold selects more)."""

    caption: str
    read: Callable[[], float]
    write: Callable[[Any], None]  # an integer spin box takes only a whole number: `dragged_value` gives one when `integer`
    low: float
    high: float
    along: str
    positive: bool = True
    px_per_doubling: float = 80.0
    decimals: int = 1
    suffix: str = ""
    integer: bool = False


def dragged_value(start: float, delta_px: float, axis: DragAxis) -> float | int:
    """`start` after dragging `delta_px` screen pixels along the axis (right / up positive): doubled per `px_per_doubling`, or halved when the
    axis is not `positive`, kept in [`low`, `high`], rounded to the axis's precision. Pure."""
    exponent = delta_px / axis.px_per_doubling
    if not axis.positive:
        exponent = -exponent
    value = min(max(start * 2.0**exponent, axis.low), axis.high)
    return int(round(value)) if axis.integer else round(value, axis.decimals)


class DragReadout:
    """The small label beside the cursor (a child of the view's viewport: never a stray top-level window, never takes the drag's events)."""

    def __init__(self, view: QGraphicsView) -> None:
        self._view = view
        self.label = QLabel(view.viewport())
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.label.hide()

    def show(self, text: str, scene_pos: Any) -> None:
        theme = get_active_theme()
        self.label.setStyleSheet(
            f"QLabel {{ background: {theme.control_bg}; color: {theme.text_primary}; border: 1px solid {theme.control_border};"
            " border-radius: 3px; padding: 2px 6px; }"
        )
        self.label.setText(text)
        self.label.adjustSize()
        point = self._view.mapFromScene(scene_pos) + _READOUT_OFFSET
        viewport = self._view.viewport()
        x = min(max(point.x(), 0), max(viewport.width() - self.label.width(), 0))
        y = min(max(point.y(), 0), max(viewport.height() - self.label.height(), 0))
        self.label.move(x, y)
        self.label.show()
        self.label.raise_()

    def hide(self) -> None:
        self.label.hide()


class DragAdjuster:
    """Turns the drag events of the view's right button into new values for the numbers of `axes()` (asked at the start of every drag, so a
    tool whose numbers depend on a setting always drags the right ones). `after_change(ev)` is called after each step (the brush redraws its
    ring). The owner decides whether the tool is armed (`CanvasInteraction.on_right_drag`); this class only adjusts."""

    def __init__(
        self, view: QGraphicsView, axes: Callable[[], Sequence[DragAxis]], after_change: Callable[[Any], None] | None = None
    ) -> None:
        self._axes = axes
        self._after_change = after_change
        self._readout = DragReadout(view)
        self._active: list[tuple[DragAxis, float]] | None = None  # each axis with its value when the button went down

    @property
    def readout(self) -> QLabel:
        return self._readout.label

    def on_right_drag(self, ev: Any) -> bool:
        """One pyqtgraph drag event of the right button. Always claims it (returns `True`)."""
        if ev.isStart() or self._active is None:
            self._active = [(axis, float(axis.read())) for axis in self._axes()]
        down, here = ev.buttonDownScenePos(), ev.scenePos()
        dx, dy_up = float(here.x() - down.x()), float(down.y() - here.y())
        parts = []
        for axis, start in self._active:
            axis.write(dragged_value(start, dx if axis.along == "x" else dy_up, axis))
            now = axis.read()
            parts.append(f"{axis.caption} {now:.{0 if axis.integer else axis.decimals}f}{axis.suffix}")
        if self._after_change is not None:
            self._after_change(ev)
        if ev.isFinish():
            self._active = None
            self._readout.hide()
        else:
            self._readout.show("   ·   ".join(parts), here)
        return True
