"""The Sensorgram's Points toggle (2026-11-13): it shows itself off while the dot limit holds, and asks before the limit is overridden.

Why: dots are drawn only for up to a number of lines (the View tab's Dot limit, 10 at first: 64,000 dots cost ~1 s per redraw). With more lines the toggle used to stay "on" and draw
nothing, which read as a dead control (maintainer, 2026-11-13). Now, while there are more lines than the limit:

- the picture is the "off" one (crossed out, dim) and the tool tip says why (`set_capped`);
- the remembered choice is **not** changed: the toggle is still checked, `isChecked()` is still what the user chose, and the dots come back by themselves when fewer lines are drawn;
- a click does not toggle: it emits `dots_requested`, and the controls ask the user (`ask_to_raise_dots_limit`) whether to raise the limit to the number of lines drawn.

Display only: the plot applies the limit itself (`SensorgramPlot.show_series`); this widget only tells the truth about it.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QMessageBox, QWidget

from ..spectra.view_menu import IconToggle


class PointsToggle(IconToggle):
    """An `IconToggle` whose picture is "off" while the dot limit holds, and whose click then asks instead of toggling."""

    dots_requested = pyqtSignal()  # the user clicked it while the limit holds

    _capped = False
    _base_tip = ""

    def __init__(self, icon_for, tip: str, parent: QWidget | None = None) -> None:
        super().__init__(icon_for, tip, parent)
        self._base_tip = tip

    def is_capped(self) -> bool:
        return self._capped

    def set_capped(self, capped: bool, note: str = "") -> None:
        """Show the toggle off (`capped`) or as the user chose it; `note` (why) goes under the tool tip. The checked state, and so what is remembered, never changes here."""
        capped = bool(capped)
        self._capped = capped
        self.setToolTip(f"{self._base_tip}\n\n{note}" if capped and note else self._base_tip)
        self._refresh_icon()

    def _shown_on(self) -> bool:
        return self.isChecked() and not self._capped

    def nextCheckState(self) -> None:  # Qt's own name: called by every click, keyboard press and `.click()`
        if self._capped:
            self.dots_requested.emit()  # no toggle: the controls ask, and raise the limit if the user agrees
            return
        super().nextCheckState()


def ask_to_raise_dots_limit(parent: QWidget, lines: int, limit: int) -> bool:
    """Tell the user about the limit and offer to raise it to `lines`. True = raise it. Enter and Escape keep the limit (the safe answer: a slow plot is not an accident)."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Information)
    box.setWindowTitle("Dots are limited")
    box.setText(f"{lines} lines are drawn, and dots are drawn only for up to {limit} lines.")
    box.setInformativeText(
        "With many lines the dots merge into the lines, and every redraw gets slower (64,000 dots, 160 lines of 400 cubes, took about 1 second). "
        f"Raise the limit to {lines} to draw them anyway? You can change it any time under Points & lines, Dot limit."
    )
    raise_button = box.addButton(f"Raise the limit to {lines}", QMessageBox.ButtonRole.AcceptRole)
    keep_button = box.addButton("Keep the limit", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(keep_button)
    box.setEscapeButton(keep_button)
    box.exec()
    return box.clickedButton() is raise_button
