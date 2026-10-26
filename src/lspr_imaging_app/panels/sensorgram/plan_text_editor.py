"""``StepTextEditor``: the one-line editor that opens over a step of the plan bar when its text is double-clicked (2026-10-26).

Screen purpose: change the note of one step. It floats over the plot (a child of the plot's viewport, as the Baseline range box is), shows the **whole** note even when the bar shows only the
solution, and keeps nothing: Enter or leaving the field commits, Escape cancels. ``committed(index, text)`` is emitted only when the text changed; an empty text means "back to the measured
note" (the module reads it so). Display only: the plot passes the text on, the panel's actions turn it into the module command.
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QKeyEvent
from PyQt6.QtWidgets import QLineEdit, QWidget

from lspr_ui import GuiTheme, get_active_theme

_MIN_WIDTH_PX = 180
_PAD_PX = 8  # beyond the text height


class StepTextEditor(QLineEdit):
    committed = pyqtSignal(int, str)  # (step index, new text)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("planStepTextEditor")
        self._index = -1
        self._initial = ""
        self._open = False
        self.hide()
        self.editingFinished.connect(self._on_finished)
        self.refresh_theme(get_active_theme())

    def is_open(self) -> bool:
        return self._open

    def open_over(self, index: int, text: str, rect: QRectF | None, font: QFont, bounds: QRectF) -> None:
        """Open for step `index` showing `text`, over `rect` (its bar, in the parent's pixels; None: the middle of `bounds`), never wider than `bounds`."""
        if self._open:
            self._close(commit=True)
        self._index, self._initial, self._open = index, text, True
        self.setFont(font)
        self.setText(text)
        height = self.fontMetrics().height() + _PAD_PX
        if rect is None:
            rect = QRectF(bounds.center().x() - _MIN_WIDTH_PX / 2.0, bounds.center().y() - height / 2.0, _MIN_WIDTH_PX, height)
        width = min(max(rect.width(), float(_MIN_WIDTH_PX)), bounds.width())
        left = min(max(rect.left(), bounds.left()), max(bounds.right() - width, bounds.left()))
        top = min(max(rect.top() + (rect.height() - height) / 2.0, bounds.top()), max(bounds.bottom() - height, bounds.top()))
        self.setGeometry(int(left), int(top), int(width), int(height))
        self.show()
        self.raise_()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.selectAll()

    def close_without_saving(self) -> None:
        if self._open:
            self._close(commit=False)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt naming
        if event.key() == Qt.Key.Key_Escape:
            self._close(commit=False)
            event.accept()
            return
        super().keyPressEvent(event)

    def _on_finished(self) -> None:
        """Enter, or the field lost the focus."""
        if self._open:
            self._close(commit=True)

    def _close(self, commit: bool) -> None:
        text, index = self.text(), self._index
        self._open = False  # first: hiding the field fires editingFinished again
        self.hide()
        if commit and text != self._initial:
            self.committed.emit(index, text)

    def refresh_theme(self, theme: GuiTheme) -> None:
        self.setStyleSheet(
            f"#planStepTextEditor {{ color: {theme.text_primary}; background: {theme.toolbar_section_bg}; border: 1px solid {theme.accent_blue}; "
            f"border-radius: 3px; padding: 0px 4px; }}"
        )
