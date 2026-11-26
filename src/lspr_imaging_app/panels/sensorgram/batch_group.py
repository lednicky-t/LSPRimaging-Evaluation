"""The Sensorgram ribbon's "Batch edit" group (2026-11-26): shrink many plan steps, or move many steps in time, with a live preview.

Screen purpose: change the edges of many steps at once (to leave a gap between steps, or to shift a series of steps) instead of dragging each edge by hand. The rules and the
arithmetic are in `plan/batch_edit.py`; this file is the two pop-ups. Display only: it holds the settings, reports them, and shows what the owner (`StepOverlays`) says; it knows
nothing of steps. The owner draws the preview on the plan bar and applies (one undo step).

Layout (a group of the Experimental plan tab, after "Edit"): two icon buttons, each opens a pop-up (a `QWidgetAction` in a `QMenu`, like the LbL button)::

    Shrink steps                                   Offset steps
    All 12 steps / 3 selected steps                All 12 steps / 3 selected steps
    Amount  [ 2 ] cubes                            Shift by [ -3 ] cubes       (+ later, - earlier)
    From    (3 pictures)                           status: 12 steps will move; ...
    status: 9 steps will shrink; 3 too short ...                                    (cross) (tick)
                                                   (cross) (tick)

  The unit ("cubes" / "s" / "%"; offset: no "%") is plain text after the number: the mouse wheel over it (or a click) changes it, `UnitScroller`.

- **Which steps**: the steps selected on the plan bar; with none selected, all of them. The pop-up's first line says which. Select before opening: the pop-up closes on a click outside it.
- **Live preview**: while a pop-up is open, every change of a setting asks the owner for a new preview (`preview_requested(spec)`); the plan bar draws the new edges. Closing the pop-up in
  any way but the tick (Esc, a click outside, the cross) changes nothing (`preview_cleared`). The tick asks for the edit (`apply_requested(spec)`) and closes the pop-up.
- **Both** (shrink) splits the amount between the two sides; a percentage is of each step's own length (taken off in total, split for both).
- The settings (unit, side, the amount of each unit) are remembered (``sensorgram/batch_<kind>/...``).
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QObject, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QButtonGroup, QDoubleSpinBox, QHBoxLayout, QLabel, QMenu, QToolButton, QVBoxLayout, QWidget, QWidgetAction

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
from .plan_icons import offset_steps_icon, shrink_side_icon, shrink_steps_icon

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
_UNIT_SUFFIX = {UNIT_CUBES: "cubes", UNIT_TIME: "s", UNIT_PERCENT: "%"}
_UNIT_TIPS = {
    UNIT_CUBES: "Cubes: count in cubes (the plotted points). Exact: the edge moves by whole cubes.",
    UNIT_TIME: "Seconds: the edge lands on the nearest cube, so an amount under half a cube spacing moves nothing.",
    UNIT_PERCENT: "Percent: a percentage of each step's own length (taken off in total: with Both, half from each side). The edge lands on the nearest cube.",
}
_NUMBER_WIDTH = 84
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


_SIDE_ICON = 28
_SIDE_ENDS = {SIDE_START: (True, False), SIDE_END: (False, True), SIDE_BOTH: (True, True)}  # (comes off the start, comes off the end)


def _side_icon(side: str, theme) -> QIcon:
    """The picture of one "From" choice with its three looks: not chosen (all muted), hovered and chosen (ink and the gold accent), so that no frame is needed to show the choice."""
    from_start, from_end = _SIDE_ENDS[side]
    size = QSize(_SIDE_ICON * 2, _SIDE_ICON * 2)
    muted = shrink_side_icon(from_start, from_end, theme.text_dim, theme.text_dim).pixmap(size)
    lit = shrink_side_icon(from_start, from_end, theme.text_primary, theme.accent_gold).pixmap(size)
    icon = QIcon()
    icon.addPixmap(muted, QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(lit, QIcon.Mode.Active, QIcon.State.Off)
    icon.addPixmap(lit, QIcon.Mode.Normal, QIcon.State.On)
    icon.addPixmap(lit, QIcon.Mode.Active, QIcon.State.On)
    return icon


class UnitScroller(QToolButton):
    """The unit written after a number, as plain text that changes with the mouse wheel: wheel up = the unit before, wheel down = the next one (round the ring); a click goes to
    the next one too. No box in any state. `chosen(unit)` is emitted by the user's change only; `set_unit` emits nothing. Its width fits the widest text, so the row never shifts."""

    chosen = pyqtSignal(str)

    def __init__(self, units, names: dict[str, str], tips: dict[str, str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.units = tuple(units)
        self._names, self._tips = names, tips
        self._unit = self.units[0]
        self.setAutoRaise(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.clicked.connect(lambda _checked=False: self._step(1))
        widest = max(self.fontMetrics().horizontalAdvance(names[unit]) for unit in self.units)
        self.setFixedWidth(widest + 14)
        self.set_unit(self._unit)

    def unit(self) -> str:
        return self._unit

    def set_unit(self, unit: str) -> None:
        self._unit = unit
        self.setText(self._names[unit])
        self.setToolTip("Unit: scroll the mouse wheel over it (or click) to change it.\n" + self._tips[unit])

    def choose(self, unit: str) -> None:
        """What the user's wheel or click does: show `unit` and report it (nothing when it is already the unit)."""
        if unit != self._unit:
            self.set_unit(unit)
            self.chosen.emit(unit)

    def _step(self, direction: int) -> None:
        self.choose(self.units[(self.units.index(self._unit) + direction) % len(self.units)])

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        delta = event.angleDelta().y()
        if delta:
            self._step(-1 if delta > 0 else 1)
        event.accept()


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
        self._title = QLabel(_TITLES[kind], self)
        outer.addWidget(self._title)
        self._target = QLabel("", self)
        outer.addWidget(self._target)
        self._captions: list[QLabel] = []

        amount_row = QHBoxLayout()  # one row: the caption, the number and its unit (a text that changes with the mouse wheel)
        amount_row.setContentsMargins(0, 0, 0, 0)
        amount_row.setSpacing(10)
        amount_row.addWidget(self._caption(_AMOUNT_LABELS[kind]))
        self._box = QDoubleSpinBox(self)
        self._box.setFixedWidth(_NUMBER_WIDTH)
        amount_row.addWidget(self._box)
        self._unit_text = UnitScroller(self._units, _UNIT_SUFFIX, _UNIT_TIPS, self)
        self._unit_text.chosen.connect(self._on_unit_chosen)
        amount_row.addWidget(self._unit_text)
        amount_row.addStretch(1)
        outer.addLayout(amount_row)
        self._side_buttons: dict[str, QToolButton] = {}
        if kind == KIND_SHRINK:
            self._side_buttons = self._choice_row(outer, "From", SIDES, _SIDE_TEXT, _SIDE_TIPS, self._on_side_clicked)  # pictures, no words (`refresh_theme`)

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

        self._unit_text.set_unit(self._unit)
        if self._side_buttons:
            self._side_buttons[self._side].setChecked(True)
        self._show_unit()
        self._box.valueChanged.connect(self._on_amount_changed)
        self.refresh_theme()

    def _caption(self, text: str) -> QLabel:
        label = QLabel(text, self)
        self._captions.append(label)
        return label

    def _choice_row(self, outer: QVBoxLayout, text: str, keys, names: dict[str, str], tips: dict[str, str], on_click) -> dict[str, QToolButton]:
        """A caption and a row of exclusive choices drawn as pictures: no box in any state, the chosen one is drawn in the accent colour (see `refresh_theme`). `names` are the
        buttons' accessible names; `tips` what hovering says."""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)
        row.addWidget(self._caption(text))
        group = QButtonGroup(self)
        group.setExclusive(True)
        buttons: dict[str, QToolButton] = {}
        for key in keys:
            button = QToolButton(self)
            button.setAccessibleName(names[key])
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            button.setIconSize(QSize(_SIDE_ICON, _SIDE_ICON))
            button.setFixedSize(_SIDE_ICON + 6, _SIDE_ICON + 6)
            button.setAutoRaise(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
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
        """The amount box for the current unit: decimals, step, range, and the value remembered for that unit (the unit's own text is beside the box). Emits nothing."""
        unit, box = self._unit, self._box
        box.blockSignals(True)
        box.setDecimals(0 if unit == UNIT_CUBES else 1)
        box.setSingleStep(1.0)
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

    def _on_unit_chosen(self, unit: str) -> None:
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
        self.setStyleSheet("QLabel { background: transparent; }")
        self._title.setStyleSheet(f"color: {theme.text_primary}; font-weight: 600; background: transparent;")
        self._target.setStyleSheet(f"color: {theme.text_secondary}; background: transparent;")
        for caption in self._captions:
            caption.setStyleSheet(f"color: {theme.text_muted}; background: transparent;")
        for side, button in self._side_buttons.items():
            button.setStyleSheet("QToolButton, QToolButton:hover, QToolButton:checked { border: none; background: transparent; padding: 0px; }")
            button.setIcon(_side_icon(side, theme))
        self._unit_text.setStyleSheet(  # the unit is coloured like a chosen option: it is the one thing in the row you can change by hand
            f"QToolButton {{ border: none; background: transparent; padding: 2px 0px; color: {theme.accent_blue}; font-weight: 400; }}"
            f"QToolButton:hover {{ color: {theme.text_primary}; background: transparent; }}"
        )
        self.update()

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
            self._unit_text.set_unit(self._unit)
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
