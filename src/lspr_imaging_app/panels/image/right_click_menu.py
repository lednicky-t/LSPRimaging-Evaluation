"""Right-click menus for the View controls (2026-11-21, maintainer request): an overlay's icon *is* its show / hide switch (a left click),
and a right-click opens a small menu with the rest of its controls (colour, transparency, ...).

Screen purpose: one way to attach a settings menu to an icon button, the same in both looks of the View controls (the ribbon's View tab and the
side ribbon right of the image, `view_tab.py`, `view_ribbon.py`).
Main user actions: a right-click on the button opens its menu; a left click does what the button always did (switch it), except for the buttons that
are only a menu opener (the Display range's "..."), which open it on a left click too.
Layout: `settings_menu` builds the menu's panel, a grid of one caption and one control per row (the look of the ROIs tab's "ROI display" menu).
The menu opens **below** the button in the ribbon's tab, and **beside it, to its left,** in the side ribbon (a column at the right edge of the image:
the menu opens over the image and covers none of the column's other buttons, as the selection toolbar's pop-ups do).
Important states: none of its own; `MenuOpener.set_beside` follows the look.
Validation / error messages: none.

The press is taken by an event filter, so the button itself never sees it: a checkable button would otherwise ignore a right-click anyway, but a
button with a menu of its own (a `QToolButton` in instant-popup mode) would open it below, over the next buttons of a column.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QEvent, QObject, QPoint, Qt
from PyQt6.QtWidgets import QGridLayout, QLabel, QMenu, QToolButton, QWidget, QWidgetAction

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

    The menu goes below the button, or beside it (to its left) after `set_beside(True)`. A second press on the button while its menu is open closes
    the menu, as with any menu: a press within `_REOPEN_GUARD_S` of the menu closing is the very click that closed it, and is ignored."""

    def __init__(self, button: QToolButton, menu: QMenu, *, left_click: bool = False, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._button = button
        self._menu = menu
        self._left_click = left_click
        self._beside = False
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

    def set_beside(self, beside: bool) -> None:
        """Beside the button (the side ribbon's column) or below it (the ribbon's tab)."""
        self._beside = bool(beside)

    def open(self) -> None:
        if time.monotonic() - self._hidden_at < _REOPEN_GUARD_S:
            return
        self._menu.ensurePolished()
        if self._beside:
            at = QPoint(-self._menu.sizeHint().width(), 0)
        else:
            at = QPoint(0, self._button.height())
        self._menu.popup(self._button.mapToGlobal(at))

    def _note_hidden(self) -> None:
        self._hidden_at = time.monotonic()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore[override]
        if watched is self._button and event.type() == QEvent.Type.MouseButtonPress and self._button.isEnabled():
            pressed = event.button()  # type: ignore[attr-defined]
            if pressed == Qt.MouseButton.RightButton or (self._left_click and pressed == Qt.MouseButton.LeftButton):
                self.open()
                return True
        return False
