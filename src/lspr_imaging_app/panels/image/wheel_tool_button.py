"""A tool button that reports a turn of the mouse wheel over it as whole steps (2026-11-15: lifted out of `selection_toolbar.py`, where
the wand button and the mode button used it, so the "Select by value" button can too)."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import QToolButton, QWidget


class WheelToolButton(QToolButton):
    """Reports a turn of the mouse wheel over it as whole steps (`wheel_steps`: +1 / -1 per notch, scrolling up is -1: back through a
    list). A smooth wheel or a touch pad sends many small deltas: they are added up until a whole notch (120) is reached."""

    wheel_steps = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._wheel_delta = 0

    def wheelEvent(self, event: QWheelEvent) -> None:  # Qt's name
        self._wheel_delta += event.angleDelta().y()
        notches = int(self._wheel_delta / 120)
        if notches:
            self._wheel_delta -= notches * 120
            self.wheel_steps.emit(-notches)
        event.accept()
