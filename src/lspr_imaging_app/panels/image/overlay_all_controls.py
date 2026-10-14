"""The View tab's "All" section: two shortcut buttons, Show all and Hide all.

They do not draw or hold anything themselves. They only ask: `ViewTab` answers by switching every overlay toggle
it carries (see `ViewTab.set_all_overlays`), so each toggle reports and redraws its own icon exactly as if you had
clicked it.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QToolButton, QWidget

from lspr_ui import GuiTheme, get_active_theme, load_tabler_icon

from .general_group import ICON_SIZE, style_general_icon_button

_RENDER_SIZE = ICON_SIZE * 2
_STROKE_WIDTH = 2.1


class OverlayAllControls(QWidget):
    show_all_requested = pyqtSignal()
    hide_all_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._show_button = QToolButton(self)
        self._show_button.setToolTip(
            "Show all: switch every overlay on (histogram highlight, mask, landmarks, ROI circles, rings and labels, "
            "group labels, scale bar)."
        )
        self._show_button.clicked.connect(lambda _checked=False: self.show_all_requested.emit())
        self._hide_button = QToolButton(self)
        self._hide_button.setToolTip("Hide all: switch every overlay off, leaving the bare image.")
        self._hide_button.clicked.connect(lambda _checked=False: self.hide_all_requested.emit())

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._show_button)
        layout.addWidget(self._hide_button)
        self.refresh_theme(get_active_theme())

    @property
    def show_button(self) -> QToolButton:
        return self._show_button

    @property
    def hide_button(self) -> QToolButton:
        return self._hide_button

    def refresh_theme(self, theme: GuiTheme) -> None:
        for button, icon_name in ((self._show_button, "eye"), (self._hide_button, "eye-off")):
            style_general_icon_button(button)
            button.setIcon(
                load_tabler_icon(icon_name, color=theme.text_primary, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH)
            )
