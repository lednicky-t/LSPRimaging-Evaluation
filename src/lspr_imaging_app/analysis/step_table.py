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
from .bulk_sensitivity import WEIGHT_INVERSE_VARIANCE, BulkFit, Excess, excess, fit_bulk
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
    line: np.ndarray | None = None  # the line itself as drawn, one value per scan position (`StoredScan.cube_indices` order): the signal map's "cube signal" reads it

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
OPERATIONS = (OP_DIFFERENCE, OP_SNR)  # the operations of an evaluation pair's column (its header menu)
OP_BULK = "bulk"  # the S_bulk column (2026-11-12): not a pair's operation; one column at most, added by its own button


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
class BulkPoint:
    """One step of the S_bulk set as a point: the step's reference and its solution's refractive index (and the RI's uncertainty, NaN when the file gives none)."""

    ref: object
    ri: float
    ri_u: float = float("nan")


@dataclass(frozen=True)
class BulkValues:
    """The S_bulk cell of one row: the slope from the step SEMs (the value shown as the mean), its uncertainty with the step SEMs (``sem``) and with the step SDs (``sd``),
    and the fits themselves for the tool tip."""

    n: int  # steps used
    slope: float
    sd: float
    sem: float
    fit_sem: BulkFit
    fit_sd: BulkFit
    keys: tuple = ()  # the step references handed to the fit, in its input order (``fit_sem.used`` marks the ones it used)

    def fit_keys(self) -> list:
        return [key for key, used in zip(self.keys, self.fit_sem.used) if used]


def bulk_values(numbers: RowNumbers, points: Sequence[BulkPoint], weighting: str = WEIGHT_INVERSE_VARIANCE) -> BulkValues:
    """The bulk sensitivity of one row from its step numbers (`analysis/bulk_sensitivity.py`). Pure arithmetic on numbers already calculated; a step this row has no number
    for is left out.

    Two fits of the same points: with each step's SEM as its uncertainty (the slope shown, and ``sem``: how well the slope is known, which shrinks with longer steps) and with its
    SD (``sd``: the uncertainty single-cube noise would give, as the SD of the other columns does not shrink with more cubes). With weighting the two can place the line
    a little differently (1/SEM^2 = n/SD^2 also counts the cubes); the value shown is always the SEM one."""
    ri, ri_u, mean, sd, sem, keys = [], [], [], [], [], []
    for point in points:
        if point.ref not in numbers.refs:
            continue
        keys.append(point.ref)
        value = numbers.value_at(point.ref)
        ri.append(point.ri)
        ri_u.append(point.ri_u)
        mean.append(value.mean)
        sd.append(value.sd)
        sem.append(value.sem)
    fit_sem = fit_bulk(ri, mean, sem, ri_u, weighting=weighting)
    fit_sd = fit_bulk(ri, mean, sd, ri_u, weighting=weighting)
    return BulkValues(fit_sem.n_points, fit_sem.slope, fit_sd.u, fit_sem.u, fit_sem, fit_sd, tuple(keys))


def bulk_excess(numbers: RowNumbers, values: BulkValues, terms: Mapping[object, tuple[float, float, float]]) -> Excess | None:
    """Measured minus what the row's own bulk sensitivity predicts (2026-11-12) for a step (``{ref: (1, RI, RI uncertainty)}``) or the difference of two
    (``{signal: (1, ...), baseline: (-1, ...)}``), with its uncertainty from the steps' SEMs (`bulk_sensitivity.excess`). None when the row has no slope or no number in
    one of the steps. The means are the row's numbers as the other columns show them (Subtract on or off: the line is fitted to the same means)."""
    fit = values.fit_sem
    if not fit.ok or any(ref not in numbers.refs for ref in terms):
        return None
    points = {}
    for ref, (_c, ri, ri_u) in terms.items():
        value = numbers.value_at(ref)
        if not math.isfinite(value.mean):
            return None
        points[ref] = (ri, ri_u, value.mean, value.sem)
    for ref in values.fit_keys():  # a fitted point's mean, in case the terms do not name it (its variance comes from the fit)
        if ref not in points:
            value = numbers.value_at(ref)
            points[ref] = (float("nan"), float("nan"), value.mean, value.sem)
    return excess(fit, {ref: c for ref, (c, _ri, _u) in terms.items()}, points, values.fit_keys())


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
    return RowNumbers(
        refs, np.asarray(values.n).copy(), np.asarray(values.mean).copy(), np.asarray(values.sd).copy(), noise, np.asarray(line.y, dtype=np.float64).copy(),
    )


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
