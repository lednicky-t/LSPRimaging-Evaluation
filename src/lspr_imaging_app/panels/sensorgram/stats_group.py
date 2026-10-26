"""The Sensorgram ribbon's "Step stats" group (2026-10-26): the controls of the table of mean +/- SD per experiment step.

Screen purpose: show or hide, on the plot, the numbers of every line in every step and choose where they go. Display only: nothing here changes a number.

Layout (left to right): ``[show] [top / bottom] [text size]``

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
from .plan_group import default_font_points
from .plan_icons import position_picture
from .stat_icons import step_stats_icon

VISIBLE_KEY = "sensorgram/stats_visible"
TEXT_PT_KEY = "sensorgram/stats_text_pt"

SHOW_TIP = (
    "Write the mean and the standard deviation (mean ± SD) of every line in every experiment step, in the line's colour, in the middle of the step and level with the "
    "line's entry in the legend. They are the mean and SD of the line as drawn over the cubes of the step, so they follow smoothing, the baseline and spike removal. "
    "Narrow steps make the text smaller, down to 6 pt, before the SD is left out. Hover a number for the details."
)
NO_STEPS_TIP = "This dataset has no experiment steps, so there is nothing to write per step."
POSITION_TIP = "Which edge of the plot the table sits on (the legend moves with it)."
SIZE_TIP = "Text size of the numbers, in points: the largest size; narrow steps make the text smaller so that mean ± SD still fits (down to 6 pt)."


class StatsTableControls(QObject):
    settings_changed = pyqtSignal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._syncing = False
        self._available = True

        self._show = IconToggle(step_stats_icon, SHOW_TIP, parent)
        self._show.setChecked(True)

        self._position = PickMenu(parent, name_on_face=False)
        self._position.add_choice(POSITION_TOP, "Top", "The table along the top edge of the plot.", position_picture("top"))
        self._position.add_choice(POSITION_BOTTOM, "Bottom", "The table along the bottom edge of the plot.", position_picture("bottom"))
        self._position.finish()
        self._position.setToolTip(POSITION_TIP)

        self._size = QSpinBox(parent)
        self._size.setRange(*FONT_RANGE)
        self._size.setValue(default_font_points())
        self._size.setSuffix(" pt")
        self._size.setKeyboardTracking(False)
        style_ribbon_number_field(self._size, f"{FONT_RANGE[1]} pt")
        self._size.setToolTip(SIZE_TIP)

        for signal in (self._show.toggled, self._position.currentIndexChanged, self._size.valueChanged):
            signal.connect(self._changed)

    # -- the group ----------------------------------------------------------------------------------------------------------

    def widgets(self) -> tuple[QWidget, ...]:
        return (self._show, self._position, self._size)

    def settings(self) -> StatsTableSettings:
        return StatsTableSettings(visible=self._show.isChecked(), position=self._position.currentData() or POSITION_TOP, text_pt=self._size.value())

    def set_available(self, available: bool, note: str = "") -> None:
        """Without experiment steps the group is greyed out and the toggle says why; `note` is one more sentence for the toggle's tooltip (why nothing is drawn now)."""
        self._available = bool(available)
        for widget in self.widgets():
            widget.setEnabled(self._available)
        self._show.setToolTip(SHOW_TIP + (f"\n{note}" if note else "") if self._available else NO_STEPS_TIP)

    def is_available(self) -> bool:
        return self._available

    def refresh_theme(self) -> None:
        self._show.refresh_theme()
        self._position.refresh_theme()

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
            index = self._position.findData(read(store, SENSORGRAM_STATS_POSITION))
            if index >= 0:
                self._position.setCurrentIndex(index)
        finally:
            self._syncing = False
        self._position.currentIndexChanged.connect(lambda *_args: store.set(SENSORGRAM_STATS_POSITION.key, self._position.currentData()))
        self.settings_changed.emit()  # whatever was restored is what the plot should show
