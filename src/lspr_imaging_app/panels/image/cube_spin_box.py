"""``CubeSpinBox`` - the Image panel's cube number field, which can show a
cube's start time instead of its number (the app-wide cube display unit,
`selection/cube_display_module.py`).

Only the *text* changes; the stored value, range and stepping stay the cube
index. While the field shows times it is read-only (a typed "0:12:30" has no
meaning as a cube number) - switch the unit back to type a number.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtWidgets import QSpinBox, QWidget


class CubeSpinBox(QSpinBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._label_for: Callable[[int], str] | None = None

    def set_label_provider(self, label_for: Callable[[int], str]) -> None:
        self._label_for = label_for

    def textFromValue(self, value: int) -> str:  # noqa: N802 - Qt override
        return self._label_for(value) if self._label_for is not None else str(value)

    def refresh_display(self) -> None:
        """Re-render the text after the unit changed (the value is untouched)."""
        text = self.textFromValue(self.value())
        line_edit = self.lineEdit()
        line_edit.setReadOnly(text != str(self.value()))
        line_edit.setText(text)
