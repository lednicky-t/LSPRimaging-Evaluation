"""Image panel ribbon, "View" tab: the "Masks" pick-up menu (2026-10-23, maintainer request).

One icon button (the mask icon) that opens a small panel with one row per tint drawn over the image:
Histogram (the highlighted intensity range) and Mask (the ignore mask), each with its show / hide toggle,
colour swatch and transparency wedge. It only *holds* the two controls `ViewTab` builds
(`HistogramHighlightOverlayControls`, `MaskOverlayControls`); it adds no state and no signals of its own, so
every control behaves exactly as it did when it sat in the ribbon. Same build as `roi_display_menu.py`.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QGridLayout, QLabel, QMenu, QToolButton, QWidget, QWidgetAction

from lspr_ui import GuiTheme, get_active_theme, load_tabler_icon

from .general_group import ICON_SIZE, style_general_icon_button


class MasksMenu(QToolButton):
    def __init__(self, highlight: QWidget, mask: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        style_general_icon_button(self)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setToolTip("Masks: show or hide, colour and transparency of the histogram highlight and the mask overlay.")

        panel = QWidget(self)
        grid = QGridLayout(panel)
        grid.setContentsMargins(10, 8, 10, 8)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        for row, (text, controls) in enumerate((("Histogram", highlight), ("Mask", mask))):
            grid.addWidget(QLabel(text, panel), row, 0)
            grid.addWidget(controls, row, 1)  # reparents the control into the panel

        self._menu = QMenu(self)
        action = QWidgetAction(self._menu)
        action.setDefaultWidget(panel)
        self._menu.addAction(action)
        self.setMenu(self._menu)
        self.refresh_theme(get_active_theme())

    def refresh_theme(self, theme: GuiTheme) -> None:
        style_general_icon_button(self)
        self.setIcon(load_tabler_icon("mask", color=theme.text_secondary, size=ICON_SIZE * 2, stroke_width=2.1))
