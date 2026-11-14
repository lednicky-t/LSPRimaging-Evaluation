"""``PlacePicker``: where something sits, chosen on the same 3 x 3 grid of cells as the ROI labels' placement (2026-11-14).

Screen purpose: the Evaluation menu's "Place" row (where the B1 / S1 / Sb chips sit on their step). Same look as `image/label_placement_picker.py` (the ROI labels' picker): small square cells
that fill with the theme's blue when picked, the middle one round. Only five of the nine places exist here, so the four unused cells are empty:

    [] .  []
    .  () .
    [] .  []

The four corners are the top / bottom edge x left / right end of the step; the middle is "auto" (in the middle of the bar's height, at the left end). Exactly one cell is picked.
(Until 2026-11-13 the cells were pip pictures, a solid dot and small rings, like the 5 of a die; the maintainer asked for the ROI labels' cells instead.)

Display only: it holds no state of its own beyond which cell is picked; the owner (`PlanBarControls`) reads `place()`, remembers it and turns it into settings (`bar_model.ROLES_PLACES`).
`changed(place)` is sent when the user presses a cell; `set_place` picks one without sending it (a restored value, a reset).
"""

from __future__ import annotations

from collections.abc import Mapping

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QButtonGroup, QGridLayout, QToolButton, QWidget

from lspr_ui import get_active_theme

from ...plan.bar_model import PLACE_AUTO
from ..image.label_placement_picker import CELL_SIZE, CELL_SPACING, cell_style

# place -> (row, column) in the 3 x 3 grid
CELLS: dict[str, tuple[int, int]] = {"top-left": (0, 0), "top-right": (0, 2), PLACE_AUTO: (1, 1), "bottom-left": (2, 0), "bottom-right": (2, 2)}


class PlacePicker(QWidget):
    changed = pyqtSignal(str)  # the place the user pressed (a key of `CELLS`)

    def __init__(self, tips: Mapping[str, str], parent: QWidget) -> None:
        """`tips`: the tool tip of each place (every key of `CELLS`)."""
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(CELL_SPACING)
        for index in range(3):  # the empty cells keep the size of a cell, or the picked ones would crowd together
            grid.setRowMinimumHeight(index, CELL_SIZE)
            grid.setColumnMinimumWidth(index, CELL_SIZE)
        self.group = QButtonGroup(self)  # exactly one cell picked
        self.buttons: dict[str, QToolButton] = {}
        for place, (row, column) in CELLS.items():
            button = QToolButton(self)
            button.setObjectName(f"place_{place}")
            button.setCheckable(True)
            button.setAutoRaise(False)
            button.setFixedSize(CELL_SIZE, CELL_SIZE)
            button.setToolTip(tips[place])
            self.group.addButton(button)
            grid.addWidget(button, row, column)
            self.buttons[place] = button
            button.clicked.connect(lambda _checked=False, picked=place: self.changed.emit(picked))
        self.buttons[PLACE_AUTO].setChecked(True)
        self.refresh_theme()

    def place(self) -> str:
        return next((place for place, button in self.buttons.items() if button.isChecked()), PLACE_AUTO)

    def set_place(self, place: str) -> None:
        """Pick the cell of `place` without sending `changed` (an unknown place is ignored)."""
        if place in self.buttons:
            self.buttons[place].setChecked(True)

    def refresh_theme(self) -> None:
        theme = get_active_theme()
        for place, button in self.buttons.items():
            button.setStyleSheet(cell_style(theme, round_cell=place == PLACE_AUTO))
