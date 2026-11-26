"""The ribbons' icon-button look, shared by every panel.

Moved out of `image/general_group.py` (2026-11-02), which still re-exports it: panels outside the Image panel (Sensorgram, ROI table, the
Live and unit toggles) imported it from there, which loaded the whole Image package and closed an import cycle
(`sensorgram` -> `live_toggle` -> `image` -> `analysis_tab` -> `live_toggle`).
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, QSize, Qt
from PyQt6.QtWidgets import QToolButton

from lspr_ui import transparent_icon_button_stylesheet

from .ribbon_icon_size import BUTTON_SIZE, ICON_SIZE


class _RightClickMenu(QObject):
    """Makes a menu button open its menu on a right click too (a left click keeps opening it, as Qt does for an instant-popup button)."""

    def __init__(self, button: QToolButton) -> None:
        super().__init__(button)
        self._button = button
        button.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore[override]
        button = self._button
        if (
            watched is button
            and event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.RightButton  # type: ignore[attr-defined]
            and button.isEnabled()
            and button.menu() is not None
        ):
            button.showMenu()  # below the button, exactly where the left click opens it
            return True
        return False


def open_menu_on_right_click(button: QToolButton) -> None:
    """A right click on `button` opens its menu (once; calling it again changes nothing). The rule of the app: the right-click is the way to a settings menu, because a
    left click is often the tool itself. Reads `button.menu()` at the click, so it may be called before `setMenu`. A button without a menu ignores it."""
    if button.findChild(_RightClickMenu) is None:
        _RightClickMenu(button)


def ribbon_icon_button_stylesheet() -> str:
    """The ribbon icon button's style sheet. An icon that opens a menu needs no drop-down arrow (the click opens it); Qt draws one on a
    menu button unless told not to."""
    return transparent_icon_button_stylesheet() + " QToolButton::menu-indicator { image: none; width: 0px; }"


def style_general_icon_button(button: QToolButton) -> None:
    """The ribbon's icon-button look (`BUTTON_SIZE`), for any icon button of a ribbon."""
    button.setAutoRaise(True)
    button.setFixedSize(BUTTON_SIZE, BUTTON_SIZE)
    button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
    button.setStyleSheet(ribbon_icon_button_stylesheet())
    open_menu_on_right_click(button)
