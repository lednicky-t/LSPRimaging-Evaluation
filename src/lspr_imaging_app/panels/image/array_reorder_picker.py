"""The Array group's "Reorder IDs" pick-up menu: number the ROIs by rows or by columns of the array.

The icon is the stable app's (Tabler "sort-ascending-numbers"; turned 270 degrees for "by rows", as there).
Picking an entry only emits `reorder_requested`; `ArrayActions` does the work.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction, QIcon, QTransform
from PyQt6.QtWidgets import QMenu, QToolButton, QWidget

from lspr_ui import get_active_theme, load_tabler_icon

from .general_group import ICON_SIZE, style_general_icon_button

_RENDER_SIZE = ICON_SIZE * 2
TIP = "Reorder the ROI numbers like an array, always starting at the top-left ROI. Works on the selected ROIs, or on all if none are selected."
_ENTRIES = (
    ("rows", "By rows: left to right, top row first", 270.0),
    ("columns", "By columns: top to bottom, left column first", 0.0),
)


def _icon(color: str, degrees: float) -> QIcon:
    icon = load_tabler_icon("sort-ascending-numbers", color=color, size=_RENDER_SIZE, stroke_width=2.1)
    if not degrees:
        return icon
    pixmap = icon.pixmap(_RENDER_SIZE, _RENDER_SIZE)
    return QIcon(pixmap.transformed(QTransform().rotate(degrees), Qt.TransformationMode.SmoothTransformation))


class ArrayReorderPicker(QToolButton):
    reorder_requested = pyqtSignal(str)  # "rows" | "columns"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        style_general_icon_button(self)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setToolTip(TIP)
        self._menu = QMenu(self)
        self._actions: dict[str, QAction] = {}
        for key, text, _degrees in _ENTRIES:
            action = QAction(text, self._menu)
            action.triggered.connect(lambda _checked=False, picked=key: self.reorder_requested.emit(picked))
            self._menu.addAction(action)
            self._actions[key] = action
        self.setMenu(self._menu)
        self.refresh_theme()

    def refresh_theme(self) -> None:
        style_general_icon_button(self)
        theme = get_active_theme()
        for key, _text, degrees in _ENTRIES:
            self._actions[key].setIcon(_icon(theme.text_primary, degrees))
        self.setIcon(_icon(theme.accent_blue, 0.0))
