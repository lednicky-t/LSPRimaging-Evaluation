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
from .edits import AppliedPlan, Edge, EffectiveStep
from .state import PlanEditsState

logger = logging.getLogger(__name__)

REASON_BOUNDARY = "boundary"
REASON_LABEL = "label"
REASON_COLOR = "color"
REASON_RESET = "reset"
REASON_RESTORED = "restored"
REASON_DATASET = "dataset"


class ExperimentPlanModule(QObject):
    steps_changed = pyqtSignal(str)  # reason, see the module docstring

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
        return self._commit("Reset all steps", REASON_RESET, edits.reset_all())
