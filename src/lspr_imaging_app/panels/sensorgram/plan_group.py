"""The Sensorgram's "Steps" and "Evaluation" menus (View tab): the controls of the plan bar (2026-10-26; menus since 2026-11-13).

Screen purpose: show or hide the bar of experiment steps on the plot and choose how it looks. Display only: nothing here changes a number or an edit of a step.

Layout: one icon button (`menu`, a `GroupMenuButton`) in the View tab's "Steps" group; its panel holds the rows below, like the Legend menu. (Until 2026-11-13 they were inline fields of the
ribbon, and the text controls were a "Step labels" pop-up of their own; the two panels are one now, with "Step labels" as a heading.)

    Show  Height  Edge  Transparency
    -- Step labels --
    Text: whole note / solution   Refractive index after the text   Aligned to: bar / data   Position   Size

A second menu of its own, **Evaluation** (`evaluation_menu`, 2026-11-13), has the markers of the evaluation steps on the bar (the ``B1`` / ``S1`` ... / ``Sb`` chips): ``Markers`` shown or
hidden, and ``Place``: five pips like the 5 of a die (`place_picker.PlacePicker`), the four corners (top / bottom edge x left / right end of the step) and the middle = auto, beside
the bar as always. The place is turned into `PlanBarSettings.roles_edge` / `roles_side` (`bar_model.ROLES_PLACES`), like the rest of the settings; the colours are
`plan/role_colors.py`'s, the layout `PlanBarItem.role_chips`.

- **show**: the bar on or off.
- **height**: 1 - 10 % of the plot height, then "Full" (the bar fills the plot, behind the traces).
- **top / bottom**: which edge of the plot it sits on. Hidden while the height is Full (a bar that fills the plot has no edge); the choice is kept.
- **transparency**: the wedge, as on the Image panel's overlays (fill only; the text stays opaque).
- **text content**: the whole note, or only the text before the separator (the solution). The separators are listed in the menu (default `_-_` and `_--_`).
- **refractive index** (2026-11-12): a checkbox; on, each step's RI (imported RIs.txt) is written in ( ) after its text. Off by default; a step with no RI keeps its text.
- **text aligned to / text position**: a 5-stop slider (low to high). Aligned to the *bar* the stops are positions inside the bar; aligned to the *data* they are distances from
  the traces inside each step (far below ... far above). Each mode remembers its own stop.
- **text size**: in points, with a chain button that links it to the Legend menu's text size (`text_link.py`; the Stats menu has a button of its own).

Every control is remembered (CLAUDE.md "Remembering UI state"): the toggle and the two number boxes by ``store.bind``, the rest by the rows in ``storage/ui_state_keys.py``.
``settings()`` returns the current values as one `PlanBarSettings`; ``settings_changed`` fires after any change. Without steps in the dataset the whole group is greyed out.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QFontInfo, QValidator
from PyQt6.QtWidgets import QApplication, QCheckBox, QHBoxLayout, QLabel, QLineEdit, QSlider, QSpinBox, QWidget

from lspr_ui import CompactWedgeSlider

from ...plan.bar_model import (
    ALIGN_BAR,
    ALIGN_DATA,
    BAR_STOP_NAMES,
    DATA_STOP_NAMES,
    FONT_RANGE,
    HEIGHT_FULL,
    PLACE_AUTO,
    POSITION_BOTTOM,
    POSITION_TOP,
    ROLES_PLACES,
    STOP_MAX,
    STOP_MIN,
    PlanBarSettings,
)
from ...plan.label_rule import MODE_SOLUTION, MODE_WHOLE, parse_separators
from ...storage.ui_state_keys import (
    SENSORGRAM_PLAN_ALPHA,
    SENSORGRAM_PLAN_POSITION,
    SENSORGRAM_PLAN_ROLES_PLACE,
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
from .group_menu import GroupMenuButton, widgets_row
from .place_picker import PlacePicker
from .plan_icons import align_picture, plan_bar_icon, position_picture, role_chips_icon, text_content_picture
from .text_link import make_link_toggle

VISIBLE_KEY = "sensorgram/plan_visible"
ROLES_KEY = "sensorgram/plan_roles_visible"
HEIGHT_KEY = "sensorgram/plan_height"
TEXT_PT_KEY = "sensorgram/plan_text_pt"
LINK_KEY = "sensorgram/plan_text_size_linked"  # the chain beside the size: linked to the Legend's text size
SHOW_RI_KEY = "sensorgram/plan_show_ri"

MENU_TIP = (
    "Steps: show or hide the bar of experiment steps on the plot, its height, edge and transparency, and how the step labels are written "
    "(which part of the note, the refractive index, where the text sits and its size)."
)
EVAL_MENU_TIP = (
    "Evaluation: show or hide the markers of the evaluation steps on the plan bar (B1 / S1, B2 / S2 ... and Sb), and where on its step each one sits."
)
ROLES_TIP = (
    "Show or hide the evaluation markers on the plan bar: B1 / S1 (the baseline and the signal step of pair 1; one colour for each pair, the baseline darker), B2 / S2 ..., "
    "and Sb for a step of the S_bulk set (a grey from dark to white by how far the lines moved in that step). Hiding them changes nothing of the evaluation."
)
ROLES_PLACE_TIPS = {  # the five pips of the place picker, like the 5 of a die
    "top-left": "Top left: inside the step's bar, at its top, at the left end of the step.",
    "top-right": "Top right: inside the step's bar, at its top, at the right end of the step.",
    PLACE_AUTO: "Middle (auto): inside the step's bar at the left end of the step, in the middle of the bar's height (at the top of a full-height bar, where the middle of the plot is the data).",
    "bottom-left": "Bottom left: inside the step's bar, at its bottom, at the left end of the step.",
    "bottom-right": "Bottom right: inside the step's bar, at its bottom, at the right end of the step.",
}
# The markers always sit in the bar, so they follow its height. A step name on the same line as them moves aside; with a bar of one line that is the whole bar.
SHOW_TIP = "Show or hide the bar of experiment steps on the plot: which solution ran when, on the same axis as the traces."
NO_STEPS_TIP = (
    "This dataset has no experiment steps. Import the measurement notes (and the pump plan) in Workflow > Experimental plan to get them."
)
POSITION_TIP = "Which edge of the plot the bar of steps sits on."
HEIGHT_TIP = (
    "Height of the bar, in percent of the plot height (1 - 10), or Full: the bar fills the plot, behind the traces. The bar is never lower than one line of its text, so that the "
    "step names stay: on a small plot, or with large text, the lowest values give the same bar."
)
ALPHA_TIP = "Transparency of the bar's fill (the text stays opaque)."
CONTENT_TIP = "Which part of a step's note the bar shows."
ALIGN_TIP = "What the text position is measured against: the bar itself, or the traces inside each step."
STOP_TIP = (
    "Text position, from low (bottom) to high (top). Aligned to the bar: inside the bar, from its bottom edge to its top edge. "
    "Aligned to the data: from far below to far above the traces of each step (the middle one sits on their median)."
)
SIZE_TIP = "Text size of the step labels, in points."
RI_TIP = (
    "Write the step's refractive index in ( ) after its text, e.g. H2O (1.33300). The index is the one of the step's solution in the imported RIs table "
    "(File > Import > Refractive Indices); a step whose solution has none keeps its text as it is."
)
SEPARATORS_TIP = "Separators of the solution mode, divided by spaces. The text before the first one found in a note is its solution."


class _HeightBox(QSpinBox):
    """1 - 10 (shown as ``5 %``), and one more stop after 10 that reads ``Full``."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setRange(1, HEIGHT_FULL)
        self.setValue(5)
        self.setKeyboardTracking(False)  # a value is taken on Enter or when the field is left

    def textFromValue(self, value: int) -> str:
        return "Full" if value >= HEIGHT_FULL else f"{value} %"

    def valueFromText(self, text: str) -> int:
        word = text.strip().lower()
        if word.startswith("f"):
            return HEIGHT_FULL
        digits = "".join(ch for ch in word if ch.isdigit())
        return min(max(int(digits), 1), HEIGHT_FULL) if digits else self.value()

    def validate(self, text: str, pos: int):
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
        self.text_link = make_link_toggle(parent, "the step names")  # links this size to the Legend menu's (`text_link.TextSizeLink`, made by the panel)

        self._show_ri = QCheckBox(parent)  # no text of its own: the row's caption "Refractive index" names it, the tool tip explains
        self._show_ri.setToolTip(RI_TIP)

        # every control lives in the one Steps menu (they are reparented into its panel)
        self.menu = GroupMenuButton(
            lambda color: plan_bar_icon(True, color, color),
            MENU_TIP,
            (
                ("Show", self._show), ("Height", self._height), ("Edge", self._position), ("Transparency", self._alpha),
                ("Step labels", None),
                ("Text", self._content), ("Refractive index", self._show_ri), ("Aligned to", self._align), ("Position", self._stop),
                ("Size", widgets_row(parent, self._size, self.text_link)),
            ),
            parent,
        )
        # The Evaluation menu (2026-11-13): the markers of the evaluation steps (B1 / S1 ... / Sb) on the bar. Its settings are part of `PlanBarSettings` like the rest.
        self._show_roles = IconToggle(role_chips_icon, ROLES_TIP, parent)
        self._show_roles.setChecked(True)
        self._roles_place = PlacePicker(ROLES_PLACE_TIPS, parent)  # five pips like the 5 of a die: the corners, and the middle = auto
        self.evaluation_menu = GroupMenuButton(
            lambda color: role_chips_icon(True, color, color), EVAL_MENU_TIP,
            (("Markers", self._show_roles), ("Place", self._roles_place)),
            parent,
        )
        self._controls = (
            self._show, self._height, self._position, self._alpha, self._content, self._show_ri, self._align, self._stop, self._size, self.text_link, self._show_roles,
            *self._roles_place.buttons.values(),
        )  # greyed out together with the menu buttons when there are no steps

        self._available = True
        self._refresh_stop_tip()
        for signal in (
            self._show.toggled, self._position.currentIndexChanged, self._height.valueChanged, self._alpha.valueChanged,
            self._content.currentIndexChanged, self._separators.editingFinished, self._size.valueChanged, self._show_ri.toggled,
            self._show_roles.toggled, self._roles_place.changed,
        ):
            signal.connect(self._changed)
        self._align.currentIndexChanged.connect(self._on_align_changed)
        self._stop.valueChanged.connect(self._on_stop_changed)
        self._height.valueChanged.connect(self._refresh_position_visible)

    # -- the group ----------------------------------------------------------------------------------------------------------

    @property
    def size_box(self) -> QSpinBox:
        """The step names' text size box (the panel links it with the legend's and the stats' sizes)."""
        return self._size

    def widgets(self) -> tuple[QWidget, ...]:
        return (self.menu,)

    def evaluation_widgets(self) -> tuple[QWidget, ...]:
        """The View tab's "Evaluation" group: the menu of the evaluation markers."""
        return (self.evaluation_menu,)

    def roles_place(self) -> str:
        """The place of the evaluation chips chosen with the dice: a key of `bar_model.ROLES_PLACES`."""
        return self._roles_place.place()

    def settings(self) -> PlanBarSettings:
        roles_edge, roles_side = ROLES_PLACES[self.roles_place()]  # the dice's place as the (edge, side) the bar draws with
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
            show_ri=self._show_ri.isChecked(),
            show_roles=self._show_roles.isChecked(),
            roles_edge=roles_edge,
            roles_side=roles_side,
        )

    def set_available(self, available: bool) -> None:
        """Without experiment steps the menu is greyed out (and every control in it), and its tool tip says why."""
        self._available = bool(available)
        for widget in (self.menu, self.evaluation_menu, *self._controls):
            widget.setEnabled(self._available)
        self.menu.setToolTip(MENU_TIP if self._available else NO_STEPS_TIP)
        self.evaluation_menu.setToolTip(EVAL_MENU_TIP if self._available else NO_STEPS_TIP)
        self._show.setToolTip(SHOW_TIP if self._available else NO_STEPS_TIP)

    def is_available(self) -> bool:
        return self._available

    def refresh_theme(self) -> None:
        self._show.refresh_theme()
        self.text_link.refresh_theme()
        for menu in (self._position, self._content, self._align):
            menu.refresh_theme()
        self.menu.refresh_theme()
        self._show_roles.refresh_theme()
        self.evaluation_menu.refresh_theme()
        self._roles_place.refresh_theme()

    # -- changes ------------------------------------------------------------------------------------------------------------

    def _changed(self, *_args: object) -> None:
        if not self._syncing:
            self.settings_changed.emit()

    def _refresh_position_visible(self, *_args: object) -> None:
        """A full-height bar fills the plot and has no edge, so its top / bottom choice is out of the way (and still remembered)."""
        self.menu.set_row_visible(self._position, self._height.value() < HEIGHT_FULL)

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
            store.bind(LINK_KEY, self.text_link)
            store.bind(SHOW_RI_KEY, self._show_ri)
            store.bind(ROLES_KEY, self._show_roles)
            self._roles_place.set_place(str(read(store, SENSORGRAM_PLAN_ROLES_PLACE)))  # an unknown remembered place is ignored: the middle (auto) stays
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
        for place, button in self._roles_place.buttons.items():  # `toggled`, not the picker's `changed`: pressing a pip in code is remembered too
            button.toggled.connect(lambda on, picked=place: store.set(SENSORGRAM_PLAN_ROLES_PLACE.key, picked) if on else None)
        self.settings_changed.emit()  # whatever was restored is what the plot should show
