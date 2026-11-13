"""From a stored scan to the Sensorgram's series (2026-10-23). Pure, no Qt.

Layer 3 over every cube: for each ROI the **metric** (the position of the fitted maximum or centroid, in nm) of each cube's spectrum. A peak
*shifting* is the measurement. Then Pillar II (``statistics.py``): spike rejection, smoothing and baseline, in that fixed order, **per ROI**;
and only then, for the average display modes, the combination of already-processed per-ROI traces (``statistics.aggregate_traces``).
Never the other way round: averaging spectra before fitting, or pooling pixels across ROIs, gives a different number (CLAUDE.md invariants).

Formula, fit, metric and statistics are never stored; this runs again when one of them changes, on a worker thread. The polynomial metric of a
ROI is computed for all cubes at once (``trace_fit.py``, ~5 ms per ROI; it was ~0.8 ms per cube and ROI, ~40 s for 160 ROIs x 314 cubes, one cube
at a time), the Gaussian fit is batched too (``gaussian_fit.py``, ~0.03 ms per cube; "classic" solver: ~1.9 ms). ``runner`` moves the fits into a helper process (``trace_pool.py``) so they never compete
with the window for the interpreter lock. The caller keeps the traces it has already computed (``known``) and computes only the missing ROIs,
checks ``cancelled`` between ROIs and reports ``progress`` per ROI.

A cube that was never scanned (its cells carry no hash), a cube with no usable wavelength and a fit that fails give NaN at that cube; nothing is
filled in. ``x`` (time in seconds or the cube number, one value per cube of the file) is supplied by the caller: this module does not know
the dataset.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

from ..applog.events import log_event
from ..progress import Cancelled
from .scan_store import StoredScan
from .scan_view import REFERENCE, SAMPLE, _primary_method
from .baseline_drift import METHOD_ANCHORS, METHOD_WINDOW, DriftFit, regions_by_roi
from .settings import MetricSettings, StatisticsSettings
from .statistics import aggregate_traces, apply_statistics_with_fit
from .step_stats import LineStepValues, StepMembership, line_step_values, step_statistics
from .query import formula_values
from .response_shape import ResponseCalibration, ResponseError, calibrate, readout
from .trace_fit import TraceInput, trace_from_input

logger = logging.getLogger(__name__)

TraceRunner = Callable[
    [list[tuple[int, TraceInput]], str, MetricSettings, Callable[[], bool] | None, Callable[[int, int], None] | None],
    tuple[dict[int, np.ndarray], bool],
]
"""Computes the traces of several ROIs somewhere else (a worker process, ``trace_pool.TracePool.run``): ``(inputs per ROI id, formula key, metric,
cancelled, progress)`` -> ``(traces finished, cancelled before the end)``."""

DISPLAY_INDIVIDUAL, DISPLAY_AVERAGE_ALL, DISPLAY_BY_GROUP = "individual", "average_all", "average_by_group"
UNGROUPED = "Ungrouped"


@dataclass(frozen=True)
class GroupSpec:
    name: str
    color: str
    roi_ids: tuple[int, ...]


@dataclass(frozen=True)
class SensorgramRequest:
    roi_ids: tuple[int, ...]
    formula_key: str
    metric: MetricSettings
    statistics: StatisticsSettings
    x: np.ndarray  # one value per cube of the file, in the file's cube order; NaN = no x for that cube
    groups: tuple[GroupSpec, ...] = ()  # for "average by group"; a selected ROI in none of them is "Ungrouped"
    roi_colors: Mapping[int, str] = field(default_factory=dict)
    segments: np.ndarray | None = None  # one label per cube: smooth each run of equal labels on its own (`step_stats.smoothing_segments`); None = the whole trace
    step_membership: StepMembership | None = None  # which cubes belong to which experiment step (`step_stats.assign_cubes`): each line then also gets its `step_values`
    cube_numbers: np.ndarray | None = None  # the cube number of each position of the trace arrays, for the drift baseline's anchor points; None = no points
    # the Range method's marked anchor steps as regions (`baseline_drift.step_regions`); None = no step marked (the window's mean); () = marked, none left
    baseline_step_regions: tuple[np.ndarray, ...] | None = None


@dataclass
class Series:
    key: str
    label: str
    color: str
    x: np.ndarray
    y: np.ndarray
    band_low: np.ndarray | None = None
    band_high: np.ndarray | None = None
    n_members: int = 1
    baseline: float = float("nan")  # the offset subtracted by the baseline correction (NaN: none)
    member_ids: tuple[int, ...] = ()  # the ROIs this line is made of (one for an individual line); the mask marks look them up
    step_values: LineStepValues | None = None  # mean +/- SD of this line in each experiment step (None: no steps were asked for)
    # The drift baseline (`baseline_drift`: method "anchors", or method "window" with marked anchor steps): the curve in the trace's own (absolute) units, what is or would be subtracted (an average:
    # the same combination of its members' curves as the line); None when there is none. `baseline_note`: why a member has no curve ("" = all fine).
    # `anchor_rms`: what is left at the anchors after correction (an average: the median of its members').
    baseline_curve: np.ndarray | None = None
    baseline_note: str = ""
    anchor_rms: float = float("nan")
    # The anchors the curve goes through (2026-11-09, drawn as hollow squares): x, the level of the trace there, and the curve's value there. An average
    # has them only when all its members have anchors at the same x (combined with the line's own centre); None otherwise.
    anchor_x: np.ndarray | None = None
    anchor_y: np.ndarray | None = None
    anchor_base: np.ndarray | None = None
    # The label without the number of ROIs ("Group A" for "Group A (n=12)", "Average" for "Average of 12 ROIs"); "" for a line that has no count in its label (one ROI).
    # The plot's legend can leave the count out (`view_settings.legend_text`); the label itself, used by the exports and the tooltips, always keeps it.
    short_label: str = ""


def response_groups(groups: Sequence[Sequence[int]]) -> tuple[tuple[int, ...], ...]:
    """The ROI groups as the response shape uses them (and as its cache key holds them): member ids, sorted, empty groups left out."""
    return tuple(tuple(sorted({int(r) for r in g})) for g in groups if len(g))


def trace_key(stored: StoredScan, formula_key: str, metric: MetricSettings, groups: Sequence[Sequence[int]] = ()) -> tuple:
    """What a metric trace depends on. A cached trace is reused only for an equal key (the stored scan is compared by identity: a
    reloaded file is a new object). `groups` (the session's ROI groups, member ids) matter only for the response shape, whose weights are learned per group."""
    return (
        id(stored), str(formula_key), metric.fit_method, int(metric.poly_order), metric.window_key(), metric.metric_key, metric.metric_range_key(),
        metric.template_key(), metric.response_key(), response_groups(groups) if metric.uses_response else (),
    )


def response_calibration(stored: StoredScan, formula_key: str, metric: MetricSettings, groups: Sequence[Sequence[int]] = ()) -> ResponseCalibration:
    """The response-shape calibration of every ROI of the scan (`response_shape.calibrate`). Raises `ResponseError` (message for the user)."""
    base_range, signal_range = metric.response_ranges()
    if signal_range is None:
        raise ResponseError("The response shape needs its signal cubes: set them in the Spectra panel (Fit menu).")
    cubes = np.asarray(stored.cube_indices, dtype=np.int64)
    values = _scan_values(stored, formula_key)
    wl = np.asarray(stored.wavelength_nm, dtype=np.float64)
    order = np.argsort(wl, kind="stable")
    base = (cubes >= base_range[0]) & (cubes <= base_range[1])
    signal = (cubes >= signal_range[0]) & (cubes <= signal_range[1])
    return calibrate(stored.roi_ids, wl[order], values[..., order], base, signal, response_groups(groups))


def _scan_values(stored: StoredScan, formula_key: str) -> np.ndarray:
    """Formula values of every (cube, ROI, wavelength) of the scan, NaN where not usable or never scanned (the rule of `trace_input`)."""
    mean = stored.values[_primary_method(stored)]
    sample, reference = mean[..., SAMPLE], mean[..., REFERENCE]
    usable = np.isfinite(sample) & np.isfinite(reference) & (stored.n_valid[..., SAMPLE] > 0) & (stored.n_valid[..., REFERENCE] > 0)
    usable &= (np.asarray(stored.cell_hash) != 0)[..., None]
    with np.errstate(all="ignore"):
        values = formula_values(sample, reference, formula_key)
    return np.where(usable, values, np.nan)


def response_traces(stored: StoredScan, formula_key: str, metric: MetricSettings, groups: Sequence[Sequence[int]] = ()) -> dict[int, np.ndarray]:
    """The response-shape trace of every ROI of the scan, ``{roi id: float64 [cube]}`` (first row of an id, as `trace_input`)."""
    calibration = response_calibration(stored, formula_key, metric, groups)
    wl = np.asarray(stored.wavelength_nm, dtype=np.float64)
    order = np.argsort(wl, kind="stable")
    traces = readout(calibration, _scan_values(stored, formula_key)[..., order])
    out: dict[int, np.ndarray] = {}
    for row, roi_id in enumerate(stored.roi_ids):
        out.setdefault(int(roi_id), traces[row])
    return out


def trace_input(stored: StoredScan, roi_id: int) -> TraceInput | None:
    """The arrays the metric of one ROI is computed from (None: the ROI is not in the file)."""
    if roi_id not in stored.roi_ids:
        return None
    row = stored.roi_ids.index(roi_id)
    mean = stored.values[_primary_method(stored)]
    sample, reference = mean[:, row, :, SAMPLE], mean[:, row, :, REFERENCE]
    usable = np.isfinite(sample) & np.isfinite(reference) & (stored.n_valid[:, row, :, SAMPLE] > 0) & (stored.n_valid[:, row, :, REFERENCE] > 0)
    return TraceInput(
        np.asarray(stored.wavelength_nm, dtype=np.float64), sample, reference, usable, stored.cell_hash[:, row] != 0,
        np.asarray(stored.cube_indices, dtype=np.int64),
    )


def metric_trace(stored: StoredScan, roi_id: int, formula_key: str, metric: MetricSettings) -> np.ndarray:
    """The metric wavelength (nm) of one ROI at every cube of the file: ``float64 [cube]``, NaN where there is none.

    All cubes at once (``trace_fit``); a fit that did not converge is a gap in the trace, not a stop."""
    data = trace_input(stored, roi_id)
    if data is None:
        return np.full(len(stored.cube_indices), np.nan)
    return trace_from_input(data, formula_key, metric)


def compute_traces(
    stored: StoredScan,
    roi_ids: Sequence[int],
    formula_key: str,
    metric: MetricSettings,
    known: Mapping[int, np.ndarray],
    *,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
    runner: TraceRunner | None = None,
    stages: dict[str, float] | None = None,
    groups: Sequence[Sequence[int]] = (),
) -> dict[int, np.ndarray]:
    """Compute the metric traces of the ROIs in ``roi_ids`` that are not in ``known`` and return only those (the caller adds them to its
    cache). ``cancelled`` is checked before each ROI; on cancel :class:`_PartialCancel` carries the traces finished so far. ``runner``: do the
    fits elsewhere (a worker process, so they never compete with the window for the interpreter lock); None = in this thread.
    ``stages`` (optional) receives ``gather_ms`` (the arrays of each ROI cut out of the stored scan) and ``fits_ms`` (the runner, or the in-thread loop)."""
    new: dict[int, np.ndarray] = {}
    missing = [r for r in roi_ids if r not in known]
    t0 = time.perf_counter()
    if metric.uses_response and missing:  # learned from every ROI of its group: all ROIs at once, here (no fit, milliseconds)
        every = response_traces(stored, formula_key, metric, groups)
        n_cube = len(stored.cube_indices)
        if stages is not None:
            stages["gather_ms"], stages["fits_ms"] = 0.0, (time.perf_counter() - t0) * 1e3
        return {r: every.get(int(r), np.full(n_cube, np.nan)) for r in missing}
    if runner is not None and missing:
        n_cube = len(stored.cube_indices)
        jobs: list[tuple[int, TraceInput]] = []
        for roi_id in missing:
            data = trace_input(stored, roi_id)
            if data is None:
                new[roi_id] = np.full(n_cube, np.nan)  # not in the file: no values, as metric_trace says
            else:
                jobs.append((roi_id, data))
        t1 = time.perf_counter()
        done, was_cancelled = runner(jobs, formula_key, metric, cancelled, progress)
        if stages is not None:
            stages["gather_ms"], stages["fits_ms"] = (t1 - t0) * 1e3, (time.perf_counter() - t1) * 1e3
        new.update(done)
        if was_cancelled:
            raise _PartialCancel(new)
        return new
    for done, roi_id in enumerate(missing):
        if cancelled is not None and cancelled():
            raise _PartialCancel(new)
        new[roi_id] = metric_trace(stored, roi_id, formula_key, metric)
        if progress is not None:
            progress(done + 1, len(missing))
    if stages is not None:
        stages["gather_ms"], stages["fits_ms"] = 0.0, (time.perf_counter() - t0) * 1e3  # no runner: the arrays are cut out inside metric_trace
    return new


class _PartialCancel(Cancelled):
    def __init__(self, partial: dict[int, np.ndarray]) -> None:
        super().__init__()
        self.partial = partial


def fit_missing_traces(
    stored: StoredScan,
    roi_ids: Sequence[int],
    formula_key: str,
    metric: MetricSettings,
    known: Mapping[int, np.ndarray],
    *,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
    runner: TraceRunner | None = None,
    groups: Sequence[Sequence[int]] = (),
) -> tuple[dict[int, np.ndarray], bool]:
    """`compute_traces` for a caller that wants what a cancelled run finished: ``(the traces computed now, whether the run was cancelled before the end)``. The finished
    traces are returned either way, so a caller can keep them for its next request (the Sensorgram does the same inside `compute_sensorgram`)."""
    try:
        return compute_traces(stored, roi_ids, formula_key, metric, known, cancelled=cancelled, progress=progress, runner=runner, groups=groups), False
    except _PartialCancel as partial:
        return partial.partial, True


def build_series(request: SensorgramRequest, traces: Mapping[int, np.ndarray], display_mode: str, center: str, band: str) -> list[Series]:
    """Statistics per ROI, then the display mode. ``traces`` must hold every ROI of the request.

    With a ``step_membership`` every line also carries its mean +/- SD in each experiment step (`Series.step_values`): of the line **as drawn** (the ROI's processed curve,
    or the centre line of an average), so the numbers follow the smoothing, the baseline and the spike removal exactly as the curve does (maintainer's choice, 2026-10-26;
    the spread *between* the ROIs of a group, which smoothing cannot change, is `step_stats.group_statistics`)."""
    series = _build_lines(request, traces, display_mode, center, band)
    if request.step_membership is not None and series:
        stats = step_statistics(list(range(len(series))), np.vstack([line.y for line in series]), request.step_membership)  # rows are the lines, not ROI ids
        for index, line in enumerate(series):
            line.step_values = line_step_values(stats, (index,), between_rois=False)
    return series


def anchor_regions_for(request: SensorgramRequest) -> dict[int, tuple[np.ndarray, ...]] | None:
    """Each ROI's anchor regions for the drift baseline, or None when there is no curve to fit (the window's mean applies, or nothing).

    - Method "window" ("Range"): the marked anchor steps (`SensorgramRequest.baseline_step_regions`), the same for every ROI; None when no step is marked.
    - Method "anchors": the ROI's anchor points (shared + its own); None when the request carries no cube numbers."""
    stats = request.statistics
    if stats.baseline_method == METHOD_WINDOW:  # "Range": the marked anchor steps, the same for every ROI; None = the window's mean
        regions = request.baseline_step_regions
        return None if regions is None else {int(r): tuple(regions) for r in request.roi_ids}
    if stats.baseline_method != METHOD_ANCHORS or request.cube_numbers is None:
        return None
    return regions_by_roi(request.roi_ids, request.cube_numbers, stats.baseline_anchor_points, stats.baseline_point_halfwidth)


def _build_lines(request: SensorgramRequest, traces: Mapping[int, np.ndarray], display_mode: str, center: str, band: str) -> list[Series]:
    x = np.asarray(request.x, dtype=np.float64)
    stats = request.statistics
    anchors = anchor_regions_for(request)
    processed: dict[int, tuple[np.ndarray, float]] = {}
    fits: dict[int, DriftFit] = {}
    for roi_id in request.roi_ids:
        trace, value, fit = apply_statistics_with_fit(x, traces[roi_id], stats, request.segments, None if anchors is None else anchors.get(roi_id))
        processed[roi_id] = (trace, value)
        if fit is not None:
            fits[roi_id] = fit
    lines = _combine(request, processed, display_mode, center, band)
    if fits:
        for line in lines:
            _attach_drift(line, [fits[r] for r in line.member_ids if r in fits], center)
    return lines


def _attach_drift(line: Series, member_fits: list[DriftFit], center: str) -> None:
    """The line's baseline curve, note and anchor RMS from its members' drift fits (combined as the line's own values are).

    The anchors are attached even when the fit could not be made (too few for the shape), so every placed point is drawn: a point that shows nothing
    until the second one is placed looks as if it was never added (maintainer, 2026-11-09). Without a fit there is no `anchor_base` and the trace is
    uncorrected, so the squares sit on the trace."""
    good = [f for f in member_fits if f.ok]
    failed = [f.message for f in member_fits if not f.ok]
    if failed:
        first = failed[0]
        line.baseline_note = first if len(member_fits) == 1 else f"{len(failed)} of {len(member_fits)} ROIs: {first}"
    first_x = member_fits[0].anchors_x if member_fits else np.empty(0)
    if member_fits and all(f.anchors_x.shape == first_x.shape and np.allclose(f.anchors_x, first_x) for f in member_fits):
        combine = np.median if str(center).strip().lower() == "median" else np.mean
        line.anchor_x = np.mean([f.anchors_x for f in member_fits], axis=0)
        line.anchor_y = combine([f.anchors_y for f in member_fits], axis=0)
        if len(good) == len(member_fits):  # so y - base is the centre of the members' residuals
            line.anchor_base = line.anchor_y - combine([f.anchors_y - f.anchors_base for f in good], axis=0)
    if not good:
        return
    if len(member_fits) == 1:
        line.baseline_curve, line.anchor_rms = good[0].curve, good[0].anchor_rms
        return
    line.baseline_curve = aggregate_traces({i: f.curve for i, f in enumerate(good)}, center=center, band="sd")[0]
    rms = [f.anchor_rms for f in good if np.isfinite(f.anchor_rms)]
    line.anchor_rms = float(np.median(rms)) if rms else float("nan")


def _combine(request: SensorgramRequest, processed: dict[int, tuple[np.ndarray, float]], display_mode: str, center: str, band: str) -> list[Series]:
    x = np.asarray(request.x, dtype=np.float64)
    if display_mode == DISPLAY_AVERAGE_ALL:
        groups = [("average", f"Average of {len(processed)} ROIs", "#e5e7eb", tuple(processed), "Average")]
    elif display_mode == DISPLAY_BY_GROUP:
        groups = []
        claimed: set[int] = set()
        for g in request.groups:
            members = tuple(r for r in g.roi_ids if r in processed and r not in claimed)
            claimed.update(members)
            if members:
                groups.append((f"group:{g.name}", f"{g.name} (n={len(members)})", g.color, members, g.name))
        rest = tuple(r for r in request.roi_ids if r not in claimed)
        if rest:
            groups.append(("group:ungrouped", f"{UNGROUPED} (n={len(rest)})", "#9ca3af", rest, UNGROUPED))
    else:
        return [
            Series(f"roi:{r}", f"ROI {r}", request.roi_colors.get(r, "#f59e0b"), x, processed[r][0], baseline=processed[r][1], member_ids=(r,))
            for r in request.roi_ids
        ]
    series: list[Series] = []
    for key, label, color, members, short_label in groups:
        centre, low, high = aggregate_traces({r: processed[r][0] for r in members}, center=center, band=band)
        baselines = [processed[r][1] for r in members if np.isfinite(processed[r][1])]
        series.append(Series(key, label, color, x, centre, low, high, n_members=len(members),
                             baseline=float(np.mean(baselines)) if baselines else float("nan"), member_ids=tuple(members), short_label=short_label))
    return series


def compute_sensorgram(
    stored: StoredScan,
    request: SensorgramRequest,
    known: Mapping[int, np.ndarray],
    *,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
    runner: TraceRunner | None = None,
) -> tuple[list[Series], dict[int, np.ndarray]]:
    """Everything the Sensorgram draws: ``(series, newly computed traces)``. If ``cancelled`` becomes true the finished traces are still
    returned (series empty) so the caller can keep them in its cache and start the next request from there."""
    stats = request.statistics
    stages: dict[str, float] = {}
    try:
        new = compute_traces(
            stored, request.roi_ids, request.formula_key, request.metric, known, cancelled=cancelled, progress=progress, runner=runner, stages=stages,
            groups=[g.roi_ids for g in request.groups],
        )
    except _PartialCancel as partial:
        return [], partial.partial
    traces = {**known, **new}
    t0 = time.perf_counter()
    series = build_series(request, traces, stats.sensorgram_display_mode, stats.sensorgram_aggregation, stats.sensorgram_band)
    log_event(
        logger, "sensorgram.stages", logging.DEBUG, rois=len(request.roi_ids), new=len(new),
        **{k: round(v, 1) for k, v in stages.items()}, series_ms=round((time.perf_counter() - t0) * 1e3, 1),
        runner="none" if runner is None else "pool",
    )
    return series, new
