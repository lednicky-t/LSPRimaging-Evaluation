"""Sensorgram ribbon, "View" tab: how the plot is drawn (2026-11-13). Display only: nothing here changes a number.

Screen purpose: every option of the plot's looks in one place, instead of a few groups on the Plot tab and a few on the Experimental plan tab.

Main user actions (groups of the tab, left to right; each pop-up is a `GroupMenuButton`, so they all look and work alike):
- **Show** - four toggles: the shaded SD / SEM band of an average (`band`), the anchors and the baseline made from them (`anchors`: the hollow squares of the Anchor points
  method and the dashed baseline curve, which the Range method's anchor steps have too), and the two that were already here, the mask marks and the out-of-date marks (the panel
  owns those two and puts them in the group).
- **Points & lines** - a pop-up (`lines_menu`): the dots (show / hide, size), the lines (show / hide, width), how the points are joined (Straight / Spline, two pictures, `line_icons.py`)
  and a "Reset to default".
- **Legend** - a pop-up (`legend_menu`): Auto / Show / Hide, the text size (with a chain button that links it to the Stats menu's, `text_link.py`), the gap between its rows, whether a group's
  name carries its number of ROIs ("Group A (n=12)"), whether it sits at the edge of the plot instead of just inside the plan bar, and a "Reset to default". The step table's rows line up with the
  legend's rows, so the gap and the place move them too.
- **Steps**, **Evaluation** and **Stats** - the plan bar's, its evaluation markers' and the step table's own menus (`plan_group.py`, `stats_group.py`, the same kind of pop-up).

Layout of a pop-up: a small grid, one caption and its control per row, the reset button under it (`group_menu.py`). Pictures of the Points & lines menu: `line_icons.py`.

Important states: the band toggle is greyed out unless the plot shows an average (there is no band on a single ROI), the anchors toggle unless a curve is made from anchors
(Anchor points, or the Range method with anchor steps marked); both keep their remembered position meanwhile. The line width and the Spline style are the two options that can
cost time (see their tool tips); 1 px and Straight, the defaults, are Qt's fast path.

`settings()` returns the current values as one `SensorgramViewSettings`; `settings_changed` fires after any change. Every control is remembered (CLAUDE.md "Remembering UI
state"): the spin boxes and toggles by `store.bind`, the legend mode and the line style by their rows in `storage/ui_state_keys.py`.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QCheckBox, QDoubleSpinBox, QSpinBox, QToolButton, QWidget

from lspr_ui import get_active_theme, load_tabler_icon

from ...storage.ui_state_keys import SENSORGRAM_LEGEND_MODE, SENSORGRAM_LINE_STYLE, read
from ..ribbon_fields import style_ribbon_number_field
from ..spectra.view_menu import IconToggle
from ..ui_state import UiStateStore
from .group_menu import GroupMenuButton, icon_choice_buttons, segment_stylesheet, segmented_buttons, widgets_row
from .line_icons import joint_picture, lines_toggle_icon, points_lines_icon, points_toggle_icon
from .plot import DOTS_MAX_LINES, MAX_LEGEND_ENTRIES, MAX_LEGEND_SERIES
from .stat_icons import band_icon
from .text_link import LINK_KEY, make_link_toggle
from .view_settings import (
    DEFAULT_LEGEND_SPACING_PX,
    DEFAULT_LEGEND_TEXT_PT,
    DEFAULT_LINE_WIDTH,
    DEFAULT_POINT_SIZE,
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
    SensorgramViewSettings,
)

BAND_KEY = "sensorgram/view_band"
ANCHORS_KEY = "sensorgram/view_anchors"
POINTS_KEY = "sensorgram/view_points"
LINES_KEY = "sensorgram/view_lines"
POINT_SIZE_KEY = "sensorgram/view_point_size"
LINE_WIDTH_KEY = "sensorgram/view_line_width"
LEGEND_TEXT_KEY = "sensorgram/view_legend_text_pt"
LEGEND_SPACING_KEY = "sensorgram/view_legend_spacing"
LEGEND_COUNTS_KEY = "sensorgram/view_legend_counts"
LEGEND_OVER_BAR_KEY = "sensorgram/view_legend_over_bar"

BAND_TIP = (
    "Show the shaded band around an average line: the standard deviation of the ROIs, or the standard error of the mean (choose which with the band pick-up on the Plot tab). "
    "Only an average has one. Hiding it changes no number."
)
BAND_OFF_TIP = "There is no band to show: the plot has one line per ROI. The band belongs to Average all / Average by group (Plot tab)."
ANCHORS_TIP = (
    "Show the anchors and the baseline made from them: the hollow squares of the Anchor points method (where each anchor point sits on its line) and the dashed baseline "
    "curve through the anchors (drawn while Subtract is off; the Range method's anchor steps have the curve too). Hiding them changes no number: the baseline is still made "
    "from the same anchors, and Subtract still takes it off."
)
ANCHORS_OFF_TIP = "There are no anchors or baseline curve to show: the baseline method is neither Anchor points nor Range with anchor steps marked (Plot tab)."
LINES_TIP = "Points & lines: show or hide the dots and the lines, their size and width, how the points are joined (straight or a smooth spline), and a reset to the defaults."
POINTS_TIP = (
    f"Show or hide the dots on the measured points. Dots are drawn for up to {DOTS_MAX_LINES} lines: with more they merge into the lines and every redraw would be slow."
)
POINT_SIZE_TIP = "Size of the dots, in pixels."
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
LEGEND_MODE_TIPS = {
    LEGEND_AUTO: (
        f"Auto: the legend names up to {MAX_LEGEND_ENTRIES} lines, and with more than {MAX_LEGEND_SERIES} it stays only while it takes at most half the height of the plot "
        "(a legend of a hundred ROIs would cover it)."
    ),
    LEGEND_SHOW: "Show: always, however many lines there are. A long legend can cover the plot.",
    LEGEND_HIDE: "Hide: never drawn. The step table still lines up as if it were there, so its rows stay where they are.",
}
LEGEND_MODE_LABELS = {LEGEND_AUTO: "Auto", LEGEND_SHOW: "Show", LEGEND_HIDE: "Hide"}
LEGEND_TEXT_TIP = "Text size of the legend, in points."
LEGEND_SPACING_TIP = "Space added between the legend's rows, in pixels (0 = as before; negative = closer). The step table's rows line up with the legend's, so they move with it."
LEGEND_OVER_BAR_TIP = (
    "Put the legend at the very edge of the plot, over the plan bar and the evaluation markers hanging from it, instead of just inside the bar. The step table's rows line up with the "
    "legend, so they go up with it (over the bar). Only matters while the step stats table is on; with it off the legend is where it always was."
)
LEGEND_COUNTS_TIP ="Write the number of ROIs after the name of a group or an average: \"Group A (n=12)\" instead of \"Group A\". A line of one ROI is always \"ROI 5\"."
RESET_TIP = "Put these options back to what the plot always did."


def anchors_icon(on: bool, off_color: str, on_color: str) -> QIcon:
    """The anchor in the accent colour when the squares are shown; the crossed-out anchor, dimmed, when they are hidden (as the mask and clock toggles)."""
    return load_tabler_icon("anchor" if on else "anchor-off", color=on_color if on else off_color, size=24)


def _tabler(name: str):
    """The picture of a Tabler icon in a theme colour, for a `GroupMenuButton`."""
    return lambda color: load_tabler_icon(name, color=color, size=24)


def _spin(parent: QWidget, name: str, low: int, high: int, value: int, suffix: str, tip: str) -> QSpinBox:
    box = QSpinBox(parent)
    box.setObjectName(name)
    box.setRange(low, high)
    box.setValue(value)
    box.setSuffix(suffix)
    box.setToolTip(tip)
    box.setKeyboardTracking(False)  # a value is taken when Enter is pressed or the field is left, not on every digit
    return box


class SensorgramViewControls(QObject):
    settings_changed = pyqtSignal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._syncing = False
        defaults = SensorgramViewSettings()

        self.band = IconToggle(band_icon, BAND_TIP, parent)
        self.band.setChecked(defaults.show_band)
        self.anchors = IconToggle(anchors_icon, ANCHORS_TIP, parent)
        self.anchors.setChecked(defaults.show_anchors)

        # -- Points & lines ----------------------------------------------------------------------------------------------------
        self.show_points = IconToggle(points_toggle_icon, POINTS_TIP, parent)
        self.show_points.setChecked(defaults.show_points)
        self.point_size = _spin(parent, "view_point_size", *POINT_SIZE_RANGE, DEFAULT_POINT_SIZE, " px", POINT_SIZE_TIP)
        self.show_lines = IconToggle(lines_toggle_icon, LINES_TOGGLE_TIP, parent)
        self.show_lines.setChecked(defaults.show_lines)
        self.line_width = QDoubleSpinBox(parent)
        self.line_width.setObjectName("view_line_width")
        self.line_width.setDecimals(1)
        self.line_width.setRange(*LINE_WIDTH_RANGE)
        self.line_width.setSingleStep(LINE_WIDTH_STEP)
        self.line_width.setValue(DEFAULT_LINE_WIDTH)
        self.line_width.setSuffix(" px")
        self.line_width.setToolTip(LINE_WIDTH_TIP)
        self.line_width.setKeyboardTracking(False)
        style_holder, self._style_group, self._style_buttons = icon_choice_buttons(parent, LINE_STYLES, LINE_STYLE_PICTURES, LINE_STYLE_TIPS, "view_line_style")
        self._lines_reset = self._reset_button(parent, "view_lines_reset", self.reset_lines)
        self.lines_menu = GroupMenuButton(
            points_lines_icon, LINES_TIP,
            (("Points", widgets_row(parent, self.show_points, self.point_size)), ("Lines", widgets_row(parent, self.show_lines, self.line_width)), ("Joined", style_holder)),
            parent, footer=self._lines_reset,
        )

        # -- Legend -------------------------------------------------------------------------------------------------------------
        modes_holder, self._mode_group, self._mode_buttons = segmented_buttons(parent, LEGEND_MODES, LEGEND_MODE_LABELS, LEGEND_MODE_TIPS, "view_legend")
        self.legend_text = _spin(parent, "view_legend_text", *LEGEND_TEXT_RANGE, DEFAULT_LEGEND_TEXT_PT, " pt", LEGEND_TEXT_TIP)
        self.text_link = make_link_toggle(parent)  # links this size with the Stats menu's (`text_link.TextSizeLink`, made by the panel)
        self.legend_spacing = _spin(parent, "view_legend_spacing", *LEGEND_SPACING_RANGE, DEFAULT_LEGEND_SPACING_PX, " px", LEGEND_SPACING_TIP)
        self.legend_counts = QCheckBox("(n=12)", parent)
        self.legend_counts.setObjectName("view_legend_counts")
        self.legend_counts.setChecked(defaults.legend_counts)
        self.legend_counts.setToolTip(LEGEND_COUNTS_TIP)
        self.legend_over_bar = QCheckBox("at the edge of the plot", parent)
        self.legend_over_bar.setObjectName("view_legend_over_bar")
        self.legend_over_bar.setChecked(defaults.legend_over_bar)
        self.legend_over_bar.setToolTip(LEGEND_OVER_BAR_TIP)
        self._legend_reset = self._reset_button(parent, "view_legend_reset", self.reset_legend)
        self.legend_menu = GroupMenuButton(
            _tabler("list-details"), LEGEND_TIP,
            (
                ("Legend", modes_holder), ("Text", widgets_row(parent, self.legend_text, self.text_link)), ("Row gap", self.legend_spacing), ("ROI count", self.legend_counts),
                ("Position", self.legend_over_bar),
            ),
            parent, footer=self._legend_reset,
        )

        style_ribbon_number_field(self.point_size, f"{POINT_SIZE_RANGE[1]} px")
        style_ribbon_number_field(self.line_width, f"{LINE_WIDTH_RANGE[1]:.1f} px")
        style_ribbon_number_field(self.legend_text, f"{LEGEND_TEXT_RANGE[1]} pt")
        style_ribbon_number_field(self.legend_spacing, f"{LEGEND_SPACING_RANGE[0]} px")
        for signal in (
            self.band.toggled, self.anchors.toggled, self.show_points.toggled, self.show_lines.toggled, self.point_size.valueChanged, self.line_width.valueChanged,
            self._style_group.buttonClicked, self.legend_text.valueChanged, self.legend_spacing.valueChanged, self.legend_counts.toggled, self._mode_group.buttonClicked,
            self.legend_over_bar.toggled,
        ):
            signal.connect(self._changed)
        self.refresh_theme()

    # -- building -----------------------------------------------------------------------------------------------------------

    @staticmethod
    def _reset_button(parent: QWidget, name: str, slot) -> QToolButton:
        button = QToolButton(parent)
        button.setObjectName(name)
        button.setText("Reset to default")
        button.setToolTip(RESET_TIP)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setAutoRaise(True)
        button.clicked.connect(lambda _checked=False: slot())
        return button

    # -- the group ------------------------------------------------------------------------------------------------------------

    def settings(self) -> SensorgramViewSettings:
        return SensorgramViewSettings(
            show_band=self.band.isChecked(), show_anchors=self.anchors.isChecked(),
            show_points=self.show_points.isChecked(), show_lines=self.show_lines.isChecked(),
            point_size=self.point_size.value(), line_width=float(self.line_width.value()), line_style=self.line_style(),
            legend_mode=self.legend_mode(), legend_text_pt=self.legend_text.value(), legend_spacing_px=self.legend_spacing.value(),
            legend_counts=self.legend_counts.isChecked(), legend_over_bar=self.legend_over_bar.isChecked(),
        ).sanitized()

    def legend_mode(self) -> str:
        return next((mode for mode, button in self._mode_buttons.items() if button.isChecked()), LEGEND_AUTO)

    def set_legend_mode(self, mode: str) -> None:
        """Press the button of `mode` without reporting it (an unknown mode is ignored)."""
        if mode in self._mode_buttons:
            self._mode_buttons[mode].setChecked(True)

    def line_style(self) -> str:
        return next((style for style, button in self._style_buttons.items() if button.isChecked()), LINE_STRAIGHT)

    def set_line_style(self, style: str) -> None:
        """Press the button of `style` without reporting it (an unknown style is ignored)."""
        if style in self._style_buttons:
            self._style_buttons[style].setChecked(True)

    def set_available(self, *, band: bool, anchors: bool) -> None:
        """Grey out a toggle that has nothing to act on now, and say why in its tool tip; it keeps its remembered position."""
        self.band.setEnabled(bool(band))
        self.band.setToolTip(BAND_TIP if band else f"{BAND_TIP}\n\n{BAND_OFF_TIP}")
        self.anchors.setEnabled(bool(anchors))
        self.anchors.setToolTip(ANCHORS_TIP if anchors else f"{ANCHORS_TIP}\n\n{ANCHORS_OFF_TIP}")

    def reset_lines(self) -> None:
        """The dots and the lines back to what the plot always did (one change reported, not one per control)."""
        def put_back() -> None:
            self.show_points.setChecked(True)
            self.show_lines.setChecked(True)
            self.point_size.setValue(DEFAULT_POINT_SIZE)
            self.line_width.setValue(DEFAULT_LINE_WIDTH)
            self.set_line_style(LINE_STRAIGHT)

        self._set_quietly(put_back)

    def reset_legend(self) -> None:
        def put_back() -> None:
            self.text_link.setChecked(False)  # unlinked first, so that putting the legend's size back does not drag the stats numbers' size along
            self.set_legend_mode(LEGEND_AUTO)
            self.legend_text.setValue(DEFAULT_LEGEND_TEXT_PT)
            self.legend_spacing.setValue(DEFAULT_LEGEND_SPACING_PX)
            self.legend_counts.setChecked(True)
            self.legend_over_bar.setChecked(False)

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

    def refresh_theme(self) -> None:
        theme = get_active_theme()
        self.band.refresh_theme()
        self.anchors.refresh_theme()
        self.show_points.refresh_theme()
        self.show_lines.refresh_theme()
        self.text_link.refresh_theme()
        self.lines_menu.refresh_theme()
        self.legend_menu.refresh_theme()
        segment = segment_stylesheet()
        for button in self._mode_buttons.values():
            button.setStyleSheet(segment)
        for button in self._style_buttons.values():  # picture buttons: they style and recolour themselves
            button.refresh_theme()
        for button in (self._lines_reset, self._legend_reset):
            button.setIcon(load_tabler_icon("refresh", color=theme.text_primary, size=18))

    # -- remembered state -------------------------------------------------------------------------------------------------------

    def restore_ui_state(self, store: UiStateStore) -> None:
        self._syncing = True
        try:
            for key, widget in (
                (BAND_KEY, self.band), (ANCHORS_KEY, self.anchors), (POINTS_KEY, self.show_points), (LINES_KEY, self.show_lines),
                (POINT_SIZE_KEY, self.point_size), (LINE_WIDTH_KEY, self.line_width),
                (LEGEND_TEXT_KEY, self.legend_text), (LEGEND_SPACING_KEY, self.legend_spacing), (LEGEND_COUNTS_KEY, self.legend_counts),
                (LEGEND_OVER_BAR_KEY, self.legend_over_bar), (LINK_KEY, self.text_link),
            ):
                store.bind(key, widget)
            self.set_legend_mode(str(read(store, SENSORGRAM_LEGEND_MODE)))
            self.set_line_style(str(read(store, SENSORGRAM_LINE_STYLE)))
        finally:
            self._syncing = False
        # `toggled`, not `buttonClicked`: a reset presses a button in code, and that must be remembered too
        for row_key, buttons in ((SENSORGRAM_LEGEND_MODE, self._mode_buttons), (SENSORGRAM_LINE_STYLE, self._style_buttons)):
            for choice, button in buttons.items():
                button.toggled.connect(lambda on, picked=choice, key=row_key: store.set(key.key, picked) if on else None)
        self.settings_changed.emit()  # whatever was restored is what the plot should show
