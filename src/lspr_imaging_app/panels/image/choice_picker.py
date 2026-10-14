"""A few icon buttons side by side, exactly one picked: a drop-down without the drop-down.

Used where a control has two or three named choices that read better as pictures than as a list (the Groups
label menu: first / last ROI, text direction). Each choice has a string value and a tooltip; the picture comes
from ``icon_for(value, picked, theme)`` so the owner can draw it in the picked or the dim colour.

Display only: it holds the pick and reports it. ``set_value(.., emit=False)`` shows a value without reporting
it (a copy following the original, a restored value). Same shape as `label_placement_picker.py`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PyQt6.QtCore import QSize, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QButtonGroup, QHBoxLayout, QToolButton, QWidget

from lspr_ui import GuiTheme, get_active_theme, transparent_icon_button_stylesheet

from ..ui_state import UiStateStore

IconFor = Callable[[str, bool, GuiTheme], QIcon]


class ChoicePicker(QWidget):
    value_changed = pyqtSignal(str)

    def __init__(
        self,
        choices: Sequence[tuple[str, str]],
        value: str,
        icon_for: IconFor,
        *,
        button_size: int = 32,
        parent: QWidget | None = None,
    ) -> None:
        """``choices`` is ``(value, tooltip)`` per button, left to right; ``value`` is the one picked first."""
        super().__init__(parent)
        values = [choice for choice, _tip in choices]
        if value not in values:
            raise ValueError(f"value must be one of {values}, got {value!r}")
        self._icon_for = icon_for
        self._buttons: dict[str, QToolButton] = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        for choice, tip in choices:
            button = QToolButton(self)
            button.setCheckable(True)
            button.setAutoRaise(True)
            button.setFixedSize(button_size, button_size)
            button.setIconSize(QSize(button_size - 2, button_size - 2))
            button.setToolTip(tip)
            button.setChecked(choice == value)
            button.toggled.connect(lambda checked, picked=choice: self._on_toggled(picked, checked))
            self._group.addButton(button)
            layout.addWidget(button)
            self._buttons[choice] = button
        self.refresh_theme(get_active_theme())

    def value(self) -> str:
        return next(name for name, button in self._buttons.items() if button.isChecked())

    def button(self, value: str) -> QToolButton:
        """The button for ``value`` (``.click()`` picks it, as a mouse click would)."""
        return self._buttons[value]

    def set_value(self, value: str, *, emit: bool = True) -> None:
        """Pick ``value``. ``emit=False`` shows it without reporting a change."""
        if value not in self._buttons:
            raise ValueError(f"value must be one of {list(self._buttons)}, got {value!r}")
        if value == self.value():
            return
        if emit:
            self._buttons[value].setChecked(True)
            return
        blocked = [button.blockSignals(True) for button in self._buttons.values()]
        try:
            self._buttons[value].setChecked(True)
        finally:
            for button, was_blocked in zip(self._buttons.values(), blocked, strict=True):
                button.blockSignals(was_blocked)
        self._refresh_icons(get_active_theme())  # the blocked toggles did not redraw the pictures

    def sync_from(self, other: ChoicePicker) -> None:
        """Show another picker's pick without reporting it (the same control sits on two ribbon tabs)."""
        self.set_value(other.value(), emit=False)

    def bind_ui_state(self, store: UiStateStore, key: str) -> None:
        """Put the pick back as last left (a saved value that is not a choice is ignored), then save each change."""
        saved = store.get(key)
        if isinstance(saved, str) and saved in self._buttons:
            self.set_value(saved)
        self.value_changed.connect(lambda value: store.set(key, value))

    def _on_toggled(self, value: str, checked: bool) -> None:
        self._refresh_icons(get_active_theme())
        if checked:  # the button that was unchecked by this pick reports `False`; one pick is one signal
            self.value_changed.emit(value)

    def _refresh_icons(self, theme: GuiTheme) -> None:
        for name, button in self._buttons.items():
            button.setIcon(self._icon_for(name, button.isChecked(), theme))

    def refresh_theme(self, theme: GuiTheme) -> None:
        style = transparent_icon_button_stylesheet(hover=theme.control_bg_hover, checked=theme.control_bg)
        for button in self._buttons.values():
            button.setStyleSheet(style)
        self._refresh_icons(theme)
