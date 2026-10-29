"""``PlanBarActions``: turns the plan bar's pointer gestures into commands of ``ExperimentPlanModule`` (2026-10-26).

Screen purpose (none of its own): the gestures are reported by ``plan_bar.PlanBarItem`` through ``SensorgramPlot``; the module holds the edits, the undo and the session state. This object
is the one place that knows both, so the plot stays free of commands and the panel free of gesture code.

- **Drag an edge of the selected step**: the pointer's x is converted to a time (`AxisMap`), the module moves the edge, snapped to a cube point (it knows the grid). Only that step
  changes; with Shift held, a boundary it shares with its neighbour moves both. The whole drag is one undo step (`undo_manager.begin_batch` ... `end_batch`). While it lasts a small
  label shows where the edge is (seconds, or the cube number).
- **Double-click a step**: the in-place editor opens over it with the whole note; a changed text becomes `set_label` (empty = back to the measured note).
- **Right-click a step**: a menu with Edit text, Colour, Default colour, Reset step and Reset all; each is one undo step. The colour dialog is the system one.

It needs to know the plot's axis, so the panel passes a function that returns it for the traces being drawn (`PlanAxisContext`), or None when nothing is drawn.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from PyQt6.QtCore import QObject, QPoint
from PyQt6.QtGui import QColor, QCursor
from PyQt6.QtWidgets import QColorDialog, QMenu, QToolTip

from ...plan.axis_map import AxisMap
from ...plan.bar_model import boundary_readout
from ...plan.module import ExperimentPlanModule
from ...undo import undo_manager
from .plot import SensorgramPlot

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlanAxisContext:
    """What a gesture needs to know about the plot behind it."""

    axis: AxisMap  # time <-> x
    grid_unix_ms: np.ndarray  # where a dragged boundary may land (the plotted cubes' start times, and the end of the data)
    time_mode: bool  # the x axis is seconds (else the cube number)


class PlanBarActions(QObject):
    def __init__(
        self, plan: ExperimentPlanModule, plot: SensorgramPlot, context: Callable[[], PlanAxisContext | None], parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._plan = plan
        self._plot = plot
        self._context = context
        self._drag_context: PlanAxisContext | None = None
        self._batch_open = False
        self._structure_tools = True  # remove / add / unify steps and the baseline / signal roles in the menu: on until the owner ties them to the Experimental plan tab
        plan.notice.connect(self._say)
        plot.plan_edge_drag_started.connect(self._drag_started)
        plot.plan_edge_dragged.connect(self._dragged)
        plot.plan_edge_drag_finished.connect(self._drag_finished)
        plot.plan_text_edit_requested.connect(self._edit_text)
        plot.plan_text_committed.connect(self._text_committed)
        plot.plan_menu_requested.connect(self._show_menu)

    # -- dragging a boundary --------------------------------------------------------------------------------------------------

    def _drag_started(self, index: int, edge: str) -> None:
        self._end_batch()
        self._drag_context = self._context()  # the axis does not change during a drag
        try:
            undo_manager.begin_batch("Move step boundary")
            self._batch_open = True
        except RuntimeError:  # another gesture holds the undo stack: the boundary still moves, as separate undo steps
            logger.warning("A plan boundary is dragged while another undo batch is open; its moves are not merged")

    def _dragged(self, index: int, edge: str, x: float, together: bool = False) -> None:
        context = self._drag_context
        if context is None:
            return
        self._plan.move_edge(index, edge, context.axis.unix_ms_at(x), context.grid_unix_ms, together=together)
        steps = self._plan.steps()
        if 0 <= index < len(steps):
            moved = steps[index].end_unix_ms if edge == "end" else steps[index].start_unix_ms
            QToolTip.showText(QCursor.pos(), boundary_readout(context.axis.x_at(moved), context.time_mode), self._plot)

    def _drag_finished(self) -> None:
        self._end_batch()
        self._drag_context = None
        QToolTip.hideText()

    def _end_batch(self) -> None:
        if self._batch_open:
            self._batch_open = False
            undo_manager.end_batch()

    # -- the note ---------------------------------------------------------------------------------------------------------------

    def _edit_text(self, index: int) -> None:
        steps = self._plan.steps()
        if 0 <= index < len(steps):
            self._plot.open_plan_text_editor(index, steps[index].label)

    def _text_committed(self, index: int, text: str) -> None:
        self._plan.set_label(index, text)

    # -- the right-click menu ---------------------------------------------------------------------------------------------------

    def _targets(self, index: int) -> list[int]:
        """The steps a menu action applies to: the whole selection when the clicked step is in it, else the clicked step alone."""
        selected = self._plot.plan_bar().selected_indices()
        return list(selected) if index in selected else [index]

    def set_structure_tools(self, active: bool) -> None:
        """Whether the menu offers the tools that change which steps exist (Remove step, Add step, Unify selected steps, Restore removed steps) and the evaluation roles (Use as
        baseline / signal). The owner turns them on while the Experimental plan tab is the active one, so nothing structural happens by accident while working on the traces."""
        self._structure_tools = bool(active)

    def structure_tools_active(self) -> bool:
        return self._structure_tools

    def _say(self, text: str) -> None:
        QToolTip.showText(QCursor.pos(), text, self._plot)

    def _has_own_colour(self, step) -> bool:
        if step.is_added:
            return step.color is not None
        edit = self._plan.state().edit_for(step.key)
        return edit is not None and edit.color is not None

    def build_menu(self, index: int) -> QMenu | None:
        """The menu for step `index` (None when there is no such step). Colour, Default colour, Reset, Remove and Unify act on all selected steps when the clicked one is
        selected; Edit text, Add step and the roles on the clicked step only. Each action is connected; `_show_menu` pops it up."""
        steps = self._plan.steps()
        if not 0 <= index < len(steps):
            return None
        targets = [i for i in self._targets(index) if 0 <= i < len(steps)]
        many = len(targets) > 1
        step = steps[index]
        menu = QMenu(self._plot)
        header = menu.addAction(f"{len(targets)} steps selected" if many else (f"Step {step.order}: {step.label}" if step.label else f"Step {step.order}"))
        header.setEnabled(False)
        menu.addSeparator()
        menu.addAction("Edit text...").triggered.connect(lambda _checked=False: self._edit_text(index))
        menu.addAction("Colour...").triggered.connect(lambda _checked=False: self._choose_colour(index, targets))
        default = menu.addAction("Default colour")
        default.setEnabled(any(self._has_own_colour(steps[i]) for i in targets))
        default.triggered.connect(lambda _checked=False: self._plan.set_colors(targets, None))
        menu.addSeparator()
        reset = menu.addAction("Reset steps to measured" if many else "Reset step to measured")
        reset.setEnabled(any(steps[i].edited for i in targets))
        reset.triggered.connect(lambda _checked=False: self._plan.reset_steps(targets))
        reset_all = menu.addAction("Reset all steps")
        state = self._plan.state()
        reset_all.setEnabled(bool(state.edits or state.removed or state.added))
        reset_all.triggered.connect(lambda _checked=False: self._plan.reset_all())
        if self._structure_tools:
            self._add_structure_items(menu, index, targets, many)
        return menu

    def _add_structure_items(self, menu: QMenu, index: int, targets: list[int], many: bool) -> None:
        """Remove / add / unify steps, restore the removed ones, and name the baseline and signal step of evaluation pair 1 (B1 / S1 on the bar)."""
        step = self._plan.steps()[index]
        menu.addSeparator()
        remove = menu.addAction("Remove steps" if many else "Remove step")
        remove.triggered.connect(lambda _checked=False: self._plan.remove_steps(targets))
        add = menu.addMenu("Add step")
        has_axis = self._context() is not None  # the grid a new boundary snaps to comes from the plotted cubes
        for text, side in (("In front of this step", "before"), ("Behind this step", "after")):
            action = add.addAction(text)
            action.setEnabled(has_axis)
            action.triggered.connect(lambda _checked=False, where=side: self._add_step(index, where))
        unify = menu.addAction("Unify selected steps")
        unify.setEnabled(self._plan.can_unify(targets))
        unify.setToolTip("Needs two or more selected steps with no other step between them.")
        unify.triggered.connect(lambda _checked=False: self._unify(targets))
        restore = menu.addAction("Restore removed steps")
        restore.setEnabled(self._plan.has_removed())
        restore.triggered.connect(lambda _checked=False: self._plan.restore_removed())
        menu.addSeparator()
        for role, text, chip in (("baseline", "Use as baseline (B1)", "B1"), ("signal", "Use as signal (S1)", "S1")):
            action = menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(chip in step.roles)
            action.setEnabled(not many)
            action.triggered.connect(lambda _checked=False, which=role, on=chip in step.roles: self._plan.set_role(which, None if on else index))

    def _add_step(self, index: int, side: str) -> None:
        context = self._context()
        if context is None:
            return
        ref = self._plan.add_step(index, side, context.grid_unix_ms)
        new = self._plan.step_by_ref(ref) if ref is not None else None
        if new is not None:
            self._plot.plan_bar().select(new.order - 1)  # the new step, ready to be dragged into place

    def _unify(self, targets: list[int]) -> None:
        if self._plan.unify_steps(targets):
            self._plot.plan_bar().select(min(targets))  # the step they became

    def _show_menu(self, index: int, position: QPoint) -> None:
        menu = self.build_menu(index)
        if menu is not None:
            menu.exec(position)
            menu.deleteLater()

    def _choose_colour(self, index: int, targets: list[int]) -> None:
        shown = next((s for s in self._plot.plan_bar().steps() if s.index == index), None)
        current = QColor(shown.color) if shown is not None else QColor("#4e79a7")
        colour = QColorDialog.getColor(current, self._plot, "Choose the colour of the steps" if len(targets) > 1 else "Choose the colour of the step")
        if colour.isValid():
            self._plan.set_colors(targets, colour.name())
