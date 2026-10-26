"""Applying the user's edits to the measured steps, and the edit operations (pure, no Qt, no files).

``apply_edits(plan, state)`` turns the dataset's measured steps plus the saved edits into the steps the plan bar draws (`EffectiveStep`). The operations
(`move_edge`, `set_label`, `set_color`, `reset_step`, `reset_all`) take the current steps and state and return the new state; ``ExperimentPlanModule`` commits
it (and its undo). Nothing here changes the measured plan.

Rules (design: ``docs/experimental_plan_bar_design_2026-10-26.md``):

- An edit applies only to the measured step it was made on (number, measured start and measured note must all still match). Otherwise it is reported in
  ``AppliedPlan.warnings`` and left out: an edit is never moved onto another step.
- A boundary is a step's start (included) or end (excluded). Dragging an edge changes **only that step**: it stops at its neighbour's nearest edge and never pushes it.
  Where one step's end is another's start (as measured: notes follow each other) the two are a *joined* boundary; with ``together=True`` (the Shift key in the UI)
  they move as one, otherwise the dragged step leaves a gap and the neighbour stays.
- A dragged edge lands on a grid point (the cube start times the plot shows, plus the end of the data): the edge is snapped to the nearest grid point that
  keeps it inside its neighbours. A cube that starts exactly on the boundary belongs to the step that begins there, so an edited boundary never splits a cube.
- Only a difference from the measured value is stored; an edit that ends up equal to the measured step disappears.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal

import numpy as np

from lspr_core import ImagingPlan

from .state import PlanEdit, PlanEditsState, normalise_color

Edge = Literal["start", "end"]

MIN_LENGTH_MS = 1  # a step keeps at least this much; the module passes one cube period so a step never shrinks below one cube

_KEEP: Any = object()  # "leave this field of the edit as it is" (None means "clear it")


@dataclass(frozen=True)
class EffectiveStep:
    """A step as drawn: the measured step with the user's edits on top."""

    step: int
    label: str  # the whole note, edited or measured (the label rule shortens it for display)
    start_unix_ms: int  # included
    end_unix_ms: int  # excluded
    color: str | None  # the user's colour, else the plan file's, else None (the display picks an automatic one)
    measured_label: str
    measured_start_unix_ms: int
    measured_end_unix_ms: int
    planned_duration_s: float | None = None
    devices: dict[str, Any] = field(default_factory=dict, compare=False)
    edited: bool = False

    @property
    def key(self) -> tuple[int, int, str]:
        return (self.step, self.measured_start_unix_ms, self.measured_label)

    @property
    def duration_s(self) -> float:
        return max(0.0, (self.end_unix_ms - self.start_unix_ms) / 1000.0)


@dataclass(frozen=True)
class AppliedPlan:
    steps: tuple[EffectiveStep, ...]
    warnings: tuple[str, ...] = ()


def apply_edits(plan: ImagingPlan | None, state: PlanEditsState) -> AppliedPlan:
    """The measured steps of `plan` (None: the dataset has none) with `state`'s edits applied, in time order."""
    warnings: list[str] = []
    measured = sorted(plan.steps, key=lambda s: s.start_unix_ms) if plan is not None else []
    by_key = {edit.key: edit for edit in state.edits}
    used: set[tuple[int, int, str]] = set()
    steps: list[EffectiveStep] = []
    for source in measured:
        key = (source.step, source.start_unix_ms, source.label)
        edit = by_key.get(key)
        start, end, label, color = source.start_unix_ms, source.end_unix_ms, source.label, normalise_color(source.color)
        if edit is not None:
            used.add(key)
            new_start = start if edit.start_unix_ms is None else edit.start_unix_ms
            new_end = end if edit.end_unix_ms is None else edit.end_unix_ms
            if new_end > new_start:
                start, end = new_start, new_end
            else:
                warnings.append(f"Step {source.step}: the edited start and end are not in order, so the measured times are used.")
            if edit.label is not None:
                label = edit.label
            if edit.color is not None:
                color = edit.color
        steps.append(
            EffectiveStep(
                step=source.step, label=label, start_unix_ms=start, end_unix_ms=end, color=color,
                measured_label=source.label, measured_start_unix_ms=source.start_unix_ms, measured_end_unix_ms=source.end_unix_ms,
                planned_duration_s=source.planned_duration_s, devices=dict(source.devices),
                edited=edit is not None and not edit.is_empty(),
            )
        )
    for edit in state.edits:
        if edit.key not in used:
            warnings.append(
                f"An edit of step {edit.step} ('{edit.measured_label}') does not match any step of this dataset any more and is not applied."
            )
    return AppliedPlan(tuple(steps), tuple(warnings))


# -- operations -----------------------------------------------------------------------------------------------------------------


def _updated(
    state: PlanEditsState, step: EffectiveStep, *, start: Any = _KEEP, end: Any = _KEEP, label: Any = _KEEP, color: Any = _KEEP
) -> PlanEditsState:
    """`state` with the given fields of `step`'s edit replaced (None clears one). A value equal to the measured one is stored as no edit."""
    current = state.edit_for(step.key) or PlanEdit(step=step.step, measured_start_unix_ms=step.measured_start_unix_ms, measured_label=step.measured_label)
    new_start = current.start_unix_ms if start is _KEEP else start
    new_end = current.end_unix_ms if end is _KEEP else end
    new_label = current.label if label is _KEEP else label
    new_color = current.color if color is _KEEP else color
    if new_start == step.measured_start_unix_ms:
        new_start = None
    if new_end == step.measured_end_unix_ms:
        new_end = None
    if new_label == step.measured_label:
        new_label = None
    updated = replace(current, start_unix_ms=new_start, end_unix_ms=new_end, label=new_label, color=new_color)
    others = [edit for edit in state.edits if edit.key != step.key]
    if not updated.is_empty():
        others.append(updated)
    return PlanEditsState(edits=tuple(sorted(others, key=lambda e: (e.step, e.measured_start_unix_ms))))


def move_edge(
    steps: Sequence[EffectiveStep],
    state: PlanEditsState,
    index: int,
    edge: Edge,
    target_unix_ms: float,
    grid_unix_ms: np.ndarray,
    *,
    together: bool = False,
    min_length_ms: int = MIN_LENGTH_MS,
) -> PlanEditsState:
    """Move the `edge` of `steps[index]` towards `target_unix_ms`, snapped to a grid point. Returns `state` itself when nothing can move.

    `grid_unix_ms`: the allowed positions (cube start times of the plot, and the end of the data). The edge lands on the grid point nearest to the target
    among those that keep every step at least `min_length_ms` long and keep the order of the steps. Only the dragged step changes, unless `together` is set and the edge is
    shared with the neighbouring step (its end is the neighbour's start): then the neighbour's edge moves with it."""
    step = steps[index]
    partner: int | None = None  # the step on the other side of a joined boundary, moved along when `together`
    if together and edge == "end" and index + 1 < len(steps) and steps[index + 1].start_unix_ms == step.end_unix_ms:
        partner = index + 1
    elif together and edge == "start" and index > 0 and steps[index - 1].end_unix_ms == step.start_unix_ms:
        partner = index - 1

    low, high = -np.inf, np.inf
    if edge == "end":
        low = step.start_unix_ms + min_length_ms
        if partner is not None:
            high = steps[partner].end_unix_ms - min_length_ms
        elif index + 1 < len(steps):
            high = steps[index + 1].start_unix_ms
    else:
        high = step.end_unix_ms - min_length_ms
        if partner is not None:
            low = steps[partner].start_unix_ms + min_length_ms
        elif index > 0:
            low = steps[index - 1].end_unix_ms

    grid = np.asarray(grid_unix_ms, dtype=np.float64)
    grid = grid[np.isfinite(grid) & (grid >= low) & (grid <= high)]
    if grid.size == 0:
        return state
    new = int(round(float(grid[int(np.argmin(np.abs(grid - float(target_unix_ms))))])))
    if new == (step.end_unix_ms if edge == "end" else step.start_unix_ms):
        return state

    if edge == "end":
        result = _updated(state, step, end=new)
        if partner is not None:
            result = _updated(result, steps[partner], start=new)
    else:
        result = _updated(state, step, start=new)
        if partner is not None:
            result = _updated(result, steps[partner], end=new)
    return result


def set_label(state: PlanEditsState, step: EffectiveStep, text: str) -> PlanEditsState:
    """The user's note for `step`. Empty text, or text equal to the measured note, goes back to the measured note."""
    cleaned = text.strip()
    return _updated(state, step, label=cleaned or None)


def set_color(state: PlanEditsState, step: EffectiveStep, color: str | None) -> PlanEditsState:
    """The user's colour for `step` (``#rgb`` or ``#rrggbb``), or None for the default colour. Raises ``ValueError`` for anything else."""
    if color is None:
        return _updated(state, step, color=None)
    normalised = normalise_color(color)
    if normalised is None:
        raise ValueError(f"{color!r} is not a colour like '#3399ff'")
    return _updated(state, step, color=normalised)


def reset_step(state: PlanEditsState, step: EffectiveStep) -> PlanEditsState:
    """Back to the measured boundaries, note and colour of `step`."""
    return PlanEditsState(edits=tuple(edit for edit in state.edits if edit.key != step.key))


def reset_all() -> PlanEditsState:
    return PlanEditsState()
