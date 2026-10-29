"""ROI position tuning, the pure part (2026-10-28, step A). No Qt, no files, nothing stored.

The question: *for a ROI, which centre gives the best Sensorgram?* The method is a plain search. The ROI's centre is moved over a small square
**lattice** of offsets (``dx``, ``dy``), the Sensorgram is computed for every shifted copy over a chosen part of the experiment (the cubes of two
**regions**: a baseline and a signal), and each copy is reduced to a few numbers (the objectives). Looking at those numbers over the lattice is the
whole result; nothing here fits a surface or picks a winner yet (step B of the plan, decided with the maintainer, 2026-10-28).

What is in this file, in the order the lab script uses it:

* :class:`Lattice` - ``size`` x ``size`` offsets (odd ``size``), ``pitch_px`` apart, the current centre in the middle.
* :func:`shifted_roi_set` - the ROIs of a scan with some of them moved by one offset. A shifted copy is *just a ROI at another place*: the scan,
  the fits and the statistics already do everything else, so no new maths enters the pixels.
* :func:`neighbours_within_reach` - which pairs of ROIs could change each other's reference ring within the lattice's reach. If there are none, a ROI
  shifted alone and all ROIs shifted together read exactly the same pixels, so one scan per lattice point serves every selected ROI (this is the
  property ``frame_reduce.local_roi_keys`` already relies on).
* :class:`TuningRegions` / :func:`region_membership` - two ranges of cube numbers, expressed as the ``StepMembership`` the step statistics take.
* :func:`lattice_objectives` - from the metric traces of every lattice point: one statistical number per region (mean, std, sem), **delta** (signal - baseline
  mean, nm) with its uncertainty and its change against the centre in units of that uncertainty (:meth:`LatticeObjectives.delta_change_sigma`), the
  detrended **noise** of the baseline region (the number ``step_stats`` calls ``noise``) and **snr** = |delta| / noise.

Units and spaces: offsets are in **processed image pixels**, the space ROI centres live in (after rotation / flip / crop), before the per-wavelength
chromatic correction, which then carries the shifted ROI to every wavelength exactly as it does the original. Traces and objectives are in nm.

Assumptions worth knowing:

* The objectives follow whatever the Sensorgram would draw (fit method, smoothing, spike rejection come in through ``statistics``), except the baseline:
  subtracting a constant per ROI changes neither ``delta`` nor ``noise``, so the baseline switch is left to the caller to turn off (it can only fail if its
  window lies outside the scanned cubes).
* ``noise`` comes from the cubes of the baseline region only (about 30 cubes: its own uncertainty is roughly 1 / sqrt(2 (n - 1)) = 13 %). Picking the best
  of nine lattice points on such numbers favours the luckiest one; judging that is what the held-out region and the thresholds of later steps are for.
* Free-form (mask) ROIs are refused: their masks have their own offset and are not moved by changing the centre.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace

import numpy as np

from ..plan.edits import EffectiveStep
from . import frame_reduce as fr
from .settings import StatisticsSettings
from .step_stats import StepMembership, StepStats, membership_segments, step_statistics, trace_matrix

BASELINE, SIGNAL = 0, 1
"""Row of the baseline / signal region in every ``[roi, region]`` array of a :class:`StepStats` made from :func:`region_membership`."""


@dataclass(frozen=True)
class Lattice:
    """A square lattice of ``size`` x ``size`` offsets, ``pitch_px`` apart, centred on the ROI's current position.

    ``size`` is odd (3, 5, 7, ...), so the middle point is the current position itself and the lattice is symmetric. ``size == 1`` is the current
    position only (``pitch_px`` is then unused)."""

    size: int = 3
    pitch_px: float = 2.0

    def __post_init__(self) -> None:
        if self.size < 1 or self.size % 2 == 0:
            raise ValueError(f"a lattice needs an odd number of points per side (1, 3, 5, ...), not {self.size}")
        if self.size > 1 and not (math.isfinite(self.pitch_px) and self.pitch_px > 0):
            raise ValueError(f"the pitch must be a positive number of pixels, not {self.pitch_px}")

    @property
    def n_points(self) -> int:
        return self.size * self.size

    @property
    def centre_index(self) -> int:
        """Position of the current centre (offset 0, 0) in :meth:`offsets`."""
        return self.n_points // 2

    @property
    def half_extent_px(self) -> float:
        """How far the outermost points are from the centre along one axis."""
        return (self.size // 2) * self.pitch_px if self.size > 1 else 0.0

    @property
    def max_shift_px(self) -> float:
        """The longest move of any lattice point (a corner)."""
        return math.hypot(self.half_extent_px, self.half_extent_px)

    def offsets(self) -> np.ndarray:
        """``float64 [n_points, 2]`` of ``(dx, dy)`` in pixels, ``dx`` varying fastest (row by row, top row = most negative ``dy``)."""
        axis = (np.arange(self.size) - self.size // 2) * float(self.pitch_px)
        dx, dy = np.meshgrid(axis, axis)  # default indexing: dx along a row, dy down the rows
        return np.column_stack([dx.ravel(), dy.ravel()])


def shifted_roi_set(rs: fr.RoiSet, indices: Sequence[int], dx: float, dy: float) -> fr.RoiSet:
    """``rs`` with the ROIs at ``indices`` moved by ``(dx, dy)`` pixels (processed space). Radii, rings and every other ROI are untouched; ``rs`` is not changed.

    Raises ``ValueError`` for a free-form (mask) ROI: moving its centre does not move its masks."""
    rois = list(rs.rois)
    for i in indices:
        if not rs.vectorised[i]:
            raise ValueError(f"ROI at position {i} has a free-form mask; its position cannot be tuned by shifting the centre")
        rois[i] = replace(rois[i], center_x=float(rois[i].center_x) + float(dx), center_y=float(rois[i].center_y) + float(dy))
    return fr.build_roi_set(rois, rs.inner_default_px, rs.outer_default_px)


def neighbours_within_reach(rs: fr.RoiSet, indices: Sequence[int], lattice: Lattice) -> list[tuple[int, int]]:
    """Pairs ``(a, k)`` (positions in ``rs``) where moving by up to the lattice's reach could change what ROI ``a``'s reference ring reads because ROI ``k``'s
    sample disc is, or could come, close to it. Empty = the ROIs do not interact over this lattice: a ROI shifted alone, or all of them shifted together,
    read the same pixels. ``a`` runs over ``indices``; ``k`` over every other ROI."""
    near = fr.ring_reach_matrix(rs, indices, fr.NEIGHBOUR_REACH_MARGIN_PX + lattice.max_shift_px)
    pairs: list[tuple[int, int]] = []
    for row, a in enumerate(indices):
        pairs.extend((int(a), int(k)) for k in np.flatnonzero(near[row]) if k != a)
    return pairs


@dataclass(frozen=True)
class TuningRegions:
    """The two parts of the experiment the objectives are read from, as inclusive ranges of dataset cube numbers (as the Sensorgram shows them).

    ``baseline``: a quiet stretch, the noise comes from here. ``signal``: a stretch after the solution changed; ``delta`` is its mean minus the baseline's."""

    baseline: tuple[int, int]
    signal: tuple[int, int]

    def __post_init__(self) -> None:
        for name, (first, last) in (("baseline", self.baseline), ("signal", self.signal)):
            if first > last:
                raise ValueError(f"the {name} region runs from cube {first} to cube {last}: first must not be after last")
        if self.baseline[0] <= self.signal[1] and self.signal[0] <= self.baseline[1]:
            raise ValueError(f"the baseline {self.baseline} and signal {self.signal} regions overlap")

    def cubes(self) -> tuple[int, ...]:
        """Every cube number of both regions, ascending."""
        return tuple(sorted({*range(self.baseline[0], self.baseline[1] + 1), *range(self.signal[0], self.signal[1] + 1)}))


def region_membership(
    regions: TuningRegions, cube_indices: Sequence[int], first_unix_ms: Sequence[float] | np.ndarray, last_unix_ms: Sequence[float] | np.ndarray
) -> StepMembership:
    """The two regions as a ``StepMembership`` over the cubes ``cube_indices`` (one entry per cube of the trace arrays, same order as the times), baseline first
    whatever the order in time, so row ``BASELINE`` / ``SIGNAL`` of every statistic is the region it says. A cube is used when its number is in the region
    **and** it has timing (the drift line of ``noise`` needs it, as in ``step_stats``). No cube is ever partial: a region is a list of whole cubes."""
    cubes = np.asarray(cube_indices, dtype=np.int64)
    first = np.asarray(first_unix_ms, dtype=np.float64)
    last = np.asarray(last_unix_ms, dtype=np.float64)
    if not (cubes.shape == first.shape == last.shape and cubes.ndim == 1):
        raise ValueError("cube_indices, first_unix_ms and last_unix_ms must be 1-D with one value per cube")
    timed = np.isfinite(first)
    used = np.zeros((2, cubes.size), dtype=bool)
    steps: list[EffectiveStep] = []
    for row, (name, (lo, hi)) in enumerate((("baseline", regions.baseline), ("signal", regions.signal))):
        used[row] = (cubes >= lo) & (cubes <= hi) & timed
        start = float(first[used[row]].min()) if used[row].any() else 0.0
        end = float(np.nanmax(np.where(used[row], np.where(np.isfinite(last), last, first), -np.inf))) + 1.0 if used[row].any() else 0.0
        steps.append(EffectiveStep(
            step=row + 1, label=name, start_unix_ms=int(start), end_unix_ms=int(end), color=None,
            measured_label=name, measured_start_unix_ms=int(start), measured_end_unix_ms=int(end),
        ))
    return StepMembership(
        steps=tuple(steps), window_start_unix_ms=np.array([s.start_unix_ms for s in steps], dtype=np.float64),
        window_end_unix_ms=np.array([s.end_unix_ms for s in steps], dtype=np.float64), cube_first_unix_ms=first, used=used,
        partial=np.zeros_like(used), n_partial=np.zeros(2, dtype=np.int64),
    )


@dataclass(frozen=True)
class LatticeObjectives:
    """The objectives of every lattice point and ROI. All arrays are ``[point, roi]`` (rows in the order of ``offsets``, columns of ``roi_ids``); nm unless noted.

    Each region is one statistical number per ROI and point: ``*_mean`` and ``*_std`` (sample SD, ``ddof=1``, the scatter of single cubes), and ``*_sem`` =
    std / sqrt(n), how well the *mean* is known. ``noise`` is the detrended SD of the baseline region (``StepStats.noise``: the SD after taking out a straight
    drift line); ``snr`` is |delta| / noise, dimensionless, NaN where either is missing or the noise is zero. ``n_baseline`` / ``n_signal`` count the cubes that
    had a value."""

    roi_ids: tuple[int, ...]
    offsets: np.ndarray  # float64 [point, 2] (dx, dy) px
    baseline_mean: np.ndarray
    signal_mean: np.ndarray
    noise: np.ndarray
    signal_noise: np.ndarray
    baseline_std: np.ndarray
    signal_std: np.ndarray
    baseline_sem: np.ndarray
    signal_sem: np.ndarray
    n_baseline: np.ndarray
    n_signal: np.ndarray

    @property
    def delta(self) -> np.ndarray:
        """Signal mean minus baseline mean (nm)."""
        return self.signal_mean - self.baseline_mean

    @property
    def delta_sem(self) -> np.ndarray:
        """Uncertainty of ``delta`` (nm): the two means' SEMs added in quadrature. Assumes independent cubes (a drift inside a region makes it a little too
        small for the mean, never too large for the std)."""
        return np.sqrt(self.baseline_sem**2 + self.signal_sem**2)

    def delta_change_sigma(self, reference_point: int) -> np.ndarray:
        """``[point, roi]``: how much larger the response is at each point than at ``reference_point`` (normally the centre), in units of the uncertainty of that
        difference (the two ``delta_sem`` in quadrature; a conservative choice, the points share their cubes). Positive = a larger response in the direction
        of the reference's own response, so a ROI whose signal goes down is treated like one whose signal goes up. NaN where an uncertainty is missing."""
        change = (self.delta - self.delta[reference_point]) * np.sign(self.delta[reference_point])
        spread = np.sqrt(self.delta_sem**2 + self.delta_sem[reference_point] ** 2)
        out = np.full(change.shape, np.nan)
        np.divide(change, spread, out=out, where=np.isfinite(change) & np.isfinite(spread) & (spread > 0))
        return out

    @property
    def snr(self) -> np.ndarray:
        out = np.full(self.noise.shape, np.nan)
        np.divide(np.abs(self.delta), self.noise, out=out, where=np.isfinite(self.delta) & np.isfinite(self.noise) & (self.noise > 0))
        return out


def lattice_objectives(
    roi_ids: Sequence[int],
    offsets: np.ndarray,
    traces: np.ndarray,
    membership: StepMembership,
    x: np.ndarray,
    statistics: StatisticsSettings,
) -> LatticeObjectives:
    """Reduce the metric traces of every lattice point to the objectives.

    ``traces``: ``float64 [point, roi, cube]``, the metric (nm) of each shifted copy at every cube of ``membership`` (NaN = no value there). ``x``: the
    Sensorgram's x of each cube (the unit of ``statistics``' baseline window; only needed by the post-processing). Each trace goes through the Sensorgram's own
    post-processing (``step_stats.trace_matrix``); smoothing, when chosen, is done inside each region on its own so it never blends the two."""
    offsets = np.asarray(offsets, dtype=np.float64)
    traces = np.asarray(traces, dtype=np.float64)
    if traces.ndim != 3 or traces.shape[:2] != (len(offsets), len(roi_ids)) or traces.shape[2] != membership.used.shape[1]:
        raise ValueError("traces must be [point, roi, cube] with one row per offset, one column per roi_id and one entry per cube of the membership")
    segments = membership_segments(membership) if statistics.smooth_within_steps and statistics.smoothing_method != "none" else None
    shape = (len(offsets), len(roi_ids))
    baseline_mean, signal_mean, noise, signal_noise, baseline_std, signal_std, baseline_sem, signal_sem = (np.full(shape, np.nan) for _ in range(8))
    n_baseline = np.zeros(shape, dtype=np.int64)
    n_signal = np.zeros(shape, dtype=np.int64)
    for point in range(len(offsets)):
        stats: StepStats = step_statistics(
            roi_ids, trace_matrix(roi_ids, {r: traces[point, i] for i, r in enumerate(roi_ids)}, x, statistics, segments), membership
        )
        baseline_mean[point], signal_mean[point] = stats.mean[:, BASELINE], stats.mean[:, SIGNAL]
        noise[point], signal_noise[point] = stats.noise[:, BASELINE], stats.noise[:, SIGNAL]
        baseline_std[point], signal_std[point] = stats.std[:, BASELINE], stats.std[:, SIGNAL]
        baseline_sem[point], signal_sem[point] = stats.sem[:, BASELINE], stats.sem[:, SIGNAL]
        n_baseline[point], n_signal[point] = stats.n[:, BASELINE], stats.n[:, SIGNAL]
    return LatticeObjectives(
        tuple(int(r) for r in roi_ids), offsets, baseline_mean, signal_mean, noise, signal_noise, baseline_std, signal_std, baseline_sem, signal_sem,
        n_baseline, n_signal,
    )
