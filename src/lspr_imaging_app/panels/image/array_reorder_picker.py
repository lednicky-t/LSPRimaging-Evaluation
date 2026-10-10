"""The Array group's "Reorder IDs" pick-up menu: number the ROIs by rows or by columns of the array.

The icon is the stable app's (Tabler "sort-ascending-numbers"; for "by rows" only the arrow is turned to point right, the 0 stands above its tail and the 9 at its tip).
Picking an entry only emits `reorder_requested`; `ArrayActions` does the work.
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QIcon, QPainter, QPixmap
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
    # "By rows": the arrow lies horizontally, the first digit (0) stands above its tail, the last (9) above its tip, both at the same height.
    source = icon.pixmap(_RENDER_SIZE, _RENDER_SIZE)
    u = _RENDER_SIZE / 24  # the Tabler icon is drawn on a 24-unit grid
    pixmap = QPixmap(_RENDER_SIZE, _RENDER_SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.drawPixmap(QRectF(3 * u, 2 * u, 6 * u, 10 * u), source, QRectF(14 * u, 2 * u, 6 * u, 10 * u))  # the 0, above the tail
    painter.drawPixmap(QRectF(14 * u, 2 * u, 6 * u, 10 * u), source, QRectF(14 * u, 12.5 * u, 6 * u, 10 * u))  # the 9, same height, above the tip
    painter.translate(11.5 * u, 16 * u)  # the arrow (left half of the icon), turned about its own centre
    painter.rotate(degrees)
    painter.scale(1.2, 1.2)
    painter.translate(-7 * u, -12 * u)
    painter.setClipRect(QRectF(0, 0, 12 * u, _RENDER_SIZE))
    painter.drawPixmap(0, 0, source)
    painter.end()
    return QIcon(pixmap)


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
        self.setIcon(_icon(theme.text_primary, 0.0))
