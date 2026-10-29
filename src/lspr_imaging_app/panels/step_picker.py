"""``StepPicker``: a drop-down of the experimental plan's steps that any panel can put in its ribbon (2026-10-29).

The plan's steps are the app's ranges (``docs/plan_steps_as_ranges_2026-10-29.md``): a function that needs a stretch of the experiment (the baseline, the signal) lets the user pick
a step instead of typing cube numbers. The picker only displays what it is given and reports the user's choice; it holds no plan state.

- ``set_steps(steps, colours, current)`` fills it: the first entry is "nothing chosen" (its text is the picker's own, e.g. ``B1: not set``), then one entry per step, in time order, as
  ``<prefix><number> · <note>`` with a small square in the step's colour. The number is the step's place in time (``EffectiveStep.order``).
- ``step_chosen(ref)`` is emitted when the **user** picks an entry (not when the owner calls ``set_steps``): the step's stable reference, or None for "nothing chosen". Keep the reference,
  not the number: the number changes when a step is added or removed.
- A chosen step that is no longer in the list (removed, another dataset) shows as "nothing chosen"; the owner decides what that means for its own state.
"""

from __future__ import annotations

from collections.abc import Sequence

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPixmap
from PyQt6.QtWidgets import QComboBox, QWidget

from ..plan.edits import EffectiveStep
from ..plan.state import StepRef
from .ribbon_fields import style_ribbon_combo

MAX_NOTE_CHARS = 22  # longer notes are cut in the list (the tooltip of an entry has the whole note)
_SWATCH_PX = 10
_NEUTRAL = "#8a8f98"  # a step without a colour of its own, when the owner has none to give


def _swatch(colour: str) -> QIcon:
    pixmap = QPixmap(_SWATCH_PX, _SWATCH_PX)
    pixmap.fill(QColor(colour) if QColor(colour).isValid() else QColor(_NEUTRAL))
    return QIcon(pixmap)


def entry_text(step: EffectiveStep, prefix: str = "") -> str:
    note = step.label if len(step.label) <= MAX_NOTE_CHARS else step.label[: MAX_NOTE_CHARS - 1] + "…"
    return f"{prefix}{step.order} · {note}" if note else f"{prefix}{step.order}"


class StepPicker(QComboBox):
    step_chosen = pyqtSignal(object)  # a step reference, or None

    def __init__(self, none_text: str, parent: QWidget | None = None, *, prefix: str = "") -> None:
        super().__init__(parent)
        self._none_text = none_text
        self._prefix = prefix
        self._refs: list[StepRef | None] = [None]
        self.setIconSize(QSize(_SWATCH_PX, _SWATCH_PX))
        self.addItem(f"{prefix}00 · {'W' * MAX_NOTE_CHARS}")  # the widest entry the picker will ever show: the ribbon look is sized from it, once
        style_ribbon_combo(self)
        self.clear()
        self.addItem(none_text)
        self.activated.connect(self._on_activated)

    def set_steps(self, steps: Sequence[EffectiveStep], colours: Sequence[str] | None = None, current: StepRef | None = None) -> None:
        """Show these steps (in the order given) with `colours` (one per step, else a neutral grey) and select the one `current` names. Emits nothing."""
        self.blockSignals(True)
        try:
            self.clear()
            self._refs = [None]
            self.addItem(self._none_text)
            for index, step in enumerate(steps):
                colour = colours[index] if colours is not None and index < len(colours) else (step.color or _NEUTRAL)
                self.addItem(_swatch(colour), entry_text(step, self._prefix))
                self.setItemData(self.count() - 1, step.label or f"Step {step.order}", Qt.ItemDataRole.ToolTipRole)
                self._refs.append(step.ref)
            self.setCurrentIndex(self._refs.index(current) if current in self._refs else 0)
        finally:
            self.blockSignals(False)

    def refresh_theme(self) -> None:
        """Re-apply the ribbon look for the active theme, keeping the size it was given at the start (the entries change, the picker's width does not)."""
        size = self.size()
        style_ribbon_combo(self)
        self.setFixedSize(size)

    def current_ref(self) -> StepRef | None:
        index = self.currentIndex()
        return self._refs[index] if 0 <= index < len(self._refs) else None

    def _on_activated(self, index: int) -> None:
        self.step_chosen.emit(self._refs[index] if 0 <= index < len(self._refs) else None)
