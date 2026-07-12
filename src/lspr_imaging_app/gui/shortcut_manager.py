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
            if self.window._navigate_spectral_cube_image(direction):
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

    def handle_key_press(self, event: QKeyEvent) -> bool:
        """Returns True if the key was handled (caller should not fall through
        to the base QWidget.keyPressEvent)."""
        window = self.window
        if self.handle_page_shortcuts(event):
            return True

        if window._active_tool == "chromatic_landmark":
            if event.key() in {Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down}:
                step = 1.0
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    step = 5.0
                dx = 0.0
                dy = 0.0
                if event.key() == Qt.Key.Key_Left:
                    dx = -step
                elif event.key() == Qt.Key.Key_Right:
                    dx = step
                elif event.key() == Qt.Key.Key_Up:
                    dy = -step
                elif event.key() == Qt.Key.Key_Down:
                    dy = step
                if window._move_selected_landmark(dx, dy):
                    event.accept()
                    return True

        if window._active_tool == "roi" and event.key() in {Qt.Key.Key_PageUp, Qt.Key.Key_PageDown}:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                direction = -1 if event.key() == Qt.Key.Key_PageUp else 1
                if window._navigate_wavelength_image(direction):
                    event.accept()
                    return True

        if window._active_tool == "rotate" and event.key() in {
            Qt.Key.Key_Left,
            Qt.Key.Key_Right,
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
        }:
            step = 0.1
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                step = 5.0
            elif event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                step = 1.0

            if event.key() in {Qt.Key.Key_Left, Qt.Key.Key_Down}:
                step *= -1.0
            window._adjust_rotation(step)
            event.accept()
            return True
        if window._active_tool == "roi" and window._roi_editor_mode == "rectangles":
            if event.key() in {Qt.Key.Key_Delete, Qt.Key.Key_Backspace}:
                window._remove_selected_rectangle_rois()
                event.accept()
                return True
            if event.key() in {
                Qt.Key.Key_Left,
                Qt.Key.Key_Right,
                Qt.Key.Key_Up,
                Qt.Key.Key_Down,
            } and window.roi_move_action.isChecked() and window._selected_rectangle_roi_ids:
                step = 1.0
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    step = 5.0
                dx = 0.0
                dy = 0.0
                if event.key() == Qt.Key.Key_Left:
                    dx = -step
                elif event.key() == Qt.Key.Key_Right:
                    dx = step
                elif event.key() == Qt.Key.Key_Up:
                    dy = -step
                elif event.key() == Qt.Key.Key_Down:
                    dy = step
                window._move_selected_rectangle_rois(dx, dy)
                event.accept()
                return True
        if window._active_tool == "roi" and event.key() in {
            Qt.Key.Key_Left,
            Qt.Key.Key_Right,
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
        }:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                if window._select_neighbor_spot(event.key()):
                    event.accept()
                    return True
            elif window._is_current_reference_image() and window.roi_move_action.isChecked() and window._selected_roi_ids:
                step = 1.0
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    step = 5.0
                dx = 0.0
                dy = 0.0
                if event.key() == Qt.Key.Key_Left:
                    dx = -step
                elif event.key() == Qt.Key.Key_Right:
                    dx = step
                elif event.key() == Qt.Key.Key_Up:
                    dy = -step
                elif event.key() == Qt.Key.Key_Down:
                    dy = step
                window._move_selected_rois(dx, dy)
                event.accept()
                return True
        return False
