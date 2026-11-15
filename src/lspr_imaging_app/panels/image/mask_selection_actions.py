"""The Mask tab's "Selection" group (2026-11-14): Add selection / Subtract selection, enabled while something is selected. 2026-11-15:
Replace (the mask := the selection) and Selection from mask (the mask at this frame becomes the selection), the two that let the
selection tools work on an existing mask.

The logic is `panels/mask_selection_editor.py` (widget-free); this is only the four buttons.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QHBoxLayout, QToolButton, QWidget

from ...selection import AreaSelectionModule
from ..mask_edit_common import ADD_COLOR, SUBTRACT_COLOR, action_button
from ..mask_selection_editor import MaskSelectionEditor

_PLAIN_COLOR = "#f8fafc"  # GuiTheme.text_primary, like the other real, active actions of this ribbon


class MaskSelectionActions(QWidget):
    def __init__(self, editor: MaskSelectionEditor, selection: AreaSelectionModule, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._editor = editor
        self._selection = selection
        self.add_button: QToolButton = action_button(
            self, "square-rounded-plus", ADD_COLOR,
            "Add the selection to the mask (at the State group's Persistent / Individual scope; Ctrl+Z undoes it).",
        )
        self.subtract_button: QToolButton = action_button(
            self, "square-rounded-minus", SUBTRACT_COLOR,
            "Take the selection out of the mask (at the State group's Persistent / Individual scope; Ctrl+Z undoes it).",
        )
        self.replace_button: QToolButton = action_button(
            self, "replace", _PLAIN_COLOR,
            "Replace the mask at this frame by the selection (at the State group's scope; Ctrl+Z undoes it). With nothing selected nothing happens.",
        )
        self.from_mask_button: QToolButton = action_button(
            self, "arrow-bar-up", _PLAIN_COLOR,
            "Selection from mask: the mask at this frame becomes the selection (the mode button, or Shift / Alt, say how it combines with "
            "the current one). Then Modify selection, then Replace.",
        )
        self.replace_button.clicked.connect(lambda _checked=False: self._editor.replace())
        self.from_mask_button.clicked.connect(lambda _checked=False: self._editor.from_mask())
        self.add_button.clicked.connect(lambda _checked=False: self._editor.apply(subtract=False))
        self.subtract_button.clicked.connect(lambda _checked=False: self._editor.apply(subtract=True))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.add_button)
        layout.addWidget(self.subtract_button)
        layout.addWidget(self.replace_button)
        layout.addWidget(self.from_mask_button)
        selection.selection_changed.connect(self._refresh)
        self._refresh()

    def _refresh(self) -> None:
        on = self._selection.has_selection()
        self.add_button.setEnabled(on)
        self.subtract_button.setEnabled(on)
        self.replace_button.setEnabled(on)
