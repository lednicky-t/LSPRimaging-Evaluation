"""The Sensorgram's "Stats" menu (View tab): the controls of the table of mean +/- SD per experiment step (2026-10-26; a menu since 2026-11-13).

Screen purpose: show or hide, on the plot, the numbers of every line in every step and choose where they go. Display only: nothing here changes a number.

Layout: one icon button (`menu`, a `GroupMenuButton`) in the View tab's "Stats" group, next to "Steps"; its panel holds the rows ``Show`` / ``Edge`` (top / bottom) / ``Text size`` /
``Legend room``, like the Legend menu. (Until 2026-11-13 the first three were fields of the ribbon.) A **link** button (a chain) beside the text size links it to the Legend menu's
text size (`text_link.TextSizeLink`: while linked, it is the legend's size and changing either changes the other; the Steps menu has a button of its own). **Legend room**: how much of the x axis before the first step is left for the
legend the rows of the numbers line up with, in percent of what holds the legend (100 % = as always, 0 = "off": none is made); `SensorgramPlot._room` applies it.

- **show**: the table on or off.
- **top / bottom**: which edge of the plot it sits on (the legend moves with it, the cells follow the legend's rows).
- **text size**: in points.

Every control is remembered (CLAUDE.md "Remembering UI state"): the toggle and the number box by ``store.bind``, the position by its row in ``storage/ui_state_keys.py``.
``settings()`` returns the current values as one `StatsTableSettings`; ``settings_changed`` fires after any change. Without experiment steps the group is greyed out.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QSpinBox, QWidget

from ...plan.bar_model import FONT_RANGE, POSITION_BOTTOM, POSITION_TOP
from ...plan.stats_table import StatsTableSettings
from ...storage.ui_state_keys import SENSORGRAM_STATS_POSITION, read
from ..ribbon_fields import style_ribbon_number_field
from ..spectra.pick_menu import PickMenu
from ..spectra.view_menu import IconToggle
from ..ui_state import UiStateStore
from .group_menu import GroupMenuButton, widgets_row
from .plan_group import default_font_points
from .plan_icons import position_picture
from .stat_icons import step_stats_icon
from .text_link import make_link_toggle

VISIBLE_KEY = "sensorgram/stats_visible"
TEXT_PT_KEY = "sensorgram/stats_text_pt"
LINK_KEY = "sensorgram/stats_text_size_linked"  # the chain beside the size: linked to the Legend's text size
ROOM_KEY = "sensorgram/stats_legend_room"
ROOM_RANGE = (0, 300)  # percent
ROOM_STEP = 25

MENU_TIP = "Stats: write the mean ± SD of every line in every experiment step on the plot, on which edge of it, the text size, and the room left of the first step for the legend."
ROOM_TIP = (
    "Room the plot makes on the left of the first step, before the start of the x axis, for the legend that the rows of the numbers line up with, in percent of what holds the "
    "legend: 100 % = as the plot always made it, more leaves a wider gap, less lets the first step's numbers start closer to (they never draw under) the legend, "
    "off = no room is made and the axis starts at the data. Steps of 25 %. Not applied while you have zoomed in yourself: reset the view (the A button) to see it."
)
SHOW_TIP = (
    "Write the mean and the standard deviation (mean ± SD) of every line in every experiment step, in the line's colour, in the middle of the step and level with the "
    "line's entry in the legend. They are the mean and SD of the line as drawn over the cubes of the step, so they follow smoothing, the baseline and spike removal. "
    "A step too narrow for it shows fewer decimals, then the mean alone. Hover a number for the details."
)
NO_STEPS_TIP = "This dataset has no experiment steps, so there is nothing to write per step."
POSITION_TIP = "Which edge of the plot the table sits on (the legend moves with it)."
SIZE_TIP = "Text size of the numbers, in points. A step too narrow for mean ± SD at this size shows fewer decimals, the mean alone, or nothing (hover the step for the full numbers)."


class StatsTableControls(QObject):
    settings_changed = pyqtSignal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._syncing = False
        self._available = True

        self._show = IconToggle(step_stats_icon, SHOW_TIP, parent)
        self._show.setChecked(True)

        self._position = PickMenu(parent, name_on_face=False)
        self._position.add_choice(POSITION_TOP, "Top", "The table along the top edge of the plot.", position_picture("top", steps=True))
        self._position.add_choice(POSITION_BOTTOM, "Bottom", "The table along the bottom edge of the plot.", position_picture("bottom", steps=True))
        self._position.finish()
        self._position.setToolTip(POSITION_TIP)

        self._size = QSpinBox(parent)
        self._size.setRange(*FONT_RANGE)
        self._size.setValue(default_font_points())
        self._size.setSuffix(" pt")
        self._size.setKeyboardTracking(False)
        style_ribbon_number_field(self._size, f"{FONT_RANGE[1]} pt")
        self._size.setToolTip(SIZE_TIP)
        self.text_link = make_link_toggle(parent, "the stats numbers")  # links this size to the Legend menu's (`text_link.TextSizeLink`, made by the panel)

        self._room = QSpinBox(parent)  # the room made for the legend before the start of the x axis (2026-11-13)
        self._room.setRange(*ROOM_RANGE)
        self._room.setSingleStep(ROOM_STEP)
        self._room.setValue(StatsTableSettings().legend_room_pct)
        self._room.setSuffix(" %")
        self._room.setSpecialValueText("off")
        self._room.setKeyboardTracking(False)
        style_ribbon_number_field(self._room, f"{ROOM_RANGE[1]} %")
        self._room.setToolTip(ROOM_TIP)

        # every control lives in the one Stats menu (they are reparented into its panel)
        self.menu = GroupMenuButton(
            lambda color: step_stats_icon(True, color, color), MENU_TIP,
            (("Show", self._show), ("Edge", self._position), ("Text size", widgets_row(parent, self._size, self.text_link)), ("Legend room", self._room)),
            parent,
        )
        self._controls = (self._show, self._position, self._size, self.text_link, self._room)  # greyed out together with the menu button when there are no steps

        for signal in (self._show.toggled, self._position.currentIndexChanged, self._size.valueChanged, self._room.valueChanged):
            signal.connect(self._changed)

    # -- the group ----------------------------------------------------------------------------------------------------------

    def widgets(self) -> tuple[QWidget, ...]:
        return (self.menu,)

    @property
    def size_box(self) -> QSpinBox:
        """The text-size box (the panel links it with the legend's)."""
        return self._size

    def settings(self) -> StatsTableSettings:
        return StatsTableSettings(
            visible=self._show.isChecked(), position=self._position.currentData() or POSITION_TOP, text_pt=self._size.value(), legend_room_pct=self._room.value(),
        )

    def set_available(self, available: bool, note: str = "") -> None:
        """Without experiment steps the menu is greyed out (and every control in it) and the tool tips say why; `note` is one more sentence for the toggle's tooltip (why nothing is drawn now)."""
        self._available = bool(available)
        for widget in (self.menu, *self._controls):
            widget.setEnabled(self._available)
        self.menu.setToolTip(MENU_TIP if self._available else NO_STEPS_TIP)
        self._show.setToolTip(SHOW_TIP + (f"\n{note}" if note else "") if self._available else NO_STEPS_TIP)

    def is_available(self) -> bool:
        return self._available

    def refresh_theme(self) -> None:
        self._show.refresh_theme()
        self._position.refresh_theme()
        self.text_link.refresh_theme()
        self.menu.refresh_theme()

    # -- changes ------------------------------------------------------------------------------------------------------------

    def _changed(self, *_args: object) -> None:
        if not self._syncing:
            self.settings_changed.emit()

    # -- remembered state ---------------------------------------------------------------------------------------------------

    def restore_ui_state(self, store: UiStateStore) -> None:
        self._syncing = True
        try:
            store.bind(VISIBLE_KEY, self._show)
            store.bind(TEXT_PT_KEY, self._size)
            store.bind(LINK_KEY, self.text_link)
            store.bind(ROOM_KEY, self._room)
            index = self._position.findData(read(store, SENSORGRAM_STATS_POSITION))
            if index >= 0:
                self._position.setCurrentIndex(index)
        finally:
            self._syncing = False
        self._position.currentIndexChanged.connect(lambda *_args: store.set(SENSORGRAM_STATS_POSITION.key, self._position.currentData()))
        self.settings_changed.emit()  # whatever was restored is what the plot should show
