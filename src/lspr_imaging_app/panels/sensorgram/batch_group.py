"""The Sensorgram ribbon's "Batch edit" group (2026-11-26): shrink many plan steps, or move many steps in time, with a live preview.

Screen purpose: change the edges of many steps at once (to leave a gap between steps, or to shift a series of steps) instead of dragging each edge by hand. The rules and the
arithmetic are in `plan/batch_edit.py`; this file is the two pop-ups. Display only: it holds the settings, reports them, and shows what the owner (`StepOverlays`) says; it knows
nothing of steps. The owner draws the preview on the plan bar and applies (one undo step).

Layout (a group of the Experimental plan tab, after "Edit"): two icon buttons, each opens a pop-up (a `QWidgetAction` in a `QMenu`, like the LbL button)::

    Shrink steps                                   Offset steps
    All 12 steps / 3 selected steps                All 12 steps / 3 selected steps
    Unit    [Cubes] [Time] [%]                     Unit    [Cubes] [Time]
    Amount  [ 2 cubes ]                            Shift by [ -3 cubes ]       (+ later, - earlier)
    From    [Start] [End] [Both]                   status: 12 steps will move; ...
    status: 9 steps will shrink; 3 too short ...                                    (cross) (tick)
                                                   (cross) (tick)

- **Which steps**: the steps selected on the plan bar; with none selected, all of them. The pop-up's first line says which. Select before opening: the pop-up closes on a click outside it.
- **Live preview**: while a pop-up is open, every change of a setting asks the owner for a new preview (`preview_requested(spec)`); the plan bar draws the new edges. Closing the pop-up in
  any way but the tick (Esc, a click outside, the cross) changes nothing (`preview_cleared`). The tick asks for the edit (`apply_requested(spec)`) and closes the pop-up.
- **Both** (shrink) splits the amount between the two sides; a percentage is of each step's own length (taken off in total, split for both).
- The settings (unit, side, the amount of each unit) are remembered (``sensorgram/batch_<kind>/...``).
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QButtonGroup, QDoubleSpinBox, QHBoxLayout, QLabel, QMenu, QPushButton, QToolButton, QVBoxLayout, QWidget, QWidgetAction

from lspr_ui import get_active_theme

from ...plan.batch_edit import (
    KIND_OFFSET,
    KIND_SHRINK,
    OFFSET_UNITS,
    SHRINK_UNITS,
    SIDE_BOTH,
    SIDE_END,
    SIDE_START,
    SIDES,
    UNIT_CUBES,
    UNIT_PERCENT,
    UNIT_TIME,
    BatchSpec,
)
from ..icon_button_style import style_general_icon_button
from ..image.array_pictograms import FlatIconButton, pictogram_icon
from ..ui_state import UiStateStore
from .edit_group import NO_STEPS_TIP
from .plan_icons import offset_steps_icon, shrink_steps_icon

SHRINK_TIP = (
    "Shrink steps: every selected step (all steps when none is selected) loses the amount from its start, its end or both, so a gap opens between neighbouring steps. "
    "A preview is drawn on the plan bar while the pop-up is open."
)
OFFSET_TIP = (
    "Offset steps: every selected step (all steps when none is selected) moves later (+) or earlier (-) by the same amount, keeping its length. "
    "A preview is drawn on the plan bar while the pop-up is open. Steps may overlap afterwards."
)
NEEDS_TRACES_TIP = "Batch edit: needs traces on the plot, an edge lands on the plotted cubes. Run a scan and select a ROI first."

_TITLES = {KIND_SHRINK: "Shrink steps", KIND_OFFSET: "Offset steps"}
_AMOUNT_LABELS = {KIND_SHRINK: "Amount", KIND_OFFSET: "Shift by"}
_UNIT_TEXT = {UNIT_CUBES: "Cubes", UNIT_TIME: "Time", UNIT_PERCENT: "%"}
_UNIT_TIPS = {
    UNIT_CUBES: "Count in cubes (the plotted points). Exact: the edge moves by whole cubes.",
    UNIT_TIME: "Count in seconds. The edge lands on the nearest cube, so an amount under half a cube spacing moves nothing.",
    UNIT_PERCENT: "A percentage of each step's own length (taken off in total: with Both, half from each side). The edge lands on the nearest cube.",
}
_SIDE_TEXT = {SIDE_START: "Start", SIDE_END: "End", SIDE_BOTH: "Both"}
_SIDE_TIPS = {
    SIDE_START: "Take the amount off the start of each step.",
    SIDE_END: "Take the amount off the end of each step.",
    SIDE_BOTH: "Split the amount: half off the start and half off the end (with whole cubes and an odd number, the start gives up the extra cube).",
}
_DEFAULT_AMOUNTS = {
    KIND_SHRINK: {UNIT_CUBES: 1.0, UNIT_TIME: 5.0, UNIT_PERCENT: 10.0},
    KIND_OFFSET: {UNIT_CUBES: 1.0, UNIT_TIME: 10.0},
}
_MAX_AMOUNT = 99999.0


class BatchToolPanel(QWidget):
    """The content of one pop-up. `changed` is emitted by the user's changes only."""

    changed = pyqtSignal()
    apply_requested = pyqtSignal()
    cancel_requested = pyqtSignal()

    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind = kind
        self._units = SHRINK_UNITS if kind == KIND_SHRINK else OFFSET_UNITS
        self._unit = UNIT_CUBES
        self._side = SIDE_BOTH
        self._amounts = dict(_DEFAULT_AMOUNTS[kind])
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(6)
        title = QLabel(_TITLES[kind], self)
        title.setStyleSheet("font-weight: bold;")
        outer.addWidget(title)
        self._target = QLabel("", self)
        self._target.setStyleSheet(f"color: {get_active_theme().text_dim};")
        outer.addWidget(self._target)

        self._unit_buttons = self._choice_row(outer, "Unit", self._units, _UNIT_TEXT, _UNIT_TIPS, self._on_unit_clicked)
        amount_row = QHBoxLayout()
        amount_row.setContentsMargins(0, 0, 0, 0)
        amount_row.addWidget(QLabel(_AMOUNT_LABELS[kind], self))
        self._box = QDoubleSpinBox(self)
        amount_row.addWidget(self._box)
        amount_row.addStretch(1)
        outer.addLayout(amount_row)
        self._side_buttons: dict[str, QPushButton] = {}
        if kind == KIND_SHRINK:
            self._side_buttons = self._choice_row(outer, "From", SIDES, _SIDE_TEXT, _SIDE_TIPS, self._on_side_clicked)

        self._status = QLabel("", self)
        self._status.setWordWrap(True)
        self._status.setFixedWidth(300)
        outer.addWidget(self._status)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.addStretch(1)
        self.cancel_button = FlatIconButton("Cancel: close without changing anything.", self)
        self.apply_button = FlatIconButton("Apply: change the steps (one undo step; Ctrl+Z takes it back).", self)
        self.cancel_button.clicked.connect(lambda _checked=False: self.cancel_requested.emit())
        self.apply_button.clicked.connect(lambda _checked=False: self.apply_requested.emit())
        bottom.addWidget(self.cancel_button)
        bottom.addWidget(self.apply_button)
        outer.addLayout(bottom)

        self._unit_buttons[self._unit].setChecked(True)
        if self._side_buttons:
            self._side_buttons[self._side].setChecked(True)
        self._show_unit()
        self._box.valueChanged.connect(self._on_amount_changed)
        self.refresh_theme()

    def _choice_row(self, outer: QVBoxLayout, text: str, keys, names: dict[str, str], tips: dict[str, str], on_click) -> dict[str, QPushButton]:
        """A label and a row of exclusive flat buttons (a segmented choice)."""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel(text, self))
        group = QButtonGroup(self)
        group.setExclusive(True)
        buttons: dict[str, QPushButton] = {}
        for key in keys:
            button = QPushButton(names[key], self)
            button.setCheckable(True)
            button.setToolTip(tips[key])
            group.addButton(button)
            button.clicked.connect(lambda _checked=False, which=key: on_click(which))
            row.addWidget(button)
            buttons[key] = button
        row.addStretch(1)
        outer.addLayout(row)
        return buttons

    # -- the settings -------------------------------------------------------------------------------------------------------

    def spec(self) -> BatchSpec:
        return BatchSpec(self._kind, self._unit, float(self._box.value()), self._side)

    def _show_unit(self) -> None:
        """The amount box for the current unit: decimals, step, suffix, range, and the value remembered for that unit. Emits nothing."""
        unit, box = self._unit, self._box
        box.blockSignals(True)
        box.setDecimals(0 if unit == UNIT_CUBES else 1)
        box.setSingleStep(1.0)
        box.setSuffix({UNIT_CUBES: " cubes", UNIT_TIME: " s", UNIT_PERCENT: " %"}[unit])
        smallest = 1.0 if unit == UNIT_CUBES else 0.1
        if self._kind == KIND_SHRINK:
            box.setRange(smallest, 100.0 if unit == UNIT_PERCENT else _MAX_AMOUNT)
        else:
            box.setRange(-_MAX_AMOUNT, _MAX_AMOUNT)
        box.setValue(self._amounts[unit])
        box.setToolTip(
            "How much each step is shortened in total (with Both, half comes off each side)."
            if self._kind == KIND_SHRINK else "How far each step moves: + later, - earlier."
        )
        box.blockSignals(False)

    def _on_unit_clicked(self, unit: str) -> None:
        if unit == self._unit:
            return
        self._unit = unit
        self._show_unit()
        self.changed.emit()

    def _on_side_clicked(self, side: str) -> None:
        if side == self._side:
            return
        self._side = side
        self.changed.emit()

    def _on_amount_changed(self, value: float) -> None:
        self._amounts[self._unit] = float(value)
        self.changed.emit()

    # -- what the owner shows -----------------------------------------------------------------------------------------------

    def set_target(self, text: str) -> None:
        self._target.setText(text)

    def set_status(self, text: str, *, problem: bool = False) -> None:
        theme = get_active_theme()
        self._status.setStyleSheet(f"color: {theme.accent_red if problem else theme.text_dim};")
        self._status.setText(text)
        self._status.adjustSize()
        self.adjustSize()

    def set_apply_enabled(self, enabled: bool) -> None:
        self.apply_button.setEnabled(bool(enabled))

    def refresh_theme(self) -> None:
        theme = get_active_theme()
        self.cancel_button.setIcon(pictogram_icon("cancel", accent=theme.text_secondary))
        self.apply_button.setIcon(pictogram_icon("apply", accent=theme.accent_green))
        self._target.setStyleSheet(f"color: {theme.text_dim};")

    # -- remembered state ---------------------------------------------------------------------------------------------------

    def _key(self, name: str) -> str:
        return f"sensorgram/batch_{self._kind}/{name}"

    def restore_ui_state(self, store: UiStateStore) -> None:
        """The last unit, side and amount of each unit come back. Wired after the restore, so a remembered value signals nothing."""
        unit, side = store.get(self._key("unit")), store.get(self._key("side"))
        for name in self._units:
            saved = store.get(self._key(f"amount_{name}"))
            if isinstance(saved, (int, float)) and not isinstance(saved, bool):
                self._amounts[name] = float(saved)
        if unit in self._units:
            self._unit = str(unit)
            self._unit_buttons[self._unit].setChecked(True)
        if self._side_buttons and side in SIDES:
            self._side = str(side)
            self._side_buttons[self._side].setChecked(True)
        self._show_unit()
        self._amounts[self._unit] = float(self._box.value())  # what the box could take (a remembered value outside the range is clipped)
        self.changed.connect(lambda: self._save(store))

    def _save(self, store: UiStateStore) -> None:
        store.set(self._key("unit"), self._unit)
        if self._side_buttons:
            store.set(self._key("side"), self._side)
        for name in self._units:
            store.set(self._key(f"amount_{name}"), float(self._amounts[name]))


@dataclass
class _Tool:
    button: QToolButton
    menu: QMenu
    panel: BatchToolPanel
    action: QWidgetAction
    ticks: int = 0


class BatchEditControls(QObject):
    preview_requested = pyqtSignal(object)  # BatchSpec: a pop-up opened, or one of its settings changed
    preview_cleared = pyqtSignal()  # a pop-up closed (by any means)
    apply_requested = pyqtSignal(object)  # BatchSpec: the tick

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._available = False
        self._tools: dict[str, _Tool] = {}
        for kind in (KIND_SHRINK, KIND_OFFSET):
            self._tools[kind] = self._build(kind, parent)
        self.refresh_theme()
        self.set_state(False, "")

    def _build(self, kind: str, parent: QWidget) -> _Tool:
        button = QToolButton(parent)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(button)
        menu.setToolTipsVisible(True)
        panel = BatchToolPanel(kind, menu)
        action = QWidgetAction(menu)
        action.setDefaultWidget(panel)
        menu.addAction(action)
        button.setMenu(menu)
        tool = _Tool(button, menu, panel, action)
        menu.aboutToShow.connect(lambda: self.preview_requested.emit(panel.spec()))
        menu.aboutToHide.connect(lambda: self.preview_cleared.emit())
        panel.changed.connect(lambda: self.preview_requested.emit(panel.spec()))
        panel.apply_requested.connect(lambda: self._apply(tool))
        panel.cancel_requested.connect(menu.close)
        return tool

    def _apply(self, tool: _Tool) -> None:
        self.apply_requested.emit(tool.panel.spec())
        tool.menu.close()

    # -- the group ----------------------------------------------------------------------------------------------------------

    def widgets(self) -> tuple[QWidget, ...]:
        return (self._tools[KIND_SHRINK].button, self._tools[KIND_OFFSET].button)

    def shrink_button(self) -> QToolButton:
        return self._tools[KIND_SHRINK].button

    def offset_button(self) -> QToolButton:
        return self._tools[KIND_OFFSET].button

    def panel(self, kind: str) -> BatchToolPanel:
        return self._tools[kind].panel

    def menu(self, kind: str) -> QMenu:
        return self._tools[kind].menu

    def set_state(self, available: bool, target_text: str, *, has_axis: bool = True) -> None:
        """`available`: the dataset has steps. `has_axis`: traces are drawn (an edge lands on their cubes). `target_text`: which steps a batch edit would act on ("All 12 steps",
        "3 selected steps"). A button that cannot act is greyed out and its tooltip says what is missing."""
        self._available = bool(available)
        usable = self._available and has_axis
        for kind, tool in self._tools.items():
            tool.button.setEnabled(usable)
            tool.button.setToolTip((SHRINK_TIP if kind == KIND_SHRINK else OFFSET_TIP) if usable else (NEEDS_TRACES_TIP if self._available else NO_STEPS_TIP))
            tool.panel.set_target(target_text)

    def set_status(self, kind: str, text: str, *, problem: bool = False, can_apply: bool = True) -> None:
        """The pop-up's status line (what the preview found) and whether the tick can act. Makes an open menu lay itself out again."""
        tool = self._tools[kind]
        tool.panel.set_status(text, problem=problem)
        tool.panel.set_apply_enabled(can_apply)
        tool.ticks += 1
        tool.action.setData(tool.ticks)

    def set_target(self, kind: str, text: str) -> None:
        self._tools[kind].panel.set_target(text)

    def refresh_theme(self) -> None:
        theme = get_active_theme()
        ink = theme.text_primary
        for kind, icon in ((KIND_SHRINK, shrink_steps_icon(ink, theme.accent_gold)), (KIND_OFFSET, offset_steps_icon(ink, theme.accent_gold))):
            tool = self._tools[kind]
            style_general_icon_button(tool.button)
            tool.button.setIcon(icon)
            tool.panel.refresh_theme()

    def restore_ui_state(self, store: UiStateStore) -> None:
        for tool in self._tools.values():
            tool.panel.restore_ui_state(store)
