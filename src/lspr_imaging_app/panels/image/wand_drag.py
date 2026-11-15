"""Dragging with the right mouse button on the image, with the magic wand armed, sets the wand's two everyday numbers
without opening its settings (2026-11-15, maintainer request; the idea is Photoshop's right-drag for the brush size).

Screen purpose: change how much the next wand click selects, with the hand already on the mouse.
Main user actions: hold the right button on the image and move. **Right = a bigger region**: the threshold of the picked
method moves (a log scale: every 80 px sideways doubles or halves it); **up = more Grow** (one pixel per 6 px). A small
readout beside the cursor names both numbers while the button is down. Then click with the left button as usual.
There is no preview of the region (maintainer decision: the selection is undoable instead).
Layout: a label over the image (child of the view's viewport), next to the cursor, shown only during the drag.
Important states: the numbers are the settings form's own spin boxes, so they are the same ones the pop-up shows, are
remembered, and never differ from what the next click uses. The direction means "bigger region" for every method: for
Contrast a bigger region is a *lower* threshold, so there the number falls as you drag right; the readout always shows the
real number.
Validation: values are kept inside the spin boxes' own ranges.
Error messages: none.

A right *click* (no movement) is not a drag: it still opens the image's context menu (pyqtgraph tells the two apart).
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtWidgets import QGraphicsView, QLabel

from .selection_toolbar import WandSettingsForm
from .tool_drag import DragReadout

PX_PER_DOUBLING = 80.0  # sideways screen pixels that double (or halve) the threshold
PX_PER_GROW_STEP = 6.0  # upward screen pixels that add one pixel of Grow


def dragged_threshold(start: float, dx: float, grows_with_value: bool, low: float, high: float) -> float:
    """The threshold after dragging `dx` screen pixels to the right: `start` doubled per `PX_PER_DOUBLING`, or halved
    when a higher number makes a *smaller* region (Contrast), kept in [`low`, `high`], rounded to one decimal."""
    exponent = dx / PX_PER_DOUBLING
    if not grows_with_value:
        exponent = -exponent
    return round(min(max(start * 2.0**exponent, low), high), 1)


def dragged_grow(start: int, dy_up: float, low: int, high: int) -> int:
    """Grow after dragging `dy_up` screen pixels upward."""
    return int(min(max(start + round(dy_up / PX_PER_GROW_STEP), low), high))


class WandDragAdjuster:
    """Turns the drag events of the view's right button into new values for the wand's settings form. The owner decides
    whether the wand is armed (`CanvasInteraction.on_right_drag`); this class only does the adjusting."""

    def __init__(self, form: WandSettingsForm, view: QGraphicsView) -> None:
        self._form = form
        self._view = view
        self._start: tuple[float, int] | None = None  # (threshold, grow) when the button went down
        self._readout = DragReadout(view)  # shared with the other tools' right-drag (`tool_drag.py`)

    @property
    def readout(self) -> QLabel:
        return self._readout.label

    def on_right_drag(self, ev: Any) -> bool:
        """One pyqtgraph drag event of the right button. Always claims it (returns `True`)."""
        box = self._form.threshold_box()
        if ev.isStart() or self._start is None:
            self._start = (float(box.value()), int(self._form.grow.value()))
        start_threshold, start_grow = self._start
        down, here = ev.buttonDownScenePos(), ev.scenePos()
        dx, dy_up = float(here.x() - down.x()), float(down.y() - here.y())
        box.setValue(dragged_threshold(start_threshold, dx, self._form.grows_with_value(), box.minimum(), box.maximum()))
        grow = self._form.grow
        grow.setValue(dragged_grow(start_grow, dy_up, grow.minimum(), grow.maximum()))
        if ev.isFinish():
            self._start = None
            self._readout.hide()
        else:
            self._show_readout(here)
        return True

    def _show_readout(self, scene_pos: Any) -> None:
        box = self._form.threshold_box()
        self._readout.show(
            f"{self._form.threshold_caption()} {box.value():.1f}{box.suffix()}   ·   Grow {self._form.grow.value()}{self._form.grow.suffix()}", scene_pos
        )
