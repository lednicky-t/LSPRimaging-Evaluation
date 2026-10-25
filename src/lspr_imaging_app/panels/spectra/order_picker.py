"""Spectra ribbon, Fit group: the polynomial order as a pick-up list, 1st to 13th, each order marked by how good it is (2026-10-25).

Screen purpose: choose the order and see at a glance whether it suits the spectra. The button face shows the current order as its ordinal with a raised ending (3rd, 5th) and a
coloured dot; the menu lists the 13 orders the same way, each with the verdict in words (colour alone is not enough for everyone). The mouse wheel over the button steps the order
(wheel up = higher, like a spin box; the same limits 1 to 13).

Colours (`analysis/order_quality.py` decides the verdict, this file only shows it):

    green   good here and at 95 % of the cubes or more  yellow  good here, but out at more than 5 % of the cubes
    amber   over-fitted at the present cube            purple  under-fitted at the present cube
    grey    not available (more terms than the fit allows for the points in the window) or nothing to judge

Display only: it holds the pick and reports it (``valueChanged``), like the spin box it replaces (``value``, ``setValue``, ``setEnabled`` keep working). The panel hands it the verdicts
(`apply_quality`) and the present cube; a change of cube re-colours it without computing anything.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import QWidget

from lspr_ui import get_active_theme

from ...analysis.order_quality import MAX_ORDER, OrderQuality
from .pick_menu import PickMenu, ordinal_picture, ordinal_suffix

STATE_COLORS = {"good": "#3fb950", "some": "#e3c516", "over": "#ff9f0a", "under": "#a371f7"}  # grey (na, unknown) follows the theme
STATE_NAMES = {
    "good": "good", "some": "some cubes out", "over": "over-fitted", "under": "under-fitted", "na": "needs more points", "unknown": "not judged",
}
LEGEND = (
    "Polynomial order (the fit uses at most half as many terms as points). The dot shows how well the order suits the selected ROIs, judged by leave-one-out cross-validation "
    "on the points inside the fit window: green = good here and at 95 % of the cubes or more; yellow = good here but out at more than 5 % of the cubes; amber = over-fitted at this "
    "cube; purple = under-fitted at this cube; grey = not available or not judged. Scroll the mouse wheel over the button to step the order."
)
_WHEEL_STEP = 120  # one notch of a mouse wheel


def ordinal(number: int) -> str:
    return f"{number}{ordinal_suffix(number)}"


def describe(order: int, state: str, counts: tuple[int, int, int]) -> str:
    """The tooltip of one order for a state; ``counts`` = (cubes judged, over-fitted, under-fitted) from ``OrderQuality.counts``."""
    judged, over, under = counts
    name = f"{ordinal(order)} order"
    if state == "good":
        if over + under == 0:
            return f"{name}: good at all {judged} cubes judged."
        return f"{name}: good at this cube and at {100 * (judged - over - under) / judged:.0f} % of the {judged} cubes judged ({over + under} out)."

    if state == "some":
        return f"{name}: good at this cube, but over-fitted at {over} and under-fitted at {under} of {judged} cubes (more than 5 % out)."
    if state == "over":
        return f"{name}: over-fitted at this cube (a lower order predicts the points better); out at {over + under} of {judged} cubes."
    if state == "under":
        return f"{name}: under-fitted at this cube (a higher order predicts the points better); out at {over + under} of {judged} cubes."
    if state == "na":
        return f"{name}: the fit never uses more than half as many terms as points, and the window holds too few points for it. Widen the fit window."
    return f"{name}: no verdict (select ROIs and run the scan, with the polynomial fit chosen)."


class OrderPicker(PickMenu):
    valueChanged = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, name_on_face=False)
        self._wheel = 0
        self._shown: dict[int, tuple[str, tuple[int, int, int]]] = {}
        self._last: tuple[OrderQuality | None, int | None] = (None, None)
        grey = get_active_theme().text_dim
        for order in range(1, MAX_ORDER + 1):
            self.add_choice(order, STATE_NAMES["na"], describe(order, "unknown", (0, 0, 0)), ordinal_picture(order, grey))  # the longest label sets the menu's width
        self.finish()
        self.setToolTip(LEGEND)
        self.currentIndexChanged.connect(lambda _index: self.valueChanged.emit(self.value()))
        self.apply_quality(None, None)

    # -- the part of QSpinBox the panel and its tests use ------------------------------------------------------------------------

    def value(self) -> int:
        return int(self.currentData())  # type: ignore[arg-type]

    def setValue(self, value: int) -> None:  # noqa: N802 - Qt naming, as QSpinBox
        self.setCurrentIndex(min(max(int(value), 1), MAX_ORDER) - 1)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - Qt naming
        """The wheel steps the order: up = higher. A touch pad's small deltas add up to one step per notch."""
        if not self.isEnabled():
            event.ignore()
            return
        self._wheel += event.angleDelta().y()
        steps = int(self._wheel / _WHEEL_STEP)
        if steps:
            self._wheel -= steps * _WHEEL_STEP
            self.setValue(self.value() + steps)
        event.accept()

    # -- the verdicts ------------------------------------------------------------------------------------------------------------

    def apply_quality(self, quality: OrderQuality | None, cube: int | None) -> None:
        """Colour every order for the present cube (``quality`` None: nothing to judge, all grey). Only the orders whose state changed are redrawn."""
        self._last = (quality, cube)
        grey = get_active_theme().text_dim
        for order in range(1, MAX_ORDER + 1):
            if quality is None:
                shown = ("unknown", (0, 0, 0))
            else:
                state = quality.state(order, cube)
                shown = (state, quality.counts(order) if state in ("good", "some", "over", "under") else (0, 0, 0))
            if self._shown.get(order) == shown:
                continue
            self._shown[order] = shown
            state, counts = shown
            self.set_choice(
                order, name=STATE_NAMES[state], tip=describe(order, state, counts), picture=ordinal_picture(order, STATE_COLORS.get(state, grey)),
            )

    def refresh_theme(self) -> None:
        super().refresh_theme()
        if hasattr(self, "_last"):  # the grey dots follow the theme; not yet there while the base class builds itself
            self._shown = {}
            self.apply_quality(*self._last)
