"""Persistent / Individual ROI-edit scope toggle (ROIs ribbon tab): a two-button icon pair on the shared `RoiScopeModule`.

Same look as the Mask's (`mask_scope_toggle.py`). Holds no scope state of its own.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import QButtonGroup, QHBoxLayout, QToolButton, QWidget

from lspr_ui import GuiTheme, get_active_theme, load_tabler_icon, transparent_icon_button_stylesheet

from ...roi.scope import SCOPE_INDIVIDUAL, SCOPE_PERSISTENT, RoiScopeModule

_BUTTON_SIZE = 28
_ICON_SIZE = 22
_ACTIVE_COLOR = "#38bdf8"

_ICON = {SCOPE_PERSISTENT: "stack-3", SCOPE_INDIVIDUAL: "focus-2"}
_TIP = {
    SCOPE_PERSISTENT: (
        "Persistent scope: a ROI edit (move, resize, Array refine) applies to this cube and every cube after it, until a "
        "later edit. Editing the first cube changes every cube."
    ),
    SCOPE_INDIVIDUAL: "Individual scope: a ROI edit applies to this cube only (all its wavelengths follow through the chromatic correction).",
}


class RoiScopeToggle(QWidget):
    def __init__(self, scope: RoiScopeModule, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scope = scope
        self._buttons: dict[str, QToolButton] = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        for name in (SCOPE_PERSISTENT, SCOPE_INDIVIDUAL):
            button = QToolButton(self)
            button.setCheckable(True)
            button.setAutoRaise(True)
            button.setFixedSize(_BUTTON_SIZE, _BUTTON_SIZE)
            button.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
            button.setStyleSheet(transparent_icon_button_stylesheet())
            button.setToolTip(_TIP[name])
            group.addButton(button)
            layout.addWidget(button)
            self._buttons[name] = button
        self._buttons[SCOPE_PERSISTENT].toggled.connect(
            lambda checked: scope.set_scope(SCOPE_PERSISTENT if checked else SCOPE_INDIVIDUAL)
        )
        self._sync(scope.scope())
        scope.scope_changed.connect(self._sync)

    def _sync(self, scope: str) -> None:
        button = self._buttons[scope]
        if not button.isChecked():
            button.setChecked(True)
        self._refresh_icons()

    def _refresh_icons(self) -> None:
        theme = get_active_theme()
        for name, button in self._buttons.items():
            color = _ACTIVE_COLOR if button.isChecked() else theme.text_dim
            button.setIcon(load_tabler_icon(_ICON[name], color=color, size=_ICON_SIZE * 2, stroke_width=2.1))

    def refresh_theme(self, _theme: GuiTheme) -> None:
        for button in self._buttons.values():
            button.setStyleSheet(transparent_icon_button_stylesheet())
        self._refresh_icons()
