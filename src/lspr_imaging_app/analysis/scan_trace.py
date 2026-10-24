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

from ..applog import log_event
from ..progress import Cancelled
from .scan_store import StoredScan
from .scan_view import REFERENCE, SAMPLE, _primary_method
from .settings import MetricSettings, StatisticsSettings
from .statistics import aggregate_traces, apply_statistics
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


def trace_key(stored: StoredScan, formula_key: str, metric: MetricSettings) -> tuple:
    """What a metric trace depends on. A cached trace is reused only for an equal key (the stored scan is compared by identity: a
    reloaded file is a new object)."""
    return (
        id(stored), str(formula_key), metric.fit_method, int(metric.poly_order), metric.fit_wl_min, metric.fit_wl_max, metric.metric_key,
    )


def trace_input(stored: StoredScan, roi_id: int) -> TraceInput | None:
    """The arrays the metric of one ROI is computed from (None: the ROI is not in the file)."""
    if roi_id not in stored.roi_ids:
        return None
    row = stored.roi_ids.index(roi_id)
    mean = stored.values[_primary_method(stored)]
    sample, reference = mean[:, row, :, SAMPLE], mean[:, row, :, REFERENCE]
    usable = np.isfinite(sample) & np.isfinite(reference) & (stored.n_valid[:, row, :, SAMPLE] > 0) & (stored.n_valid[:, row, :, REFERENCE] > 0)
    return TraceInput(np.asarray(stored.wavelength_nm, dtype=np.float64), sample, reference, usable, stored.cell_hash[:, row] != 0)


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
) -> dict[int, np.ndarray]:
    """Compute the metric traces of the ROIs in ``roi_ids`` that are not in ``known`` and return only those (the caller adds them to its
    cache). ``cancelled`` is checked before each ROI; on cancel :class:`_PartialCancel` carries the traces finished so far. ``runner``: do the
    fits elsewhere (a worker process, so they never compete with the window for the interpreter lock); None = in this thread.
    ``stages`` (optional) receives ``gather_ms`` (the arrays of each ROI cut out of the stored scan) and ``fits_ms`` (the runner, or the in-thread loop)."""
    new: dict[int, np.ndarray] = {}
    missing = [r for r in roi_ids if r not in known]
    t0 = time.perf_counter()
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


def build_series(request: SensorgramRequest, traces: Mapping[int, np.ndarray], display_mode: str, center: str, band: str) -> list[Series]:
    """Statistics per ROI, then the display mode. ``traces`` must hold every ROI of the request."""
    x = np.asarray(request.x, dtype=np.float64)
    processed: dict[int, tuple[np.ndarray, float]] = {}
    for roi_id in request.roi_ids:
        processed[roi_id] = apply_statistics(x, traces[roi_id], request.statistics)
    if display_mode == DISPLAY_AVERAGE_ALL:
        groups = [("average", f"Average of {len(processed)} ROIs", "#e5e7eb", tuple(processed))]
    elif display_mode == DISPLAY_BY_GROUP:
        groups = []
        claimed: set[int] = set()
        for g in request.groups:
            members = tuple(r for r in g.roi_ids if r in processed and r not in claimed)
            claimed.update(members)
            if members:
                groups.append((f"group:{g.name}", f"{g.name} (n={len(members)})", g.color, members))
        rest = tuple(r for r in request.roi_ids if r not in claimed)
        if rest:
            groups.append(("group:ungrouped", f"{UNGROUPED} (n={len(rest)})", "#9ca3af", rest))
    else:
        return [
            Series(f"roi:{r}", f"ROI {r}", request.roi_colors.get(r, "#f59e0b"), x, processed[r][0], baseline=processed[r][1], member_ids=(r,))
            for r in request.roi_ids
        ]
    series: list[Series] = []
    for key, label, color, members in groups:
        centre, low, high = aggregate_traces({r: processed[r][0] for r in members}, center=center, band=band)
        baselines = [processed[r][1] for r in members if np.isfinite(processed[r][1])]
        series.append(Series(key, label, color, x, centre, low, high, n_members=len(members),
                             baseline=float(np.mean(baselines)) if baselines else float("nan"), member_ids=tuple(members)))
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
            stored, request.roi_ids, request.formula_key, request.metric, known, cancelled=cancelled, progress=progress, runner=runner, stages=stages
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
