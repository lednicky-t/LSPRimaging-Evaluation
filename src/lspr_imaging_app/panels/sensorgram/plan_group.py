"""The Sensorgram ribbon's "Experimental plan" group (2026-10-26): the controls of the plan bar.

Screen purpose: show or hide the bar of experiment steps on the plot and choose how it looks. Display only: nothing here changes a number or an edit of a step.

Layout (left to right, inline for now; the ribbon is to be re-ordered and grouped into pick-up menus later, so the whole group is this one file):

    [show] [height] [top / bottom] [transparency] [step labels menu: text: whole note / solution, text aligned to: bar / data, text position, text size]

- **show**: the bar on or off.
- **height**: 1 - 10 % of the plot height, then "Full" (the bar fills the plot, behind the traces).
- **top / bottom**: which edge of the plot it sits on. Hidden while the height is Full (a bar that fills the plot has no edge); the choice is kept.
- **transparency**: the wedge, as on the Image panel's overlays (fill only; the text stays opaque).
- **text content**: the whole note, or only the text before the separator (the solution). The separators are listed in the menu (default `_-_` and `_--_`).
- **text aligned to / text position**: a 5-stop slider (low to high). Aligned to the *bar* the stops are positions inside the bar; aligned to the *data* they are distances from
  the traces inside each step (far below ... far above). Each mode remembers its own stop.
- **text size**: in points.

Every control is remembered (CLAUDE.md "Remembering UI state"): the toggle and the two number boxes by ``store.bind``, the rest by the rows in ``storage/ui_state_keys.py``.
``settings()`` returns the current values as one `PlanBarSettings`; ``settings_changed`` fires after any change. Without steps in the dataset the whole group is greyed out.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QFontInfo, QValidator
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QLineEdit, QSlider, QSpinBox, QWidget

from lspr_ui import CompactWedgeSlider, get_active_theme

from ...plan.bar_model import (
    ALIGN_BAR,
    ALIGN_DATA,
    BAR_STOP_NAMES,
    DATA_STOP_NAMES,
    FONT_RANGE,
    HEIGHT_FULL,
    POSITION_BOTTOM,
    POSITION_TOP,
    STOP_MAX,
    STOP_MIN,
    PlanBarSettings,
)
from ...plan.label_rule import MODE_SOLUTION, MODE_WHOLE, parse_separators
from ...storage.ui_state_keys import (
    SENSORGRAM_PLAN_ALPHA,
    SENSORGRAM_PLAN_POSITION,
    SENSORGRAM_PLAN_SEPARATORS,
    SENSORGRAM_PLAN_TEXT_ALIGN,
    SENSORGRAM_PLAN_TEXT_MODE,
    SENSORGRAM_PLAN_TEXT_STOP_BAR,
    SENSORGRAM_PLAN_TEXT_STOP_DATA,
    read,
)
from ..ribbon_fields import style_ribbon_number_field
from ..ui_state import UiStateStore
from ..spectra.pick_menu import PickMenu
from ..spectra.view_menu import IconToggle
from .plan_icons import align_picture, plan_bar_icon, position_picture, text_content_picture
from .plan_labels_menu import PlanLabelsMenu

VISIBLE_KEY = "sensorgram/plan_visible"
HEIGHT_KEY = "sensorgram/plan_height"
TEXT_PT_KEY = "sensorgram/plan_text_pt"

SHOW_TIP = "Show or hide the bar of experiment steps on the plot: which solution ran when, on the same axis as the traces."
NO_STEPS_TIP = (
    "This dataset has no experiment steps. Import the measurement notes (and the pump plan) in Workflow > Experimental plan to get them."
)
POSITION_TIP = "Which edge of the plot the bar of steps sits on."
HEIGHT_TIP = "Height of the bar, in percent of the plot height (1 - 10), or Full: the bar fills the plot, behind the traces."
ALPHA_TIP = "Transparency of the bar's fill (the text stays opaque)."
CONTENT_TIP = "Which part of a step's note the bar shows."
ALIGN_TIP = "What the text position is measured against: the bar itself, or the traces inside each step."
STOP_TIP = (
    "Text position, from low (bottom) to high (top). Aligned to the bar: inside the bar, from its bottom edge to its top edge. "
    "Aligned to the data: from far below to far above the traces of each step (the middle one sits on their median)."
)
SIZE_TIP = "Text size of the step labels, in points."
SEPARATORS_TIP = "Separators of the solution mode, divided by spaces. The text before the first one found in a note is its solution."


class _HeightBox(QSpinBox):
    """1 - 10 (shown as ``5 %``), and one more stop after 10 that reads ``Full``."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setRange(1, HEIGHT_FULL)
        self.setValue(5)
        self.setKeyboardTracking(False)  # a value is taken on Enter or when the field is left

    def textFromValue(self, value: int) -> str:  # noqa: N802 - Qt naming
        return "Full" if value >= HEIGHT_FULL else f"{value} %"

    def valueFromText(self, text: str) -> int:  # noqa: N802 - Qt naming
        word = text.strip().lower()
        if word.startswith("f"):
            return HEIGHT_FULL
        digits = "".join(ch for ch in word if ch.isdigit())
        return min(max(int(digits), 1), HEIGHT_FULL) if digits else self.value()

    def validate(self, text: str, pos: int):  # noqa: A003 - Qt naming
        word = text.strip().lower()
        if not word or "full".startswith(word) or word.rstrip("% ").isdigit():
            return QValidator.State.Acceptable, text, pos
        return QValidator.State.Invalid, text, pos


def default_font_points() -> int:
    """The application font's size in whole points: what the step labels use until a size is chosen."""
    return min(max(round(QFontInfo(QApplication.font()).pointSizeF()), FONT_RANGE[0]), FONT_RANGE[1])


class PlanBarControls(QObject):
    settings_changed = pyqtSignal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._syncing = False
        self._stops = {ALIGN_BAR: 3, ALIGN_DATA: 4}  # the text position remembered for each mode
        self._align_key = ALIGN_BAR

        self._show = IconToggle(plan_bar_icon, SHOW_TIP, parent)
        self._show.setChecked(True)

        self._position = PickMenu(parent, name_on_face=False)
        self._position.add_choice(POSITION_TOP, "Top", "The bar along the top edge of the plot.", position_picture("top"))
        self._position.add_choice(POSITION_BOTTOM, "Bottom", "The bar along the bottom edge of the plot.", position_picture("bottom"))
        self._position.finish()
        self._position.setToolTip(POSITION_TIP)

        self._height = _HeightBox(parent)
        style_ribbon_number_field(self._height, "10 %")  # "Full" is narrower
        self._height.setToolTip(HEIGHT_TIP)

        self._alpha = CompactWedgeSlider(parent=parent)
        self._alpha.setFixedWidth(18)
        self._alpha.setRange(0, 100)
        self._alpha.setValue(int(round(PlanBarSettings().alpha * 100)))
        self._alpha.setToolTip(ALPHA_TIP)

        self._content = PickMenu(parent, name_on_face=False)
        self._content.add_choice(MODE_WHOLE, "Whole note", "The note exactly as it was recorded.", text_content_picture("whole"))
        self._content.add_choice(
            MODE_SOLUTION, "Solution only", "Only the text before the first separator (usually the name of the solution).", text_content_picture("solution")
        )
        self._separators_row = QWidget(parent)
        row = QHBoxLayout(self._separators_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(QLabel("Separators", self._separators_row))
        self._separators = QLineEdit(SENSORGRAM_PLAN_SEPARATORS.default, self._separators_row)  # type: ignore[arg-type]
        self._separators.setToolTip(SEPARATORS_TIP)
        self._separators.setMinimumWidth(110)
        row.addWidget(self._separators)
        self._content.add_footer(self._separators_row)
        self._content.finish()
        self._content.setToolTip(CONTENT_TIP)

        self._align = PickMenu(parent, name_on_face=False)
        self._align.add_choice(ALIGN_BAR, "Aligned to the bar", "The text sits inside the bar; the slider moves it from the bottom edge to the top edge.", align_picture("bar"))
        self._align.add_choice(
            ALIGN_DATA, "Aligned to the data", "The text sits next to the traces inside each step; the slider moves it from far below to far above them.", align_picture("data")
        )
        self._align.finish()
        self._align.setToolTip(ALIGN_TIP)

        self._stop = QSlider(Qt.Orientation.Vertical, parent)  # low at the bottom, high at the top, like the text it moves
        self._stop.setRange(STOP_MIN, STOP_MAX)
        self._stop.setValue(self._stops[ALIGN_BAR])
        self._stop.setTickPosition(QSlider.TickPosition.TicksRight)
        self._stop.setTickInterval(1)
        self._stop.setPageStep(1)
        self._stop.setFixedSize(24, 52)

        self._size = QSpinBox(parent)
        self._size.setRange(*FONT_RANGE)
        self._size.setValue(default_font_points())
        self._size.setSuffix(" pt")
        self._size.setKeyboardTracking(False)
        style_ribbon_number_field(self._size, f"{FONT_RANGE[1]} pt")
        self._size.setToolTip(SIZE_TIP)

        # the four text controls live in one pick-up menu (they are reparented into its panel)
        self._labels = PlanLabelsMenu(
            (("Text", self._content), ("Aligned to", self._align), ("Position", self._stop), ("Size", self._size)), parent
        )

        self._available = True
        self._refresh_stop_tip()
        for signal in (
            self._show.toggled, self._position.currentIndexChanged, self._height.valueChanged, self._alpha.valueChanged,
            self._content.currentIndexChanged, self._separators.editingFinished, self._size.valueChanged,
        ):
            signal.connect(self._changed)
        self._align.currentIndexChanged.connect(self._on_align_changed)
        self._stop.valueChanged.connect(self._on_stop_changed)
        self._height.valueChanged.connect(self._refresh_position_visible)

    # -- the group ----------------------------------------------------------------------------------------------------------

    def widgets(self) -> tuple[QWidget, ...]:
        return (self._show, self._height, self._position, self._alpha, self._labels)

    def settings(self) -> PlanBarSettings:
        return PlanBarSettings(
            visible=self._show.isChecked(),
            position=self._position.currentData() or POSITION_TOP,
            height_stop=self._height.value(),
            alpha=self._alpha.value() / 100.0,
            text_mode=self._content.currentData() or MODE_WHOLE,
            separators=parse_separators(self._separators.text()),
            text_align=self._align.currentData() or ALIGN_BAR,
            text_stop=self._stop.value(),
            text_pt=self._size.value(),
        )

    def set_available(self, available: bool) -> None:
        """Without experiment steps the group is greyed out, and the toggle says why."""
        self._available = bool(available)
        for widget in self.widgets():
            widget.setEnabled(self._available)
        self._show.setToolTip(SHOW_TIP if self._available else NO_STEPS_TIP)

    def is_available(self) -> bool:
        return self._available

    def refresh_theme(self) -> None:
        self._show.refresh_theme()
        for menu in (self._position, self._content, self._align):
            menu.refresh_theme()
        self._labels.refresh_theme(get_active_theme())

    # -- changes ------------------------------------------------------------------------------------------------------------

    def _changed(self, *_args: object) -> None:
        if not self._syncing:
            self.settings_changed.emit()

    def _refresh_position_visible(self, *_args: object) -> None:
        """A full-height bar fills the plot and has no edge, so its top / bottom choice is out of the way (and still remembered)."""
        self._position.setVisible(self._height.value() < HEIGHT_FULL)

    def _refresh_stop_tip(self) -> None:
        names = DATA_STOP_NAMES if self._align_key == ALIGN_DATA else BAR_STOP_NAMES
        self._stop.setToolTip(f"{names[self._stop.value() - 1]}.\n{STOP_TIP}")

    def _on_align_changed(self, *_args: object) -> None:
        """Another mode: the slider shows the stop remembered for it."""
        key = self._align.currentData() or ALIGN_BAR
        self._align_key = key
        previous = self._syncing
        self._syncing = True
        try:
            self._stop.setValue(self._stops[key])
        finally:
            self._syncing = previous
        self._refresh_stop_tip()
        self._changed()

    def _on_stop_changed(self, value: int) -> None:
        self._stops[self._align_key] = int(value)
        self._refresh_stop_tip()
        self._changed()

    # -- remembered state ---------------------------------------------------------------------------------------------------

    def restore_ui_state(self, store: UiStateStore) -> None:
        self._syncing = True
        try:
            store.bind(VISIBLE_KEY, self._show)
            store.bind(HEIGHT_KEY, self._height)
            store.bind(TEXT_PT_KEY, self._size)
            for menu, key in (
                (self._position, SENSORGRAM_PLAN_POSITION), (self._content, SENSORGRAM_PLAN_TEXT_MODE), (self._align, SENSORGRAM_PLAN_TEXT_ALIGN),
            ):
                index = menu.findData(read(store, key))
                if index >= 0:
                    menu.setCurrentIndex(index)
            self._alpha.setValue(int(round(min(max(float(read(store, SENSORGRAM_PLAN_ALPHA)), 0.0), 1.0) * 100)))
            text = read(store, SENSORGRAM_PLAN_SEPARATORS)
            self._separators.setText(text if isinstance(text, str) else SENSORGRAM_PLAN_SEPARATORS.default)  # type: ignore[arg-type]
            for mode, key in ((ALIGN_BAR, SENSORGRAM_PLAN_TEXT_STOP_BAR), (ALIGN_DATA, SENSORGRAM_PLAN_TEXT_STOP_DATA)):
                self._stops[mode] = min(max(int(read(store, key)), STOP_MIN), STOP_MAX)
            self._align_key = self._align.currentData() or ALIGN_BAR
            self._stop.setValue(self._stops[self._align_key])
        finally:
            self._syncing = False
        self._refresh_stop_tip()
        self._refresh_position_visible()

        def remember(signal, key, value_of: Callable[[], object]) -> None:
            signal.connect(lambda *_args: store.set(key.key, value_of()))

        remember(self._position.currentIndexChanged, SENSORGRAM_PLAN_POSITION, lambda: self._position.currentData())
        remember(self._content.currentIndexChanged, SENSORGRAM_PLAN_TEXT_MODE, lambda: self._content.currentData())
        remember(self._align.currentIndexChanged, SENSORGRAM_PLAN_TEXT_ALIGN, lambda: self._align.currentData())
        remember(self._alpha.valueChanged, SENSORGRAM_PLAN_ALPHA, lambda: self._alpha.value() / 100.0)
        remember(self._separators.editingFinished, SENSORGRAM_PLAN_SEPARATORS, lambda: self._separators.text())
        remember(self._stop.valueChanged, SENSORGRAM_PLAN_TEXT_STOP_BAR, lambda: self._stops[ALIGN_BAR])
        remember(self._stop.valueChanged, SENSORGRAM_PLAN_TEXT_STOP_DATA, lambda: self._stops[ALIGN_DATA])
        self.settings_changed.emit()  # whatever was restored is what the plot should show
