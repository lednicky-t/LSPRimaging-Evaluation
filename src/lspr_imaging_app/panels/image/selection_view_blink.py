"""The View (eye) button of the selection toolbar says "a selection is applied" (2026-11-23).

**While there is an area selection the eye blinks without stopping** between its plain icon and a lit one in the selection's colour (maintainer,
2026-11-23: first three blinks then steady, changed to nonstop). Without a selection it is the plain icon and the timer is stopped, so nothing runs
when there is nothing to show. `INTERVAL_MS` is one lit or one plain phase.

A change of the selection (a brush stroke, a new shape) lights the eye at once and keeps the rhythm going, so it never flashes plain in the middle
of an edit.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QAbstractButton

from ...selection import AreaSelectionModule

INTERVAL_MS = 500  # one lit or one plain phase (a full blink is twice this)


class ViewButtonBlink(QObject):
    def __init__(self, button: QAbstractButton, selection: AreaSelectionModule, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._button = button
        self._selection = selection
        self._plain: QIcon | None = None
        self._lit: QIcon | None = None
        self._lit_now = False  # which face the button wears while blinking
        self._timer = QTimer(self)
        self._timer.setInterval(INTERVAL_MS)
        self._timer.timeout.connect(self._tick)
        selection.selection_changed.connect(self._on_selection_changed)

    def set_icons(self, plain: QIcon, lit: QIcon) -> None:
        """The two faces (the toolbar builds them again on a theme or colour change)."""
        self._plain, self._lit = plain, lit
        self._apply(self._lit_now if self._timer.isActive() else False)

    def is_blinking(self) -> bool:
        return self._timer.isActive()

    def _apply(self, lit: bool) -> None:
        self._lit_now = lit
        icon = self._lit if lit else self._plain
        if icon is not None:
            self._button.setIcon(icon)

    def _on_selection_changed(self) -> None:
        if not self._selection.has_selection():
            self._timer.stop()
            self._apply(False)
            return
        self._apply(True)
        self._timer.start()  # (re)starts the phase: lit for a whole interval after every change

    def _tick(self) -> None:
        self._apply(not self._lit_now)
