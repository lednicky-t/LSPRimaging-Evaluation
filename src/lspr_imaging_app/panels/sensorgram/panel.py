"""``SensorgramPanel`` (sketch §7 "Display panels" > Sensorgram, §10): the metric of the selected ROIs against time.

**Screen.** A ribbon of compact controls on top (display mode, centre and band for averages, smoothing, spike removal, baseline) in the look of
the Image panel's ribbon (captioned groups, `ribbon_row.py`): a double-click on its empty space rolls it up to a "..." handle, and a panel
narrower than the row scrolls it with an arrow at each edge. The plot fills the rest and one status line sits below it. For the ROIs selected in the ROI table or on the image it plots, at every scanned cube, the **metric** of the
Spectra panel (the position of the fitted maximum or centroid, in nm: a peak *shifting* is the measurement). The x axis is time in seconds or the
cube number, following the app-wide Cube / Time choice. A dashed line marks the current cube; clicking the plot moves the cube slider there.

**Where the numbers come from.** ``analysis/scan.h5`` through :class:`~...analysis.scan_results.ScanResultsModule`; nothing here starts an
analysis. ``analysis/scan_trace.py`` fits every cube of every selected ROI (~0.8 ms each) and then applies the statistics of
``AnalysisSettingsModule`` **per ROI** (spike removal, smoothing, baseline, in that order) and only then, for "Average all" / "Average by group",
combines the finished per-ROI traces (mean or median, band = SD or SEM). Averaging fitted values, never spectra (CLAUDE.md). Points of cubes
whose results are out of date are marked with a hollow ring, never hidden.

**Threads and pacing.** All of that runs on a plain worker thread, never the GUI thread. The traces already computed are kept (a trace depends on the
formula, fit settings and metric only, so a statistics change or another ROI selection reuses them); a new request cancels a long one between ROIs
and keeps what it finished. Selecting all 160 ROIs costs ~40 s once, with a progress line. 100 ms coalescing, newest request wins, a result from an
older file is dropped, and nothing happens while the dock is hidden (``HiddenPanelGate``).

**Status bar.** A computation that lasts more than a moment (``panel_task.PanelTask``) appears in the app's task indicator ("Sensorgram: fitting
ROI 3 of 160"), with a Cancel button that stops it between ROIs and clears the plot (the traces already fitted are kept for the next request); a
quick one leaves no trace.

**State.** The statistics settings live in ``AnalysisSettingsModule`` (saved with the session); this panel edits them through it. The x unit of the
baseline window follows the axis, so switching Cube / Time re-places the window at its default (first tenth of the axis).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace

import numpy as np
from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication, QCheckBox, QComboBox, QHBoxLayout, QLabel, QSpinBox, QVBoxLayout, QWidget

from lspr_ui import get_active_theme

from ...analysis.live_state import AnalysisLiveModule
from ...analysis.scan_results import ScanResultsModule
from ...analysis.scan_store import StoredScan
from ...analysis.scan_trace import (
    DISPLAY_AVERAGE_ALL,
    DISPLAY_BY_GROUP,
    DISPLAY_INDIVIDUAL,
    GroupSpec,
    SensorgramRequest,
    Series,
    compute_sensorgram,
    trace_key,
)
from ...analysis.settings import (
    SENSORGRAM_AGGREGATIONS,
    SENSORGRAM_BANDS,
    SENSORGRAM_DISPLAY_MODES,
    SMOOTHING_METHODS,
)
from ...analysis.settings_module import AnalysisSettingsModule
from ...applog import timed
from ...dataset import DatasetModule
from ...roi import RoiToolbox
from ...selection import CubeDisplayModule, SelectionModule
from ...selection.cube_display_module import MODE_TIME
from ...storage.ui_state_keys import SENSORGRAM_RIBBON_OPEN, read
from ..live_toggle import LiveToggle
from ..ribbon_fields import style_ribbon_check, style_ribbon_combo, style_ribbon_number_field
from ..ribbon_row import RibbonRow, rolled_ribbon
from ..image_first import ImageFirst
from ..panel_task import PanelTask
from ..ui_state import UiStateStore
from ..visibility_gate import HiddenPanelGate
from .plot import SensorgramPlot

logger = logging.getLogger(__name__)

TASK_ID = "sensorgram.compute"
_REDRAW_COALESCE_MS = 100  # sketch §8
_JOIN_SECONDS = 5.0
_PROGRESS_MIN_ROIS = 8  # a status line "Fitting ROI 3 of 160" only for runs long enough to need it
DISPLAY_LABELS = {DISPLAY_INDIVIDUAL: "Individual", DISPLAY_AVERAGE_ALL: "Average all", DISPLAY_BY_GROUP: "Average by group"}
CENTER_LABELS = {"mean": "Mean", "median": "Median"}
BAND_LABELS = {"sd": "SD band", "sem": "SEM band"}
SMOOTHING_LABELS = {"none": "No smoothing", "savgol": "Savitzky-Golay", "moving_average": "Moving average"}
_RELEVANT_ROI_CHANGES = {"detection_settings", "session_restored", "added", "deleted", "detected"}  # a drag changes no sensorgram point
_FALLBACK_COLORS = ("#f59e0b", "#38bdf8", "#34d399", "#f472b6", "#a78bfa", "#fb7185", "#facc15", "#22d3ee", "#a3e635")


@dataclass(frozen=True)
class _Job:
    generation: int  # a job from before a clear (dataset or scan changed) is dropped
    stored: StoredScan
    request: SensorgramRequest
    key: tuple  # trace_key: which cached traces this job may reuse and add to
    cancel: threading.Event


class SensorgramPanel(QWidget):
    """Plots the metric of the selected ROIs against time from the stored scan."""

    _result_ready = pyqtSignal(object, object, object, object)  # (job, series | None, new traces, error | None) - from the worker thread
    _progress = pyqtSignal(int, int)  # (ROIs done, ROIs to do) - from the worker thread

    def __init__(
        self,
        scan_results: ScanResultsModule,
        roi_toolbox: RoiToolbox,
        dataset: DatasetModule,
        selection: SelectionModule,
        analysis_settings: AnalysisSettingsModule,
        cube_display: CubeDisplayModule,
        parent: QWidget | None = None,
        image_busy: Callable[[], bool] | None = None,
        live: AnalysisLiveModule | None = None,
    ) -> None:
        """`image_busy`: True while the Image panel has a frame in flight; a computation then waits for it (`ImageFirst`)."""
        super().__init__(parent)
        self._live = live if live is not None else AnalysisLiveModule(self)  # without an owner: always live
        self._dirty_while_paused = False
        self._image_first = ImageFirst(image_busy)
        self._scan = scan_results
        self._roi_toolbox = roi_toolbox
        self._dataset = dataset
        self._selection = selection
        self._settings = analysis_settings
        self._cube_display = cube_display
        self._generation = 0
        self._job: _Job | None = None
        self._pending = False
        self._thread: threading.Thread | None = None
        self._cache_key: tuple | None = None
        self._cache: dict[int, np.ndarray] = {}
        self._x_cache: tuple[tuple, np.ndarray] | None = None
        self._last: tuple[_Job, list[Series]] | None = None
        self._syncing = False

        self._display = QComboBox(self)
        for key in SENSORGRAM_DISPLAY_MODES:
            self._display.addItem(DISPLAY_LABELS.get(key, key), key)
        style_ribbon_combo(self._display)
        self._display.setToolTip(
            "Individual: one line per selected ROI. Average all / by group: each ROI is processed alone first, then the finished traces "
            "are combined (never the spectra)."
        )
        self._center = QComboBox(self)
        for key in SENSORGRAM_AGGREGATIONS:
            self._center.addItem(CENTER_LABELS.get(key, key), key)
        style_ribbon_combo(self._center)
        self._center.setToolTip("How the ROIs of an average are combined at each point.")
        self._band = QComboBox(self)
        for key in SENSORGRAM_BANDS:
            self._band.addItem(BAND_LABELS.get(key, key), key)
        style_ribbon_combo(self._band)
        self._band.setToolTip("Shaded band around an average: standard deviation of the ROIs, or standard error of the mean (SD / sqrt(n)).")
        self._smoothing = QComboBox(self)
        for key in SMOOTHING_METHODS:
            self._smoothing.addItem(SMOOTHING_LABELS.get(key, key), key)
        style_ribbon_combo(self._smoothing)
        self._smoothing.setToolTip("Smoothing of each ROI's trace, after spike removal.")
        self._window = QSpinBox(self)
        self._window.setRange(3, 301)
        self._window.setSingleStep(2)
        self._window.setPrefix("window ")
        style_ribbon_number_field(self._window, "window 301")
        self._window.setToolTip("Smoothing window in points (made odd, never longer than the trace).")
        self._spikes = QCheckBox("Remove", self)
        self._spikes.setToolTip("Replace single-point outliers (Hampel filter: 3.5 scaled MADs from the rolling median of 5 points) before smoothing.")
        self._baseline = QCheckBox("Subtract", self)
        self._baseline.setToolTip("Subtract the mean of each trace inside the shaded band, so every trace reads as a shift from it. Drag the band.")
        style_ribbon_check(self._spikes)
        style_ribbon_check(self._baseline)
        self._status = QLabel("", self)
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f"color: {get_active_theme().text_dim};")
        self._plot = SensorgramPlot(self)
        self.task = PanelTask(TASK_ID, "Sensorgram", self)  # connected to the status bar's task indicator by app_rewrite

        self._live_toggle = LiveToggle(self)
        self._live_toggle.bind(self._live)
        self._ribbon = RibbonRow(self)
        self._ribbon.add_group("Live", self._live_toggle)
        self._ribbon.add_group("Display", self._display)
        self._ribbon.add_group("Average", self._center, self._band)
        self._ribbon.add_group("Smoothing", self._smoothing, self._window)
        self._ribbon.add_group("Spikes", self._spikes)
        self._ribbon.add_group("Baseline", self._baseline)
        self._ribbon.add_stretch()
        self._ribbon_drawer = rolled_ribbon(self._ribbon, "Show the sensorgram controls.", self)
        controls = QHBoxLayout()
        controls.setContentsMargins(4, 2, 6, 2)
        controls.addWidget(self._ribbon_drawer)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addLayout(controls)
        layout.addWidget(self._plot, 1)
        self._status.hide()  # the status row is not shown (maintainer, 2026-10-23); the label stays so the panel can still keep its message text

        self._gate = HiddenPanelGate(on_shown=self._schedule_redraw)
        self._redraw_timer = QTimer(self)
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.setInterval(_REDRAW_COALESCE_MS)
        self._redraw_timer.timeout.connect(self._redraw)

        self._result_ready.connect(self._on_result)
        self._live.live_changed.connect(self._on_live_changed)
        self._progress.connect(self._on_progress)
        for combo in (self._display, self._center, self._band, self._smoothing):
            combo.currentIndexChanged.connect(self._on_controls_changed)
        self._window.valueChanged.connect(self._on_controls_changed)
        self._spikes.toggled.connect(self._on_controls_changed)
        self._baseline.toggled.connect(self._on_baseline_toggled)
        self._plot.baseline_window_changed.connect(self._on_baseline_dragged)
        self._plot.x_clicked.connect(self._on_plot_clicked)

        self._selection.roi_selection_changed.connect(lambda _ids: self._schedule_redraw())
        self._selection.cube_changed.connect(lambda _cube: self._update_cursor())
        self._settings.settings_changed.connect(lambda _change: self._on_settings_changed())
        self._roi_toolbox.geometry_changed.connect(self._on_roi_changed)
        self._roi_toolbox.cosmetic_changed.connect(lambda _change: self._schedule_redraw())
        self._cube_display.mode_changed.connect(lambda _mode: self._on_axis_changed())
        self._scan.results_changed.connect(self._on_results_changed)
        self._scan.staleness_changed.connect(self._draw_last)

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)
        self._sync_controls()
        self._schedule_redraw()

    # -- wiring with the owner -------------------------------------------------------------------------------------------------

    def restore_ui_state(self, store: UiStateStore) -> None:
        self._ribbon_drawer.set_expanded(read(store, SENSORGRAM_RIBBON_OPEN) is not False, animate=False)
        self._ribbon_drawer.expanded_changed.connect(lambda open_: store.set(SENSORGRAM_RIBBON_OPEN.key, bool(open_)))

    def set_panel_visible(self, visible: bool) -> None:
        """Connected to the dock's ``visibilityChanged``: a hidden panel does no work and catches up once when shown."""
        self._gate.set_visible(visible)

    def refresh_theme(self) -> None:
        self._live_toggle.refresh_theme()
        self._ribbon.refresh_theme()
        self._ribbon_drawer.refresh_theme()
        self._plot.refresh_theme()
        self._status.setStyleSheet(f"color: {get_active_theme().text_dim};")

    def shutdown(self) -> None:
        """Application is quitting: stop a running computation and wait briefly, so it never emits into a widget Qt is tearing down."""
        self._generation += 1
        if self._job is not None:
            self._job.cancel.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=_JOIN_SECONDS)

    # -- controls <-> the statistics settings ---------------------------------------------------------------------------------

    def _sync_controls(self) -> None:
        stats = self._settings.statistics_settings()
        self._syncing = True
        try:
            self._display.setCurrentIndex(max(self._display.findData(stats.sensorgram_display_mode), 0))
            self._center.setCurrentIndex(max(self._center.findData(stats.sensorgram_aggregation), 0))
            self._band.setCurrentIndex(max(self._band.findData(stats.sensorgram_band), 0))
            averaged = stats.sensorgram_display_mode != DISPLAY_INDIVIDUAL
            self._center.setEnabled(averaged)
            self._band.setEnabled(averaged)
            self._smoothing.setCurrentIndex(max(self._smoothing.findData(stats.smoothing_method), 0))
            self._window.setValue(int(stats.smoothing_window))
            self._window.setEnabled(stats.smoothing_method != "none")
            self._spikes.setChecked(bool(stats.spike_rejection_enabled))
            self._baseline.setChecked(bool(stats.baseline_enabled))
        finally:
            self._syncing = False

    def _on_controls_changed(self, *_args: object) -> None:
        if self._syncing:
            return
        self._settings.set_statistics_settings(replace(
            self._settings.statistics_settings(),
            sensorgram_display_mode=self._display.currentData(), sensorgram_aggregation=self._center.currentData(),
            sensorgram_band=self._band.currentData(), smoothing_method=self._smoothing.currentData(),
            smoothing_window=self._window.value(), spike_rejection_enabled=self._spikes.isChecked(),
        ))

    def _default_baseline_window(self) -> tuple[float, float]:
        x = self._x_axis()[1]
        finite = x[np.isfinite(x)]
        if finite.size < 2:
            low, high = self._plot.x_range()
        else:
            low, high = float(finite.min()), float(finite.max())
        return low, low + 0.1 * (high - low)

    def _on_baseline_toggled(self, on: bool) -> None:
        if self._syncing:
            return
        current = self._settings.statistics_settings()
        if on:
            window = (current.baseline_window_start, current.baseline_window_end)
            if window[0] is None or window[1] is None:
                window = self._default_baseline_window()
            self._settings.set_statistics_settings(replace(current, baseline_enabled=True, baseline_window_start=window[0], baseline_window_end=window[1]))
        else:
            self._settings.set_statistics_settings(replace(current, baseline_enabled=False))

    def _on_baseline_dragged(self, low: float, high: float) -> None:
        self._settings.set_statistics_settings(replace(
            self._settings.statistics_settings(), baseline_enabled=True, baseline_window_start=min(low, high), baseline_window_end=max(low, high)
        ))

    def _on_settings_changed(self) -> None:
        self._sync_controls()
        self._schedule_redraw()

    def _on_roi_changed(self, change: object) -> None:
        if getattr(change, "reason", "") in _RELEVANT_ROI_CHANGES:
            self._schedule_redraw()

    def _on_results_changed(self) -> None:
        self._generation += 1  # what was computed from the old file must not be drawn
        if self._job is not None:
            self._job.cancel.set()
        self._last = None
        self._cache.clear()
        self._cache_key = None
        self._x_cache = None
        self._schedule_redraw()

    def _on_axis_changed(self) -> None:
        """The Cube / Time choice changed: the x values change, and so does the unit of the baseline window."""
        self._x_cache = None
        stats = self._settings.statistics_settings()
        if stats.baseline_enabled:
            low, high = self._default_baseline_window()
            self._settings.set_statistics_settings(replace(stats, baseline_window_start=low, baseline_window_end=high))  # redraws via settings_changed
        else:
            self._schedule_redraw()

    # -- the x axis -----------------------------------------------------------------------------------------------------------------

    def _x_axis(self) -> tuple[str, np.ndarray]:
        """(axis label, x per cube of the file): time in seconds, or the cube number. Cached until the file or the mode changes."""
        stored = self._scan.stored()
        if stored is None:
            return "Cube (#)", np.empty(0)
        use_time = self._cube_display.effective_mode() == MODE_TIME
        key = (id(stored), use_time)
        if self._x_cache is None or self._x_cache[0] != key:
            if use_time:
                seconds = [self._dataset.cube_start_seconds(c) for c in stored.cube_indices]
                x = np.array([np.nan if s is None else float(s) for s in seconds])
            else:
                x = np.array(stored.cube_indices, dtype=np.float64)
            self._x_cache = (key, x)
        return ("Time (s)" if use_time else "Cube (#)"), self._x_cache[1]

    def _on_plot_clicked(self, x: float) -> None:
        stored = self._scan.stored()
        if stored is None:
            return
        _label, axis = self._x_axis()
        finite = np.flatnonzero(np.isfinite(axis))
        if finite.size == 0:
            return
        nearest = finite[int(np.argmin(np.abs(axis[finite] - x)))]
        self._selection.set_cube(int(stored.cube_indices[nearest]))

    def _update_cursor(self) -> None:
        """The dashed line follows the cube slider without recomputing anything."""
        if self._gate.defer():
            return
        stored = self._scan.stored()
        if stored is None or self._last is None:
            self._plot.set_current_x(None)
            return
        cube = int(self._selection.current_cube())
        if cube not in stored.cube_indices:
            self._plot.set_current_x(None)
            return
        self._plot.set_current_x(float(self._x_axis()[1][stored.cube_indices.index(cube)]))

    # -- computing off the GUI thread --------------------------------------------------------------------------------------------

    # -- live / paused ---------------------------------------------------------------------------------------------------------

    def _held_by_pause(self) -> bool:
        """Paused: nothing is recomputed. The request is remembered; going live again makes one catch-up redraw."""
        if self._live.is_live():
            return False
        self._dirty_while_paused = True
        return True

    def _on_live_changed(self, live: bool) -> None:
        if live and self._dirty_while_paused:
            self._dirty_while_paused = False
            self._schedule_redraw()

    def _schedule_redraw(self) -> None:
        if self._gate.defer() or self._held_by_pause():
            return
        self._redraw_timer.start()

    def _roi_colors(self, roi_ids: tuple[int, ...]) -> dict[int, str]:
        by_id = {roi.area_roi_id: roi for roi in self._roi_toolbox.rois()}
        colors: dict[int, str] = {}
        for index, roi_id in enumerate(roi_ids):
            roi = by_id.get(roi_id)
            chosen = roi.sample_color_hex if roi is not None else None
            colors[roi_id] = chosen or _FALLBACK_COLORS[index % len(_FALLBACK_COLORS)]
        return colors

    def _clear_with(self, text: str) -> None:
        self._generation += 1
        if self._job is not None:
            self._job.cancel.set()
        self._last = None
        self._plot.clear()
        self._plot.set_baseline_window(None)
        self._plot.set_current_x(None)
        self._status.setText(text)
        self.task.end("cancelled", text)

    def cancel(self) -> None:
        """Task indicator's Cancel: stop the running computation between ROIs (what it fitted is kept) and clear the plot."""
        self._redraw_timer.stop()
        self._pending = False
        self._clear_with("Cancelled. Change a setting or the selection to compute the sensorgram again.")

    def _redraw(self) -> None:
        if self._held_by_pause():  # paused after the timer was started
            return
        stored = self._scan.stored()
        roi_ids = tuple(sorted(self._selection.selected_roi_ids()))
        if stored is None:
            if self._scan.message():
                self._clear_with(self._scan.message())
            elif self._scan.is_loading():
                self._clear_with("Reading the scan...")
            else:
                self._clear_with("No scan yet. Run it in Workflow > Analysis.")
            return
        if not roi_ids:
            self._clear_with("Select one or more ROIs (ROI table or image) to see their sensorgram.")
            return
        _label, x = self._x_axis()
        detection = self._roi_toolbox.detection_settings()
        metric = self._settings.metric_settings()
        groups = tuple(GroupSpec(g.name, g.sample_color_hex, tuple(g.area_roi_ids)) for g in self._roi_toolbox.groups())
        request = SensorgramRequest(roi_ids, detection.formula_key, metric, self._settings.statistics_settings(), x, groups, self._roi_colors(roi_ids))
        key = trace_key(stored, detection.formula_key, metric)
        if key != self._cache_key:
            self._cache, self._cache_key = {}, key  # other formula / fit / metric: the old traces do not apply
        if self._job is not None:
            self._job.cancel.set()  # a running (maybe long) computation is stopped between ROIs; what it finished is kept
            self._pending = True
            return
        if self._image_first.should_wait():  # the frame the user is waiting for goes first (the fit slows its read down 10-20x)
            self._schedule_redraw()  # ask again in one coalescing step (and not at all while the dock is hidden)
            return
        job = _Job(self._generation, stored, request, key, threading.Event())
        self._job = job
        missing = sum(1 for r in roi_ids if r not in self._cache)
        self.task.begin(f"fitting {missing} ROI{'s' if missing != 1 else ''}" if missing else "applying the statistics")
        self._thread = threading.Thread(target=self._compute, args=(job, dict(self._cache)), name="sensorgram-compute", daemon=True)
        self._thread.start()

    def _compute(self, job: _Job, known: dict[int, np.ndarray]) -> None:
        total_missing = sum(1 for r in job.request.roi_ids if r not in known)
        report = (lambda done, total: self._progress.emit(done, total)) if total_missing >= _PROGRESS_MIN_ROIS else None
        try:
            with timed(logger, "sensorgram.compute", rois=len(job.request.roi_ids), new=total_missing):
                series, new = compute_sensorgram(job.stored, job.request, known, cancelled=job.cancel.is_set, progress=report)
            self._result_ready.emit(job, series, new, None)
        except BaseException as exc:  # noqa: BLE001 - nothing may escape a thread silently
            logger.exception("Computing the sensorgram failed")
            self._result_ready.emit(job, None, {}, exc)

    def _on_progress(self, done: int, total: int) -> None:
        if self._job is not None and not self._job.cancel.is_set():
            self._status.setText(f"Fitting ROI {done} of {total}...")
            self.task.update(done / total if total else -1.0, f"fitting ROI {done} of {total}")

    def _on_result(self, job: _Job, series: object, new: object, error: object) -> None:
        self._job = None
        if job.key == self._cache_key and isinstance(new, dict):
            self._cache.update(new)  # a cancelled run's finished traces are kept as well
        if isinstance(error, BaseException):
            self._status.setText(f"The sensorgram could not be computed: {error}")
        elif series and job.generation == self._generation and not job.cancel.is_set():
            self._last = (job, series)  # type: ignore[assignment]
            self._draw_last()
        if self._pending:
            self._pending = False
            self._schedule_redraw()  # the task stays open: the newer computation continues it
            if not self._redraw_timer.isActive():  # hidden panel: nothing was started, so nothing continues it
                self.task.end("cancelled", "superseded while hidden")
        elif isinstance(error, BaseException):
            self.task.end("failed", str(error))
        else:
            self.task.end("completed")

    # -- drawing ---------------------------------------------------------------------------------------------------------------------

    def _stale_mask(self, stored: StoredScan) -> np.ndarray | None:
        stale = self._scan.stale_cubes()
        if not stale:
            return None
        return np.array([c in stale for c in stored.cube_indices], dtype=bool)

    def _draw_last(self) -> None:
        if self._gate.defer() or self._last is None:
            return
        job, series = self._last
        stale = self._stale_mask(job.stored)
        label, _x = self._x_axis()
        stats = job.request.statistics
        y_label = "Centroid position (nm)" if job.request.metric.metric_key == "centroid" else "Peak position (nm)"
        if stats.baseline_enabled:
            y_label = y_label.replace("position", "shift")
        self._plot.set_axis_labels(label, y_label)
        self._plot.show_series(series, stale)
        if stats.baseline_enabled and stats.baseline_window_start is not None and stats.baseline_window_end is not None:
            self._plot.set_baseline_window((stats.baseline_window_start, stats.baseline_window_end))
        else:
            self._plot.set_baseline_window(None)
        self._update_cursor()
        self._status.setText(self._status_line(job, series, stale))

    def _status_line(self, job: _Job, series: list[Series], stale: np.ndarray | None) -> str:
        parts = [f"{len(job.request.roi_ids)} ROI{'s' if len(job.request.roi_ids) != 1 else ''}"]
        if job.request.metric.fit_method == "none":
            parts.append("metric read off the measured points (no fit)")
        if stale is not None:
            reason = self._scan.stale_reason()
            parts.append(f"{int(stale.sum())} of {len(stale)} cubes OUT OF DATE (hollow rings{': ' + reason if reason else ''}); run the scan again")
        elif self._scan.is_checking() and self._scan.stale_cubes() is None:
            parts.append("checking if the scan is up to date...")
        stats = job.request.statistics
        if stats.baseline_enabled and series and not any(np.isfinite(s.baseline) for s in series):
            parts.append("the baseline band holds no points: drag it over some")
        gaps = sum(1 for s in series if not np.isfinite(s.y).any())
        if gaps:
            parts.append(f"{gaps} series without any value")
        return " - ".join(parts)
