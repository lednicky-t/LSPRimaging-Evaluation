"""Which steps a layer-by-layer (LbL) series uses (2026-11-22). Pure: no Qt, no numbers of the analysis.

An LbL experiment repeats one cycle: e.g. water (rinse) - PAH - water - PSS - water ... The user names the **starting step** (cycle 0: the rinse before the first layer) and the
**number of steps in one cycle** (the period). The series then uses the start step and every ``period``-th step after it in time order: with start step 1 and period 6, steps 1, 7,
13 ... are cycles 0, 1, 2 ... Steps are counted as the plan has them now (steps added, removed or unified shift the count; the period is a count of steps, not of time). A step the
period would land past the last step is simply not there: no partial cycle.
"""

from __future__ import annotations

from collections.abc import Sequence

from .state import LblSeries, StepRef


def lbl_indices(refs: Sequence[StepRef], series: LblSeries | None) -> tuple[int, ...]:
    """The indices (into `refs`, the plan's steps in time order) of cycle 0, 1, 2 ...; empty when there is no series or its start step is not in `refs`."""
    if series is None or series.period < 1:
        return ()
    try:
        first = list(refs).index(series.start)
    except ValueError:
        return ()
    return tuple(range(first, len(refs), series.period))
