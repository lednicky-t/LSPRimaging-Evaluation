"""A 3 x 3 grid of cells, one of them checked: where a label sits around its ROI.

Used for the ROI labels (ROIs tab, Display menu) and the group labels (Groups label menu), and by the View tab's
copies of both. The middle cell (drawn round, like the ROI it stands for) centres the label on the ROI; the eight
squares around it put the label just outside the ROI's circle on that side or corner (geometry:
`label_placement.py`).

Display only: it holds the pick and reports it. `set_placement(..., emit=False)` shows a value without reporting
it, which is how a copy follows the original and how a restored value is shown.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QButtonGroup, QGridLayout, QToolButton, QWidget

from lspr_ui import GuiTheme, get_active_theme

from ..ui_state import UiStateStore
from .label_placement import PLACEMENTS

_CELL_SIZE = 9  # was 12 (the whole grid 40 px, now 31 px)
_SPACING = 2
_TIPS = {
    "top-left": "Label off the top-left corner of the ROI.",
    "top": "Label above the ROI.",
    "top-right": "Label off the top-right corner of the ROI.",
    "left": "Label to the left of the ROI.",
    "center": "Label centred on the ROI.",
    "right": "Label to the right of the ROI.",
    "bottom-left": "Label off the bottom-left corner of the ROI.",
    "bottom": "Label below the ROI.",
    "bottom-right": "Label off the bottom-right corner of the ROI.",
}


class LabelPlacementPicker(QWidget):
    placement_changed = pyqtSignal(str)

    def __init__(self, placement: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if placement not in PLACEMENTS:
            raise ValueError(f"placement must be one of {PLACEMENTS}, got {placement!r}")
        self._buttons: dict[str, QToolButton] = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(_SPACING)
        for index, name in enumerate(PLACEMENTS):
            button = QToolButton(self)
            button.setCheckable(True)
            button.setAutoRaise(False)
            button.setFixedSize(_CELL_SIZE, _CELL_SIZE)
            button.setToolTip(_TIPS[name])
            button.setChecked(name == placement)
            button.toggled.connect(lambda checked, picked=name: self._on_toggled(picked, checked))
            self._group.addButton(button)
            grid.addWidget(button, index // 3, index % 3)
            self._buttons[name] = button
        self.refresh_theme(get_active_theme())

    def placement(self) -> str:
        return next(name for name, button in self._buttons.items() if button.isChecked())

    def button(self, placement: str) -> QToolButton:
        """The cell for ``placement`` (``.click()`` picks it, as a mouse click would)."""
        return self._buttons[placement]

    def set_placement(self, placement: str, *, emit: bool = True) -> None:
        """Check the cell for ``placement``. ``emit=False`` shows it without reporting a change."""
        if placement not in self._buttons:
            raise ValueError(f"placement must be one of {PLACEMENTS}, got {placement!r}")
        if placement == self.placement():
            return
        if emit:
            self._buttons[placement].setChecked(True)
            return
        blocked = [button.blockSignals(True) for button in self._buttons.values()]
        try:
            self._buttons[placement].setChecked(True)
        finally:
            for button, was_blocked in zip(self._buttons.values(), blocked, strict=True):
                button.blockSignals(was_blocked)

    def sync_from(self, other: LabelPlacementPicker) -> None:
        """Show another picker's pick without reporting it (the same control sits on two ribbon tabs)."""
        self.set_placement(other.placement(), emit=False)

    def bind_ui_state(self, store: UiStateStore, key: str) -> None:
        """Put the pick back as last left (a saved value that is not a placement is ignored), then save each change."""
        saved = store.get(key)
        if isinstance(saved, str) and saved in self._buttons:
            self.set_placement(saved)
        self.placement_changed.connect(lambda placement: store.set(key, placement))

    def _on_toggled(self, placement: str, checked: bool) -> None:
        if checked:  # the cell that was unchecked by this pick reports `False`; one pick is one signal
            self.placement_changed.emit(placement)

    def refresh_theme(self, theme: GuiTheme) -> None:
        radius = _CELL_SIZE // 2
        for name, button in self._buttons.items():
            button.setStyleSheet(
                f"QToolButton {{ background-color: {theme.control_bg}; border: 1px solid {theme.control_border}; "
                f"border-radius: {radius if name == 'center' else 2}px; padding: 0; }}"
                f"QToolButton:hover {{ border-color: {theme.control_border_hover}; }}"
                f"QToolButton:checked {{ background-color: {theme.accent_blue}; border-color: {theme.accent_blue}; }}"
            )
