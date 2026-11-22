"""Layer-by-layer (LbL) series (2026-11-22): how far a ROI's resonance has moved after each deposited bilayer. Pure, no Qt, no files.

Screen purpose (of the numbers): the LbL plot (`panels/sensorgram/lbl_plot.py`) shows, for each line, one point per cycle: the shift against the cycle number (bilayers
deposited). The steps are the plan's LbL steps (`plan/lbl.py`: the start step and every ``period``-th step after it), cycle 0 being the start (the rinse before the first layer).

**Points.** For cycle k the value is the mean of the line, as drawn, in that step (the same number as the ROI / Groups table's step column, `step_table.RowNumbers`), with its
standard error of the mean (SEM, `step_table.sem_of`). Nothing is fitted or recalculated: it is arithmetic on numbers already calculated.

**Two shifts**, chosen on the plot:

- *Accumulated*: ``mean_k - mean_0``, the total shift from the start step. It grows with every bilayer (zero at cycle 0 by definition).
- *Per cycle*: ``mean_k - mean_(k-1)``, the shift added by the k-th bilayer alone. It has no value at cycle 0 (nothing before it). For a regularly growing film it falls
  towards a constant, or decreases as the layers saturate.

A baseline subtracted from the line (the Sensorgram's Subtract) moves every mean by the same amount and so changes neither shift.

**Uncertainty.** The two means are independent measurements, so the uncertainty of a difference is the two SEMs added in quadrature, ``sqrt(SEM_a^2 + SEM_b^2)``. It counts
every cube as independent; smoothing makes neighbouring cubes alike, so it is then a little too small (as `step_table.sem_of`). Accumulated shifts all share the start step's
mean, so their errors are correlated (a common error in the start step moves every accumulated point together); the bars are each point's own uncertainty, not a joint one.
Two means with a missing SEM give NaN, never a guessed uncertainty.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from .step_table import RowNumbers

NAN = float("nan")


@dataclass(frozen=True)
class LblPoint:
    """One cycle of one line: the step's mean and SEM, and the two shifts with their uncertainties (NaN where there is none)."""

    cycle: int
    ref: object
    n: int
    mean: float
    sem: float
    accumulated: float
    accumulated_u: float
    cycle_shift: float
    cycle_shift_u: float


def _difference(a_mean: float, a_sem: float, b_mean: float, b_sem: float) -> tuple[float, float]:
    """``(a - b, sqrt(sem_a^2 + sem_b^2))``; NaN where a mean is missing, the uncertainty NaN where a SEM is."""
    if not (math.isfinite(a_mean) and math.isfinite(b_mean)):
        return NAN, NAN
    if not (math.isfinite(a_sem) and math.isfinite(b_sem)):
        return a_mean - b_mean, NAN
    return a_mean - b_mean, math.hypot(a_sem, b_sem)


def lbl_points(numbers: RowNumbers, refs: Sequence[object]) -> list[LblPoint]:
    """The points of one line: `refs` are the LbL steps in cycle order (cycle 0 first), `numbers` the line's step numbers. A step the line has no number for gives NaNs."""
    values = [numbers.value_at(ref) for ref in refs]
    points: list[LblPoint] = []
    for cycle, (ref, value) in enumerate(zip(refs, values)):
        mean, sem = value.mean, value.sem
        if cycle == 0:
            accumulated, accumulated_u = (0.0, 0.0) if math.isfinite(mean) else (NAN, NAN)  # zero by definition, with no uncertainty of its own
            step, step_u = NAN, NAN
        else:
            first, previous = values[0], values[cycle - 1]
            accumulated, accumulated_u = _difference(mean, sem, first.mean, first.sem)
            step, step_u = _difference(mean, sem, previous.mean, previous.sem)
        points.append(LblPoint(cycle, ref, int(value.n), mean, sem, accumulated, accumulated_u, step, step_u))
    return points
