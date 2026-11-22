"""Right-click menus for the View controls (2026-11-21, maintainer request): an overlay's icon *is* its show / hide switch (a left click),
and a right-click opens a small menu with the rest of its controls (colour, transparency, ...).

Screen purpose: one way to attach a settings menu to an icon button of the View ribbon (the column beside the image, `view_tab.py`, `view_ribbon.py`).
Main user actions: a right-click on the button opens its menu; a left click does what the button always did (switch it), except for the buttons that
are only a menu opener (the Display range's "..."), which open it on a left click too.
Layout: `settings_menu` builds the menu's panel, a grid of one caption and one control per row (the look of the ROIs tab's "ROI display" menu).
The menu opens **beside** the button, over the image (to its left for a column on the right of the image, to its right for one on the left, since the
column can be docked on either side, `side_rails.py`): it covers none of the column's other buttons, as the selection toolbar's pop-ups do.
Important states: none of its own; `MenuOpener.set_side` follows the side the column stands on.
Validation / error messages: none.

The press is taken by an event filter, so the button itself never sees it: a checkable button would otherwise ignore a right-click anyway, but a
button with a menu of its own (a `QToolButton` in instant-popup mode) would open it below, over the next buttons of a column.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtWidgets import QGridLayout, QLabel, QMenu, QToolButton, QWidget, QWidgetAction

from .side_rails import RIGHT, beside_point, valid_side

_REOPEN_GUARD_S = 0.25  # a press this soon after the menu closed is the click that closed it (Qt hands it to the button again)


def settings_menu(parent: QWidget, rows: list[tuple[str, QWidget]]) -> QMenu:
    """A menu holding one captioned row per ``(caption, control)``. The controls are moved into the menu (reparented): they are the same widgets
    as before, so every one behaves exactly as it did."""
    panel = QWidget(parent)
    grid = QGridLayout(panel)
    grid.setContentsMargins(10, 8, 10, 8)
    grid.setHorizontalSpacing(10)
    grid.setVerticalSpacing(6)
    for row, (caption, control) in enumerate(rows):
        grid.addWidget(QLabel(caption, panel), row, 0)
        grid.addWidget(control, row, 1)
    menu = QMenu(parent)
    action = QWidgetAction(menu)
    action.setDefaultWidget(panel)
    menu.addAction(action)
    return menu


class MenuOpener(QObject):
    """Opens `menu` at `button` when the button is pressed with the right mouse button, and with the left one too if `left_click`.

    The menu goes beside the button, over the image: to its left for a column on the right of the image (the default), to its right after
    `set_side(LEFT)`. A second press on the button while its menu is open closes the menu, as with any menu: a press within `_REOPEN_GUARD_S` of
    the menu closing is the very click that closed it, and is ignored."""

    def __init__(
        self, button: QToolButton, menu: QMenu, *, left_click: bool = False, also_disabled: bool = False, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._button = button
        self._menu = menu
        self._left_click = left_click
        self._also_disabled = also_disabled  # a greyed-out button still opens its menu (the chunk icon of a TIFF stack: its menu is not about the stack)
        self._side = RIGHT  # the side of the image the button's column stands on
        self._hidden_at = float("-inf")
        menu.aboutToHide.connect(self._note_hidden)
        button.installEventFilter(self)

    @property
    def button(self) -> QToolButton:
        return self._button

    @property
    def menu(self) -> QMenu:
        return self._menu

    @property
    def left_click(self) -> bool:
        return self._left_click

    @property
    def also_disabled(self) -> bool:
        return self._also_disabled

    def set_side(self, side: str) -> None:
        """The side of the image the button's column stands on: the menu opens to the left of the button for a column on the right and to its right
        for one on the left, so it always opens over the image."""
        self._side = valid_side(side, self._side)

    def open(self) -> None:
        if time.monotonic() - self._hidden_at < _REOPEN_GUARD_S:
            return
        self._menu.ensurePolished()
        self._menu.popup(self._button.mapToGlobal(beside_point(self._button, self._side, self._menu.sizeHint().width())))

    def _note_hidden(self) -> None:
        self._hidden_at = time.monotonic()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore[override]
        if watched is self._button and event.type() == QEvent.Type.MouseButtonPress and (self._also_disabled or self._button.isEnabled()):
            pressed = event.button()  # type: ignore[attr-defined]
            if pressed == Qt.MouseButton.RightButton or (self._left_click and pressed == Qt.MouseButton.LeftButton):
                self.open()
                return True
        return False
