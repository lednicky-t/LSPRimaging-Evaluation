"""``PlotLookControls``: the Points & lines and Legend menus of every plot, defined once (2026-11-22). Also ``UncertaintyControls`` (the Uncertainty menu of the plot windows).

Screen purpose: the Sensorgram's View tab, the LbL window and the S_bulk window all offer the same options of how points, lines and the legend are drawn. **The code and the look of
the menus are shared; the values are not**: every plot makes its own `PlotLookControls`, with its own remembered keys and its own defaults, so the LbL plot can have big dots while the
Sensorgram has small ones. (Maintainer, 2026-11-22: "different settings can be set in different plots; when I add a function to the view menu, e.g. another mark shape, it is added in
every plot menu".) So a new option is one field in `view_settings.SensorgramViewSettings`, one row here, and every plot's menu has it; each plot then draws it (the Sensorgram in `plot.py`,
the windows through `plot_window_draw.py`).

What differs between plots is passed in, never copied:
- ``keys``: where the plot remembers its values (`PlotLookKeys`; the Sensorgram keeps its old `sensorgram/view_*` keys, a window uses ``PlotLookKeys.with_prefix(...)``);
- ``defaults``: the plot's own starting look (and what its Reset puts back);
- ``dot_limit``: the Sensorgram's Dot limit row and the Points toggle that shows itself off above it (`points_toggle.py`). The windows draw a dozen lines at most and have neither;
- ``legend_position``: the "at the edge of the plot" row, which places the legend against the Sensorgram's plan bar (the windows have no plan bar);
- ``joined``: the Straight / Spline row (not for a plot that joins no points: the S_bulk window's line is a straight fit; the value is kept, the row is not shown).

`settings()` returns the plot's `SensorgramViewSettings` (the fields a plot has no control for keep their default); `settings_changed` fires after any change. Every control is
remembered (CLAUDE.md "Remembering UI state") once `restore_ui_state` was called.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QDoubleSpinBox, QLabel, QSpinBox, QToolButton, QWidget

from lspr_ui import get_active_theme, load_tabler_icon

from ..ribbon_fields import style_ribbon_number_field
from ..spectra.pick_menu import PickMenu
from ..spectra.view_menu import IconToggle
from ...selection.plot_line_order_module import PlotLineOrderModule
from ..ui_state import UiStateStore
from .group_menu import GroupMenuButton, icon_choice_buttons, segment_stylesheet, segmented_buttons, widgets_row
from .draw_order import DRAW_AUTO, DRAW_LEGEND, DRAW_MANUAL, DRAW_MODES
from .legend_corner import BOTTOM_LEFT, BOTTOM_RIGHT, DEFAULT_CORNER, TOP_LEFT, TOP_RIGHT
from .legend_corner_picker import LegendCornerPicker
from .legend_order import SORT_GROUP, SORT_KEYS, SORT_NAME, SORT_VALUE, VALUE_MEAN, VALUE_SOURCES, VALUE_STEP
from .line_order_dialog import LineOrderDialog, LineRow
from .line_icons import joint_picture, lines_toggle_icon, points_lines_icon, points_toggle_icon
from .plot import MAX_LEGEND_ENTRIES, MAX_LEGEND_SERIES
from .points_toggle import PointsToggle
from .stat_icons import display_picture
from .window_look_icons import band_picture, bars_picture, error_bars_icon, uncertainty_menu_icon
from .view_settings import (
    DEFAULT_DOTS_MAX_LINES,
    DOTS_MAX_LINES_RANGE,
    LEGEND_AUTO,
    LEGEND_HIDE,
    LEGEND_MODES,
    LEGEND_SHOW,
    LEGEND_SPACING_RANGE,
    LEGEND_TEXT_RANGE,
    LINE_SPLINE,
    LINE_STRAIGHT,
    LINE_STYLES,
    LINE_WIDTH_RANGE,
    LINE_WIDTH_STEP,
    POINT_SIZE_RANGE,
    UNCERTAINTY_BAND,
    UNCERTAINTY_BARS,
    UNCERTAINTY_KINDS,
    UNCERTAINTY_SD,
    UNCERTAINTY_SEM,
    UNCERTAINTY_STYLES,
    SensorgramViewSettings,
    UncertaintyLook,
)

# -- texts (the Sensorgram's View tab wording; a window shows the same) ------------------------------------------------------------------------------

LINES_TIP = "Points & lines: show or hide the dots and the lines, their size and width, how the points are joined (straight or a smooth spline), and a reset to the defaults."
POINTS_TIP = (
    "Show or hide the dots on the measured points. Dots are drawn only for up to the Dot limit (below) lines: with more they merge into the lines and every redraw would be slow. "
    "While more lines are drawn than that, this button shows itself off and your choice is kept."
)
POINTS_TIP_PLAIN = "Show or hide the dots on the points."
POINTS_CAPPED_NOTE = "Dots are not drawn now: {lines} lines are drawn and the Dot limit is {limit}. Click to see your options, or raise the Dot limit (Points & lines menu)."
POINT_SIZE_TIP = "Size of the dots, in pixels."
DOTS_LIMIT_TIP = (
    f"Dots are drawn only for up to this many lines ({DEFAULT_DOTS_MAX_LINES} at first). With more lines the dots merge into the lines, and every redraw gets slower: 64,000 dots "
    "(160 lines of 400 cubes) took about 1 second. Raise it if you want the dots anyway; the Points button shows itself off while more lines are drawn than this."
)
LINES_TOGGLE_TIP = "Show or hide the lines that join the points. Hidden lines still frame the view, and the legend keeps their colours."
LINE_WIDTH_TIP = (
    "Width of the lines, in pixels. 1 px is the fast one: a wider line makes every redraw slower with many ROIs (about 5 times slower at 1.5 px with 160 ROIs), a few ROIs "
    "do not notice."
)
LINE_STYLE_PICTURES = {LINE_STRAIGHT: joint_picture("straight"), LINE_SPLINE: joint_picture("spline")}  # the same three dots joined straight and by a smooth curve (`line_icons.py`)
LINE_STYLE_TIPS = {
    LINE_STRAIGHT: "Straight: a straight segment between neighbouring points.",
    LINE_SPLINE: (
        "Spline: a smooth curve through every point (PCHIP, a cubic spline that never leaves the range of its two neighbours, so it invents no peaks between noisy points). "
        "Drawing only: the dots and every number stay where they are, and a gap in the data stays a gap. Slower with many ROIs: a redraw takes about 3 times as long with 160 ROIs "
        "(about 65 ms, then 200 ms), 20 ms more with 40, nothing you notice with a few."
    ),
}
LEGEND_TIP = "Legend: when it is shown, its text size, the gap between its rows (the step table's rows follow them) and whether a group's name carries its number of ROIs."
LEGEND_TIP_PLAIN = "Legend: when it is shown, its text size, the gap between its rows and whether a group's name carries its number of ROIs."
LEGEND_MODE_TIPS = {
    LEGEND_AUTO: (
        f"Auto: the legend names up to {MAX_LEGEND_ENTRIES} lines, and with more than {MAX_LEGEND_SERIES} it stays only while it takes at most half the height of the plot "
        "(a legend of a hundred ROIs would cover it)."
    ),
    LEGEND_SHOW: "Show: always, however many lines there are. A long legend can cover the plot.",
    LEGEND_HIDE: "Hide: never drawn. (The Sensorgram's step table still lines up as if it were there, so its rows stay where they are.)",
}
LEGEND_MODE_LABELS = {LEGEND_AUTO: "Auto", LEGEND_SHOW: "Show", LEGEND_HIDE: "Hide"}
LEGEND_TEXT_TIP = "Text size of the legend, in points."
LEGEND_SPACING_TIP = "Space added between the legend's rows, in pixels (0 = as before; negative = closer). The Sensorgram's step table rows line up with the legend's, so they move with it."
LEGEND_OVER_BAR_TIP = (
    "Put the legend at the very edge of the plot, over the plan bar and the evaluation markers in it, instead of just inside the bar. The step table's rows line up with the "
    "legend, so they go up with it (over the bar). Only when the step names are aligned to the data: with the names written inside the bar, the rows stay under the bar so they "
    "never cover them. Without effect when the step stats table is off, when the bar fills the plot (a full bar takes no edge, and its names keep out of the rows) or when the bar "
    "is on the other edge."
)
LEGEND_COUNTS_TIP = "Write the number of ROIs after the name of a group or an average: \"Group A (n=12)\" instead of \"Group A\". A line of one ROI is always \"ROI 5\"."
CORNER_TIPS = {
    TOP_LEFT: "Legend in the top-left corner of the plot area (where it has always been).",
    TOP_RIGHT: "Legend in the top-right corner of the plot area.",
    BOTTOM_LEFT: "Legend in the bottom-left corner of the plot area.",
    BOTTOM_RIGHT: "Legend in the bottom-right corner of the plot area.",
}
CORNER_TIP = "Which corner of the plot area the legend sits in (the four cells are the four corners). Dragging the legend with the mouse still works; picking a corner puts it back in that corner."
SORT_LABELS = {SORT_GROUP: "Group", SORT_NAME: "Name", SORT_VALUE: "Value"}
SORT_TIPS = {
    SORT_GROUP: "Group: the order the lines come in (a group's number, a ROI's id). This is the order the legend always had.",
    SORT_NAME: "Name: alphabetical by the line's name, numbers read as numbers (ROI 2 before ROI 10).",
    SORT_VALUE: "Value: by a number of the line, chosen under \"Value of\". A line without that number is always last.",
}
SORT_DIRECTION_TIP = "Direction of the order: arrow up = ascending (first to last, smallest first), arrow down = descending. Only the legend rows (and the Sensorgram's step table rows with them) move."
VALUE_LABELS = {VALUE_MEAN: "Mean", VALUE_STEP: "At step"}
VALUE_TIPS = {
    VALUE_MEAN: "Mean: the mean of the line over the whole plot, as it is drawn (the Sensorgram: all its cubes; the LbL plot: its cycles; the S_bulk plot: its steps).",
    VALUE_STEP: (
        "At step: the line's value at one step. Sensorgram: the step selected on the plan bar (select exactly one). LbL and S_bulk plots: the cycle or step the cursor stands on "
        "(drag the cursor, it jumps from one to the next)."
    ),
}
DRAW_LABELS = {DRAW_AUTO: "Auto", DRAW_LEGEND: "Legend", DRAW_MANUAL: "Manual"}
DRAW_TIPS = {
    DRAW_AUTO: (
        "Auto: the smoothest line in front, the noisiest at the back, so a smooth line is not scribbled over. Roughness = the median absolute second difference of the line's points "
        "(a ramp or a slow drift is smooth, jitter is rough). Lines equally rough keep their own order, the later one in front."
    ),
    DRAW_LEGEND: "Legend: the stacking follows the legend rows (the Order row of the Legend menu): the first row is in front, the last at the back.",
    DRAW_MANUAL: "Manual: your own order, set in the Line order table (the Order... button). The first row of the table is in front. A new line goes in front.",
}
DRAW_TIP = "Draw order: which line lies over which where lines overlap. Display only, no number changes."
DRAW_EDIT_TIP = "Open the Line order table: the lines of this plot in rows (colour and name), the first row in front. Drag rows, or use To top / Up / Down / To bottom (also in the right-click menu)."
DRAW_EDIT_OFF_TIP = "The table is used when the Draw order is Manual."
CURSOR_TIP = (
    "Show or hide the cursor. Sensorgram: the cube cursor. LbL and S_bulk plots: the step cursor that picks the step of \"At step\"; it is drawn only while the order reads the value at a step. "
    "Hiding it does not change the step the order uses."
)
RESET_TIP = "Put these options back to what the plot always did."

UNCERTAINTY_TIP = "Uncertainty: show or hide the bars or band of the points, what they show (SEM or SD) and how they are drawn."
UNCERTAINTY_TOGGLE_TIP = (
    "Show or hide the uncertainty of the points (and, in the S_bulk plot, the band around the fitted line). Hiding it changes no number. The S_bulk plot's horizontal bars (the refractive "
    "index's Std) are part of it."
)
UNCERTAINTY_KIND_TIPS = {
    UNCERTAINTY_SEM: (
        "SEM: the standard error of a step's mean, the SD of its cubes divided by the square root of their number: how well the mean is known (it narrows with a longer step). A difference "
        "of two steps (LbL) adds the two in quadrature."
    ),
    UNCERTAINTY_SD: (
        "SD: the standard deviation of the cubes inside a step: how much the line moves within the step (it does not narrow with a longer step). A difference of two steps (LbL) adds the "
        "two in quadrature. The S_bulk fit itself still weights with the SEM."
    ),
}
UNCERTAINTY_KIND_LABELS = {UNCERTAINTY_SEM: "SEM", UNCERTAINTY_SD: "SD"}
UNCERTAINTY_STYLE_TIPS = {
    UNCERTAINTY_BARS: "Bars: a vertical bar at each point.",
    UNCERTAINTY_BAND: "Band: a shaded band along the points (the points sorted along the x axis).",
}


# -- which lines a plot window draws: the picture buttons both windows have ----------------------------------------------------------------------

LINES_SENSORGRAM, LINES_GROUPS = "sensorgram", "groups"
LINES_PICTURES = {LINES_SENSORGRAM: display_picture("individual"), LINES_GROUPS: display_picture("average_by_group")}  # the Sensorgram's own pictures: one line per ROI / one per group
LINES_TIPS = {
    LINES_SENSORGRAM: "Sensorgram lines: the lines drawn on the Sensorgram now (one per ROI, or the averages it shows).",
    LINES_GROUPS: "All groups: every ROI group, each as its averaged line (the ROI table's group rows).",
}
LINES_LABELS = {LINES_SENSORGRAM: "Sensorgram lines", LINES_GROUPS: "All groups"}


def lines_pick_menu(parent: QWidget) -> PickMenu:
    """The pick-up menu that chooses which lines a plot window draws, the kind the Sensorgram's Plot tab uses for Individual / Average (the picked choice's picture is the button, no
    frame; the menu lists every choice as picture + name). Answers like a combo box: `currentData()`, `findData`, `setCurrentIndex`, `currentIndexChanged`."""
    menu = PickMenu(parent, name_on_face=False)
    for key in (LINES_SENSORGRAM, LINES_GROUPS):
        menu.add_choice(key, LINES_LABELS[key], LINES_TIPS[key], LINES_PICTURES[key])
    menu.finish()
    menu.setToolTip("Which lines this plot draws.")
    return menu


def restore_pick(store: UiStateStore, key: str, menu: PickMenu) -> None:
    """Show the remembered choice of a pick-up menu without reporting it (an unknown value leaves the default), then remember every later pick."""
    saved = store.get(key)
    index = menu.findData(saved) if isinstance(saved, str) else -1
    if index >= 0:
        menu.blockSignals(True)
        try:
            menu.setCurrentIndex(index)
        finally:
            menu.blockSignals(False)
    menu.currentIndexChanged.connect(lambda _i: store.set(key, menu.currentData()))


# The plot windows' starting look: their points were 9 px and their lines 1.5 px before the menus; the Sensorgram's defaults (4 px, 1 px) suit hundreds of lines, not a dozen.
WINDOW_LOOK_DEFAULTS = SensorgramViewSettings(point_size=9, line_width=1.5)


# -- where a plot remembers its look ------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class PlotLookKeys:
    """The remembered-state keys of one plot's look (one row each, `store.bind` / `store.set`)."""

    points: str
    lines: str
    point_size: str
    dots_limit: str
    line_width: str
    line_style: str
    legend_mode: str
    legend_text: str
    legend_spacing: str
    legend_counts: str
    legend_over_bar: str
    legend_corner: str
    legend_sort: str
    legend_descending: str
    legend_value: str
    show_cursor: str
    draw_mode: str

    @staticmethod
    def with_prefix(prefix: str) -> PlotLookKeys:
        """``prefix`` + a field name: ``with_prefix("sensorgram/lbl_plot")`` -> ``"sensorgram/lbl_plot_point_size"`` ..."""
        return PlotLookKeys(
            points=f"{prefix}_points", lines=f"{prefix}_lines", point_size=f"{prefix}_point_size", dots_limit=f"{prefix}_dots_limit", line_width=f"{prefix}_line_width",
            line_style=f"{prefix}_line_style", legend_mode=f"{prefix}_legend_mode", legend_text=f"{prefix}_legend_text_pt", legend_spacing=f"{prefix}_legend_spacing",
            legend_counts=f"{prefix}_legend_counts", legend_over_bar=f"{prefix}_legend_over_bar",
            legend_corner=f"{prefix}_legend_corner",
            legend_sort=f"{prefix}_legend_sort", legend_descending=f"{prefix}_legend_descending", legend_value=f"{prefix}_legend_value", show_cursor=f"{prefix}_show_cursor", draw_mode=f"{prefix}_draw_mode",
        )


def _spin(parent: QWidget, name: str, low: int, high: int, value: int, suffix: str, tip: str) -> QSpinBox:
    box = QSpinBox(parent)
    box.setObjectName(name)
    box.setRange(low, high)
    box.setValue(value)
    box.setSuffix(suffix)
    box.setToolTip(tip)
    box.setKeyboardTracking(False)  # a value is taken when Enter is pressed or the field is left, not on every digit
    return box


def tabler(name: str):
    """The picture of a Tabler icon in a theme colour, for a `GroupMenuButton`."""
    return lambda color: load_tabler_icon(name, color=color, size=24)


def direction_icon(descending: bool, _off_color: str, on_color: str):
    """The order's direction: an arrow up (ascending) or down (descending), always in the accent colour (both are a choice, neither is "off")."""
    return load_tabler_icon("arrow-down" if descending else "arrow-up", color=on_color, size=24)


def cursor_icon(on: bool, off_color: str, on_color: str):
    return load_tabler_icon("crosshair", color=on_color if on else off_color, size=24)


def reset_button(parent: QWidget, name: str, slot: Callable[[], None]) -> QToolButton:
    button = QToolButton(parent)
    button.setObjectName(name)
    button.setText("Reset to default")
    button.setToolTip(RESET_TIP)
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    button.setAutoRaise(True)
    button.clicked.connect(lambda _checked=False: slot())
    return button


def pressed(buttons: dict) -> str | None:
    """The key of the pressed button of a choice row (`segmented_buttons`, `icon_choice_buttons`), or None."""
    return next((key for key, button in buttons.items() if button.isChecked()), None)


def press(buttons: dict, key: str) -> None:
    """Press the button of `key` (an unknown key is ignored)."""
    if key in buttons:
        buttons[key].setChecked(True)


def _restore_choice(store: UiStateStore, key: str, buttons: dict) -> None:
    saved = store.get(key)
    if isinstance(saved, str):
        press(buttons, saved)  # an unknown value leaves the default pressed


def _remember_choice(store: UiStateStore, key: str, buttons: dict) -> None:
    # `toggled`, not `buttonClicked`: a reset presses a button in code, and that must be remembered too
    for choice, button in buttons.items():
        button.toggled.connect(lambda on, picked=choice: store.set(key, picked) if on else None)


# -- Points & lines and Legend ------------------------------------------------------------------------------------------------------------

class PlotLookControls(QObject):
    settings_changed = pyqtSignal()

    def __init__(
        self,
        parent: QWidget,
        keys: PlotLookKeys,
        defaults: SensorgramViewSettings | None = None,
        *,
        object_prefix: str = "view",
        dot_limit: bool = False,
        legend_position: bool = False,
        joined: bool = True,
        ask_to_raise: Callable[[QWidget, int, int], bool] | None = None,
    ) -> None:
        super().__init__(parent)
        self._keys = keys
        self._defaults = (defaults or SensorgramViewSettings()).sanitized()
        self._syncing = False
        self._line_count = 0  # how many lines the plot draws now (told by the plot after each draw): the Points toggle shows itself off above the Dot limit
        self._ask_to_raise = ask_to_raise
        self._has_position = legend_position
        d = self._defaults

        # -- Points & lines ----------------------------------------------------------------------------------------------------
        if dot_limit:
            self.show_points: IconToggle = PointsToggle(points_toggle_icon, POINTS_TIP, parent)
            self.show_points.dots_requested.connect(self._dots_requested)
        else:
            self.show_points = IconToggle(points_toggle_icon, POINTS_TIP_PLAIN, parent)
        self.show_points.setChecked(d.show_points)
        self.point_size = _spin(parent, f"{object_prefix}_point_size", *POINT_SIZE_RANGE, d.point_size, " px", POINT_SIZE_TIP)
        self.dots_limit: QSpinBox | None = None
        if dot_limit:
            self.dots_limit = _spin(parent, f"{object_prefix}_dots_limit", *DOTS_MAX_LINES_RANGE, d.dots_max_lines, " lines", DOTS_LIMIT_TIP)
            self.dots_limit.valueChanged.connect(lambda _value: self._refresh_cap())
        self.show_lines = IconToggle(lines_toggle_icon, LINES_TOGGLE_TIP, parent)
        self.show_lines.setChecked(d.show_lines)
        self.line_width = QDoubleSpinBox(parent)
        self.line_width.setObjectName(f"{object_prefix}_line_width")
        self.line_width.setDecimals(1)
        self.line_width.setRange(*LINE_WIDTH_RANGE)
        self.line_width.setSingleStep(LINE_WIDTH_STEP)
        self.line_width.setValue(d.line_width)
        self.line_width.setSuffix(" px")
        self.line_width.setToolTip(LINE_WIDTH_TIP)
        self.line_width.setKeyboardTracking(False)
        style_holder, self._style_group, self._style_buttons = icon_choice_buttons(parent, LINE_STYLES, LINE_STYLE_PICTURES, LINE_STYLE_TIPS, f"{object_prefix}_line_style")
        self.set_line_style(d.line_style)
        draw_holder, self._draw_group, self._draw_buttons = segmented_buttons(parent, DRAW_MODES, DRAW_LABELS, DRAW_TIPS, f"{object_prefix}_draw")
        press(self._draw_buttons, d.draw_mode)
        draw_holder.setToolTip(DRAW_TIP)
        self.edit_order_button = QToolButton(parent)
        self.edit_order_button.setObjectName(f"{object_prefix}_draw_edit")
        self.edit_order_button.setText("Order...")
        self.edit_order_button.setToolTip(DRAW_EDIT_TIP)
        self.edit_order_button.clicked.connect(lambda _checked=False: self.open_line_order())
        self._line_order: PlotLineOrderModule | None = None
        self._plot_id = ""
        self._plot_title = ""
        self._line_source: Callable[[], Sequence[LineRow]] | None = None
        self._order_dialog: LineOrderDialog | None = None
        self._parent_widget = parent
        self._lines_reset = reset_button(parent, f"{object_prefix}_lines_reset", self.reset_lines)
        rows = [("Points", widgets_row(parent, self.show_points, self.point_size))]
        if self.dots_limit is not None:
            rows.append(("Dot limit", self.dots_limit))
        rows.append(("Lines", widgets_row(parent, self.show_lines, self.line_width)))
        rows.append(("Draw order", widgets_row(parent, draw_holder, self.edit_order_button)))
        if joined:
            rows.append(("Joined", style_holder))
        else:
            style_holder.hide()  # kept (the buttons still hold the value) but in no panel
        self.lines_menu = GroupMenuButton(points_lines_icon, LINES_TIP, rows, parent, footer=self._lines_reset)

        # -- Legend -------------------------------------------------------------------------------------------------------------
        modes_holder, self._mode_group, self._mode_buttons = segmented_buttons(parent, LEGEND_MODES, LEGEND_MODE_LABELS, LEGEND_MODE_TIPS, f"{object_prefix}_legend")
        self.set_legend_mode(d.legend_mode)
        self.legend_text = _spin(parent, f"{object_prefix}_legend_text", *LEGEND_TEXT_RANGE, d.legend_text_pt, " pt", LEGEND_TEXT_TIP)
        self.legend_spacing = _spin(parent, f"{object_prefix}_legend_spacing", *LEGEND_SPACING_RANGE, d.legend_spacing_px, " px", LEGEND_SPACING_TIP)
        self.legend_counts = QCheckBox("(n=12)", parent)
        self.legend_counts.setObjectName(f"{object_prefix}_legend_counts")
        self.legend_counts.setChecked(d.legend_counts)
        self.legend_counts.setToolTip(LEGEND_COUNTS_TIP)
        self.legend_over_bar = QCheckBox("at the edge of the plot", parent)
        self.legend_over_bar.setObjectName(f"{object_prefix}_legend_over_bar")
        self.legend_over_bar.setChecked(d.legend_over_bar)
        self.legend_over_bar.setToolTip(LEGEND_OVER_BAR_TIP)
        # -- the corner of the plot area the legend sits in (legend_corner.py): a 2 x 2 block of cells, and a note for the Sensorgram while its step table keeps the legend at its edge
        self.corner_picker = LegendCornerPicker(CORNER_TIPS, parent, object_prefix=f"{object_prefix}_legend")
        self.corner_picker.setToolTip(CORNER_TIP)
        press(self.corner_picker.buttons, d.legend_corner)
        self.corner_note = QLabel("", parent)
        self.corner_note.setObjectName(f"{object_prefix}_legend_corner_note")
        self.corner_note.setWordWrap(True)
        self.corner_note.setMaximumWidth(260)
        # -- the order of the rows (legend_order.py): Group / Name / Value + a direction arrow; "Value of" only while Value is picked; the cursor toggle; a note when the order has no number
        sort_holder, self._sort_group, self._sort_buttons = segmented_buttons(parent, SORT_KEYS, SORT_LABELS, SORT_TIPS, f"{object_prefix}_legend_sort")
        self.sort_descending = IconToggle(direction_icon, SORT_DIRECTION_TIP, parent)
        self.sort_descending.setObjectName(f"{object_prefix}_legend_descending")
        value_holder, self._value_group, self._value_buttons = segmented_buttons(parent, VALUE_SOURCES, VALUE_LABELS, VALUE_TIPS, f"{object_prefix}_legend_value")
        self.show_cursor = IconToggle(cursor_icon, CURSOR_TIP, parent)
        self.show_cursor.setObjectName(f"{object_prefix}_show_cursor")
        self.show_cursor.setChecked(d.show_cursor)
        self.order_note = QLabel("", parent)
        self.order_note.setObjectName(f"{object_prefix}_legend_order_note")
        self.order_note.setWordWrap(True)
        self.order_note.setMaximumWidth(260)
        self.press_sort(d.legend_sort)
        self.sort_descending.setChecked(d.legend_descending)
        press(self._value_buttons, d.legend_value)
        self._legend_reset = reset_button(parent, f"{object_prefix}_legend_reset", self.reset_legend)
        order_row = widgets_row(parent, sort_holder, self.sort_descending)
        legend_rows = [
            ("Legend", modes_holder), ("Text", self.legend_text), ("Row gap", self.legend_spacing), ("ROI count", self.legend_counts),
            ("Corner", self.corner_picker), ("", self.corner_note), ("Order", order_row), ("Value of", value_holder), ("Cursor", self.show_cursor), ("", self.order_note),
        ]
        if legend_position:
            legend_rows.append(("Position", self.legend_over_bar))
        else:
            self.legend_over_bar.hide()
        self.legend_menu = GroupMenuButton(tabler("list-details"), LEGEND_TIP if legend_position else LEGEND_TIP_PLAIN, legend_rows, parent, footer=self._legend_reset)
        self._value_holder = value_holder
        self._sync_order_rows()
        self.set_order_note("")
        self.set_corner_note("")

        style_ribbon_number_field(self.point_size, f"{POINT_SIZE_RANGE[1]} px")
        if self.dots_limit is not None:
            style_ribbon_number_field(self.dots_limit, f"{DOTS_MAX_LINES_RANGE[1]} lines")
        style_ribbon_number_field(self.line_width, f"{LINE_WIDTH_RANGE[1]:.1f} px")
        style_ribbon_number_field(self.legend_text, f"{LEGEND_TEXT_RANGE[1]} pt")
        style_ribbon_number_field(self.legend_spacing, f"{LEGEND_SPACING_RANGE[0]} px")
        signals = [
            self.show_points.toggled, self.show_lines.toggled, self.point_size.valueChanged, self.line_width.valueChanged, self._style_group.buttonClicked,
            self.legend_text.valueChanged, self.legend_spacing.valueChanged, self.legend_counts.toggled, self._mode_group.buttonClicked, self.legend_over_bar.toggled,
            self.corner_picker.group.buttonClicked, self._sort_group.buttonClicked, self.sort_descending.toggled, self._value_group.buttonClicked, self.show_cursor.toggled,
            self._draw_group.buttonClicked,
        ]
        if self.dots_limit is not None:
            signals.append(self.dots_limit.valueChanged)
        for signal in signals:
            signal.connect(self._changed)
        self._sort_group.buttonClicked.connect(lambda _button: self._sync_order_rows())
        self._draw_group.buttonClicked.connect(lambda _button: self._sync_draw_edit())
        self._sync_draw_edit()
        self.refresh_theme()

    # -- values ---------------------------------------------------------------------------------------------------------------

    def settings(self) -> SensorgramViewSettings:
        """The plot's options as set now. A field this plot has no control for (the band and anchors of the Sensorgram, a window's Dot limit) is the plot's default."""
        return replace(
            self._defaults,
            show_points=self.show_points.isChecked(), show_lines=self.show_lines.isChecked(),  # the choice: the toggle can only look off (`PointsToggle`), the plot applies the limit
            point_size=self.point_size.value(), line_width=float(self.line_width.value()), line_style=self.line_style(),
            dots_max_lines=self.dots_limit.value() if self.dots_limit is not None else self._defaults.dots_max_lines,
            legend_mode=self.legend_mode(), legend_text_pt=self.legend_text.value(), legend_spacing_px=self.legend_spacing.value(),
            legend_counts=self.legend_counts.isChecked(),
            legend_over_bar=self.legend_over_bar.isChecked() if self._has_position else self._defaults.legend_over_bar,
            legend_corner=pressed(self.corner_picker.buttons) or DEFAULT_CORNER,
            legend_sort=pressed(self._sort_buttons) or SORT_GROUP, legend_descending=self.sort_descending.isChecked(),
            legend_value=pressed(self._value_buttons) or VALUE_MEAN, show_cursor=self.show_cursor.isChecked(),
            draw_mode=pressed(self._draw_buttons) or DRAW_LEGEND,
        ).sanitized()

    # -- the draw order's table (Manual) ----------------------------------------------------------------------------------------

    def attach_line_order(self, module: PlotLineOrderModule | None, plot_id: str, title: str, source: Callable[[], Sequence[LineRow]]) -> None:
        """Connect this plot to the session's manual stacking list (`module`, key `plot_id`) and to what it draws (`source`: the lines now, in the plot's own order). A change of the list
        reports as a change of the look, so the plot redraws through the way it already does. Without a module (a plot with no session) the table is not offered."""
        self._line_order, self._plot_id, self._plot_title, self._line_source = module, plot_id, title, source
        if module is not None:
            module.changed.connect(self._line_order_changed)
        self._sync_draw_edit()

    def _line_order_changed(self, plot: str) -> None:
        """The session's manual list of this plot changed (a bound method, so the connection ends with this object)."""
        if plot == self._plot_id:
            self._changed()

    def manual_order(self) -> tuple[str, ...]:
        """The plot's manual list, first = in front (empty: none set, or no session)."""
        return () if self._line_order is None else self._line_order.order(self._plot_id)

    def lines_changed(self) -> None:
        """The plot drew other lines: the open Line order table follows them."""
        if self._order_dialog is not None and self._order_dialog.isVisible():
            self._order_dialog.refresh()

    def open_line_order(self) -> None:
        """Show the Line order table of this plot (made once, then shown again)."""
        if self._line_order is None or self._line_source is None:
            return
        if self._order_dialog is None:
            self._order_dialog = LineOrderDialog(self._parent_widget, self._plot_title, self._line_source, self._line_order, self._plot_id, mode_now=lambda: self.settings().draw_mode)
        self._order_dialog.refresh()
        self._order_dialog.show()
        self._order_dialog.raise_()
        self._order_dialog.activateWindow()

    def _sync_draw_edit(self) -> None:
        """The Order... button works while the mode is Manual (and the plot has a session list); the table follows the mode in its hint."""
        manual = pressed(self._draw_buttons) == DRAW_MANUAL
        self.edit_order_button.setEnabled(manual and self._line_order is not None)
        self.edit_order_button.setToolTip(DRAW_EDIT_TIP if manual else f"{DRAW_EDIT_TIP}\n\n{DRAW_EDIT_OFF_TIP}")
        if self._order_dialog is not None:
            self._order_dialog.refresh()

    def press_sort(self, key: str) -> None:
        """Press the button of the order's key without reporting it (an unknown key is ignored)."""
        press(self._sort_buttons, key)

    def _sync_order_rows(self) -> None:
        """"Value of" means something only while the order is Value: it is shown then."""
        self.legend_menu.set_row_visible(self._value_holder, pressed(self._sort_buttons) == SORT_VALUE)

    def set_order_note(self, text: str) -> None:
        """Say why the order cannot read its number now ("Select one step on the plan bar"); "" hides the note. Reports no change."""
        self.order_note.setText(text)
        self.legend_menu.set_row_visible(self.order_note, bool(text))

    def set_corner_note(self, text: str) -> None:
        """Say why the corner has no effect now (the Sensorgram: its step table keeps the legend at its edge); "" hides the note. Reports no change."""
        self.corner_note.setText(text)
        self.legend_menu.set_row_visible(self.corner_note, bool(text))

    def legend_mode(self) -> str:
        return pressed(self._mode_buttons) or LEGEND_AUTO

    def set_legend_mode(self, mode: str) -> None:
        """Press the button of `mode` without reporting it (an unknown mode is ignored)."""
        press(self._mode_buttons, mode)

    def line_style(self) -> str:
        return pressed(self._style_buttons) or LINE_STRAIGHT

    def set_line_style(self, style: str) -> None:
        """Press the button of `style` without reporting it (an unknown style is ignored)."""
        press(self._style_buttons, style)

    # -- the Dot limit (the Sensorgram only) -------------------------------------------------------------------------------------

    def set_line_count(self, count: int) -> None:
        """The plot says how many lines it draws now (after every draw): above the Dot limit the Points toggle shows itself off and says why. Reports no change."""
        self._line_count = max(int(count), 0)
        self._refresh_cap()

    def _refresh_cap(self) -> None:
        if self.dots_limit is None or not isinstance(self.show_points, PointsToggle):
            return
        limit = self.dots_limit.value()
        capped = self._line_count > limit
        self.show_points.set_capped(capped, POINTS_CAPPED_NOTE.format(lines=self._line_count, limit=limit) if capped else "")

    def _dots_requested(self) -> None:
        """The user clicked the Points toggle while the Dot limit holds: say so, and raise the limit to the number of lines drawn if they agree (the toggle is then on, one change reported)."""
        lines, limit = self._line_count, self.dots_limit.value()
        if self._ask_to_raise is None or not self._ask_to_raise(self.show_points, lines, limit):
            return

        def raise_it() -> None:
            self.show_points.setChecked(True)
            self.dots_limit.setValue(max(lines, limit))

        self._set_quietly(raise_it)

    # -- resets -------------------------------------------------------------------------------------------------------------------

    def reset_lines(self) -> None:
        """The dots and the lines back to the plot's defaults (one change reported, not one per control)."""
        d = self._defaults

        def put_back() -> None:
            self.show_points.setChecked(d.show_points)
            self.show_lines.setChecked(d.show_lines)
            self.point_size.setValue(d.point_size)
            if self.dots_limit is not None:
                self.dots_limit.setValue(d.dots_max_lines)
            self.line_width.setValue(d.line_width)
            self.set_line_style(d.line_style)
            press(self._draw_buttons, d.draw_mode)

        self._set_quietly(put_back)
        self._sync_draw_edit()

    def reset_legend(self) -> None:
        d = self._defaults

        def put_back() -> None:
            self.set_legend_mode(d.legend_mode)
            self.legend_text.setValue(d.legend_text_pt)
            self.legend_spacing.setValue(d.legend_spacing_px)
            self.legend_counts.setChecked(d.legend_counts)
            self.legend_over_bar.setChecked(d.legend_over_bar)
            press(self.corner_picker.buttons, d.legend_corner)
            self.press_sort(d.legend_sort)
            self.sort_descending.setChecked(d.legend_descending)
            press(self._value_buttons, d.legend_value)
            self.show_cursor.setChecked(d.show_cursor)

        self._set_quietly(put_back)
        self._sync_order_rows()

    def _set_quietly(self, change) -> None:
        self._syncing = True
        try:
            change()
        finally:
            self._syncing = False
        self.settings_changed.emit()

    def _changed(self, *_args: object) -> None:
        if not self._syncing:
            self.settings_changed.emit()

    # -- theme --------------------------------------------------------------------------------------------------------------------

    def refresh_theme(self) -> None:
        theme = get_active_theme()
        self.show_points.refresh_theme()
        self.show_lines.refresh_theme()
        self.sort_descending.refresh_theme()
        self.show_cursor.refresh_theme()
        self.lines_menu.refresh_theme()
        self.legend_menu.refresh_theme()
        segment = segment_stylesheet()
        for button in (*self._mode_buttons.values(), *self._sort_buttons.values(), *self._value_buttons.values(), *self._draw_buttons.values()):
            button.setStyleSheet(segment)
        for button in self._style_buttons.values():  # picture buttons: they style and recolour themselves
            button.refresh_theme()
        self.corner_picker.refresh_theme()
        for button in (self._lines_reset, self._legend_reset):
            button.setIcon(load_tabler_icon("refresh", color=theme.text_primary, size=18))

    # -- remembered state -----------------------------------------------------------------------------------------------------------

    def restore_ui_state(self, store: UiStateStore, *, report: bool = True) -> None:
        """Take the remembered values and remember every later change. `report=False`: the owner reports the restore itself (the Sensorgram does, after its own controls)."""
        k = self._keys
        self._syncing = True
        try:
            pairs = [
                (k.points, self.show_points), (k.lines, self.show_lines), (k.point_size, self.point_size), (k.line_width, self.line_width),
                (k.legend_text, self.legend_text), (k.legend_spacing, self.legend_spacing), (k.legend_counts, self.legend_counts),
                (k.legend_descending, self.sort_descending), (k.show_cursor, self.show_cursor),
            ]
            if self._has_position:
                pairs.append((k.legend_over_bar, self.legend_over_bar))
            if self.dots_limit is not None:
                pairs.append((k.dots_limit, self.dots_limit))
            for key, widget in pairs:
                store.bind(key, widget)
            _restore_choice(store, k.legend_mode, self._mode_buttons)
            _restore_choice(store, k.legend_corner, self.corner_picker.buttons)
            _restore_choice(store, k.line_style, self._style_buttons)
            _restore_choice(store, k.legend_sort, self._sort_buttons)
            _restore_choice(store, k.legend_value, self._value_buttons)
            _restore_choice(store, k.draw_mode, self._draw_buttons)
        finally:
            self._syncing = False
        self._sync_order_rows()
        self._sync_draw_edit()
        _remember_choice(store, k.legend_mode, self._mode_buttons)
        _remember_choice(store, k.legend_corner, self.corner_picker.buttons)
        _remember_choice(store, k.line_style, self._style_buttons)
        _remember_choice(store, k.legend_sort, self._sort_buttons)
        _remember_choice(store, k.legend_value, self._value_buttons)
        _remember_choice(store, k.draw_mode, self._draw_buttons)
        if report:
            self.settings_changed.emit()  # whatever was restored is what the plot should show


# -- Uncertainty (the plot windows) -------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class UncertaintyKeys:
    show: str
    kind: str
    style: str

    @staticmethod
    def with_prefix(prefix: str) -> UncertaintyKeys:
        return UncertaintyKeys(show=f"{prefix}_uncertainty_show", kind=f"{prefix}_uncertainty_kind", style=f"{prefix}_uncertainty_style")


class UncertaintyControls(QObject):
    """The Uncertainty menu of a plot window: show / hide, SEM or SD, Bars or Band. Its own values and keys per window, the one definition for all."""

    settings_changed = pyqtSignal()

    def __init__(self, parent: QWidget, keys: UncertaintyKeys, *, object_prefix: str = "plot") -> None:
        super().__init__(parent)
        self._keys = keys
        self._syncing = False
        self.show_toggle = IconToggle(error_bars_icon, UNCERTAINTY_TOGGLE_TIP, parent)
        self.show_toggle.setChecked(True)
        kind_holder, self._kind_group, self._kind_buttons = segmented_buttons(
            parent, UNCERTAINTY_KINDS, UNCERTAINTY_KIND_LABELS, UNCERTAINTY_KIND_TIPS, f"{object_prefix}_uncertainty"
        )
        shape_holder, self._shape_group, self._shape_buttons = icon_choice_buttons(
            parent, UNCERTAINTY_STYLES, {UNCERTAINTY_BARS: bars_picture(), UNCERTAINTY_BAND: band_picture()}, UNCERTAINTY_STYLE_TIPS, f"{object_prefix}_uncertainty_style"
        )
        self._reset = reset_button(parent, f"{object_prefix}_uncertainty_reset", self.reset)
        self.menu = GroupMenuButton(uncertainty_menu_icon, UNCERTAINTY_TIP, (("Show", self.show_toggle), ("Value", kind_holder), ("Style", shape_holder)), parent, footer=self._reset)
        for signal in (self.show_toggle.toggled, self._kind_group.buttonClicked, self._shape_group.buttonClicked):
            signal.connect(self._changed)
        self.refresh_theme()

    def settings(self) -> UncertaintyLook:
        return UncertaintyLook(
            show=self.show_toggle.isChecked(), kind=pressed(self._kind_buttons) or UNCERTAINTY_SEM, style=pressed(self._shape_buttons) or UNCERTAINTY_BARS
        ).sanitized()

    def reset(self) -> None:
        d = UncertaintyLook()
        self._syncing = True
        try:
            self.show_toggle.setChecked(d.show)
            press(self._kind_buttons, d.kind)
            press(self._shape_buttons, d.style)
        finally:
            self._syncing = False
        self.settings_changed.emit()

    def _changed(self, *_args: object) -> None:
        if not self._syncing:
            self.settings_changed.emit()

    def refresh_theme(self) -> None:
        theme = get_active_theme()
        self.show_toggle.refresh_theme()
        self.menu.refresh_theme()
        segment = segment_stylesheet()
        for button in self._kind_buttons.values():
            button.setStyleSheet(segment)
        for button in self._shape_buttons.values():
            button.refresh_theme()
        self._reset.setIcon(load_tabler_icon("refresh", color=theme.text_primary, size=18))

    def restore_ui_state(self, store: UiStateStore) -> None:
        k = self._keys
        self._syncing = True
        try:
            store.bind(k.show, self.show_toggle)
            _restore_choice(store, k.kind, self._kind_buttons)
            _restore_choice(store, k.style, self._shape_buttons)
        finally:
            self._syncing = False
        _remember_choice(store, k.kind, self._kind_buttons)
        _remember_choice(store, k.style, self._shape_buttons)
        self.settings_changed.emit()
