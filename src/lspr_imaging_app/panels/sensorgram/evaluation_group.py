"""The Sensorgram ribbon's "Evaluation" group (2026-10-29, one icon since 2026-10-30): which steps are the baseline and the signal of each evaluation pair.

Screen purpose: say once, in the plan, which stretch of the experiment is the baseline (``B1``) and which is the signal (``S1``) of evaluation pair 1, and the same for further pairs
(``B2`` / ``S2`` ...). Everything that compares a signal with a baseline (ROI position tuning, the map of which ROI or group gives the best signal) reads these steps from the plan
module instead of asking for cube numbers. The same choice can be made in a step's right-click menu ("Use as baseline", "Use as signal"; live while this tab is active); the plan bar
shows the roles as chips (``B1`` / ``S1``) on the steps.

Layout: the ribbon shows ONE icon button. It opens a pop-up (a `QWidgetAction` in a `QMenu`, like the other pick-up menus) with a table of two columns and, under it, two icons::

    B1  [ 1 · H2O        v]        column 1: the role, column 2: a `StepPicker` (the plan's steps with their colours)
    S1  [ 2 · 10perEG    v]        rows come in pairs, baseline above signal
    B2  [ not set        v]
    S2  [ not set        v]
    ------------------------------
    [+] [-]                        add a pair (B2 + S2, ...) / remove the last pair

Display only: the group holds no choice. It is filled by ``set_steps`` from the module's steps and pairs and reports the user's pick as ``role_chosen(role, step reference or None, pair)``;
`StepOverlays` turns that into ``ExperimentPlanModule.set_role``. "-" reports ``pair_removed(number)`` before the row goes, so the owner can clear the roles of that pair in one undo step.
The group greys out when the dataset has no steps.

The one thing it keeps is **how many pairs the pop-up shows** (1 - `MAX_PAIRS`), remembered like any control (``sensorgram/eval_pairs``). A pair that is set in the plan is always
shown, whatever the remembered count (a session, an undo or the right-click menu can set pair 3), so the count only ever rises to meet the plan, never hides a role.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from PyQt6.QtCore import QObject, QSize, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu, QToolButton, QVBoxLayout, QWidget, QWidgetAction

from lspr_ui import get_active_theme, load_tabler_icon

from ...plan.edits import EffectiveStep
from ...plan.state import EvalPair, StepRef
from ...storage.ui_state_keys import SENSORGRAM_EVAL_PAIRS, read
from ..image.general_group import style_general_icon_button
from ..ribbon_icon_size import ICON_SIZE
from ..step_picker import StepPicker
from ..ui_state import UiStateStore
from .plan_icons import evaluation_picture

MIN_PAIRS = 1
MAX_PAIRS = 8  # 16 rows: tall, but still a pop-up that fits a laptop screen

BUTTON_TIP = (
    "Evaluation: which steps of the experimental plan are the baseline (B) and the signal (S) of each evaluation pair. Click to pick them, "
    "or add more pairs (B2 + S2, ...)."
)
BASELINE_TIP = (
    "The baseline step (B{n}) of pair {n}: the stretch of the experiment taken as the starting level. Everything that compares a signal with a baseline reads it from here. "
    "The same as 'Use as baseline' in a step's right-click menu."
)
SIGNAL_TIP = (
    "The signal step (S{n}) of pair {n}: the stretch of the experiment whose level is compared with the baseline. "
    "The same as 'Use as signal' in a step's right-click menu."
)
ADD_TIP = "Add an evaluation pair: a new baseline and signal row (B{n} + S{n})."
ADD_FULL_TIP = f"There can be at most {MAX_PAIRS} evaluation pairs."
REMOVE_TIP = "Remove the last pair (B{n} + S{n}). If it has steps set, they are cleared; Edit > Undo brings them back."
REMOVE_LAST_TIP = "The first pair (B1 + S1) always stays."
NO_STEPS_TIP = "This dataset has no experiment steps. Import the measurement notes (and the pump plan) in Workflow > Experimental plan to get them."

_ROLES = ("baseline", "signal")
_RENDER_SIZE = ICON_SIZE * 2
_STROKE_WIDTH = 2.1


def clamp_pairs(value: object) -> int:
    return min(max(int(value), MIN_PAIRS), MAX_PAIRS) if isinstance(value, (int, float)) else MIN_PAIRS


class EvaluationControls(QObject):
    role_chosen = pyqtSignal(str, object, int)  # ("baseline" | "signal", the step's reference or None, the pair's number)
    pair_removed = pyqtSignal(int)  # the number of the pair being taken away, sent before its rows go

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._count = MIN_PAIRS
        self._layout_tick = 0
        self._store: UiStateStore | None = None
        self._available = False
        self._steps: Sequence[EffectiveStep] = ()
        self._colours: Sequence[str] | None = None
        self._pairs: dict[int, EvalPair] = {}
        self._labels: list[QLabel] = []  # B1, S1, B2, S2 ... (rows come in pairs, the baseline first)
        self._pickers: list[StepPicker] = []

        self._button = QToolButton(parent)
        self._button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._menu = QMenu(self._button)
        self._menu.setToolTipsVisible(True)
        self._panel = QWidget(self._menu)
        outer = QVBoxLayout(self._panel)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(6)
        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(10)
        self._grid.setVerticalSpacing(6)
        outer.addLayout(self._grid)
        line = QFrame(self._panel)
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        outer.addWidget(line)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(2)
        self._add = QToolButton(self._panel)
        self._remove = QToolButton(self._panel)
        for button in (self._add, self._remove):
            style_general_icon_button(button)
            buttons.addWidget(button)
        buttons.addStretch(1)
        outer.addLayout(buttons)
        self._action = QWidgetAction(self._menu)
        self._action.setDefaultWidget(self._panel)
        self._menu.addAction(self._action)
        self._button.setMenu(self._menu)

        self._add.clicked.connect(lambda _checked=False: self._add_pair())
        self._remove.clicked.connect(lambda _checked=False: self._remove_pair())
        self._rebuild()
        self.refresh_theme()
        self.set_available(False)

    # -- the group ------------------------------------------------------------------------------------------------------------

    def widgets(self) -> tuple[QWidget, ...]:
        return (self._button,)

    def set_available(self, available: bool) -> None:
        self._available = bool(available)
        self._button.setEnabled(self._available)
        self._button.setToolTip(BUTTON_TIP if self._available else NO_STEPS_TIP)

    def pair_count(self) -> int:
        return self._count

    def set_steps(self, steps: Sequence[EffectiveStep], colours: Sequence[str] | None, pairs: Iterable[EvalPair]) -> None:
        """Show the plan's steps and the roles of its pairs (a reference to a step that is not in `steps` shows as not set). A pair that is set but past the shown count raises the
        count to it. Emits nothing."""
        self._steps, self._colours = tuple(steps), colours
        self._pairs = {pair.number: pair for pair in pairs}
        highest = max((n for n, pair in self._pairs.items() if pair.baseline is not None or pair.signal is not None), default=MIN_PAIRS)
        self._count = max(self._count, clamp_pairs(highest))
        self._rebuild()
        self.set_available(bool(self._steps))

    def baseline_ref(self, pair: int = 1) -> StepRef | None:
        return self._picker(pair, "baseline").current_ref()

    def signal_ref(self, pair: int = 1) -> StepRef | None:
        return self._picker(pair, "signal").current_ref()

    def _picker(self, pair: int, role: str) -> StepPicker:
        return self._pickers[2 * (pair - 1) + _ROLES.index(role)]

    def refresh_theme(self) -> None:
        style_general_icon_button(self._button)
        self._button.setIcon(QIcon(evaluation_picture()(get_active_theme().text_primary)))
        theme = get_active_theme()
        for button, name in ((self._add, "plus"), (self._remove, "minus")):
            style_general_icon_button(button)
            button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
            button.setIcon(load_tabler_icon(name, color=theme.text_primary, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH))
        self._style_labels()
        for picker in self._pickers:
            picker.refresh_theme()

    # -- the rows -------------------------------------------------------------------------------------------------------------

    def _rebuild(self) -> None:
        """Make the rows match the count, fill the pickers, set the two icons, and tell an open pop-up its content changed size."""
        wanted = 2 * self._count
        while len(self._pickers) < wanted:
            self._add_row(len(self._pickers))
        while len(self._pickers) > wanted:
            for widgets in (self._labels, self._pickers):
                widget = widgets.pop()
                self._grid.removeWidget(widget)
                widget.hide()  # still parented to the panel here: it is not a window of its own
                widget.deleteLater()
        for index, picker in enumerate(self._pickers):
            pair, role = index // 2 + 1, _ROLES[index % 2]
            found = self._pairs.get(pair)
            picker.set_steps(self._steps, self._colours, None if found is None else getattr(found, role))
        self._style_labels()
        self._update_buttons()
        self._panel.adjustSize()
        self._layout_tick += 1
        self._action.setData(self._layout_tick)  # a QAction changed: an open QMenu then lays itself out again, so the new rows are not cut off (a value that always differs)

    def _add_row(self, index: int) -> None:
        pair, role = index // 2 + 1, _ROLES[index % 2]
        label = QLabel(f"{'B' if role == 'baseline' else 'S'}{pair}", self._panel)
        picker = StepPicker("not set", self._panel, ribbon=False)
        label.setToolTip((BASELINE_TIP if role == "baseline" else SIGNAL_TIP).format(n=pair))
        picker.setToolTip(label.toolTip())
        picker.step_chosen.connect(lambda ref, which=role, number=pair: self.role_chosen.emit(which, ref, number))
        self._grid.addWidget(label, index, 0)
        self._grid.addWidget(picker, index, 1)
        self._labels.append(label)
        self._pickers.append(picker)
        if self._panel.isVisible():  # a widget added to a layout that is already shown is not shown by itself until the next event
            label.show()
            picker.show()

    def _style_labels(self) -> None:
        """Bold role names; the signal's in the accent colour, as its chip on the plan bar (the baseline's chip is neutral)."""
        accent = get_active_theme().accent_blue
        for index, label in enumerate(self._labels):
            label.setStyleSheet(f"font-weight: bold; color: {accent};" if index % 2 else "font-weight: bold;")

    def _update_buttons(self) -> None:
        can_add, can_remove = self._count < MAX_PAIRS, self._count > MIN_PAIRS
        self._add.setEnabled(can_add)
        self._remove.setEnabled(can_remove)
        self._add.setToolTip(ADD_TIP.format(n=self._count + 1) if can_add else ADD_FULL_TIP)
        self._remove.setToolTip(REMOVE_TIP.format(n=self._count) if can_remove else REMOVE_LAST_TIP)

    # -- adding and removing a pair -------------------------------------------------------------------------------------------

    def _add_pair(self) -> None:
        if self._count >= MAX_PAIRS:
            return
        self._count += 1
        self._remember()
        self._rebuild()

    def _remove_pair(self) -> None:
        if self._count <= MIN_PAIRS:
            return
        number = self._count
        self.pair_removed.emit(number)  # the owner clears the roles of the pair first (its refresh may call `set_steps`, which still shows the pair)
        self._count = number - 1
        self._remember()
        self._rebuild()

    # -- remembered state -----------------------------------------------------------------------------------------------------

    def restore_ui_state(self, store: UiStateStore) -> None:
        self._store = store
        self._count = max(clamp_pairs(read(store, SENSORGRAM_EVAL_PAIRS)), self._count)
        self._rebuild()

    def _remember(self) -> None:
        if self._store is not None:
            self._store.set(SENSORGRAM_EVAL_PAIRS.key, self._count)
