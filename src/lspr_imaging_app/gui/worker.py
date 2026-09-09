from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QObject, QRunnable, pyqtSignal
from PyQt6.QtWidgets import QGraphicsPathItem

from lspr_imaging_app.domain.models import FormulaSpectrumResult, AnalysisState


class WorkflowLogBridge(QObject):
    record_received = pyqtSignal(int, str)
    records_flushed = pyqtSignal(int, list)


class WorkflowLogHandler(logging.Handler):
    def __init__(self, bridge: WorkflowLogBridge) -> None:
        super().__init__()
        self._bridge = bridge

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            self.handleError(record)
            return
        self._bridge.record_received.emit(int(record.levelno), message)


@dataclass(slots=True)
class RoiOverlayBundle:
    curve: pg.PlotCurveItem
    reference_fill: QGraphicsPathItem | None = None
    inner_curve: pg.PlotCurveItem | None = None
    outer_curve: pg.PlotCurveItem | None = None
    label: pg.TextItem | None = None


@dataclass(slots=True)
class GuideOverlayBundle:
    vertical: pg.PlotCurveItem
    horizontal: pg.PlotCurveItem
    marker: pg.TargetItem


@dataclass(slots=True)
class MeasurementOverlayBundle:
    connector: pg.PlotCurveItem
    marker_a: pg.TargetItem
    marker_b: pg.TargetItem
    label: pg.TextItem


@dataclass(slots=True)
class ScaleBarOverlayBundle:
    dark_outline_line: pg.PlotCurveItem
    outline_line: pg.PlotCurveItem
    line: pg.PlotCurveItem
    dark_outline_left_tick: pg.PlotCurveItem
    outline_left_tick: pg.PlotCurveItem
    left_tick: pg.PlotCurveItem
    dark_outline_right_tick: pg.PlotCurveItem
    outline_right_tick: pg.PlotCurveItem
    right_tick: pg.PlotCurveItem
    label: pg.TextItem


@dataclass(slots=True)
class LandmarkOverlayBundle:
    curve: pg.PlotCurveItem
    label: pg.TextItem


@dataclass(slots=True)
class ChromaticLandmarkAllOverlayBundle:
    points: pg.ScatterPlotItem
    active_cross: pg.PlotCurveItem | None
    label: pg.TextItem


@dataclass(slots=True)
class UndoSnapshot:
    label: str
    state: AnalysisState
    folder_text: str
    spectral_cube_slider_value: int
    wavelength_slider_value: int
    selected_roi_ids: set[int]
    sample_visual_color: str
    reference_visual_color: str
    mask_visual_color: str
    histogram_mask_visual_color: str
    figure_mask_visual_color: str
    highlight_visual_color: str
    roi_alpha: float
    reference_alpha: float
    mask_alpha: float
    histogram_mask_alpha: float
    figure_mask_alpha: float
    highlight_alpha: float
    rois_visible: bool
    reference_rois_visible: bool
    mask_visible: bool
    reference_points_visible: bool
    histogram_mask_visible: bool
    figure_mask_visible: bool
    highlight_visible: bool
    file_mask: np.ndarray | None
    file_mask_path: str | None
    file_mask_revision: int
    file_mask_wavelength_diffs: dict[tuple[int, float], dict[tuple[int, int], bool]]


@dataclass(slots=True)
class SensorgramPointResult:
    spectral_cube_index: int
    metric_value: float | None
    metric_signal: float | None
    # Populated only when this point's full spectrum was actually computed
    # this call (a full cache/disk miss) - None when a faster metric-only
    # shortcut supplied the point instead (see _sensorgram_metric_task),
    # since those never have a full spectrum to hand back. This is how the
    # per-cube spectrum crosses from the sensorgram worker thread to the
    # main thread (on_sensorgram_partial_result) for backup + optional live
    # display, over the same thread-safe partial-result Qt signal the metric
    # value already used.
    roi_formula_spectrum_results: dict[int, FormulaSpectrumResult] | None = None
    # Each selected ROI's own (metric_value, metric_signal) - fit from that
    # ROI's own spectrum in roi_formula_spectrum_results above, under the
    # same fit method/metric/poly order as the combined metric_value on this
    # same point - not a per-ROI copy of the combined (pooled-selection)
    # value. Same conditional-population rule as roi_formula_spectrum_
    # results: None whenever that's None, since there's no per-ROI spectrum
    # to fit from on a metric-only shortcut hit either.
    per_roi_metric_values: dict[int, tuple[float, float]] | None = None


@dataclass(slots=True)
class SensorgramComputationResult:
    spectral_cube_indices: np.ndarray
    metric_values: np.ndarray
    metric_signal: np.ndarray
    completed_count: int
    total_count: int
    prep_seconds: float = 0.0
    fit_seconds: float = 0.0
    total_seconds: float = 0.0
    cancelled: bool = False


class WorkerSignals(QObject):
    result = pyqtSignal(object)
    error = pyqtSignal(str)
    progress = pyqtSignal(int, str)
    partial = pyqtSignal(object)


class FunctionWorker(QRunnable):
    """Runs `fn` off the GUI thread, reporting back via `.signals` (a
    QObject; Qt safely queues a cross-thread `.emit()` onto the receiver's
    thread via AutoConnection regardless of what kind of thread emitted it).

    Call `.start()` (spawns a plain `threading.Thread`) for virtually every
    use of this class - NOT `QThreadPool.start(worker)`, despite this still
    being a QRunnable. See qthreadpool_zarr_crash_investigation.md:
    stress-testing found that a QThreadPool worker thread that ever becomes
    part of a wait/read chain touching a zarr array (this app's own dataset
    reads, directly or via blocking on a future for one) reliably crashes
    the whole process with a native memory-corruption error
    (STATUS_HEAP_CORRUPTION / STATUS_STACK_BUFFER_OVERRUN, both reproduced) -
    independent of the `zarrs` codec pipeline, and independent of any second
    thread pool being involved. A plain `threading.Thread` running the
    identical zarr-reading workload, under the same stress, never crashed.
    Since nearly every use of this class in this app reads from the dataset
    somewhere (directly or via a helper it calls), and QThreadPool worker
    threads are reused across unrelated submissions, there was no safe
    subset of call sites to leave on QThreadPool - see the investigation doc
    for the full isolation matrix.

    Still a QRunnable only so `window._measurement_backup_flush_pool` (HDF5
    writes only, never zarr - not implicated by the crash above) can keep
    dispatching it through `QThreadPool.start(worker)` instead, for that
    pool's own real reason: max_thread_count=1 there serializes flushes
    against each other, a guarantee `.start()`'s unordered raw threads don't
    provide. That is the ONLY call site that should still use
    `QThreadPool.start(worker)` - every other caller should use `.start()`.

    Unlike QThreadPool, `.start()` has no built-in concurrency cap - each
    call spawns a new OS thread immediately rather than queuing behind a
    fixed pool size. Not a concern for this app's actual usage (nothing
    dispatches more than a handful of these concurrently), but a real
    behavior difference from the QThreadPool version if that ever changes.
    """

    _active_lock = threading.Lock()
    _active_threads: set[threading.Thread] = set()

    def __init__(self, fn, *args, supports_progress: bool = False, supports_partial: bool = False, **kwargs) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._supports_progress = supports_progress
        self._supports_partial = supports_partial
        self.signals = WorkerSignals()

    def start(self) -> None:
        thread = threading.Thread(target=self._run_tracked, daemon=True)
        with FunctionWorker._active_lock:
            FunctionWorker._active_threads.add(thread)
        thread.start()

    def _run_tracked(self) -> None:
        try:
            self.run()
        finally:
            with FunctionWorker._active_lock:
                FunctionWorker._active_threads.discard(threading.current_thread())

    def run(self) -> None:
        try:
            kwargs = dict(self._kwargs)
            if self._supports_progress:
                kwargs["progress_callback"] = self.signals.progress.emit
            if self._supports_partial:
                kwargs["partial_callback"] = self.signals.partial.emit
            result = self._fn(*self._args, **kwargs)
        except Exception as exc:  # pragma: no cover - worker thread error path
            logging.getLogger(__name__).exception("FunctionWorker task failed: %s", self._fn)
            self.signals.error.emit(str(exc))
            return
        self.signals.result.emit(result)

    @classmethod
    def active_count(cls) -> int:
        with cls._active_lock:
            return len(cls._active_threads)

    @classmethod
    def wait_for_all(cls, timeout_ms: int) -> bool:
        """Join every currently-tracked worker thread, bounded so the total
        wait across all of them never exceeds timeout_ms - mirrors
        QThreadPool.waitForDone(timeout_ms)'s contract. Returns True if every
        tracked thread finished within the budget."""
        deadline = time.monotonic() + timeout_ms / 1000.0
        with cls._active_lock:
            threads = list(cls._active_threads)
        for thread in threads:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(timeout=remaining)
        return cls.active_count() == 0
