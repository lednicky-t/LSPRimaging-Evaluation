from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent


class ShortcutManager:
    def __init__(self, window) -> None:
        self.window = window

    def handle_page_shortcuts(self, event: QKeyEvent) -> bool:
        if event.key() not in {Qt.Key.Key_PageUp, Qt.Key.Key_PageDown}:
            return False
        direction = -1 if event.key() == Qt.Key.Key_PageUp else 1
        modifiers = event.modifiers()
        if (modifiers & Qt.KeyboardModifier.ControlModifier) and (modifiers & Qt.KeyboardModifier.ShiftModifier):
            if self.window._navigate_frame_image(direction):
                event.accept()
                return True
            return False
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            if self.window._navigate_wavelength_image(direction):
                event.accept()
                return True
            return False
        if self.window._active_tool == "chromatic_landmark":
            if self.window._navigate_chromatic_sample(direction):
                event.accept()
                return True
            if self.window._switch_chromatic_feature(direction):
                event.accept()
                return True
        return False
