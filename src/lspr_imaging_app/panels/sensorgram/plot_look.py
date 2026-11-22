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

from collections.abc import Callable
from dataclasses import dataclass, replace

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QDoubleSpinBox, QSpinBox, QToolButton, QWidget

from lspr_ui import get_active_theme, load_tabler_icon

from ..ribbon_fields import style_ribbon_number_field
from ..spectra.pick_menu import PickMenu
from ..spectra.view_menu import IconToggle
from ..ui_state import UiStateStore
from .group_menu import GroupMenuButton, icon_choice_buttons, segment_stylesheet, segmented_buttons, widgets_row
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

    @staticmethod
    def with_prefix(prefix: str) -> PlotLookKeys:
        """``prefix`` + a field name: ``with_prefix("sensorgram/lbl_plot")`` -> ``"sensorgram/lbl_plot_point_size"`` ..."""
        return PlotLookKeys(
            points=f"{prefix}_points", lines=f"{prefix}_lines", point_size=f"{prefix}_point_size", dots_limit=f"{prefix}_dots_limit", line_width=f"{prefix}_line_width",
            line_style=f"{prefix}_line_style", legend_mode=f"{prefix}_legend_mode", legend_text=f"{prefix}_legend_text_pt", legend_spacing=f"{prefix}_legend_spacing",
            legend_counts=f"{prefix}_legend_counts", legend_over_bar=f"{prefix}_legend_over_bar",
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
        self._lines_reset = reset_button(parent, f"{object_prefix}_lines_reset", self.reset_lines)
        rows = [("Points", widgets_row(parent, self.show_points, self.point_size))]
        if self.dots_limit is not None:
            rows.append(("Dot limit", self.dots_limit))
        rows.append(("Lines", widgets_row(parent, self.show_lines, self.line_width)))
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
        self._legend_reset = reset_button(parent, f"{object_prefix}_legend_reset", self.reset_legend)
        legend_rows = [("Legend", modes_holder), ("Text", self.legend_text), ("Row gap", self.legend_spacing), ("ROI count", self.legend_counts)]
        if legend_position:
            legend_rows.append(("Position", self.legend_over_bar))
        else:
            self.legend_over_bar.hide()
        self.legend_menu = GroupMenuButton(tabler("list-details"), LEGEND_TIP if legend_position else LEGEND_TIP_PLAIN, legend_rows, parent, footer=self._legend_reset)

        style_ribbon_number_field(self.point_size, f"{POINT_SIZE_RANGE[1]} px")
        if self.dots_limit is not None:
            style_ribbon_number_field(self.dots_limit, f"{DOTS_MAX_LINES_RANGE[1]} lines")
        style_ribbon_number_field(self.line_width, f"{LINE_WIDTH_RANGE[1]:.1f} px")
        style_ribbon_number_field(self.legend_text, f"{LEGEND_TEXT_RANGE[1]} pt")
        style_ribbon_number_field(self.legend_spacing, f"{LEGEND_SPACING_RANGE[0]} px")
        signals = [
            self.show_points.toggled, self.show_lines.toggled, self.point_size.valueChanged, self.line_width.valueChanged, self._style_group.buttonClicked,
            self.legend_text.valueChanged, self.legend_spacing.valueChanged, self.legend_counts.toggled, self._mode_group.buttonClicked, self.legend_over_bar.toggled,
        ]
        if self.dots_limit is not None:
            signals.append(self.dots_limit.valueChanged)
        for signal in signals:
            signal.connect(self._changed)
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
        ).sanitized()

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

        self._set_quietly(put_back)

    def reset_legend(self) -> None:
        d = self._defaults

        def put_back() -> None:
            self.set_legend_mode(d.legend_mode)
            self.legend_text.setValue(d.legend_text_pt)
            self.legend_spacing.setValue(d.legend_spacing_px)
            self.legend_counts.setChecked(d.legend_counts)
            self.legend_over_bar.setChecked(d.legend_over_bar)

        self._set_quietly(put_back)

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
        self.lines_menu.refresh_theme()
        self.legend_menu.refresh_theme()
        segment = segment_stylesheet()
        for button in self._mode_buttons.values():
            button.setStyleSheet(segment)
        for button in self._style_buttons.values():  # picture buttons: they style and recolour themselves
            button.refresh_theme()
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
            ]
            if self._has_position:
                pairs.append((k.legend_over_bar, self.legend_over_bar))
            if self.dots_limit is not None:
                pairs.append((k.dots_limit, self.dots_limit))
            for key, widget in pairs:
                store.bind(key, widget)
            _restore_choice(store, k.legend_mode, self._mode_buttons)
            _restore_choice(store, k.line_style, self._style_buttons)
        finally:
            self._syncing = False
        _remember_choice(store, k.legend_mode, self._mode_buttons)
        _remember_choice(store, k.line_style, self._style_buttons)
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
