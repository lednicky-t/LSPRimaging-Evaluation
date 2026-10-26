"""Statistics of the experiment steps, per ROI and per group (2026-10-26). Pure, no Qt, no files.

The backbone for comparing the steps of an experiment (buffer, sample, rinse, ...) with each other: for every step, the cubes that lie inside it, and from the
metric value of those cubes a few plain numbers. Nothing here is stored. Like the formula, the fit and the metric, the numbers are recomputed from the traces
whenever the steps or the traces change (a few ms for 160 ROIs x 14 steps), so there is no file format and nothing to go stale.

**What the app calls today**: `assign_cubes` / `membership_segments` / `smoothing_segments` (the Sensorgram's panel), `step_statistics` / `line_step_values` (`scan_trace.build_series`,
which gives every plotted line its mean +/- SD per step for the table on the plot, `LineStepValues`). The rest is the same numbers as a ready-made API for what comes next and is
exercised by the tests only: `compute_step_statistics` / `trace_matrix` (one call from traces to numbers), `StepWindow` (trimming the settling at the start of a step),
`group_statistics` (the spread between the ROIs of a group).

**Which cubes belong to a step.** A step is ``[start, end)`` in unix ms (`plan.edits.EffectiveStep`, the user's boundary edits already applied). A cube is a
run of frames that takes about one period. It is used only if *all* its frames lie inside the step's **evaluation window** (`StepWindow`: the whole step by
default; or without its first / last seconds, e.g. while the new solution is still arriving; or only its last seconds, e.g. the equilibrium value). A cube
that straddles the edge of the window holds images of two solutions: it is left out and counted in `StepMembership.n_partial`, never assigned by guess. A cube
with no timing is in no step.

**What is computed**, per ROI and step, from the cubes' finite values (a failed fit is a gap and is skipped; ``n`` counts what was used). All in the unit
of the trace (nm, or nm relative to the baseline when the Sensorgram's baseline correction is on); time in seconds from the cube's first frame:

- ``mean``: the arithmetic mean.
- ``std``: the *sample* standard deviation (``ddof=1``), the scatter of single cube values around the mean. If the signal drifts inside the step, the drift
  is part of it, so it is an upper bound of the noise. NaN for n < 2.
- ``sem``: ``std / sqrt(n)``, how well the *mean* is known. It is **not** noise: it shrinks as the step gets longer. NaN for n < 2. (It assumes the cube values
  are independent; slow wander makes it too small.)
- ``slope_per_s``: the least-squares straight line of the values against time, the drift rate inside the step. NaN for n < 2.
- ``noise``: the standard deviation of what is left after that straight line is taken out (``ddof=2``, two fitted numbers). This is the number to quote as the
  noise of the measurement: it equals ``std`` for a flat step and does not count a steady drift or a slow binding trend as noise. NaN for n < 3.

**Groups** (`group_statistics`) answer a different question from a single ROI: not "how steady is one ROI in this step" but "how much do the ROIs of the
group differ in this step". Each member ROI is first reduced to its own step mean, then the means are combined: the mean of the means, the spread *between*
ROIs (``ddof=1``) and its SEM (``n`` = number of ROIs). The group's ``noise`` is the **median** of its members' noise: the typical ROI, not thrown off by
a few disturbed ones (on the example dataset 32 of 160 ROIs are disturbed in one step: their root mean square is 1.6 nm, the typical ROI 0.2 nm); a
disturbed ROI shows in its own row. ROIs are never pooled before that and every ROI counts once whatever its size (CLAUDE.md: average the already-reduced
per-ROI values).

A group's spread between ROIs is only a comparison of *responses* when the means are relative: ROIs sit at different resonance positions (about 5 nm
apart on the example dataset), so on absolute means it measures that, not the experiment. With the Sensorgram's baseline correction on, the same spread is
0.01 - 1 nm.

**Which values go in** (`trace_matrix`): the per-ROI traces exactly as the Sensorgram plots them, i.e. through whatever spike rejection, smoothing and baseline
correction the user selected (maintainer's choice, 2026-10-26). A baseline correction shifts every mean by a constant per ROI and leaves ``std`` and ``noise``
alone. Two things about **smoothing** (measured on the example dataset, `docs/step_statistics_backbone_2026-10-26.md`):

- Smoothing the whole trace and then cutting it into steps lets the first and last cubes of a step carry the neighbouring step's level (a window of 15 cubes is
  about half a step): ``std`` and ``noise`` came out *larger* than unsmoothed (0.09 -> 0.23 nm). So with the "smooth inside steps" switch on (the default) and
  steps present, each step is smoothed on its own (`smoothing_segments`, `statistics.apply_statistics(segments=...)`); the Sensorgram draws the same curve.
  Without steps, or with the switch off, the whole trace is smoothed.
- Neighbouring smoothed values are strongly correlated, so ``sem = std / sqrt(n)`` counts independent values that are not there and is too small
  (by about the square root of the window).
"""

from __future__ import annotations

import logging
import time
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from ..applog import log_event
from ..plan.edits import EffectiveStep
from .settings import StatisticsSettings
from .statistics import apply_statistics

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StepWindow:
    """The part of each step that is evaluated. All in seconds; the defaults (nothing skipped, no limit) evaluate the whole step.

    ``skip_start_s``: drop the first seconds of every step (the new solution has not arrived yet). ``skip_end_s``: drop the last seconds. ``last_s``: use only
    the last seconds that remain (the equilibrium value); applied after the two skips. A window that ends up empty selects no cube."""

    skip_start_s: float = 0.0
    skip_end_s: float = 0.0
    last_s: float | None = None

    def bounds(self, start_unix_ms: float, end_unix_ms: float) -> tuple[float, float]:
        """``[low, high)`` in unix ms for a step that runs from `start_unix_ms` (included) to `end_unix_ms` (excluded)."""
        low = float(start_unix_ms) + 1000.0 * max(0.0, float(self.skip_start_s))
        high = float(end_unix_ms) - 1000.0 * max(0.0, float(self.skip_end_s))
        if self.last_s is not None:
            low = max(low, high - 1000.0 * max(0.0, float(self.last_s)))
        return low, high


@dataclass(frozen=True)
class StepMembership:
    """Which cubes of the trace arrays are used for which step. ``steps`` is in time order; all arrays are indexed ``[step, cube]`` or ``[step]``."""

    steps: tuple[EffectiveStep, ...]
    window_start_unix_ms: np.ndarray  # float64 [step], included
    window_end_unix_ms: np.ndarray  # float64 [step], excluded
    cube_first_unix_ms: np.ndarray  # float64 [cube], NaN = no timing
    used: np.ndarray  # bool [step, cube]: every frame of the cube is inside the step's window
    partial: np.ndarray  # bool [step, cube]: the cube touches the window but is not wholly inside it (left out)
    n_partial: np.ndarray  # int [step]: how many of those

    @property
    def n_cubes(self) -> np.ndarray:
        """Cubes used per step (before looking at any ROI's gaps)."""
        return self.used.sum(axis=1)


def assign_cubes(
    steps: Sequence[EffectiveStep],
    first_unix_ms: Sequence[float] | np.ndarray,
    last_unix_ms: Sequence[float] | np.ndarray,
    window: StepWindow | None = None,
) -> StepMembership:
    """Decide which cubes belong to which step. `first_unix_ms` / `last_unix_ms`: the time of each cube's first and last frame, one value per cube of the
    trace arrays (NaN = no timing; `DatasetModule.cube_spans_unix_ms`). Steps are taken in time order; the cubes of two steps never overlap unless the steps do."""
    window = window or StepWindow()
    ordered = tuple(sorted(steps, key=lambda s: s.start_unix_ms))
    first = np.asarray(first_unix_ms, dtype=np.float64)
    last = np.asarray(last_unix_ms, dtype=np.float64)
    if first.shape != last.shape or first.ndim != 1:
        raise ValueError("first_unix_ms and last_unix_ms must be 1-D with one value per cube")
    last = np.where(np.isfinite(last), last, first)  # a cube with one known time is a point
    bounds = [window.bounds(s.start_unix_ms, s.end_unix_ms) for s in ordered]
    low = np.array([b[0] for b in bounds], dtype=np.float64)
    high = np.array([b[1] for b in bounds], dtype=np.float64)
    with np.errstate(invalid="ignore"):  # a cube without timing is NaN: every comparison is False, so it is in no step
        inside = (first[None, :] >= low[:, None]) & (last[None, :] < high[:, None])
        touches = (last[None, :] >= low[:, None]) & (first[None, :] < high[:, None])
    partial = touches & ~inside
    return StepMembership(
        steps=ordered, window_start_unix_ms=low, window_end_unix_ms=high, cube_first_unix_ms=first, used=inside, partial=partial,
        n_partial=partial.sum(axis=1).astype(np.int64),
    )


def membership_segments(membership: StepMembership) -> np.ndarray:
    """One label per cube for "smooth inside the steps" (`statistics.apply_statistics(..., segments=...)`), made from a membership of the *whole* steps (`assign_cubes`
    without an evaluation window): ``int64``, the cubes of a run of equal labels are smoothed together and never with their neighbours.

    A cube wholly inside a step gets the step's position in time order, 0, 1, ...; a cube in no step, or without timing, gets -1 (a run of such cubes is smoothed as one,
    apart from the steps); a cube that straddles a boundary holds two solutions and is a run of its own, so it stays unsmoothed and does not pull the edge of either step.
    Same cube rule as the statistics, so the plotted curve and the numbers taken from it agree."""
    labels = np.full(membership.used.shape[1], -1, dtype=np.int64)
    for index in range(len(membership.steps)):
        labels[membership.used[index]] = index
    straddling = membership.partial.any(axis=0) & ~membership.used.any(axis=0)
    labels[straddling] = -2 - np.flatnonzero(straddling)  # distinct and below -1: each straddling cube its own run
    return labels


def smoothing_segments(
    steps: Sequence[EffectiveStep], first_unix_ms: Sequence[float] | np.ndarray, last_unix_ms: Sequence[float] | np.ndarray
) -> np.ndarray:
    """`membership_segments` of the whole steps (`assign_cubes` at its default window) for these cube times."""
    return membership_segments(assign_cubes(steps, first_unix_ms, last_unix_ms))


@dataclass(frozen=True)
class StepStats:
    """The numbers of each ROI in each step (module docstring). All arrays are ``[roi, step]``, rows in the order of ``roi_ids``, columns in the order of
    ``membership.steps``. NaN = not defined (too few values); ``n`` is an integer count, 0 where nothing was used."""

    roi_ids: tuple[int, ...]
    membership: StepMembership
    n: np.ndarray
    mean: np.ndarray
    std: np.ndarray
    sem: np.ndarray
    slope_per_s: np.ndarray
    noise: np.ndarray

    @property
    def steps(self) -> tuple[EffectiveStep, ...]:
        return self.membership.steps


@dataclass(frozen=True)
class GroupStepStats:
    """One group in each step; arrays are ``[step]``. `roi_ids` are the members that are in the statistics."""

    name: str
    roi_ids: tuple[int, ...]
    n_roi: np.ndarray  # int: member ROIs that have a mean in the step
    mean: np.ndarray  # mean of the members' step means
    std: np.ndarray  # spread of the members' step means between ROIs (ddof=1)
    sem: np.ndarray  # std / sqrt(n_roi)
    noise: np.ndarray  # median of the members' noise (the typical ROI)


def _divide(numerator: np.ndarray, denominator: np.ndarray, where: np.ndarray) -> np.ndarray:
    """``numerator / denominator`` where `where`, NaN elsewhere (no warnings)."""
    out = np.full(np.broadcast(numerator, denominator).shape, np.nan)
    np.divide(numerator, denominator, out=out, where=where)
    return out


def _spread(values: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Along the last axis of `values` (NaN = missing): ``(finite, n, mean, deviation, sum of squares, std)``; `deviation` is 0 at a missing value."""
    finite = np.isfinite(values)
    n = finite.sum(axis=-1)
    mean = _divide(np.where(finite, values, 0.0).sum(axis=-1), n, n > 0)
    deviation = np.where(finite, values - mean[..., None], 0.0)
    ss = np.sum(deviation**2, axis=-1)
    std = np.sqrt(_divide(ss, n - 1, n > 1))
    return finite, n, mean, deviation, ss, std


def step_statistics(roi_ids: Sequence[int], values: np.ndarray, membership: StepMembership) -> StepStats:
    """The statistics of every ROI in every step. `values`: ``float64 [roi, cube]`` (`trace_matrix`), the same cube order as the membership."""
    values = np.asarray(values, dtype=np.float64)
    if values.ndim != 2 or values.shape != (len(roi_ids), membership.used.shape[1]):
        raise ValueError("values must be [roi, cube] with one row per roi_id and one column per cube of the membership")
    shape = (len(roi_ids), len(membership.steps))
    n = np.zeros(shape, dtype=np.int64)
    mean, std, sem, slope, noise = (np.full(shape, np.nan) for _ in range(5))
    for s in range(len(membership.steps)):
        cubes = np.flatnonzero(membership.used[s])
        if cubes.size == 0:
            continue
        sub = values[:, cubes]
        t_s = (membership.cube_first_unix_ms[cubes] - membership.cube_first_unix_ms[cubes[0]]) / 1000.0
        finite, n_s, mean_s, dev, _, std_s = _spread(sub)
        n[:, s], mean[:, s], std[:, s] = n_s, mean_s, std_s
        sem[:, s] = _divide(std_s, np.sqrt(n_s), n_s > 1)
        # Least-squares line through the finite points of each ROI (its own time mean, so a gap does not bias the slope).
        t_mean = _divide(np.where(finite, t_s[None, :], 0.0).sum(axis=1), n_s, n_s > 0)
        dt = np.where(finite, t_s[None, :] - t_mean[:, None], 0.0)
        sxx = np.sum(dt**2, axis=1)
        sxy = np.sum(dt * dev, axis=1)
        slope_s = _divide(sxy, sxx, (n_s > 1) & (sxx > 0))
        slope[:, s] = slope_s
        residual = np.where(finite, dev - slope_s[:, None] * dt, 0.0)  # from the residuals themselves: "ss - slope * sxy" loses ~1e-8 nm to cancellation
        noise[:, s] = np.sqrt(_divide(np.sum(residual**2, axis=1), n_s - 2, (n_s > 2) & np.isfinite(slope_s)))
    return StepStats(tuple(int(r) for r in roi_ids), membership, n, mean, std, sem, slope, noise)


def group_statistics(stats: StepStats, groups: Sequence[tuple[str, Sequence[int]]]) -> tuple[GroupStepStats, ...]:
    """One `GroupStepStats` per `(name, member roi ids)`. Members that are not in `stats` are ignored; a group with none of its members in `stats` is left out.
    Every group is computed on its own, so a ROI listed in two groups counts in both."""
    row_of = {roi: i for i, roi in enumerate(stats.roi_ids)}
    result: list[GroupStepStats] = []
    for name, members in groups:
        rows = [row_of[int(r)] for r in members if int(r) in row_of]
        if not rows:
            continue
        means = stats.mean[rows].T  # [step, member]
        _, n_roi, mean, _, _, std = _spread(means)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # "All-NaN slice": a step where no member has a noise value is NaN, as it should be
            noise = np.nanmedian(stats.noise[rows].T, axis=1)
        result.append(GroupStepStats(
            name=str(name), roi_ids=tuple(stats.roi_ids[i] for i in rows), n_roi=n_roi, mean=mean, std=std,
            sem=_divide(std, np.sqrt(n_roi), n_roi > 1), noise=noise,
        ))
    return tuple(result)


@dataclass(frozen=True)
class LineStepValues:
    """What one plotted line is in each step, for the table on the Sensorgram: arrays ``[step]`` (columns of the membership). Normally (`between_rois` False) ``mean`` and
    ``sd`` are those of the line's own values in the step and ``n`` counts the cubes, whether the line is one ROI or the centre line of an average (`scan_trace.build_series`
    takes them from the drawn curve, so they follow smoothing). With `between_rois` True (`line_step_values` on a group; the table does not use it now): ``mean`` is the
    mean of the ROIs' own step means, ``sd`` the spread *between* those ROIs (the quantity the line's SD band shows, which smoothing cannot change) and ``n`` counts the ROIs."""

    n: np.ndarray
    mean: np.ndarray
    sd: np.ndarray
    between_rois: bool


def line_step_values(stats: StepStats, members: Sequence[int], *, between_rois: bool) -> LineStepValues:
    """The values of the line made of the ROIs `members` (a single ROI when not `between_rois`). A line none of whose ROIs is in `stats` is all NaN with n = 0."""
    empty = LineStepValues(np.zeros(len(stats.steps), dtype=np.int64), np.full(len(stats.steps), np.nan), np.full(len(stats.steps), np.nan), between_rois)
    if not between_rois:
        if not members or int(members[0]) not in stats.roi_ids:
            return empty
        row = stats.roi_ids.index(int(members[0]))
        return LineStepValues(stats.n[row].copy(), stats.mean[row].copy(), stats.std[row].copy(), False)
    groups = group_statistics(stats, [("", members)])
    return empty if not groups else LineStepValues(groups[0].n_roi, groups[0].mean, groups[0].std, True)


def trace_matrix(
    roi_ids: Sequence[int], traces: Mapping[int, np.ndarray], x: np.ndarray, statistics: StatisticsSettings, segments: np.ndarray | None = None
) -> np.ndarray:
    """The values the statistics are taken from: ``float64 [roi, cube]``. Each ROI's metric trace goes through the Sensorgram's own post-processing
    (`statistics.apply_statistics`: spike rejection, smoothing, baseline, whatever is selected), so the numbers describe the curve that is plotted.
    `x`: the Sensorgram's x of each cube (the baseline window is in its units). `segments`: smooth each run of equal labels on its own
    (`smoothing_segments`); None smooths the whole trace. `traces` must hold every ROI."""
    if not roi_ids:
        return np.empty((0, len(x)), dtype=np.float64)
    return np.vstack([apply_statistics(x, traces[r], statistics, segments)[0] for r in roi_ids])


def compute_step_statistics(
    steps: Sequence[EffectiveStep],
    roi_ids: Sequence[int],
    traces: Mapping[int, np.ndarray],
    x: np.ndarray,
    statistics: StatisticsSettings,
    first_unix_ms: Sequence[float] | np.ndarray,
    last_unix_ms: Sequence[float] | np.ndarray,
    window: StepWindow | None = None,
) -> StepStats:
    """Everything in one call: the cubes of each step (`assign_cubes`), the values (`trace_matrix`), the numbers (`step_statistics`). Arrays over cubes
    (`x`, `first_unix_ms`, `last_unix_ms`, each trace) all have one entry per cube of the file, in the same order. Smoothing follows the plotted curve:
    inside each step when the "smooth inside steps" switch is on and there are steps, else over the whole trace (`smoothing_segments`)."""
    t0 = time.perf_counter()
    membership = assign_cubes(steps, first_unix_ms, last_unix_ms, window)
    within = statistics.smooth_within_steps and statistics.smoothing_method != "none" and len(steps) > 0
    segments = smoothing_segments(steps, first_unix_ms, last_unix_ms) if within else None
    stats = step_statistics(roi_ids, trace_matrix(roi_ids, traces, x, statistics, segments), membership)
    log_event(
        logger, "step_stats.compute", logging.DEBUG, rois=len(roi_ids), steps=len(membership.steps), cubes_used=int(membership.used.any(axis=0).sum()),
        partial=int(membership.n_partial.sum()), ms=round((time.perf_counter() - t0) * 1e3, 2),
    )
    return stats
