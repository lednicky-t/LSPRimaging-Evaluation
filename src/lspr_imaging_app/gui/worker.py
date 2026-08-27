from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QObject, QRunnable, pyqtSignal
from PyQt6.QtWidgets import QGraphicsPathItem

from lspr_imaging_app.domain.models import AbsorbanceSpectrumResult, AnalysisState


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
    roi_absorbance_results: dict[int, AbsorbanceSpectrumResult] | None = None


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
    def __init__(self, fn, *args, supports_progress: bool = False, supports_partial: bool = False, **kwargs) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._supports_progress = supports_progress
        self._supports_partial = supports_partial
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            kwargs = dict(self._kwargs)
            if self._supports_progress:
                kwargs["progress_callback"] = self.signals.progress.emit
            if self._supports_partial:
                kwargs["partial_callback"] = self.signals.partial.emit
            result = self._fn(*self._args, **kwargs)
        except Exception as exc:  # pragma: no cover - worker thread error path
            self.signals.error.emit(str(exc))
            return
        self.signals.result.emit(result)
