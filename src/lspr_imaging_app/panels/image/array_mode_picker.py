"""The Array group's mode picker: Auto / Semi / Manual as an icon pick-up menu.

The button face is the icon of the current mode: a car (Auto: drives itself), half a car
(Semi: only half automatic) and a walking pedestrian (Manual: you do it on foot). Same
design as the other pick-up menus of this ribbon (`MaskEditToolPicker`): an instant-popup
menu with a checkmark on the current entry. Real `QAction`s, so tests can `.trigger()` them.
"""

from __future__ import annotations

from PyQt6.QtCore import QRect, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QMenu, QToolButton, QWidget

from lspr_ui import get_active_theme, load_tabler_icon

from .general_group import ICON_SIZE, style_general_icon_button

MODES = (("auto", "Auto"), ("semi", "Semi"), ("manual", "Manual"))
_UNFINISHED = {"semi", "manual"}  # still being built: usable, but marked
_RENDER_SIZE = ICON_SIZE * 2
_STROKE_WIDTH = 2.1


def _mode_icon(mode: str, color: str) -> QIcon:
    if mode == "manual":
        return load_tabler_icon("walk", color=color, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH)
    car = load_tabler_icon("car", color=color, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH)
    if mode == "auto":
        return car
    pixmap = QPixmap(_RENDER_SIZE, _RENDER_SIZE)  # semi: the left half of the car only
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setClipRect(QRect(0, 0, _RENDER_SIZE // 2, _RENDER_SIZE))
    painter.drawPixmap(0, 0, car.pixmap(_RENDER_SIZE, _RENDER_SIZE))
    painter.end()
    return QIcon(pixmap)


class ArrayModePicker(QToolButton):
    mode_changed = pyqtSignal(str)  # "auto" | "semi" | "manual"

    def __init__(self, tips: dict[str, str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tips = tips
        self._mode = MODES[0][0]
        style_general_icon_button(self)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._menu = QMenu(self)
        group = QActionGroup(self)
        self._actions: dict[str, QAction] = {}
        for key, text in MODES:
            action = QAction(f"{text} (not finished yet)" if key in _UNFINISHED else text, group)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, picked=key: self.set_mode(picked))
            self._menu.addAction(action)
            self._actions[key] = action
        self.setMenu(self._menu)
        self.refresh_theme()

    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        """Pick a mode (unknown names are ignored); emits only when it changes."""
        if mode not in self._actions or mode == self._mode:
            self._refresh()
            return
        self._mode = mode
        self._refresh()
        self.mode_changed.emit(mode)

    def refresh_theme(self) -> None:
        style_general_icon_button(self)
        theme = get_active_theme()
        for key, action in self._actions.items():
            action.setIcon(_mode_icon(key, theme.text_primary))
        self._refresh()

    def _refresh(self) -> None:
        self._actions[self._mode].setChecked(True)
        self.setIcon(_mode_icon(self._mode, get_active_theme().accent_blue))
        note = "(Not finished yet.) " if self._mode in _UNFINISHED else ""
        self.setToolTip(note + self._tips[self._mode])
