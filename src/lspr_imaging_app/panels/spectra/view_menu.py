"""Spectra ribbon, "View" group: one icon button that opens a small panel with how the measured points are drawn (2026-10-25).

Screen purpose: display options of the spectra plot in one place, in the look of the Image ribbon's "ROI display" menu (`image/roi_display_menu.py`): one icon button, an
instant-popup menu holding a captioned grid, closed by a click outside it.

Layout of the panel (one caption and its controls per row):

    Data points   [dots toggle] [size  6 px]
    Lines         [line toggle] [solid | dashed | dotted | dash-dot]
    Error bars    [error-bar toggle]

Display only: this module builds the controls and holds them in the panel; the Spectra panel owns their values, remembers them (`restore_ui_state`) and hands them to the plot
(`SpectraPlot.set_display`), which redraws the stored result without computing anything. Every toggle is a checkable button with a pictogram that is coloured when on and grey and
crossed out when off (`view_icons.py`). The line styles are four buttons in a row, not a drop-down, so the panel never opens a menu inside a menu.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QSize, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QButtonGroup, QGridLayout, QHBoxLayout, QLabel, QMenu, QSpinBox, QToolButton, QWidget, QWidgetAction

from lspr_ui import get_active_theme, transparent_icon_button_stylesheet

from ..ribbon_fields import style_ribbon_number_field
from ..ribbon_icon_size import BUTTON_SIZE, ICON_SIZE
from .plot import DEFAULT_POINT_SIZE, LINE_STYLES, POINT_SIZE_RANGE
from .view_icons import line_style_icon, view_icon

LINE_STYLE_LABELS = {"solid": "Solid", "dash": "Dashed", "dot": "Dotted", "dashdot": "Dash-dot"}


def _style_icon_button(button: QToolButton) -> None:
    button.setAutoRaise(True)
    button.setFixedSize(BUTTON_SIZE, BUTTON_SIZE)
    button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
    button.setStyleSheet(transparent_icon_button_stylesheet())


class IconToggle(QToolButton):
    """A checkable icon button whose pictogram follows its state: ``icon_for(checked, off_color, on_color)`` draws it (``view_icons``)."""

    def __init__(self, icon_for: Callable[[bool, str, str], QIcon], tip: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._icon_for = icon_for
        self.setCheckable(True)
        self.setToolTip(tip)
        self.toggled.connect(lambda _checked: self._refresh_icon())
        self.refresh_theme()

    def _refresh_icon(self) -> None:
        theme = get_active_theme()
        self.setIcon(self._icon_for(self.isChecked(), theme.text_dim, theme.accent_blue))

    def refresh_theme(self) -> None:
        _style_icon_button(self)
        self._refresh_icon()


class LineStylePicker(QWidget):
    """Four buttons, one per line style (``plot.LINE_STYLES``), exactly one pressed. ``style_changed(key)`` when the user picks another."""

    style_changed = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QToolButton] = {}
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        for key in LINE_STYLES:
            button = QToolButton(self)
            button.setCheckable(True)
            button.setToolTip(f"{LINE_STYLE_LABELS[key]} line between the measured points.")
            button.clicked.connect(lambda _checked=False, picked=key: self._picked(picked))
            self._group.addButton(button)
            self._buttons[key] = button
            row.addWidget(button)
        self._buttons["solid"].setChecked(True)
        self.refresh_theme()

    def style(self) -> str:
        return next((key for key, button in self._buttons.items() if button.isChecked()), "solid")

    def set_style(self, key: str) -> None:
        """Press the button of ``key`` without reporting it (an unknown key is ignored)."""
        if key in self._buttons:
            self._buttons[key].setChecked(True)

    def _picked(self, key: str) -> None:
        self.style_changed.emit(key)

    def refresh_theme(self) -> None:
        theme = get_active_theme()
        for key, button in self._buttons.items():
            _style_icon_button(button)
            button.setStyleSheet(
                transparent_icon_button_stylesheet()
                + f"QToolButton:checked {{ background: {theme.control_bg}; border-radius: 3px; }}"
            )
            button.setIcon(line_style_icon(key, theme.text_primary))


def make_point_size_box(parent: QWidget) -> QSpinBox:
    box = QSpinBox(parent)
    box.setRange(*POINT_SIZE_RANGE)
    box.setValue(DEFAULT_POINT_SIZE)
    box.setSuffix(" px")
    box.setKeyboardTracking(False)  # a value is taken when Enter is pressed or the field is left, not on every digit
    box.setToolTip("Size of the dots that mark the measured points, in pixels.")
    style_ribbon_number_field(box, f"{POINT_SIZE_RANGE[1]} px")
    return box


class SpectraViewMenu(QToolButton):
    """The View button: opens the panel that holds the controls it is given (it adds no state and no signals of its own)."""

    def __init__(
        self,
        points_toggle: QWidget,
        size_box: QWidget,
        lines_toggle: QWidget,
        style_picker: QWidget,
        error_toggle: QWidget,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        _style_icon_button(self)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setToolTip(
            "View: show or hide the measured points and set their size, show or hide the line between them and choose its style, show or hide the error bars."
        )
        panel = QWidget(self)
        grid = QGridLayout(panel)
        grid.setContentsMargins(10, 8, 10, 8)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        points_row = QWidget(panel)
        points_layout = QHBoxLayout(points_row)
        points_layout.setContentsMargins(0, 0, 0, 0)
        points_layout.setSpacing(8)
        points_layout.addWidget(points_toggle)
        points_layout.addWidget(size_box)
        points_layout.addStretch(1)
        lines_row = QWidget(panel)
        lines_layout = QHBoxLayout(lines_row)
        lines_layout.setContentsMargins(0, 0, 0, 0)
        lines_layout.setSpacing(8)
        lines_layout.addWidget(lines_toggle)
        lines_layout.addWidget(style_picker)
        lines_layout.addStretch(1)
        self._captions: list[QLabel] = []
        for row, (text, controls) in enumerate((("Data points", points_row), ("Lines", lines_row), ("Error bars", error_toggle))):
            caption = QLabel(text, panel)
            self._captions.append(caption)
            grid.addWidget(caption, row, 0)
            grid.addWidget(controls, row, 1)  # reparents the control into the panel
        self._menu = QMenu(self)
        action = QWidgetAction(self._menu)
        action.setDefaultWidget(panel)
        self._menu.addAction(action)
        self.setMenu(self._menu)
        self.refresh_theme()

    def refresh_theme(self) -> None:
        theme = get_active_theme()
        _style_icon_button(self)
        self.setIcon(view_icon(theme.text_primary, theme.accent_blue, theme.accent_gold))
