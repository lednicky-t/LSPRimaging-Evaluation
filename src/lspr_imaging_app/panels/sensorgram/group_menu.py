"""``GroupMenuButton``: the one icon button of a View-tab group that opens its options in a small captioned panel (2026-11-13).

Screen purpose: every group of the Sensorgram's View tab looks and works the same: one icon in the ribbon, a click opens a panel with the group's controls, a click outside
closes it. Points & lines, Legend, Steps, Evaluation and Stats are all this button. Next to it, the helpers their panels share: `segmented_buttons` (a row of text buttons, one pressed), `icon_choice_buttons`
and `PictureChoice` (the same with a picture on each button), `widgets_row` (several controls as one row) and `segment_stylesheet` (the look of a pressed choice).

Layout of the panel: a grid, one caption and its control per row, an optional heading row that spans both columns, and an optional footer (the reset button) under it:

    Show        [toggle]
    Height      [ 5 % ]
    Step labels                 <- a heading
    Text        [pick-up]
    [Reset to default]          <- the footer

The controls are made, remembered and read by their owner (`PlanBarControls`, `StatsTableControls`, `SensorgramViewControls`); this button only reparents them into its panel,
so it adds no state and no signal of its own. A pick-up menu inside the panel is fine: the plan bar's text controls have always been that.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QButtonGroup, QGridLayout, QHBoxLayout, QLabel, QMenu, QToolButton, QWidget, QWidgetAction

from lspr_ui import get_active_theme

from ..icon_button_style import style_general_icon_button
from ..spectra.pick_menu import PictureFor

Row = tuple[str, QWidget | None]  # (caption, control); control None = a heading


def segmented_buttons(
    parent: QWidget, keys: Sequence[str], labels: dict[str, str], tips: dict[str, str], prefix: str
) -> tuple[QWidget, QButtonGroup, dict[str, QToolButton]]:
    """A row of text buttons of which exactly one is pressed (Auto / Show / Hide, Straight / Spline, Left / Right). The first key starts pressed. Style them with
    `segment_stylesheet()` (again after a theme change). Returns the row to put in a panel, the button group and the buttons by key."""
    holder = QWidget(parent)
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)
    group = QButtonGroup(holder)
    buttons: dict[str, QToolButton] = {}
    for key in keys:
        button = QToolButton(holder)
        button.setObjectName(f"{prefix}_{key}")
        button.setText(labels[key])
        button.setToolTip(tips[key])
        button.setCheckable(True)
        group.addButton(button)
        layout.addWidget(button)
        buttons[key] = button
    buttons[keys[0]].setChecked(True)
    return holder, group, buttons


PICTURE_CHOICE_PX = 28  # a picture-choice button, square: a 20 px picture and room for the pressed frame
_PICTURE_PX = 20  # the pick-up menus' pictures are 20 x 20 shown pixels


class PictureChoice(QToolButton):
    """A pick-up menu picture (`PictureFor`, the kind the baseline shapes and the smoothing methods use) as one button of a choice row. Pressed: the same frame as a pressed text
    segment (control background, accent border), so the picture itself can keep its own colours (the joint pictures: dots in the text colour, the line in the gold accent)."""

    def __init__(self, picture: PictureFor, tip: str, parent: QWidget) -> None:
        super().__init__(parent)
        self._picture = picture
        self.setCheckable(True)
        self.setToolTip(tip)
        self.setIconSize(QSize(_PICTURE_PX, _PICTURE_PX))
        self.setFixedSize(PICTURE_CHOICE_PX, PICTURE_CHOICE_PX)
        self.refresh_theme()

    def refresh_theme(self) -> None:
        self.setStyleSheet(segment_stylesheet(padding="2px"))
        self.setIcon(QIcon(self._picture(get_active_theme().text_primary)))


def icon_choice_buttons(
    parent: QWidget, keys: Sequence[str], pictures: dict[str, PictureFor], tips: dict[str, str], prefix: str
) -> tuple[QWidget, QButtonGroup, dict[str, PictureChoice]]:
    """Like `segmented_buttons`, with a picture on each button instead of a word (Straight / Spline): `PictureChoice` buttons of which exactly one is pressed, the first at the
    start. `pictures[key]` draws a button's picture; they style and recolour themselves (`refresh_theme`, again after a theme change)."""
    holder = QWidget(parent)
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)
    group = QButtonGroup(holder)
    buttons: dict[str, PictureChoice] = {}
    for key in keys:
        button = PictureChoice(pictures[key], tips[key], holder)
        button.setObjectName(f"{prefix}_{key}")
        group.addButton(button)
        layout.addWidget(button)
        buttons[key] = button
    buttons[keys[0]].setChecked(True)
    return holder, group, buttons


def widgets_row(parent: QWidget, *widgets: QWidget) -> QWidget:
    """Several controls side by side as one row of a pop-up panel (they are reparented into it)."""
    row = QWidget(parent)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    for widget in widgets:
        layout.addWidget(widget)
    return row


def segment_stylesheet(padding: str = "2px 8px") -> str:
    """The look of a `segmented_buttons` button in the active theme: a pressed one has the control background and an accent frame. `padding`: round the text (a picture button
    passes a small one)."""
    theme = get_active_theme()
    return (
        f"QToolButton {{ border: 1px solid transparent; border-radius: 3px; padding: {padding}; background: transparent; color: {theme.text_primary}; }}"
        f"QToolButton:hover {{ background: {theme.control_bg_hover}; }}"
        f"QToolButton:checked {{ background: {theme.control_bg}; border: 1px solid {theme.accent_blue}; }}"
    )


class GroupMenuButton(QToolButton):
    def __init__(
        self,
        icon_for: Callable[[str], QIcon],
        tip: str,
        rows: Sequence[Row],
        parent: QWidget,
        footer: QWidget | None = None,
    ) -> None:
        """`icon_for(color)`: the button's picture in a theme colour (it is drawn again when the theme changes)."""
        super().__init__(parent)
        self._icon_for = icon_for
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setToolTip(tip)
        panel = QWidget(self)
        grid = QGridLayout(panel)
        grid.setContentsMargins(10, 8, 10, 8)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        self._captions: dict[QWidget, QLabel] = {}
        self._headings: list[QLabel] = []
        for row, (caption, control) in enumerate(rows):
            label = QLabel(caption, panel)
            if control is None:
                self._headings.append(label)
                grid.addWidget(label, row, 0, 1, 2)
                continue
            self._captions[control] = label
            grid.addWidget(label, row, 0)
            grid.addWidget(control, row, 1, Qt.AlignmentFlag.AlignLeft)  # reparents the control into the panel
        if footer is not None:
            grid.addWidget(footer, len(rows), 0, 1, 2, Qt.AlignmentFlag.AlignLeft)
        self._menu = QMenu(self)
        action = QWidgetAction(self._menu)
        action.setDefaultWidget(panel)
        self._menu.addAction(action)
        self.setMenu(self._menu)
        self.refresh_theme()

    def set_row_visible(self, control: QWidget, visible: bool) -> None:
        """Show or hide a control together with its caption (a row that means nothing now, e.g. the edge of a bar that fills the plot)."""
        control.setVisible(visible)
        label = self._captions.get(control)
        if label is not None:
            label.setVisible(visible)

    def refresh_theme(self) -> None:
        theme = get_active_theme()
        style_general_icon_button(self)
        self.setIcon(self._icon_for(theme.text_primary))
        for heading in self._headings:
            heading.setStyleSheet(f"color: {theme.text_dim}; font-weight: 600; padding-top: 4px;")
