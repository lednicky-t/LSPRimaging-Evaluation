"""``ExperimentPlanModule``: the experiment steps as the user sees them, the dataset's measured steps with the user's edits on top (2026-10-26).

Owner of one saved value, ``PlanEditsState`` (the session's ``"plan"`` block). The measured steps stay in the dataset's acquisition metadata and are never
written; this module reads them, applies the edits (`edits.apply_edits`) and hands the result out. Panels draw ``steps()`` and turn gestures into the
commands below; they keep no step state of their own.

- **Commands** each push one undo step (a drag is several `move_edge` calls inside an ``undo_manager`` batch, so it is one step). `steps_changed(reason)`
  tells a view what moved: ``"boundary"`` (a step's time range changed: anything computed from it may be stale), ``"label"`` and ``"color"`` (cosmetic),
  ``"reset"``, ``"restored"`` (a session was loaded), ``"dataset"`` (the dataset or its metadata changed).
- **Session restore** (`restore_state`) replaces the edits without an undo step and without a stored-file decision: edits that no longer match the
  dataset's steps are listed in `warnings()` (and logged) and not applied. The module does not clear itself on `dataset_loaded` for the same reason
  `FrameRolesModule` does not: the session restore runs after the load and must not be undone by a second reset. `dataset_cleared` does reset.
- The steps are computed on demand and cached against the measured plan object and the state, so a dataset replaced or re-imported is picked up without
  any other wiring.
- **Structure and evaluation roles (2026-10-29).** `remove_steps`, `restore_removed`, `unify_steps` and `add_step` change which steps exist (reason ``"structure"``: the bar's
  selection is by index, so a view clears it, and the smoothing pieces and the step statistics are made from the steps, so they are redrawn like after a moved boundary).
  `set_role` names the baseline or the signal step of an evaluation pair (reason ``"roles"``, cosmetic). **Other panels read the steps and the roles from here**: `steps()` (each
  has ``order``, the number the user sees, and ``ref``, which stays the same when steps are added or removed), `step_by_ref`, `baseline_step(pair)` / `signal_step(pair)`, and
  `cubes_of(step)`, the whole cubes of a step. A role whose step is removed is cleared and `notice(text)` says so.
"""

from __future__ import annotations

import logging

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from lspr_core import ImagingPlan

from ..dataset.module import DatasetModule
from ..diagnostics import instrumented
from ..undo import FunctionCommand, undo_manager
from . import edits
from .axis_map import AxisMap
from .edits import AppliedPlan, Edge, EffectiveStep, Role, Side
from .state import PlanEditsState, StepRef

logger = logging.getLogger(__name__)

REASON_STRUCTURE = "structure"
REASON_ROLES = "roles"
REASON_BOUNDARY = "boundary"
REASON_LABEL = "label"
REASON_COLOR = "color"
REASON_RESET = "reset"
REASON_RESTORED = "restored"
REASON_DATASET = "dataset"


class ExperimentPlanModule(QObject):
    steps_changed = pyqtSignal(str)  # reason, see the module docstring
    notice = pyqtSignal(str)  # a plain sentence for the user: a role was cleared, a step could not be added

    def __init__(self, dataset: DatasetModule, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._dataset = dataset
        self._state = PlanEditsState()
        self._cache: tuple[ImagingPlan | None, PlanEditsState, AppliedPlan] | None = None
        dataset.dataset_loaded.connect(lambda _dataset: self.steps_changed.emit(REASON_DATASET))  # also fires when the metadata is replaced
        dataset.dataset_cleared.connect(self._on_dataset_cleared)

    # -- queries ------------------------------------------------------------------------------------------------------------

    def measured_plan(self) -> ImagingPlan | None:
        """The dataset's own steps, exactly as measured (None: no dataset, no metadata or no steps recorded)."""
        metadata = self._dataset.acquisition_metadata()
        return None if metadata is None else metadata.plan

    def applied(self) -> AppliedPlan:
        plan = self.measured_plan()
        if self._cache is not None and self._cache[0] is plan and self._cache[1] == self._state:
            return self._cache[2]
        applied = edits.apply_edits(plan, self._state)
        self._cache = (plan, self._state, applied)
        return applied

    def steps(self) -> tuple[EffectiveStep, ...]:
        """The steps to draw, in time order: measured, with the user's edits applied."""
        return self.applied().steps

    def warnings(self) -> tuple[str, ...]:
        """What was wrong with the saved edits (ones that match no step of this dataset, times out of order)."""
        return self.applied().warnings

    def has_steps(self) -> bool:
        return bool(self.steps())

    def state(self) -> PlanEditsState:
        return self._state

    def step_by_ref(self, ref: StepRef | None) -> EffectiveStep | None:
        """The step a reference names (None: no reference, or that step does not exist any more)."""
        if ref is None:
            return None
        return next((step for step in self.steps() if step.ref == ref), None)

    def baseline_step(self, pair: int = 1) -> EffectiveStep | None:
        found = self._state.pair(pair)
        return None if found is None else self.step_by_ref(found.baseline)

    def signal_step(self, pair: int = 1) -> EffectiveStep | None:
        found = self._state.pair(pair)
        return None if found is None else self.step_by_ref(found.signal)

    def has_removed(self) -> bool:
        return bool(self._state.removed)

    def cubes_of(self, step: EffectiveStep) -> tuple[int, ...]:
        """The whole cubes of `step` in cube order (a cube that straddles the step's edge belongs to neither step; `analysis.step_stats.assign_cubes`). Empty without a
        dataset or timing."""
        from ..analysis.step_stats import assign_cubes  # here, not at the top: that module imports this package

        cubes = self._dataset.spectral_cubes()
        if not cubes:
            return ()
        first, last = self._dataset.cube_spans_unix_ms(cubes)
        used = assign_cubes((step,), first, last).used[0]
        return tuple(int(c) for c in np.asarray(cubes)[used])

    def axis_map(self, cubes: list[int], x: list[float]) -> AxisMap | None:
        """The time <-> x map of the cubes the Sensorgram plots (`x`: the plot's x of each, same order). None when it cannot be built (no timing)."""
        return AxisMap.build([self._dataset.cube_start_unix_ms(cube) for cube in cubes], x)

    def snap_grid_unix_ms(self, cubes: list[int]) -> np.ndarray:
        """Where a dragged edge may land: the start time of each plotted cube, and the end of the data."""
        points = [self._dataset.cube_start_unix_ms(cube) for cube in cubes]
        end = self._dataset.data_end_unix_ms()
        if end is not None:
            points.append(end)
        return np.unique(np.array([p for p in points if p is not None], dtype=np.float64))

    # -- session restore ----------------------------------------------------------------------------------------------------

    @instrumented("ExperimentPlanModule.restore_state")
    def restore_state(self, state: PlanEditsState) -> None:
        """Replace the edits as a session load does (not undo-tracked, but emits)."""
        self._state = state
        for warning in self.applied().warnings:
            logger.warning("Plan edits: %s", warning)
        self.steps_changed.emit(REASON_RESTORED)

    def _on_dataset_cleared(self) -> None:
        self._state = PlanEditsState()
        self.steps_changed.emit(REASON_DATASET)

    # -- commands (undoable) ------------------------------------------------------------------------------------------------

    def _set_state(self, state: PlanEditsState, reason: str) -> None:
        self._state = state
        self.steps_changed.emit(reason)

    def _commit(self, label: str, reason: str, new: PlanEditsState) -> bool:
        old = self._state
        if old == new:
            return False
        self._set_state(new, reason)
        undo_manager.push(FunctionCommand(label, undo_fn=lambda: self._set_state(old, reason), redo_fn=lambda: self._set_state(new, reason)))
        return True

    def _step(self, index: int) -> EffectiveStep | None:
        steps = self.steps()
        return steps[index] if 0 <= index < len(steps) else None

    @instrumented("ExperimentPlanModule.move_edge")
    def move_edge(self, index: int, edge: Edge, target_unix_ms: float, grid_unix_ms: np.ndarray, *, together: bool = False) -> bool:
        """Move the start or end of step `index` towards a time, snapped to `grid_unix_ms` (`snap_grid_unix_ms`). Only that step changes and it stops at its
        neighbour's edge; with `together`, a boundary shared with the neighbouring step moves both. Call it inside ``undo_manager.begin_batch`` /
        ``end_batch`` for a drag. True when something moved."""
        if self._step(index) is None:
            return False
        grid = np.asarray(grid_unix_ms, dtype=np.float64)
        spacing = np.diff(grid) if grid.size > 1 else np.empty(0)
        min_length = max(edits.MIN_LENGTH_MS, int(np.median(spacing))) if spacing.size else edits.MIN_LENGTH_MS  # a step keeps at least one cube
        new = edits.move_edge(self.steps(), self._state, index, edge, target_unix_ms, grid, together=together, min_length_ms=min_length)
        return self._commit("Move step boundary", REASON_BOUNDARY, new)

    @instrumented("ExperimentPlanModule.set_label")
    def set_label(self, index: int, text: str) -> bool:
        """The user's note for step `index` (empty: back to the measured note)."""
        step = self._step(index)
        return step is not None and self._commit("Edit step text", REASON_LABEL, edits.set_label(self._state, step, text))

    @instrumented("ExperimentPlanModule.set_color")
    def set_color(self, index: int, color: str | None) -> bool:
        """The user's colour for step `index` (``#rrggbb``), or None for the default colour."""
        step = self._step(index)
        if step is None:
            return False
        return self._commit("Colour step" if color else "Default step colour", REASON_COLOR, edits.set_color(self._state, step, color))

    @instrumented("ExperimentPlanModule.set_colors")
    def set_colors(self, indices: list[int] | tuple[int, ...], color: str | None) -> bool:
        """The same colour for several steps (None: the default colours), as one undo step."""
        steps = self.steps()
        state = self._state
        for index in indices:
            if 0 <= index < len(steps):
                state = edits.set_color(state, steps[index], color)
        return self._commit("Colour steps" if color else "Default step colours", REASON_COLOR, state)

    @instrumented("ExperimentPlanModule.reset_steps")
    def reset_steps(self, indices: list[int] | tuple[int, ...]) -> bool:
        """Several steps back to the measured boundaries, note and colour, as one undo step."""
        steps = self.steps()
        state = self._state
        for index in indices:
            if 0 <= index < len(steps):
                state = edits.reset_step(state, steps[index])
        return self._commit("Reset steps", REASON_RESET, state)

    @instrumented("ExperimentPlanModule.reset_step")
    def reset_step(self, index: int) -> bool:
        step = self._step(index)
        return step is not None and self._commit("Reset step", REASON_RESET, edits.reset_step(self._state, step))

    @instrumented("ExperimentPlanModule.reset_all")
    def reset_all(self) -> bool:
        """Every measured step as measured: no edits, none removed, none added."""
        new = edits.reset_all(self._state)
        changes_structure = bool(self._state.removed or self._state.added)  # then the steps are numbered differently afterwards: a view clears its selection
        return self._commit("Reset all steps", REASON_STRUCTURE if changes_structure else REASON_RESET, new)

    # -- structure: remove, restore, unify, add -------------------------------------------------------------------------------

    def _commit_structure(self, label: str, new: PlanEditsState) -> bool:
        """Commit a change of which steps exist, and say which roles it cleared."""
        lost = edits.roles_lost(self._state, new)
        changed = self._commit(label, REASON_STRUCTURE, new)
        if changed and lost:
            self.notice.emit(f"Cleared the {' and the '.join(lost)}: that step is gone.")
        return changed

    @instrumented("ExperimentPlanModule.remove_steps")
    def remove_steps(self, indices: list[int] | tuple[int, ...]) -> bool:
        """Take these steps away (by index into `steps()`): a measured step is hidden and can be restored, a step the user added is deleted."""
        new = edits.remove_steps(self.steps(), self._state, indices)
        return self._commit_structure("Remove steps" if len(indices) > 1 else "Remove step", new)

    @instrumented("ExperimentPlanModule.restore_removed")
    def restore_removed(self) -> bool:
        return self._commit("Restore removed steps", REASON_STRUCTURE, edits.restore_removed(self._state))

    def can_unify(self, indices: list[int] | tuple[int, ...]) -> bool:
        return edits.can_unify(self.steps(), indices)

    @instrumented("ExperimentPlanModule.unify_steps")
    def unify_steps(self, indices: list[int] | tuple[int, ...]) -> bool:
        """One step from the first to the last of these, which must lie next to each other in time (False otherwise)."""
        return self._commit_structure("Unify steps", edits.unify(self.steps(), self._state, indices))

    @instrumented("ExperimentPlanModule.add_step")
    def add_step(self, index: int, side: Side, grid_unix_ms: np.ndarray) -> StepRef | None:
        """A new empty step in front of or behind step `index`, snapped to `grid_unix_ms` (`snap_grid_unix_ms`). Returns its reference, or None when there is no room."""
        steps = self.steps()
        grid = np.asarray(grid_unix_ms, dtype=np.float64)
        spacing = np.diff(grid) if grid.size > 1 else np.empty(0)
        min_length = max(edits.MIN_LENGTH_MS, int(np.median(spacing))) if spacing.size else edits.MIN_LENGTH_MS  # a step keeps at least one cube
        new, ref = edits.add_step(steps, self._state, index, side, grid, min_length_ms=min_length)
        if ref is None:
            self.notice.emit("There is no room for a new step there: the step is too short to share.")
            return None
        self._commit("Add step", REASON_STRUCTURE, new)
        return ref

    # -- evaluation roles ---------------------------------------------------------------------------------------------------

    @instrumented("ExperimentPlanModule.set_role")
    def set_role(self, role: Role, index: int | None, pair: int = 1) -> bool:
        """Make step `index` the baseline or the signal of evaluation pair `pair`; None clears that role. Naming the pair's other step moves it (a step is not both)."""
        step = self._step(index) if index is not None else None
        if index is not None and step is None:
            return False
        label = f"{'Baseline' if role == 'baseline' else 'Signal'} step"
        return self._commit(label, REASON_ROLES, edits.set_role(self._state, pair, role, None if step is None else step.ref))
