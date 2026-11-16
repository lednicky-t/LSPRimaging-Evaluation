"""The selection toolbar's View pop-up (2026-11-16, maintainer request): how the selection looks on the image.

Screen purpose: change the colour of the selection's outlines and give them an infill, without leaving the image.
Main user actions: click the eye button at the bottom of the selection toolbar; in the pop-up click a swatch to pick a colour, click or drag
on a wedge to pick the infill's opacity (5 steps; the first, empty position = no fill, the default), or scroll the wheel over it.
Layout: compact, like View -> Masks of the main ribbon (`masks_menu.py`): one row per look, a caption, a border switch (a dashed circle, crossed
out when the border is hidden), a colour swatch and a wedge (as narrow as the ROI overlay's). With the border off only the infill is drawn and nothing animates.
  * **Selection** (past): the selection as it stands, and also what a Subtract or Intersect drag will leave of it (the "result" outline wears this
    look: it was a third row until the maintainer found it confusing, 2026-11-16). Its colour is the left circle of the Venn mode icons.
  * **Drawn shape** (present): the rectangle, lasso or brush stroke being dragged. The right circle of the Venn icons.
Important states: fill step 0 = no fill (the default, borders only as before); the colours start as the palette's (`selection_palette.py`).
Validation: the opacity has five positions; a colour the dialog cancels changes nothing.
Error messages: none. Everything is remembered (`SelectionStyle.bind_ui_state`).

The form holds no state: `SelectionStyle` does, and the form follows it (a change made anywhere shows here), so it can never disagree with the
outlines or the icons.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QColorDialog, QGridLayout, QLabel, QToolButton, QWidget

from lspr_ui import CompactWedgeSlider, GuiTheme, get_active_theme, transparent_icon_button_stylesheet

from .selection_palette import FILL_OPACITY_STEPS, PAST, PRESENT
from .selection_style import MAX_FILL_STEP, SelectionStyle

_BORDER_BUTTON = 18
_BORDER_ICON = 14
_SWATCH_SIZE = 14
_WEDGE_WIDTH = 18  # the same narrow wedge as the ROI overlay controls (`roi_overlay_controls._SLIDER_WIDTH`), 2026-11-16, maintainer
_BORDER_DASHES = 8  # the segments of the dashed circle


def _border_icon(shown: bool, color: str) -> QIcon:
    """A circle drawn as a segmented (dashed) line, the look of the crawling border; crossed out when the border is hidden. Hand-painted at 2x
    (the Venn mode icons' circles are the same shape), not a vendored Tabler icon: none has a dashed circle (`packages/lspr_ui/ICONS.md`)."""
    size = _BORDER_ICON * 2
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    width = 2.6
    inset = 4.0
    diameter = size - 2 * inset
    # Round caps add half a pen width at each end of a dash, so the dash is drawn one pen width shorter than it looks; the period divides the
    # circumference into whole segments so the seam is not a short one.
    period = math.pi * diameter / _BORDER_DASHES / width
    pen = QPen(QColor(color), width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setDashPattern([0.8, period - 0.8])
    painter.setPen(pen)
    painter.drawEllipse(QRectF(inset, inset, diameter, diameter))
    if not shown:
        slash = QPen(QColor(color), width)
        slash.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(slash)
        painter.drawLine(int(inset), int(inset), int(size - inset), int(size - inset))
    painter.end()
    return QIcon(pixmap)
_ROWS = (
    (PAST, "Selection", "The selection as it stands, and what a Subtract or Intersect drag will leave of it. This colour is also the left circle of the mode icons."),
    (PRESENT, "Drawn shape", "The rectangle, lasso or brush stroke being dragged. This colour is also the right circle of the mode icons."),
)


class _ScrollWedge(CompactWedgeSlider):
    """The opacity wedge that also reads the mouse wheel (2026-11-16, maintainer: "make opacity controls scrollable"): one position per
    notch, wheel up = more opaque, stopping at both ends. A local subclass, so the shared wedge (and the ROI overlay's) stays as it is; a smooth
    wheel or touch pad adds its small deltas up until a whole notch (120)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent=parent)
        self._wheel_remainder = 0

    def wheelEvent(self, event) -> None:  # type: ignore[override]  # Qt's name
        self._wheel_remainder += event.angleDelta().y()
        notches = int(self._wheel_remainder / 120)  # towards zero: 119 units is not a notch yet
        if notches:
            self._wheel_remainder -= notches * 120
            self.setValue(self.value() + notches)  # `setValue` stops at the ends
        event.accept()


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
            wedge = _ScrollWedge(self)
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
        button.setIcon(_border_icon(shown, theme.text_primary if shown else theme.text_muted))
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
                wedge.setToolTip(
                    "Infill opacity: " + (f"{percent} %" if percent else "none")
                    + ". Five steps; the first, empty position is no fill.\nClick or drag, or scroll the wheel over it."
                )
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
