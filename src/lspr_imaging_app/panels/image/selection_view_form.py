"""The selection toolbar's View pop-up (2026-11-16, maintainer request): how the selection looks on the image.

Screen purpose: change the colour of the selection's outlines and give them an infill, without leaving the image.
Main user actions: click the eye button at the bottom of the selection toolbar; in the pop-up click a swatch to pick a colour, click or drag
on a wedge to pick the infill's opacity (5 steps; the first, empty position = no fill, the default).
Layout: compact, like View -> Masks of the main ribbon (`masks_menu.py`): one row per outline, a caption, a border switch (a square, crossed out
when the border is hidden), a colour swatch and a wedge. With the border off only the infill is drawn and nothing animates.
  * **Selection** (past): the selection as it stands. Its colour is the left circle of the Venn mode icons.
  * **Drawn shape** (present): the rectangle, lasso or brush stroke being dragged. The right circle of the Venn icons.
  * **Result**: what a Subtract or Intersect drag will leave selected. (The Venn icons fill it with a fixed grey, so it has no circle there.)
Important states: fill step 0 = no fill (the default, borders only as before); the colours start as the palette's (`selection_palette.py`).
Validation: the opacity has five positions; a colour the dialog cancels changes nothing.
Error messages: none. Everything is remembered (`SelectionStyle.bind_ui_state`).

The form holds no state: `SelectionStyle` does, and the form follows it (a change made anywhere shows here), so it can never disagree with the
outlines or the icons.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QColorDialog, QGridLayout, QLabel, QToolButton, QWidget

from lspr_ui import CompactWedgeSlider, GuiTheme, get_active_theme, load_tabler_icon, transparent_icon_button_stylesheet

from .selection_palette import FILL_OPACITY_STEPS, PAST, PRESENT, RESULT
from .selection_style import MAX_FILL_STEP, SelectionStyle

_BORDER_BUTTON = 18
_BORDER_ICON = 14
_SWATCH_SIZE = 14
_WEDGE_WIDTH = 44  # five positions, about 9 px apart: the Masks menu's 18 px wedge would be too fiddly
_ROWS = (
    (PAST, "Selection", "The selection as it stands. This colour is also the left circle of the mode icons."),
    (PRESENT, "Drawn shape", "The rectangle, lasso or brush stroke being dragged. This colour is also the right circle of the mode icons."),
    (RESULT, "Result", "What a Subtract or Intersect drag will leave selected."),
)


class SelectionViewForm(QWidget):
    def __init__(self, style: SelectionStyle, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._style = style
        self._syncing = False
        self.swatches: dict[str, QToolButton] = {}
        self.wedges: dict[str, CompactWedgeSlider] = {}
        self.border_buttons: dict[str, QToolButton] = {}

        grid = QGridLayout(self)
        grid.setContentsMargins(10, 8, 10, 8)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        for row, (role, caption, what) in enumerate(_ROWS):
            label = QLabel(caption, self)
            label.setToolTip(what)
            border = QToolButton(self)
            border.setCheckable(True)
            border.setAutoRaise(True)
            border.setFixedSize(_BORDER_BUTTON, _BORDER_BUTTON)
            border.setIconSize(QSize(_BORDER_ICON, _BORDER_ICON))
            border.setStyleSheet(transparent_icon_button_stylesheet())
            border.toggled.connect(lambda shown, picked=role: self._on_border(picked, shown))
            self.border_buttons[role] = border
            swatch = QToolButton(self)
            swatch.setFixedSize(QSize(_SWATCH_SIZE, _SWATCH_SIZE))
            swatch.clicked.connect(lambda _checked=False, picked=role: self._choose_color(picked))
            wedge = CompactWedgeSlider(parent=self)
            wedge.setFixedWidth(_WEDGE_WIDTH)
            wedge.setRange(0, MAX_FILL_STEP)
            wedge.valueChanged.connect(lambda step, picked=role: self._on_step(picked, step))
            grid.addWidget(label, row, 0)
            grid.addWidget(border, row, 1)
            grid.addWidget(swatch, row, 2)
            grid.addWidget(wedge, row, 3)
            self.swatches[role] = swatch
            self.wedges[role] = wedge
            swatch.setToolTip(f"{caption}: the colour of its border and of its infill. {what}")

        style.changed.connect(self._sync)
        self._sync()

    def _choose_color(self, role: str) -> None:
        color = QColorDialog.getColor(QColor(self._style.color(role)), self, "Selection colour")
        if color.isValid():
            self._style.set_color(role, color.name())

    def _on_step(self, role: str, step: int) -> None:
        if not self._syncing:
            self._style.set_step(role, step)

    def _on_border(self, role: str, shown: bool) -> None:
        if not self._syncing:
            self._style.set_border_shown(role, shown)

    def _paint_border_button(self, role: str, theme: GuiTheme) -> None:
        shown = self._style.border_shown(role)
        button = self.border_buttons[role]
        button.setIcon(
            load_tabler_icon("square" if shown else "square-off", color=theme.text_primary if shown else theme.text_muted, size=_BORDER_ICON * 2, stroke_width=2.1)
        )
        button.setToolTip(
            "Border shown (the crawling dashes). Click to hide it: only the infill is drawn, and nothing animates."
            if shown else "Border hidden: only the infill is drawn (set an opacity, or this outline is invisible). Click to show it."
        )

    def _sync(self) -> None:
        """Show what the style holds: the swatches' colours, the border switches and the wedges' positions (without writing it back)."""
        theme = get_active_theme()
        self._syncing = True
        try:
            for role, _caption, _what in _ROWS:
                self._paint_swatch(role, theme)
                border = self.border_buttons[role]
                border.setChecked(self._style.border_shown(role))
                self._paint_border_button(role, theme)
                wedge = self.wedges[role]
                wedge.setValue(self._style.step(role))
                percent = round(FILL_OPACITY_STEPS[self._style.step(role)] * 100)
                wedge.setToolTip("Infill opacity: " + (f"{percent} %" if percent else "none") + ". Five steps; the first, empty position is no fill.")
        finally:
            self._syncing = False

    def _paint_swatch(self, role: str, theme: GuiTheme) -> None:
        self.swatches[role].setStyleSheet(
            f"QToolButton {{ background-color: {self._style.color(role)}; min-width: {_SWATCH_SIZE}px; max-width: {_SWATCH_SIZE}px; "
            f"min-height: {_SWATCH_SIZE}px; max-height: {_SWATCH_SIZE}px; border: 1px solid {theme.control_border}; border-radius: 3px; padding: 0; }}"
        )

    def refresh_theme(self, theme: GuiTheme) -> None:
        for role, _caption, _what in _ROWS:
            self._paint_swatch(role, theme)
            self._paint_border_button(role, theme)
