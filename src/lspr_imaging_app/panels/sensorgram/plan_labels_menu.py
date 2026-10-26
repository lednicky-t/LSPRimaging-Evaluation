"""Sensorgram ribbon, "Experimental plan" group: the "Step labels" pick-up menu (2026-10-27).

Screen purpose: one button that holds every control of the text written on the plan bar (which part of the note, what its position is measured against, where it sits, its
size), so the ribbon shows one icon instead of four fields.

Built like the "Baseline selector" menu (`baseline_selector_menu.py`): an icon button that opens a captioned grid in a `QWidgetAction` inside a `QMenu`, which opens on click and
closes when you click outside it. The controls are made, remembered and read by `PlanBarControls` (`plan_group.py`); this button only reparents them into its panel
(it adds no state and no signals of its own).
"""

from __future__ import annotations

from collections.abc import Sequence

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QGridLayout, QLabel, QMenu, QToolButton, QWidget, QWidgetAction

from lspr_ui import GuiTheme, get_active_theme

from ..image.general_group import style_general_icon_button
from .plan_icons import position_picture

TIP = "Step labels: which part of each step's note the bar shows, what the text position is measured against, where the text sits, and its size."


class PlanLabelsMenu(QToolButton):
    """The Step labels button: opens the panel that holds the (caption, control) rows it is given."""

    def __init__(self, rows: Sequence[tuple[str, QWidget]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        style_general_icon_button(self)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setToolTip(TIP)
        panel = QWidget(self)
        grid = QGridLayout(panel)
        grid.setContentsMargins(10, 8, 10, 8)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        self._captions: list[QLabel] = []
        for row, (caption, control) in enumerate(rows):
            label = QLabel(caption, panel)
            self._captions.append(label)
            grid.addWidget(label, row, 0)
            grid.addWidget(control, row, 1)  # reparents the control into the panel
        self._menu = QMenu(self)
        action = QWidgetAction(self._menu)
        action.setDefaultWidget(panel)
        self._menu.addAction(action)
        self.setMenu(self._menu)
        self.refresh_theme(get_active_theme())

    def refresh_theme(self, theme: GuiTheme) -> None:
        style_general_icon_button(self)
        self.setIcon(QIcon(position_picture("top")(theme.text_primary)))  # the picture of "bar along the top edge" (the Top choice of the placement menu)
