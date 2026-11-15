"""The Mask tab's "Selection" group (2026-11-14): Add selection / Subtract selection, enabled while something is selected.

The logic is `panels/mask_selection_editor.py` (widget-free); this is only the two buttons.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QHBoxLayout, QToolButton, QWidget

from ...selection import AreaSelectionModule
from ..mask_edit_common import ADD_COLOR, SUBTRACT_COLOR, action_button
from ..mask_selection_editor import MaskSelectionEditor


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
        self.add_button.clicked.connect(lambda _checked=False: self._editor.apply(subtract=False))
        self.subtract_button.clicked.connect(lambda _checked=False: self._editor.apply(subtract=True))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.add_button)
        layout.addWidget(self.subtract_button)
        selection.selection_changed.connect(self._refresh)
        self._refresh()

    def _refresh(self) -> None:
        on = self._selection.has_selection()
        self.add_button.setEnabled(on)
        self.subtract_button.setEnabled(on)
