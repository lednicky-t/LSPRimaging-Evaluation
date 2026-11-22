"""Sensorgram ribbon, "View" tab: how the plot is drawn (2026-11-13). Display only: nothing here changes a number.

Screen purpose: every option of the plot's looks in one place, instead of a few groups on the Plot tab and a few on the Experimental plan tab.

Main user actions (groups of the tab, left to right; each pop-up is a `GroupMenuButton`, so they all look and work alike):
- **Show** - four toggles: the shaded SD / SEM band of an average (`band`), the anchors and the baseline made from them (`anchors`: the hollow squares of the Anchor points
  method and the dashed baseline curve, which the Range method's anchor steps have too), and the two that were already here, the mask marks and the out-of-date marks (the panel
  owns those two and puts them in the group).
- **Points & lines** and **Legend** - pop-ups (`lines_menu`, `legend_menu`) that are **`PlotLookControls`** (`plot_look.py`, 2026-11-22): the one definition of these menus, also used by the LbL and
  S_bulk windows, each plot with its own values and remembered keys. Here they have the two extras only the Sensorgram needs: the Dot limit (dots are drawn only for up to that many lines;
  while more lines are drawn the Points toggle shows itself off and a click asks whether to raise the limit, `points_toggle.py`) and the legend's "at the edge of the plot" (it places the legend
  against the plan bar). The Legend's text size is the size the Steps and Stats menus' chain buttons link to (`text_link.py`); the step table's rows line up with the legend's rows, so the gap and the
  place move them too. A new option of the points, lines or legend is added once, in `plot_look.py`, and every plot's menu has it.
- **Steps**, **Evaluation** and **Stats** - the plan bar's, its evaluation markers' and the step table's own menus (`plan_group.py`, `stats_group.py`, the same kind of pop-up).

Layout of a pop-up: a small grid, one caption and its control per row, the reset button under it (`group_menu.py`). Pictures of the Points & lines menu: `line_icons.py`.

Important states: the band toggle is greyed out unless the plot shows an average (there is no band on a single ROI), the anchors toggle unless a curve is made from anchors
(Anchor points, or the Range method with anchor steps marked); both keep their remembered position meanwhile. The line width and the Spline style are the two options that can
cost time (see their tool tips); 1 px and Straight, the defaults, are Qt's fast path.

`settings()` returns the current values as one `SensorgramViewSettings`; `settings_changed` fires after any change. Every control is remembered (CLAUDE.md "Remembering UI
state"): the keys are `SENSORGRAM_LOOK_KEYS` (the names the tab always used; the legend mode and the line style are rows in `storage/ui_state_keys.py`).
"""

from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QWidget

from lspr_ui import load_tabler_icon

from ...storage.ui_state_keys import SENSORGRAM_LEGEND_MODE, SENSORGRAM_LINE_STYLE
from ..spectra.view_menu import IconToggle
from ..ui_state import UiStateStore
from .plot_look import PlotLookControls, PlotLookKeys
from .points_toggle import ask_to_raise_dots_limit
from .stat_icons import band_icon
from .view_settings import SensorgramViewSettings

BAND_KEY = "sensorgram/view_band"
ANCHORS_KEY = "sensorgram/view_anchors"
SENSORGRAM_LOOK_KEYS = PlotLookKeys(
    points="sensorgram/view_points", lines="sensorgram/view_lines", point_size="sensorgram/view_point_size", dots_limit="sensorgram/view_dots_limit",
    line_width="sensorgram/view_line_width", line_style=SENSORGRAM_LINE_STYLE.key, legend_mode=SENSORGRAM_LEGEND_MODE.key, legend_text="sensorgram/view_legend_text_pt",
    legend_spacing="sensorgram/view_legend_spacing", legend_counts="sensorgram/view_legend_counts", legend_over_bar="sensorgram/view_legend_over_bar",
    legend_sort="sensorgram/view_legend_sort", legend_descending="sensorgram/view_legend_descending", legend_value="sensorgram/view_legend_value", show_cursor="sensorgram/view_cursor",
    draw_mode="sensorgram/view_draw_mode",
)

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


def anchors_icon(on: bool, off_color: str, on_color: str) -> QIcon:
    """The anchor in the accent colour when the squares are shown; the crossed-out anchor, dimmed, when they are hidden (as the mask and clock toggles)."""
    return load_tabler_icon("anchor" if on else "anchor-off", color=on_color if on else off_color, size=24)


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

        # Points & lines and Legend: the shared definition (`plot_look.py`), with the Sensorgram's Dot limit and legend position.
        # (`ask_to_raise_dots_limit` is looked up here at the time of the click, so a test can replace it in this module.)
        self.look = PlotLookControls(
            parent, SENSORGRAM_LOOK_KEYS, defaults, dot_limit=True, legend_position=True, ask_to_raise=lambda widget, lines, limit: ask_to_raise_dots_limit(widget, lines, limit)
        )
        self.look.settings_changed.connect(self._changed)
        self.lines_menu, self.legend_menu = self.look.lines_menu, self.look.legend_menu
        # The controls the panel, the text link and the tests reach by name:
        self.show_points, self.point_size, self.dots_limit = self.look.show_points, self.look.point_size, self.look.dots_limit
        self.show_lines, self.line_width = self.look.show_lines, self.look.line_width
        self.legend_text, self.legend_spacing, self.legend_counts, self.legend_over_bar = (
            self.look.legend_text, self.look.legend_spacing, self.look.legend_counts, self.look.legend_over_bar,
        )
        self._mode_buttons, self._style_buttons = self.look._mode_buttons, self.look._style_buttons

        for signal in (self.band.toggled, self.anchors.toggled):
            signal.connect(self._changed)
        self.refresh_theme()

    # -- the group ------------------------------------------------------------------------------------------------------------

    def settings(self) -> SensorgramViewSettings:
        return replace(self.look.settings(), show_band=self.band.isChecked(), show_anchors=self.anchors.isChecked()).sanitized()

    def legend_mode(self) -> str:
        return self.look.legend_mode()

    def set_legend_mode(self, mode: str) -> None:
        """Press the button of `mode` without reporting it (an unknown mode is ignored)."""
        self.look.set_legend_mode(mode)

    def line_style(self) -> str:
        return self.look.line_style()

    def set_line_style(self, style: str) -> None:
        """Press the button of `style` without reporting it (an unknown style is ignored)."""
        self.look.set_line_style(style)

    def set_available(self, *, band: bool, anchors: bool) -> None:
        """Grey out a toggle that has nothing to act on now, and say why in its tool tip; it keeps its remembered position."""
        self.band.setEnabled(bool(band))
        self.band.setToolTip(BAND_TIP if band else f"{BAND_TIP}\n\n{BAND_OFF_TIP}")
        self.anchors.setEnabled(bool(anchors))
        self.anchors.setToolTip(ANCHORS_TIP if anchors else f"{ANCHORS_TIP}\n\n{ANCHORS_OFF_TIP}")

    def set_line_count(self, count: int) -> None:
        """The panel says how many lines the plot draws now (after every draw): above the Dot limit the Points toggle shows itself off and says why. Reports no change."""
        self.look.set_line_count(count)

    def reset_lines(self) -> None:
        self.look.reset_lines()

    def reset_legend(self) -> None:
        self.look.reset_legend()

    def _changed(self, *_args: object) -> None:
        if not self._syncing:
            self.settings_changed.emit()

    def refresh_theme(self) -> None:
        self.band.refresh_theme()
        self.anchors.refresh_theme()
        self.look.refresh_theme()

    # -- remembered state -------------------------------------------------------------------------------------------------------

    def restore_ui_state(self, store: UiStateStore) -> None:
        self._syncing = True
        try:
            store.bind(BAND_KEY, self.band)
            store.bind(ANCHORS_KEY, self.anchors)
            self.look.restore_ui_state(store, report=False)
        finally:
            self._syncing = False
        self.settings_changed.emit()  # whatever was restored is what the plot should show
