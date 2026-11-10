"""The numbers behind the ROI / Groups table's step columns (2026-11-05). Pure, no Qt, no files.

Screen purpose: for every ROI and every group, the mean and the SD in each experiment step, so the user can see which group does best at a step and sort by it. This
file computes those numbers; ``step_table_module.py`` decides when, and ``panels/roi_table`` shows them.

**The numbers are the Sensorgram's numbers, by construction.** They are not recomputed by a second formula: every row goes through `scan_trace.build_series`, the code
that makes the lines the Sensorgram draws, and takes the ``step_values`` the line carries (mean and SD over the cubes of the step, of the line *as drawn*: after spike
removal, smoothing - inside each step when that switch is on - and baseline subtraction, `step_stats.step_statistics`). A ROI row is the Sensorgram's "Individual" line of that
ROI. A group row is its "Average by group" line: the member ROIs are each processed alone, combined at every cube (mean or median, the Sensorgram's Centre setting) and the
mean and SD are those of that combined line over the step. So the SD is the *temporal* SD of the group's line, not the spread between its ROIs
(`step_stats.group_statistics` has that one; the table does not use it now).

Two differences to what the Sensorgram shows, both on purpose: a group row averages **all** its member ROIs (the Sensorgram averages the *selected* members), and the
"Ungrouped" row is the average of the ROIs in no group, as the Sensorgram's by-group mode draws it.

**Out of date.** The numbers are never stored in the session (like the formula, the fit and the statistics, they are recomputed). A row remembers what it was computed
from (`numbers_key`, the scan, the fit, the steps, the x axis, the members of a group); the module compares that with the present to tell the user a row needs
recalculation. Settings that cannot change a number (the Sensorgram's display mode and band) are left out of the key, so switching them does not dim the table.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

import numpy as np

from .scan_trace import (
    DISPLAY_BY_GROUP,
    DISPLAY_INDIVIDUAL,
    GroupSpec,
    SensorgramRequest,
    Series,
    build_series,
)
from .baseline_drift import window_step_regions
from .settings import MetricSettings, StatisticsSettings
from .step_stats import StepMembership

UNGROUPED = None
"""The key of the "Ungrouped" row (a group's own key is its ``group_id``)."""

_UNGROUPED_SERIES_KEY = "group:ungrouped"


@dataclass(frozen=True)
class RowNumbers:
    """One table row in every step it was computed for: arrays ``[step]`` in the order of ``refs`` (the steps' references, ``plan.state.StepRef``, in time order).
    NaN = no number (too few cubes, nothing in the step); ``n`` counts the cubes used."""

    refs: tuple
    n: np.ndarray
    mean: np.ndarray
    sd: np.ndarray
    noise: np.ndarray | None = None  # SD about the straight line through the step: what a signal-to-noise column divides by

    def at(self, ref: object) -> tuple[int, float, float]:
        """``(n, mean, sd)`` in the step named by `ref`; ``(0, NaN, NaN)`` if this row was computed without that step (the steps changed since)."""
        try:
            i = self.refs.index(ref)
        except ValueError:
            return 0, float("nan"), float("nan")
        return int(self.n[i]), float(self.mean[i]), float(self.sd[i])

    def value_at(self, ref: object) -> StepValue:
        """Everything this row has in one step, for a comparison; all NaN / 0 if the row was computed without that step."""
        n, mean, sd = self.at(ref)
        noise = float("nan")
        if self.noise is not None and ref in self.refs:
            noise = float(self.noise[self.refs.index(ref)])
        return StepValue(n, mean, sd, noise)


def sem_of(n: int, sd: float) -> float:
    """Standard error of the mean, ``sd / sqrt(n)``: how well the *mean* of the step is known (it shrinks as the step gets longer; the SD does not). NaN for fewer than two
    cubes or no SD. It counts every cube as an independent value; smoothing makes neighbouring cubes alike, so it is then a little too small (`step_stats` module docstring)."""
    return sd / math.sqrt(n) if n >= 2 and math.isfinite(sd) else float("nan")


@dataclass(frozen=True)
class StepValue:
    """One row in one step: the cubes used, mean, SD, and the noise (SD about the straight line through the step; NaN with fewer than three cubes)."""

    n: int
    mean: float
    sd: float
    noise: float

    @property
    def sem(self) -> float:
        return sem_of(self.n, self.sd)


OP_DIFFERENCE = "difference"
OP_SNR = "snr"
OPERATIONS = (OP_DIFFERENCE, OP_SNR)


def compare_values(op: str, baseline: StepValue, signal: StepValue) -> tuple[int, float, float, float]:
    """``(n, value, sd, sem)`` of the signal step compared with the baseline step. Pure arithmetic on numbers already calculated; nothing is fitted again.

    - `OP_DIFFERENCE`: ``value`` = signal mean - baseline mean, in the unit of the traces (nm; a shift, whatever the Subtract switch says, because the baseline step's
      mean is taken off). ``sd`` = the two steps' SDs added in quadrature, ``sqrt(SD_S^2 + SD_B^2)``: the scatter of a difference of two single cube values. It assumes the cubes are
      independent (smoothing makes neighbours alike, so it is then a little too small) and it does **not** shrink with more cubes. ``sem`` = the two steps' SEMs added in
      quadrature, ``sqrt(SEM_S^2 + SEM_B^2)``: the uncertainty of the *difference of the means*, which does shrink with more cubes (`sem_of`).
    - `OP_SNR`: ``value`` = (signal mean - baseline mean) / noise of the baseline step. The noise is the SD about a straight line through the baseline step, so a steady drift of the
      baseline is not counted as noise (`step_stats` module docstring). NaN if the baseline has fewer than three cubes or no noise at all. ``sd`` and ``sem`` are NaN: a ratio of
      two estimates has no spread worth quoting here.

    ``n`` is the smaller of the two cube counts. Everything is NaN where an input is."""
    nan = float("nan")
    if not (math.isfinite(baseline.mean) and math.isfinite(signal.mean)):
        return 0, nan, nan, nan
    n = min(baseline.n, signal.n)
    difference = signal.mean - baseline.mean
    if op == OP_SNR:
        usable = math.isfinite(baseline.noise) and baseline.noise > 0.0
        return n, (difference / baseline.noise if usable else nan), nan, nan
    sd = math.sqrt(signal.sd**2 + baseline.sd**2) if math.isfinite(signal.sd) and math.isfinite(baseline.sd) else nan
    sem = math.sqrt(signal.sem**2 + baseline.sem**2) if math.isfinite(signal.sem) and math.isfinite(baseline.sem) else nan
    return n, difference, sd, sem


@dataclass(frozen=True)
class GroupMembers:
    key: str | None  # the group's id; `UNGROUPED` for the ROIs in no group
    roi_ids: tuple[int, ...]


@dataclass(frozen=True)
class StepTableNumbers:
    rois: dict[int, RowNumbers]
    groups: dict[str | None, RowNumbers]


def numbers_key(statistics: StatisticsSettings, *, group: bool) -> StatisticsSettings:
    """`statistics` with the settings that cannot change a number set to fixed values, for comparing what a row was computed from with what is set now. The Sensorgram's display
    mode and band only change how lines are drawn; the centre (mean / median) changes a group's line and nothing else."""
    return replace(statistics, sensorgram_display_mode="individual", sensorgram_band="sd", **({} if group else {"sensorgram_aggregation": "mean"}))


def _request(
    roi_ids: Sequence[int], groups: Sequence[GroupSpec], x: np.ndarray, statistics: StatisticsSettings, segments: np.ndarray | None, membership: StepMembership,
    cube_numbers: np.ndarray | None = None,
) -> SensorgramRequest:
    # The formula and the metric are not read by `build_series` (they decided the traces, which are handed in); the defaults stand in.
    # `cube_numbers` places the drift baseline's anchor points, as the Sensorgram's does. `membership` is of the whole steps (`assign_cubes` at its
    # default window), so it also places the Range method's anchor steps the way the Sensorgram does (`window_step_regions`).
    return SensorgramRequest(
        tuple(roi_ids), "", MetricSettings(), statistics, np.asarray(x, dtype=np.float64), tuple(groups), {}, segments, membership, cube_numbers=cube_numbers,
        baseline_step_regions=window_step_regions(membership, statistics),
    )


def _numbers(line: Series, refs: tuple, n_steps: int) -> RowNumbers:
    values = line.step_values
    if values is None:  # build_series was given a membership, so every line has them; a line without is a bug, not a gap
        raise ValueError("a line without step values: the request carried no step membership")
    if len(values.mean) != n_steps:
        raise ValueError("the step values do not match the steps of the membership")
    noise = np.asarray(values.noise).copy() if values.noise is not None else np.full(n_steps, np.nan)
    return RowNumbers(refs, np.asarray(values.n).copy(), np.asarray(values.mean).copy(), np.asarray(values.sd).copy(), noise)


def compute_step_numbers(
    roi_rows: Sequence[int],
    group_rows: Sequence[GroupMembers],
    traces: Mapping[int, np.ndarray],
    x: np.ndarray,
    statistics: StatisticsSettings,
    segments: np.ndarray | None,
    membership: StepMembership,
    cube_numbers: np.ndarray | None = None,
) -> StepTableNumbers:
    """The mean and SD per step of the ROIs `roi_rows` and of the groups `group_rows`.

    `traces` must hold the metric trace of every ROI in `roi_rows` and of every member of `group_rows` (`scan_trace.compute_traces`); `x`, `segments` and `membership` are the
    Sensorgram's own (`SensorgramAxis.x`, `step_stats.smoothing_segments` when "smooth inside steps" applies, `step_stats.assign_cubes`). A group with no member is left out
    (no row to show); a ROI row for a ROI without a trace is an error of the caller. `cube_numbers` (the scan's cube number per position) places the drift
    baseline's anchor points; None = no points."""
    refs = tuple(step.ref for step in membership.steps)
    n_steps = len(refs)
    rois: dict[int, RowNumbers] = {}
    groups: dict[str | None, RowNumbers] = {}
    if roi_rows:
        lines = build_series(_request(roi_rows, (), x, statistics, segments, membership, cube_numbers), traces, DISPLAY_INDIVIDUAL, statistics.sensorgram_aggregation, statistics.sensorgram_band)
        for roi_id, line in zip(roi_rows, lines):  # `build_series` keeps the order of the request in individual mode
            rois[int(roi_id)] = _numbers(line, refs, n_steps)
    for g in group_rows:
        if not g.roi_ids:
            continue
        # One group at a time, so a ROI listed in two groups counts in both (a request with several groups gives each ROI to the first). A named group is the Sensorgram's line
        # "group:<name>" (the id stands in for the name); the ROIs of the request that no group claims form its "Ungrouped" line, which is what the Ungrouped row is.
        spec = () if g.key is UNGROUPED else (GroupSpec(str(g.key), "", tuple(g.roi_ids)),)
        wanted = _UNGROUPED_SERIES_KEY if g.key is UNGROUPED else f"group:{g.key}"
        lines = build_series(_request(g.roi_ids, spec, x, statistics, segments, membership, cube_numbers), traces, DISPLAY_BY_GROUP, statistics.sensorgram_aggregation, statistics.sensorgram_band)
        line = next((ln for ln in lines if ln.key == wanted), None)
        if line is not None:
            groups[g.key] = _numbers(line, refs, n_steps)
    return StepTableNumbers(rois, groups)
