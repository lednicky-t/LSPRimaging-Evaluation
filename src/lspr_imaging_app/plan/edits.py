"""Applying the user's edits to the measured steps, and the edit operations (pure, no Qt, no files).

``apply_edits(plan, state)`` turns the dataset's measured steps plus the saved edits into the steps the plan bar draws (`EffectiveStep`). The operations
(`move_edge`, `set_label`, `set_color`, `reset_step`, `reset_all`) take the current steps and state and return the new state; ``ExperimentPlanModule`` commits
it (and its undo). Nothing here changes the measured plan.

Rules (design: ``docs/experimental_plan_bar_design_2026-10-26.md``, structure edits ``docs/plan_steps_as_ranges_2026-10-29.md``):

- An edit applies only to the measured step it was made on (number, measured start and measured note must all still match). Otherwise it is reported in
  ``AppliedPlan.warnings`` and left out: an edit is never moved onto another step.
- Structure (2026-10-29): a measured step can be **removed** (hidden; its edit is kept for a restore), the user can **add** steps of their own (stored whole, they have no measured
  counterpart), and **unify** neighbouring steps (the first step's end moves to the last step's end, the others are removed). Every step carries its place in time (``order``, what the
  user sees as its number), a stable reference other panels keep (``ref``), and the labels of the evaluation roles it plays (``roles``: ``"B1"``, ``"S1"``).
- Evaluation pairs: step references for baseline and signal. A role whose step is removed or unified away is cleared by the operation that did it.
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

from .state import AddedStep, EvalPair, PlanEdit, PlanEditsState, StepRef, added_ref, measured_ref, normalise_color

Edge = Literal["start", "end"]
Role = Literal["baseline", "signal"]
Side = Literal["before", "after"]

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
    order: int = 0  # 1, 2, 3 ... by start time: the number the user sees (``step`` is the plan file's own number, 0 for an added step)
    added_uid: int | None = None  # set for a step the user made
    roles: tuple[str, ...] = ()  # "B1", "S1", ... the evaluation roles this step plays

    @property
    def key(self) -> tuple[int, int, str]:
        """The fingerprint of the measured step (an added step has none: it is ``(0, uid, "")``, which no edit matches)."""
        if self.added_uid is not None:
            return (0, self.added_uid, "")
        return (self.step, self.measured_start_unix_ms, self.measured_label)

    @property
    def ref(self) -> StepRef:
        """What other panels keep to name this step: stays the same when steps are added or removed (the number does not)."""
        return added_ref(self.added_uid) if self.added_uid is not None else measured_ref(*self.key)

    @property
    def is_added(self) -> bool:
        return self.added_uid is not None

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
    removed_keys = {ref[1:] for ref in state.removed}
    used: set[tuple[int, int, str]] = set()
    steps: list[EffectiveStep] = []
    for source in measured:
        key = (source.step, source.start_unix_ms, source.label)
        edit = by_key.get(key)
        if key in removed_keys:
            used.add(key)  # taken away by the user: nothing is drawn, and its edit (if any) waits for a restore
            continue
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
    measured_keys = {(s.step, s.start_unix_ms, s.label) for s in measured}
    for ref in state.removed:
        if ref[1:] not in measured_keys:
            warnings.append(f"A removed step {ref[1]} ('{ref[3]}') does not match any step of this dataset any more.")
    for added in state.added:
        if added.end_unix_ms <= added.start_unix_ms:
            warnings.append(f"The step you added (id {added.uid}) ends before it starts and is left out.")
            continue
        steps.append(
            EffectiveStep(
                step=0, label=added.label, start_unix_ms=added.start_unix_ms, end_unix_ms=added.end_unix_ms, color=added.color,
                measured_label="", measured_start_unix_ms=added.start_unix_ms, measured_end_unix_ms=added.end_unix_ms, added_uid=added.uid,
            )
        )
    steps.sort(key=lambda s: (s.start_unix_ms, s.end_unix_ms))  # stable: measured steps first on a tie
    roles: dict[StepRef, list[str]] = {}
    for pair in state.pairs:
        if pair.baseline is not None:
            roles.setdefault(pair.baseline, []).append(f"B{pair.number}")
        if pair.signal is not None:
            roles.setdefault(pair.signal, []).append(f"S{pair.number}")
    ordered = tuple(replace(step, order=index + 1, roles=tuple(roles.get(step.ref, ()))) for index, step in enumerate(steps))
    return AppliedPlan(ordered, tuple(warnings))


# -- operations -----------------------------------------------------------------------------------------------------------------


def _updated(
    state: PlanEditsState, step: EffectiveStep, *, start: Any = _KEEP, end: Any = _KEEP, label: Any = _KEEP, color: Any = _KEEP
) -> PlanEditsState:
    """`state` with the given fields of `step`'s edit replaced (None clears one). A value equal to the measured one is stored as no edit. A step the user added has no
    measured value to compare with: its record is changed in place (a cleared note is an empty one)."""
    if step.is_added:
        return _updated_added(state, step, start=start, end=end, label=label, color=color)
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
    return replace(state, edits=tuple(sorted(others, key=lambda e: (e.step, e.measured_start_unix_ms))))


def _updated_added(state: PlanEditsState, step: EffectiveStep, *, start: Any, end: Any, label: Any, color: Any) -> PlanEditsState:
    current = next((a for a in state.added if a.uid == step.added_uid), None)
    if current is None:
        return state
    changed = replace(
        current,
        start_unix_ms=current.start_unix_ms if start is _KEEP else int(start),
        end_unix_ms=current.end_unix_ms if end is _KEEP else int(end),
        label=current.label if label is _KEEP else (label or ""),
        color=current.color if color is _KEEP else color,
    )
    if changed == current:
        return state
    return replace(state, added=tuple(changed if a.uid == current.uid else a for a in state.added))


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
    """Back to the measured boundaries, note and colour of `step`. A step the user added has nothing measured to go back to: it is left as it is."""
    if step.is_added:
        return state
    return replace(state, edits=tuple(edit for edit in state.edits if edit.key != step.key))


def reset_all(state: PlanEditsState | None = None) -> PlanEditsState:
    """Every measured step as measured: no edits, none removed, none added. The evaluation pairs keep the roles of measured steps (those steps are all back);
    roles on steps the user added go with them. The uid counter stays, so an added step's id is never given twice."""
    if state is None:
        return PlanEditsState()
    pairs = _cleared(state.pairs, {added_ref(a.uid) for a in state.added})
    return PlanEditsState(next_uid=state.next_uid, pairs=pairs)


# -- structure: remove, restore, unify, add ----------------------------------------------------------------------------------------------


def _cleared(pairs: tuple[EvalPair, ...], gone: set[StepRef]) -> tuple[EvalPair, ...]:
    """The pairs without the roles of the steps in `gone`; a pair left with nothing set is dropped."""
    kept = []
    for pair in pairs:
        pair = replace(pair, baseline=None if pair.baseline in gone else pair.baseline, signal=None if pair.signal in gone else pair.signal)
        if pair.baseline is not None or pair.signal is not None:
            kept.append(pair)
    return tuple(kept)


def roles_lost(before: PlanEditsState, after: PlanEditsState) -> list[str]:
    """Plain-language names of the roles that `before` had and `after` does not, for a message ("baseline of pair 1")."""
    lost = []
    for pair in before.pairs:
        now = after.pair(pair.number)
        for name, ref in (("baseline", pair.baseline), ("signal", pair.signal)):
            if ref is not None and (now is None or getattr(now, name) != ref):
                lost.append(f"{name} of pair {pair.number}")
    return lost


def _valid(steps: Sequence[EffectiveStep], indices: Sequence[int]) -> list[int]:
    return sorted({int(i) for i in indices if 0 <= int(i) < len(steps)})


def remove_steps(steps: Sequence[EffectiveStep], state: PlanEditsState, indices: Sequence[int]) -> PlanEditsState:
    """Take the steps at `indices` away. A measured step is hidden (its edit is kept for `restore_removed`); a step the user added is deleted. Roles they played are cleared.
    Returns `state` itself when `indices` names no step."""
    chosen = _valid(steps, indices)
    if not chosen:
        return state
    removed = list(state.removed)
    added = list(state.added)
    gone: set[StepRef] = set()
    for index in chosen:
        step = steps[index]
        gone.add(step.ref)
        if step.is_added:
            added = [a for a in added if a.uid != step.added_uid]
        elif step.ref not in removed:
            removed.append(step.ref)
    return replace(
        state, removed=tuple(sorted(removed, key=lambda r: r[1:])), added=tuple(added), pairs=_cleared(state.pairs, gone),
    )


def restore_removed(state: PlanEditsState) -> PlanEditsState:
    """Bring back every measured step the user removed, as it was before (with its edits)."""
    return replace(state, removed=()) if state.removed else state


def unify(steps: Sequence[EffectiveStep], state: PlanEditsState, indices: Sequence[int]) -> PlanEditsState:
    """One step from the first of `indices` to the end of the last: the first step keeps its note, colour and place, its end moves to the last step's end, the others are
    removed (roles they played are cleared). Needs two or more steps next to each other in time (nothing between them); otherwise returns `state` itself. A gap between
    two of them is absorbed."""
    if not can_unify(steps, indices):
        return state
    chosen = _valid(steps, indices)
    first, last = steps[chosen[0]], steps[chosen[-1]]
    merged = _updated(state, first, end=max(first.end_unix_ms, last.end_unix_ms))
    return remove_steps(steps, merged, chosen[1:])


def can_unify(steps: Sequence[EffectiveStep], indices: Sequence[int]) -> bool:
    chosen = _valid(steps, indices)
    return len(chosen) >= 2 and chosen == list(range(chosen[0], chosen[-1] + 1))


def add_step(
    steps: Sequence[EffectiveStep], state: PlanEditsState, index: int, side: Side, grid_unix_ms: np.ndarray, *, min_length_ms: int = MIN_LENGTH_MS,
) -> tuple[PlanEditsState, StepRef | None]:
    """A new, empty step in front of (``"before"``) or behind (``"after"``) `steps[index]`. Returns the new state and the new step's reference, or `state` and None when
    there is no room.

    If there is free time on that side (up to the neighbouring step, or to the end of the data), the new step fills all of it. If the steps are joined, the new step is cut off
    the step itself: its half on that side, on a grid point (`grid_unix_ms`, the cube start times and the end of the data), and the step keeps the other half. Either way the
    result is two steps that do not overlap, which the user then drags into place."""
    if not 0 <= index < len(steps):
        return state, None
    step = steps[index]
    grid = np.asarray(grid_unix_ms, dtype=np.float64)
    grid = np.unique(grid[np.isfinite(grid)])
    if grid.size == 0:
        return state, None
    if side == "after":
        free_from = float(step.end_unix_ms)
        free_to = float(steps[index + 1].start_unix_ms) if index + 1 < len(steps) else float(grid[-1])
    else:
        free_to = float(step.start_unix_ms)
        free_from = float(steps[index - 1].end_unix_ms) if index > 0 else float(grid[0])

    base = state
    if free_to - free_from >= min_length_ms:
        start, end = int(round(free_from)), int(round(free_to))
    else:
        inside = grid[(grid >= step.start_unix_ms + min_length_ms) & (grid <= step.end_unix_ms - min_length_ms)]
        if inside.size == 0:
            return state, None
        middle = int(round(float(inside[int(np.argmin(np.abs(inside - 0.5 * (step.start_unix_ms + step.end_unix_ms))))])))
        if side == "after":
            base, start, end = _updated(state, step, end=middle), middle, step.end_unix_ms
        else:
            base, start, end = _updated(state, step, start=middle), step.start_unix_ms, middle
    uid = state.next_uid
    made = AddedStep(uid=uid, start_unix_ms=int(start), end_unix_ms=int(end))
    return replace(base, added=base.added + (made,), next_uid=uid + 1), added_ref(uid)  # `base`, not `state`: a shortened added step is in `base.added`


# -- evaluation pairs ------------------------------------------------------------------------------------------------------------------


def set_role(state: PlanEditsState, pair_number: int, role: Role, ref: StepRef | None) -> PlanEditsState:
    """Make the step `ref` the baseline or the signal of pair `pair_number` (None clears that role). A step cannot be both roles of one pair: naming it for one clears the
    other. A pair with nothing set disappears. Returns `state` itself when nothing changes."""
    if pair_number < 1:
        raise ValueError("an evaluation pair starts at 1")
    pairs = {p.number: p for p in state.pairs}
    pair = pairs.get(pair_number, EvalPair(number=pair_number))
    other = "signal" if role == "baseline" else "baseline"
    changes: dict[str, StepRef | None] = {role: ref}
    if ref is not None and getattr(pair, other) == ref:
        changes[other] = None
    pair = replace(pair, **changes)
    if pair.baseline is None and pair.signal is None:
        pairs.pop(pair_number, None)
    else:
        pairs[pair_number] = pair
    new = tuple(sorted(pairs.values(), key=lambda p: p.number))
    return state if new == state.pairs else replace(state, pairs=new)


def clear_pair(state: PlanEditsState, pair_number: int) -> PlanEditsState:
    """Both roles of pair `pair_number` cleared, so the pair disappears. Returns `state` itself when that pair has nothing set."""
    new = tuple(p for p in state.pairs if p.number != pair_number)
    return state if new == state.pairs else replace(state, pairs=new)
