"""Keeps a pop-out panel (a child widget of the Image panel) sized to its content and just under its button, inside the window.

The Array and Find* pop-outs use it (the Array one carried its own copy until the 2026-11-25 maintenance pass). Qt re-lays-out a widget in a later event, but the size is
needed now, so the inner layouts are updated first and once more when the event loop has polished the widgets (fonts and paddings change the row heights).
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, QPoint, QTimer
from PyQt6.QtWidgets import QWidget


class PopoutPlacer(QObject):
    def __init__(self, popup: QWidget, panel: QWidget, button: QWidget, host: QWidget, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._popup, self._panel, self._button, self._host = popup, panel, button, host
        self._active = False
        host.installEventFilter(self)  # a resized window keeps the pop-out inside it

    def set_active(self, active: bool) -> None:
        self._active = bool(active)

    def fit(self) -> None:
        """Grow or shrink to the content and put the pop-out where it belongs (now, and once more after the event loop has settled the widgets)."""
        self._relayout()
        self._position()
        QTimer.singleShot(0, self._settle)

    def _relayout(self) -> None:
        for layout in (self._panel.layout(), self._popup.layout()):
            if layout is not None:
                layout.invalidate()
                layout.activate()

    def _settle(self) -> None:
        if self._active:
            self._relayout()
            self._position()

    def _position(self) -> None:
        self._popup.adjustSize()
        spot = self._button.mapTo(self._host, QPoint(0, self._button.height() + 4))
        x = max(4, min(spot.x(), self._host.width() - self._popup.width() - 4))
        self._popup.move(x, spot.y())

    def eventFilter(self, watched: QObject | None, event: QEvent | None) -> bool:
        if getattr(self, "_active", False) and watched is self._host and event is not None and event.type() == QEvent.Type.Resize:
            self._position()
        return False
