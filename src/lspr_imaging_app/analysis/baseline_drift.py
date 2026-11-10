"""Drift baseline from anchor points (2026-11-09). Pure, no Qt, no files.

The Sensorgram's first baseline ("window") takes the mean of one stretch of the trace and subtracts that single number: it removes an offset, not a
drift. This module is the second method ("anchors"): the user clicks points on the plot where the trace is known to be "only baseline" (e.g. the end
of the buffer step at the start and at the end). A curve of the chosen shape is laid through them and subtracted, so a buffer that reads 0.3 nm higher
at the end than at the start reads 0 at both after correction. (The same idea as OriginLab's anchor-point baseline: pick where the baseline is, then
how to connect it.)

**Anchor points** (`point_regions`): a cube the user clicked on the plot. One anchor per point: the mean x and mean y of the cubes within
``halfwidth`` cubes of it (in the file's cube order), so one noisy cube does not decide the baseline. Stored as cube numbers, not plot x, so switching
the axis between cubes and time keeps them in place. A point is shared by every ROI (roi id `ALL_ROIS`) or belongs to one ROI.

(Simplified the same day at the maintainer's request: anchors used to come from plan steps with a chosen note as well; that was removed because two
sources of anchors were hard to follow. What is clicked is what is used.)

**Anchor steps of the Range method** (`step_regions`, added back later the same day in another form): the "window" method, shown as "Range", takes
the mean of the dragged range while no step is marked; once plan steps are marked as baseline anchors (right-click on a step with the Range pencil on),
each marked step minus a border offset (cubes at each end) is an anchor region, and the chosen shape connects them. Marked by the step's identity
(`EffectiveStep.ref`), so a moved boundary moves the anchor with it.

**Shapes** (`SHAPES`) and how many anchors each needs (`MIN_ANCHORS`). A shape is never swapped for a simpler one when there are too few anchors: the
trace is then left uncorrected and the reason is returned (the controls mean the same for every selection).

- ``constant``: the mean of the anchor levels (1 anchor).
- ``linear`` / ``quadratic`` / ``cubic``: least-squares polynomial through the anchors (2 / 3 / 4 anchors; with exactly that many it passes through
  each). Extrapolated beyond the first and last anchor, as the polynomial says.
- ``pchip``: a smooth curve through every anchor that never overshoots between them (shape-preserving piecewise cubic; with two anchors a straight
  line). Held flat beyond the first and last anchor, because a piecewise curve has nothing to say there.

The fit is made in the units of the plot's x axis (seconds, or cube number when the axis is cubes), the same axis the "window" method uses. A cube
whose x is NaN (no timing on a time axis) gets no baseline and so no corrected value.

**Quality.** `DriftFit.anchor_rms` is the root mean square of the corrected trace over every anchor cube: what is left at the anchors after
correction. Close to the noise it means the shape fits; well above it means the shape is wrong (or a point is not on baseline).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

METHOD_WINDOW, METHOD_ANCHORS = "window", "anchors"
SHAPES = ("constant", "linear", "quadratic", "cubic", "pchip")
MIN_ANCHORS = {"constant": 1, "linear": 2, "quadratic": 3, "cubic": 4, "pchip": 2}
_POLY_DEGREE = {"linear": 1, "quadratic": 2, "cubic": 3}
ALL_ROIS = 0  # the roi id of an anchor point every ROI shares
TRIM_RANGE = (0, 500)  # cubes left out at each border of an anchor step (the Range method)


@dataclass(frozen=True)
class DriftFit:
    """One trace's drift baseline. ``curve`` is ``float64 [cube]`` (NaN where there is none: too few anchors, or no x); ``anchors_x`` / ``anchors_y``
    are the anchor points used; ``anchors_base`` the curve's value at each anchor's x (so ``anchors_y - anchors_base`` is where an anchor sits after
    correction); ``level`` is the mean of the anchor levels (what `Series.baseline` reports); ``message`` says why there is no curve."""

    curve: np.ndarray
    anchors_x: np.ndarray
    anchors_y: np.ndarray
    level: float
    anchor_rms: float
    message: str = ""
    anchors_base: np.ndarray | None = None

    @property
    def ok(self) -> bool:
        return not self.message


def _failed(n: int, anchors_x: np.ndarray, anchors_y: np.ndarray, message: str) -> DriftFit:
    return DriftFit(np.full(n, np.nan), anchors_x, anchors_y, float("nan"), float("nan"), message)


def fit_drift(x_values: np.ndarray, y_values: np.ndarray, regions: Sequence[np.ndarray], shape: str) -> DriftFit:
    """The drift baseline of one trace through its anchor regions (see the module docstring). Never raises on data: a curve that cannot be made is
    an all-NaN curve with a message."""
    x = np.asarray(x_values, dtype=np.float64)
    y = np.asarray(y_values, dtype=np.float64)
    n = y.size
    shape = str(shape).strip().lower()
    if shape not in MIN_ANCHORS:
        raise ValueError(f"unknown baseline shape {shape!r}; one of {SHAPES}")
    finite = np.isfinite(x) & np.isfinite(y)
    points_x, points_y, used = [], [], np.zeros(n, dtype=bool)
    for region in regions:
        inside = np.asarray(region, dtype=bool) & finite
        if inside.any():
            points_x.append(float(np.mean(x[inside])))
            points_y.append(float(np.mean(y[inside])))
            used |= inside
    ax, ay = np.asarray(points_x), np.asarray(points_y)
    if not regions:
        return _failed(n, ax, ay, "no anchors (no anchor point placed, or no cube left in the anchor steps after the border offset)")
    need = MIN_ANCHORS[shape]
    if ax.size < need:
        # "anchors", not "anchor points": the Range method's anchors are plan steps
        return _failed(n, ax, ay, f"{shape} needs {need} anchor{'s' if need != 1 else ''} with values, found {ax.size}")
    order = np.argsort(ax, kind="stable")
    ax, ay = ax[order], ay[order]
    curve = np.full(n, np.nan)
    xs = x[np.isfinite(x)]
    if shape == "constant":
        values = np.full(xs.size, float(np.mean(ay)))
        at_anchors = np.full(ax.size, float(np.mean(ay)))
    elif shape == "pchip":
        if np.unique(ax).size < 2:
            return _failed(n, ax, ay, "pchip needs anchors at two different x")
        from scipy.interpolate import PchipInterpolator

        unique_x, inverse = np.unique(ax, return_inverse=True)  # two anchors at one x (cube axis with equal means) are averaged
        unique_y = np.bincount(inverse, weights=ay) / np.bincount(inverse)
        model = PchipInterpolator(unique_x, unique_y, extrapolate=False)
        values = model(np.clip(xs, unique_x[0], unique_x[-1]))  # flat beyond the ends
        at_anchors = model(ax)
    else:
        degree = _POLY_DEGREE[shape]
        if np.unique(ax).size <= degree:
            return _failed(n, ax, ay, f"{shape} needs anchors at {degree + 1} different x")
        model = np.polynomial.Polynomial.fit(ax, ay, degree)  # fitted on a scaled domain: well conditioned for x in seconds
        values = model(xs)
        at_anchors = model(ax)
    curve[np.isfinite(x)] = values
    with np.errstate(invalid="ignore"):
        residual = y[used] - curve[used]
    residual = residual[np.isfinite(residual)]
    rms = float(np.sqrt(np.mean(residual**2))) if residual.size else float("nan")
    return DriftFit(curve, ax, ay, float(np.mean(ay)), rms, anchors_base=np.asarray(at_anchors, dtype=np.float64))


class StepMembership(Protocol):
    """What `step_regions` reads of `step_stats.StepMembership` (not imported: `step_stats` pulls in the app's logging, which the Qt-free contract of
    this module does not allow)."""

    steps: tuple[Any, ...]  # `plan.edits.EffectiveStep`: only `.ref` is read
    used: np.ndarray
    cube_first_unix_ms: np.ndarray


def step_regions(membership: StepMembership, refs: Sequence[tuple], trim_cubes: int) -> tuple[np.ndarray, ...]:
    """The Range method's anchor steps (2026-11-09): one ``bool [cube]`` per step whose `ref` is in `refs` (time order), holding its cubes minus
    `trim_cubes` at each border (right after a solution change, and right before the next, the signal is not baseline). Only cubes wholly inside the
    step count (`step_stats.assign_cubes`, the rule used everywhere else); `membership` must be of the *whole* steps. A step with no cube left after the
    trim gives no region (the fit then says how many anchors it found)."""
    wanted = {tuple(r) for r in refs}
    if not wanted:
        return ()
    trim = min(max(int(trim_cubes), TRIM_RANGE[0]), TRIM_RANGE[1])
    order_key = np.asarray(membership.cube_first_unix_ms, dtype=np.float64)
    regions: list[np.ndarray] = []
    for index, step in enumerate(membership.steps):
        if tuple(step.ref) not in wanted:
            continue
        cubes = np.flatnonzero(membership.used[index])
        cubes = cubes[np.argsort(order_key[cubes], kind="stable")]
        kept = cubes[trim: cubes.size - trim] if trim else cubes
        if kept.size == 0:
            continue
        region = np.zeros(membership.used.shape[1], dtype=bool)
        region[kept] = True
        regions.append(region)
    return tuple(regions)


def uses_anchor_steps(statistics: Any) -> bool:
    """The Range ("window") method with plan steps marked as its anchors: a curve through them replaces the window's mean. `statistics` is a
    `StatisticsSettings` (not imported: settings stay free of this module and the other way round)."""
    return statistics.baseline_method == METHOD_WINDOW and bool(statistics.baseline_anchor_steps)


def window_step_regions(membership: StepMembership | None, statistics: Any) -> tuple[np.ndarray, ...] | None:
    """What a Sensorgram request carries for the Range method: None when the method is another one or no step is marked (the window's mean), else
    the marked steps' regions (`step_regions`; empty when none has a cube left, or `membership` is None - no steps to place them on)."""
    if not uses_anchor_steps(statistics):
        return None
    if membership is None:
        return ()
    return step_regions(membership, statistics.baseline_anchor_steps, statistics.baseline_step_trim)


def point_regions(cube_numbers: Sequence[int] | np.ndarray, cubes: Sequence[int], halfwidth: int) -> tuple[np.ndarray, ...]:
    """One ``bool [cube]`` per anchor point: the cubes within `halfwidth` positions of the point's cube in file order (`cube_numbers`: the cube number of
    each position of the trace arrays). A point whose cube is not in the file gives no region. In the order of `cubes`."""
    numbers = np.asarray(cube_numbers)
    half = max(0, int(halfwidth))
    regions: list[np.ndarray] = []
    for cube in cubes:
        where = np.flatnonzero(numbers == int(cube))
        if where.size == 0:
            continue
        position = int(where[0])
        region = np.zeros(numbers.size, dtype=bool)
        region[max(0, position - half): position + half + 1] = True
        regions.append(region)
    return tuple(regions)


def points_for(roi_id: int, points: Sequence[tuple[int, int]]) -> tuple[int, ...]:
    """The cube numbers of one ROI's anchor points: the shared ones and its own, sorted, each once."""
    return tuple(sorted({int(c) for r, c in points if int(r) in (ALL_ROIS, int(roi_id))}))


def regions_by_roi(
    roi_ids: Sequence[int],
    cube_numbers: Sequence[int] | np.ndarray,
    points: Sequence[tuple[int, int]],
    halfwidth: int = 2,
) -> dict[int, tuple[np.ndarray, ...]]:
    """Every ROI's anchor regions (`point_regions` of `points_for`). Computed once per distinct set of points."""
    by_points: dict[tuple[int, ...], tuple[np.ndarray, ...]] = {}
    out: dict[int, tuple[np.ndarray, ...]] = {}
    for roi_id in roi_ids:
        cubes = points_for(roi_id, points)
        if cubes not in by_points:
            by_points[cubes] = point_regions(cube_numbers, cubes, halfwidth) if cubes else ()
        out[int(roi_id)] = by_points[cubes]
    return out


def curve_subtracted(statistics: Any) -> bool:
    """A drift curve is subtracted from the traces: the Subtract switch is on, and the method is Anchor points or Range with marked anchor steps.
    False for the Range method's plain mean (a single number per trace, which changes no difference between two stretches and no scatter)."""
    return bool(statistics.baseline_enabled) and (statistics.baseline_method == METHOD_ANCHORS or uses_anchor_steps(statistics))


AnchorCubes = dict[int, tuple[tuple[int, ...], ...]]
"""ROI id -> the cube numbers of each of its anchor regions: the anchors as plain numbers, for work on other cubes than the Sensorgram's."""


def anchor_cubes_by_roi(
    roi_ids: Sequence[int], cube_numbers: Sequence[int] | np.ndarray, statistics: Any, membership: StepMembership | None
) -> AnchorCubes | None:
    """The anchor regions the Sensorgram uses for these ROIs, as cube numbers (2026-11-10, for the ROI position tuning, which scans only some cubes and
    has to scan the anchors' cubes as well). `cube_numbers`: every cube of the dataset in file order, as the Sensorgram has them; `membership`: of the
    *whole* steps over those cubes (only read by the Range method). None when no curve is subtracted (`curve_subtracted`)."""
    if not curve_subtracted(statistics):
        return None
    numbers = np.asarray(cube_numbers, dtype=np.int64)
    if statistics.baseline_method == METHOD_ANCHORS:
        regions = regions_by_roi(roi_ids, numbers, statistics.baseline_anchor_points, statistics.baseline_point_halfwidth)
    else:
        shared = window_step_regions(membership, statistics) or ()
        regions = {int(r): shared for r in roi_ids}
    return {int(r): tuple(tuple(int(c) for c in numbers[mask]) for mask in masks) for r, masks in regions.items()}


def regions_from_cubes(cube_numbers: Sequence[int] | np.ndarray, anchor_cubes: AnchorCubes) -> dict[int, tuple[np.ndarray, ...]]:
    """`anchor_cubes_by_roi` back to one ``bool [cube]`` per region over the cubes `cube_numbers` (any subset of the dataset's, any order). The caller
    makes sure every anchor cube is among them: a region with a cube missing would average fewer cubes than the Sensorgram's."""
    numbers = np.asarray(cube_numbers, dtype=np.int64)
    return {int(r): tuple(np.isin(numbers, np.asarray(region, dtype=np.int64)) for region in regions) for r, regions in anchor_cubes.items()}


def cubes_needed(anchor_cubes: AnchorCubes | None) -> frozenset[int]:
    """Every cube any anchor region of `anchor_cubes` holds (empty for None)."""
    return frozenset(c for regions in (anchor_cubes or {}).values() for region in regions for c in region)
