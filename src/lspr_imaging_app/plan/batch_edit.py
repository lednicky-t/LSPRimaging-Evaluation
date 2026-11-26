"""Batch edits of the plan steps' edges: shrink many steps, or move many steps in time (pure, no Qt, no files; 2026-11-26).

The Sensorgram's ribbon group "Batch edit" asks for two things, and this module works out what each would do:

- **Shrink**: every chosen step loses `amount` from its start, its end, or both (**both splits the amount**: half from each side). The amount is in cubes, in seconds or in per cent of
  the step's own length. A step that is too short to lose that much (it would keep less than one cube) is **left as it is**, never cut down to whatever fits.
- **Offset**: every chosen step moves `amount` later (+) or earlier (-), in cubes or in seconds, keeping its length. Steps **may overlap** afterwards (maintainer's decision); a step
  that would leave the data (before the first cube, after the end of the data) is left as it is.

`plan_batch` only *proposes* new edges (`BatchPlan`): the same answer drives the live preview on the plan bar and Apply, so what is previewed is what is applied. `apply_batch`
writes the proposal into the plan edits (the module commits it as one undo step). Nothing here changes the measured plan, and the steps' notes, colours and roles are untouched.

How an amount becomes an edge (the grid is the plotted cubes' start times plus the end of the data, as for a dragged edge: an edge always lands on a grid point):

- **Cubes** count grid points. A step's first whole cube starts at the first grid point at or after its start; its edge after the last whole cube is the last grid point at or before its
  end. Shrinking by n moves the start n grid points later and/or the end n grid points earlier, so exactly n cubes are dropped per side. Both with an odd n: the start gives up the
  extra cube.
- **Seconds / per cent** aim at a time and snap to the nearest grid point that keeps the edge inside the step. An amount smaller than half a cube spacing therefore moves nothing
  (reported as "would not move").
- An offset in cubes moves each edge from its nearest grid point by n points; in seconds, each edge is aimed at its time plus the offset and snapped to the nearest grid point.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np

from .edits import EffectiveStep, set_bounds
from .state import PlanEditsState

KIND_SHRINK = "shrink"
KIND_OFFSET = "offset"
UNIT_CUBES = "cubes"
UNIT_TIME = "time"
UNIT_PERCENT = "percent"
SIDE_START = "start"
SIDE_END = "end"
SIDE_BOTH = "both"

SHRINK_UNITS = (UNIT_CUBES, UNIT_TIME, UNIT_PERCENT)
OFFSET_UNITS = (UNIT_CUBES, UNIT_TIME)
SIDES = (SIDE_START, SIDE_END, SIDE_BOTH)


@dataclass(frozen=True)
class BatchSpec:
    """What the user asked for. `amount`: cubes (a whole number), seconds, or per cent of each step's length (shrink, > 0); for an offset, signed (+ later, - earlier). `side` only
    matters for a shrink."""

    kind: str
    unit: str
    amount: float
    side: str = SIDE_BOTH


@dataclass(frozen=True)
class EdgeChange:
    """New edges (unix ms) for the step at `index` of the plan's step list."""

    index: int
    start_unix_ms: int
    end_unix_ms: int


@dataclass(frozen=True)
class BatchPlan:
    """What a `BatchSpec` would do to the chosen steps. `asked`: steps looked at. `refused`: steps that cannot take it (a shrink would leave under one cube; an offset would leave the
    data), left as they are. `unmoved`: steps for which the amount rounds to nothing (under half a cube), left as they are."""

    changes: tuple[EdgeChange, ...]
    asked: int
    refused: int = 0
    unmoved: int = 0


def _as_ms(value: float) -> int:
    return int(round(float(value)))


def _nearest(grid: np.ndarray, target: float, low: float, high: float) -> float | None:
    """The grid point nearest to `target` among those in [low, high]; None when there is none."""
    first = int(np.searchsorted(grid, low, side="left"))
    last = int(np.searchsorted(grid, high, side="right"))
    if first >= last:
        return None
    part = grid[first:last]
    return float(part[int(np.argmin(np.abs(part - target)))])


def _nearest_index(grid: np.ndarray, value: float) -> int:
    return int(np.argmin(np.abs(grid - value)))


def _shrink_one(start: int, end: int, spec: BatchSpec, grid: np.ndarray, min_length_ms: int) -> tuple[int, int] | None | str:
    """(new start, new end), None when the step cannot take it, ``"unmoved"`` when the amount rounds to nothing."""
    if spec.unit == UNIT_CUBES:
        n = int(round(spec.amount))
        if n <= 0:
            return "unmoved"
        n_start, n_end = {SIDE_START: (n, 0), SIDE_END: (0, n), SIDE_BOTH: ((n + 1) // 2, n // 2)}[spec.side]
        first = int(np.searchsorted(grid, start, side="left"))  # the first whole cube starts here
        last = int(np.searchsorted(grid, end, side="right")) - 1  # the edge after the last whole cube
        if first + n_start >= grid.size or last - n_end < 0:
            return None
        new_start = start if n_start == 0 else _as_ms(grid[first + n_start])
        new_end = end if n_end == 0 else _as_ms(grid[last - n_end])
    else:
        total = spec.amount * 1000.0 if spec.unit == UNIT_TIME else (end - start) * spec.amount / 100.0
        if total <= 0:
            return "unmoved"
        ms_start, ms_end = {SIDE_START: (total, 0.0), SIDE_END: (0.0, total), SIDE_BOTH: (total / 2.0, total / 2.0)}[spec.side]
        new_start, new_end = start, end
        if ms_start > 0:
            found = _nearest(grid, start + ms_start, start, end - min_length_ms)
            if found is None:
                return None
            new_start = _as_ms(found)
        if ms_end > 0:
            found = _nearest(grid, end - ms_end, start + min_length_ms, end)
            if found is None:
                return None
            new_end = _as_ms(found)
    if new_end - new_start < min_length_ms:
        return None
    if new_start == start and new_end == end:
        return "unmoved"
    return new_start, new_end


def _offset_one(start: int, end: int, spec: BatchSpec, grid: np.ndarray, min_length_ms: int) -> tuple[int, int] | None | str:
    if spec.unit == UNIT_CUBES:
        n = int(round(spec.amount))
        if n == 0:
            return "unmoved"
        i_start, i_end = _nearest_index(grid, start) + n, _nearest_index(grid, end) + n
        if not (0 <= i_start < grid.size and 0 <= i_end < grid.size):
            return None  # it would leave the data
        new_start, new_end = _as_ms(grid[i_start]), _as_ms(grid[i_end])
    else:
        shift = spec.amount * 1000.0
        if shift == 0:
            return "unmoved"
        low, high = min(float(grid[0]), float(start)), max(float(grid[-1]), float(end))  # an edge already outside the data may stay there, none goes further out
        if start + shift < low or end + shift > high:
            return None
        new_start, new_end = _as_ms(grid[_nearest_index(grid, start + shift)]), _as_ms(grid[_nearest_index(grid, end + shift)])
    if new_end - new_start < min_length_ms:
        return None
    if new_start == start and new_end == end:
        return "unmoved"
    return new_start, new_end


def plan_batch(steps: Sequence[EffectiveStep], indices: Iterable[int], spec: BatchSpec, grid_unix_ms: np.ndarray, min_length_ms: int = 1) -> BatchPlan:
    """What `spec` would do to the steps at `indices` (positions in `steps`; ones that do not exist are ignored). `grid_unix_ms`: where an edge may land (cube start times of the plot
    and the end of the data). `min_length_ms`: a step keeps at least this much (one cube)."""
    chosen = sorted({int(i) for i in indices if 0 <= int(i) < len(steps)})
    grid = np.unique(np.asarray(grid_unix_ms, dtype=np.float64))
    grid = grid[np.isfinite(grid)]
    if grid.size == 0:
        return BatchPlan((), len(chosen), refused=len(chosen))
    work = _shrink_one if spec.kind == KIND_SHRINK else _offset_one
    changes: list[EdgeChange] = []
    refused = unmoved = 0
    for index in chosen:
        step = steps[index]
        found = work(int(step.start_unix_ms), int(step.end_unix_ms), spec, grid, max(1, int(min_length_ms)))
        if found is None:
            refused += 1
        elif found == "unmoved":
            unmoved += 1
        else:
            changes.append(EdgeChange(index, found[0], found[1]))
    return BatchPlan(tuple(changes), len(chosen), refused=refused, unmoved=unmoved)


def apply_batch(steps: Sequence[EffectiveStep], state: PlanEditsState, plan: BatchPlan) -> PlanEditsState:
    """`state` with the planned edges written in. `steps` must be the list `plan` was made from."""
    for change in plan.changes:
        state = set_bounds(state, steps[change.index], change.start_unix_ms, change.end_unix_ms)
    return state


def _steps_word(count: int) -> str:
    return f"{count} step" if count == 1 else f"{count} steps"


def status_text(plan: BatchPlan, spec: BatchSpec, *, done: bool = False) -> str:
    """One sentence for the pop-up's status line (before Apply: what would happen) or the notice after it (`done`)."""
    if plan.asked == 0:
        return "There are no steps to edit."
    shrink = spec.kind == KIND_SHRINK
    count = len(plan.changes)
    if done:
        head = f"{_steps_word(count)} {'shrunk' if shrink else 'moved'}" if count else f"No step {'shrunk' if shrink else 'moved'}"
    else:
        action = "shrink" if shrink else "move"
        head = f"{_steps_word(count)} will {action}" if count else f"No step would {action}"
    parts = [head]
    if plan.refused:
        reason = "too short to lose that much" if shrink else "would leave the data"
        parts.append(f"{_steps_word(plan.refused)} {reason}, left unchanged")
    if plan.unmoved:
        parts.append(f"{_steps_word(plan.unmoved)} would not move (under half a cube), left unchanged")
    return "; ".join(parts) + "."
