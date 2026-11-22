"""``LegendCornerPicker``: where the legend sits in the plot area, chosen on a 2 x 2 block of cells, one per corner (2026-11-22).

Screen purpose: the "Corner" row of the Legend menu of every plot. Same look as the ROI labels' 3 x 3 picker (`image/label_placement_picker.py`) and the Evaluation menu's `PlacePicker`:
small square cells that fill with the theme's blue when picked. The block stands for the plot area, so a cell is the corner the legend goes to:

    [] []
    [] []

Exactly one cell is picked. Display only: it holds which cell is picked; the owner (`PlotLookControls`) reads it, remembers it and turns it into settings. It offers `buttons` and `group`
like the segmented rows of the menu, so the owner restores, presses and remembers it with the same helpers (`plot_look.press` picks a cell, `group.buttonClicked` reports a click).
"""

from __future__ import annotations

from collections.abc import Mapping

from PyQt6.QtWidgets import QButtonGroup, QGridLayout, QToolButton, QWidget

from lspr_ui import get_active_theme

from ..image.label_placement_picker import CELL_SIZE, CELL_SPACING, cell_style
from .legend_corner import CORNERS, DEFAULT_CORNER


class LegendCornerPicker(QWidget):
    def __init__(self, tips: Mapping[str, str], parent: QWidget, *, object_prefix: str = "legend") -> None:
        """`tips`: the tool tip of each corner (every one of `CORNERS`)."""
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(CELL_SPACING)
        self.group = QButtonGroup(self)  # exactly one cell picked
        self.buttons: dict[str, QToolButton] = {}
        for index, corner in enumerate(CORNERS):
            button = QToolButton(self)
            button.setObjectName(f"{object_prefix}_corner_{corner}")
            button.setCheckable(True)
            button.setAutoRaise(False)
            button.setFixedSize(CELL_SIZE, CELL_SIZE)
            button.setToolTip(tips[corner])
            self.group.addButton(button)
            grid.addWidget(button, index // 2, index % 2)
            self.buttons[corner] = button
        self.buttons[DEFAULT_CORNER].setChecked(True)
        self.refresh_theme()

    def corner(self) -> str:
        return next((corner for corner, button in self.buttons.items() if button.isChecked()), DEFAULT_CORNER)

    def refresh_theme(self) -> None:
        style = cell_style(get_active_theme(), round_cell=False)
        for button in self.buttons.values():
            button.setStyleSheet(style)
