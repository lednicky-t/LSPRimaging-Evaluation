"""``PlacePicker``: five pips like the 5 of a die, to choose where something sits: the four corners and the middle (2026-11-13).

Screen purpose: the Evaluation menu's "Place" row (where the B1 / S1 / Sb chips sit on their step): one picture instead of two rows of words. Each corner is a pip, the middle pip
is "auto" (in the middle of the bar's height, at the left end). Exactly one pip is solid, the others are small rings.

Layout: a 3 x 3 grid with the four unused cells empty, so the pips sit like the 5 of a die:

    o   .   o
    .   o   .
    o   .   o

Compact (2026-11-13, maintainer: "it takes too much space, the points are far apart"): a cell is `PIP_CELL_PX` (16) px with no gap between cells, 48 px for the whole die; it was ribbon-sized
buttons (24 px) with 2 px between, 76 px. The pips are `PIP_ICON_PX` (14) px pictures.

Display only: it holds no state of its own beyond which pip is pressed; the owner (`PlanBarControls`) reads `place()`, remembers it and turns it into settings (`bar_model.ROLES_PLACES`).
`changed(place)` is sent when the user presses a pip; `set_place` presses one without sending it (a restored value, a reset).
"""

from __future__ import annotations

from collections.abc import Mapping

from PyQt6.QtCore import QSize, pyqtSignal
from PyQt6.QtWidgets import QButtonGroup, QGridLayout, QWidget

from ...plan.bar_model import PLACE_AUTO
from ..spectra.view_menu import IconToggle
from .plan_icons import pip_icon

# place -> (row, column) in the 3 x 3 grid
CELLS: dict[str, tuple[int, int]] = {"top-left": (0, 0), "top-right": (0, 2), PLACE_AUTO: (1, 1), "bottom-left": (2, 0), "bottom-right": (2, 2)}
PIP_CELL_PX = 16  # one cell of the die (a pip's button), square
PIP_ICON_PX = 14  # the pip's picture inside it
_SPACING = 0  # no gap between cells: the pips are 16 px apart


class _Pip(IconToggle):
    """A pip: an `IconToggle` that keeps the small size. (`IconToggle.refresh_theme` puts a button back to the ribbon's 24 px every time, so the size is set again after it.)"""

    def refresh_theme(self) -> None:
        super().refresh_theme()
        self.setFixedSize(PIP_CELL_PX, PIP_CELL_PX)
        self.setIconSize(QSize(PIP_ICON_PX, PIP_ICON_PX))


class PlacePicker(QWidget):
    changed = pyqtSignal(str)  # the place the user pressed (a key of `CELLS`)

    def __init__(self, tips: Mapping[str, str], parent: QWidget) -> None:
        """`tips`: the tool tip of each place (every key of `CELLS`)."""
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(_SPACING)
        for index in range(3):  # the empty cells keep the size of a pip, or the pips would crowd together
            grid.setRowMinimumHeight(index, PIP_CELL_PX)
            grid.setColumnMinimumWidth(index, PIP_CELL_PX)
        self.group = QButtonGroup(self)  # exactly one pip pressed
        self.buttons: dict[str, _Pip] = {}
        for place, (row, column) in CELLS.items():
            button = _Pip(pip_icon, tips[place], self)
            button.setObjectName(f"place_{place}")
            self.group.addButton(button)
            grid.addWidget(button, row, column)
            self.buttons[place] = button
            button.clicked.connect(lambda _checked=False, picked=place: self.changed.emit(picked))
        self.buttons[PLACE_AUTO].setChecked(True)

    def place(self) -> str:
        return next((place for place, button in self.buttons.items() if button.isChecked()), PLACE_AUTO)

    def set_place(self, place: str) -> None:
        """Press the pip of `place` without sending `changed` (an unknown place is ignored)."""
        if place in self.buttons:
            self.buttons[place].setChecked(True)

    def refresh_theme(self) -> None:
        for button in self.buttons.values():
            button.refresh_theme()
