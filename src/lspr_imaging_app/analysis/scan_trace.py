"""From a stored scan to the Sensorgram's series (2026-10-23). Pure, no Qt.

Layer 3 over every cube: for each ROI the **metric** (the position of the fitted maximum or centroid, in nm) of each cube's spectrum. A peak
*shifting* is the measurement. Then Pillar II (``statistics.py``): spike rejection, smoothing and baseline, in that fixed order, **per ROI**;
and only then, for the average display modes, the combination of already-processed per-ROI traces (``statistics.aggregate_traces``).
Never the other way round: averaging spectra before fitting, or pooling pixels across ROIs, gives a different number (CLAUDE.md invariants).

Formula, fit, metric and statistics are never stored; this runs again when one of them changes, on a worker thread. A metric trace costs about
0.8 ms per ROI and cube (a polynomial fit), so 160 ROIs x 314 cubes is ~40 s: the caller keeps the traces it has already computed (``known``)
and computes only the missing ROIs, checks ``cancelled`` between ROIs and reports ``progress`` per ROI.

A cube that was never scanned (its cells carry no hash), a cube with no usable wavelength and a fit that fails give NaN at that cube; nothing is
filled in. ``x`` (time in seconds or the cube number, one value per cube of the file) is supplied by the caller: this module does not know
the dataset.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

from ..progress import Cancelled
from .query import formula_spectrum
from .scan_store import StoredScan
from .scan_view import REFERENCE, SAMPLE, _primary_method, fit_and_metric
from .settings import MetricSettings, StatisticsSettings
from .statistics import aggregate_traces, apply_statistics

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


def trace_key(stored: StoredScan, formula_key: str, metric: MetricSettings) -> tuple:
    """What a metric trace depends on. A cached trace is reused only for an equal key (the stored scan is compared by identity: a
    reloaded file is a new object)."""
    return (id(stored), str(formula_key), metric.fit_method, int(metric.poly_order), metric.fit_wl_min, metric.fit_wl_max, metric.metric_key)


def metric_trace(stored: StoredScan, roi_id: int, formula_key: str, metric: MetricSettings) -> np.ndarray:
    """The metric wavelength (nm) of one ROI at every cube of the file: ``float64 [cube]``, NaN where there is none."""
    n_cube = len(stored.cube_indices)
    out = np.full(n_cube, np.nan)
    if roi_id not in stored.roi_ids:
        return out
    row = stored.roi_ids.index(roi_id)
    mean = stored.values[_primary_method(stored)]
    wavelengths = np.asarray(stored.wavelength_nm, dtype=np.float64)
    sample_all, reference_all = mean[:, row, :, SAMPLE], mean[:, row, :, REFERENCE]
    usable_all = (
        np.isfinite(sample_all) & np.isfinite(reference_all) & (stored.n_valid[:, row, :, SAMPLE] > 0) & (stored.n_valid[:, row, :, REFERENCE] > 0)
    )
    for cube in range(n_cube):
        usable = usable_all[cube]
        if not stored.cell_hash[cube, row] or not usable.any():
            continue
        spectrum = formula_spectrum(wavelengths[usable], sample_all[cube][usable], reference_all[cube][usable], formula_key)
        try:
            _fit, x, _y = fit_and_metric(spectrum, metric)
        except (RuntimeError, ValueError, FloatingPointError, np.linalg.LinAlgError):
            continue  # a fit that did not converge is a gap in the trace, not a stop
        if x is not None and np.isfinite(x):
            out[cube] = x
    return out


def compute_traces(
    stored: StoredScan,
    roi_ids: Sequence[int],
    formula_key: str,
    metric: MetricSettings,
    known: Mapping[int, np.ndarray],
    *,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict[int, np.ndarray]:
    """Compute the metric traces of the ROIs in ``roi_ids`` that are not in ``known`` and return only those (the caller adds them to its
    cache). ``cancelled`` is checked before each ROI; on cancel :class:`_PartialCancel` carries the traces finished so far."""
    new: dict[int, np.ndarray] = {}
    missing = [r for r in roi_ids if r not in known]
    for done, roi_id in enumerate(missing):
        if cancelled is not None and cancelled():
            raise _PartialCancel(new)
        new[roi_id] = metric_trace(stored, roi_id, formula_key, metric)
        if progress is not None:
            progress(done + 1, len(missing))
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
            Series(f"roi:{r}", f"ROI {r}", request.roi_colors.get(r, "#f59e0b"), x, processed[r][0], baseline=processed[r][1])
            for r in request.roi_ids
        ]
    series: list[Series] = []
    for key, label, color, members in groups:
        centre, low, high = aggregate_traces({r: processed[r][0] for r in members}, center=center, band=band)
        baselines = [processed[r][1] for r in members if np.isfinite(processed[r][1])]
        series.append(Series(key, label, color, x, centre, low, high, n_members=len(members),
                             baseline=float(np.mean(baselines)) if baselines else float("nan")))
    return series


def compute_sensorgram(
    stored: StoredScan,
    request: SensorgramRequest,
    known: Mapping[int, np.ndarray],
    *,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[list[Series], dict[int, np.ndarray]]:
    """Everything the Sensorgram draws: ``(series, newly computed traces)``. If ``cancelled`` becomes true the finished traces are still
    returned (series empty) so the caller can keep them in its cache and start the next request from there."""
    stats = request.statistics
    try:
        new = compute_traces(stored, request.roi_ids, request.formula_key, request.metric, known, cancelled=cancelled, progress=progress)
    except _PartialCancel as partial:
        return [], partial.partial
    traces = {**known, **new}
    return build_series(request, traces, stats.sensorgram_display_mode, stats.sensorgram_aggregation, stats.sensorgram_band), new
