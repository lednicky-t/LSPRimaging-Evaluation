"""``SpectraPanel`` (sketch §7 "Display panels" > Spectra, §10): the spectra of the selected ROIs on the current cube.

**Screen.** A ribbon of compact controls on top (formula, fit, polynomial order, metric, fit window, error bars) in the look of the Image
panel's ribbon (captioned groups, `ribbon_row.py`): a double-click on its empty space rolls it up to a "..." handle, and a panel narrower than
the row scrolls it with an arrow at each edge. The plot fills the rest and one status line sits below it. It follows the selection: the ROIs selected in the ROI table or on the image, and the cube on the Image panel's
slider. Nothing here starts an analysis (CLAUDE.md): the numbers come from ``analysis/scan.h5`` through
:class:`~...analysis.scan_results.ScanResultsModule`, which says whether they are current.

**What it shows.** Per selected ROI, the formula of the stored sample and reference values (absorbance by default) at every scanned wavelength,
the fitted curve (``MetricSettings``) and a diamond at the metric (maximum or centroid of the fit). Error bars (off by default) are the pixel
spread inside the aperture divided by the square root of the pixel count (``analysis/spectrum_errors.py``: not the reproducibility between
measurements). A wavelength with no valid pixel is left out and counted in the status line. Results that are out of date (the inputs changed
since the scan: ``ScanResultsModule.stale_cubes``) are drawn dimmed with a note, never hidden, and never silently recomputed.

**Threads and pacing.** The formula, the fit and the metric (``analysis/scan_view.compute_spectra``) run on a plain worker thread, never on the
GUI thread (CLAUDE.md: no fit on the GUI thread, "live preview" included). A redraw is coalesced for 100 ms, only the newest request is
computed, a result from before a clear is dropped, and nothing is done while the dock is hidden (``HiddenPanelGate``).

**State.** Formula, fit method, order, metric and the fit window belong to modules (``AreaRoiDetectionSettings.formula_key``,
``AnalysisSettingsModule``) and are saved with the session; this panel only edits them through those modules. The error-bar choice is a
remembered UI choice (``ui_state``). The "Fit window" box means "the window is set": checking it sets the middle 80 % of the plotted
wavelengths, dragging the shaded band moves it, unchecking clears it.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, replace

import pyqtgraph as pg
from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QCheckBox, QComboBox, QHBoxLayout, QLabel, QSpinBox, QVBoxLayout, QWidget

from lspr_ui import get_active_theme

from ...analysis.query import FORMULA_KEYS
from ...analysis.scan_results import ScanResultsModule
from ...analysis.scan_store import StoredScan
from ...analysis.scan_view import RoiSpectrum, SpectraRequest, compute_spectra
from ...analysis.settings import FIT_METHODS, METRIC_KEYS
from ...analysis.settings_module import AnalysisSettingsModule
from ...applog import timed
from ...roi import RoiToolbox
from ...selection import SelectionModule
from ...storage.ui_state_keys import SPECTRA_RIBBON_OPEN, read
from ..ribbon_fields import style_ribbon_check, style_ribbon_combo, style_ribbon_number_field
from ..ribbon_row import RibbonRow, rolled_ribbon
from ..ui_state import UiStateStore
from ..visibility_gate import HiddenPanelGate
from .plot import SpectraPlot

logger = logging.getLogger(__name__)

_REDRAW_COALESCE_MS = 100  # sketch §8
_JOIN_SECONDS = 5.0
FORMULA_LABELS = {
    "absorbance": "Absorbance",
    "ratio": "Sample / reference",
    "relative_change": "Relative change",
    "mod_absorbance": "Modified absorbance",
}
FIT_LABELS = {"none": "No fit", "polynomial": "Polynomial", "gaussian": "Gaussian"}
METRIC_LABELS = {"maximum": "Maximum", "centroid": "Centroid"}
ERROR_BARS_KEY = "spectra/error_bars"
_RELEVANT_ROI_CHANGES = {"detection_settings", "session_restored", "added", "deleted", "detected"}  # a drag changes no spectrum
_FALLBACK_HUES = 9


@dataclass(frozen=True)
class _Job:
    generation: int  # a job from before a clear (dataset or scan changed) is dropped
    stored: StoredScan
    request: SpectraRequest


class SpectraPanel(QWidget):
    """Plots the spectra of the selected ROIs at the current cube from the stored scan."""

    _spectra_ready = pyqtSignal(object, object)  # (job, list[RoiSpectrum] | exception) - emitted from the worker thread

    def __init__(
        self,
        scan_results: ScanResultsModule,
        roi_toolbox: RoiToolbox,
        selection: SelectionModule,
        analysis_settings: AnalysisSettingsModule,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._scan = scan_results
        self._roi_toolbox = roi_toolbox
        self._selection = selection
        self._settings = analysis_settings
        self._generation = 0
        self._busy = False
        self._pending = False
        self._thread: threading.Thread | None = None
        self._last: tuple[_Job, list[RoiSpectrum]] | None = None
        self._syncing = False

        self._formula = QComboBox(self)
        for key in FORMULA_KEYS:
            self._formula.addItem(FORMULA_LABELS.get(key, key), key)
        style_ribbon_combo(self._formula)
        self._formula.setToolTip("How the stored sample and reference values are combined into one value per wavelength.")
        self._fit = QComboBox(self)
        for key in FIT_METHODS:
            self._fit.addItem(FIT_LABELS.get(key, key), key)
        style_ribbon_combo(self._fit)
        self._fit.setToolTip("Curve fitted through the points of each ROI's spectrum (inside the fit window, if one is set).")
        self._order = QSpinBox(self)
        self._order.setRange(1, 8)
        self._order.setPrefix("order ")
        style_ribbon_number_field(self._order, "order 8")
        self._order.setToolTip("Polynomial order (capped at half the number of points).")
        self._metric = QComboBox(self)
        for key in METRIC_KEYS:
            self._metric.addItem(METRIC_LABELS.get(key, key), key)
        style_ribbon_combo(self._metric)
        self._metric.setToolTip("The number read off each fitted spectrum (marked with a diamond): what the Sensorgram will plot.")
        self._window_box = QCheckBox("Set", self)
        self._window_box.setToolTip("Restrict the fit and the metric to a band of wavelengths. Drag the shaded band to move or resize it.")
        self._error_box = QCheckBox("Show", self)
        self._error_box.setToolTip(
            "Pixel-to-pixel spread inside each aperture divided by the square root of the pixel count (standard error of the mean value), "
            "propagated through the formula. Not the reproducibility between measurements."
        )
        style_ribbon_check(self._window_box)
        style_ribbon_check(self._error_box)
        self._status = QLabel("", self)
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f"color: {get_active_theme().text_dim};")
        self._plot = SpectraPlot(self)

        self._ribbon = RibbonRow(self)
        self._ribbon.add_group("Formula", self._formula)
        self._ribbon.add_group("Fit", self._fit, self._order)
        self._ribbon.add_group("Metric", self._metric)
        self._ribbon.add_group("Fit window", self._window_box)
        self._ribbon.add_group("Error bars", self._error_box)
        self._ribbon.add_stretch()
        self._ribbon_drawer = rolled_ribbon(self._ribbon, "Show the spectra controls.", self)
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

        self._spectra_ready.connect(self._on_ready)
        self._formula.currentIndexChanged.connect(self._on_formula_chosen)
        self._fit.currentIndexChanged.connect(self._on_metric_controls_changed)
        self._order.valueChanged.connect(self._on_metric_controls_changed)
        self._metric.currentIndexChanged.connect(self._on_metric_controls_changed)
        self._window_box.toggled.connect(self._on_window_toggled)
        self._error_box.toggled.connect(lambda _on: self._draw_last())
        self._plot.fit_window_changed.connect(self._on_window_dragged)

        self._selection.cube_changed.connect(lambda _cube: self._schedule_redraw())
        self._selection.roi_selection_changed.connect(lambda _ids: self._schedule_redraw())
        self._settings.settings_changed.connect(lambda _change: self._on_settings_changed())
        self._roi_toolbox.geometry_changed.connect(self._on_roi_changed)
        self._roi_toolbox.cosmetic_changed.connect(lambda _change: self._schedule_redraw())
        self._scan.results_changed.connect(self._on_results_changed)
        self._scan.staleness_changed.connect(self._draw_last)

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)
        self._sync_controls()
        self._schedule_redraw()

    # -- wiring with the owner -------------------------------------------------------------------------------------------------

    def restore_ui_state(self, store: UiStateStore) -> None:
        store.bind(ERROR_BARS_KEY, self._error_box)
        self._ribbon_drawer.set_expanded(read(store, SPECTRA_RIBBON_OPEN) is not False, animate=False)
        self._ribbon_drawer.expanded_changed.connect(lambda open_: store.set(SPECTRA_RIBBON_OPEN.key, bool(open_)))

    def set_panel_visible(self, visible: bool) -> None:
        """Connected to the dock's ``visibilityChanged``: a hidden panel does no work and catches up once when shown."""
        self._gate.set_visible(visible)

    def refresh_theme(self) -> None:
        self._ribbon.refresh_theme()
        self._ribbon_drawer.refresh_theme()
        self._plot.refresh_theme()
        self._status.setStyleSheet(f"color: {get_active_theme().text_dim};")

    def shutdown(self) -> None:
        """Application is quitting: let a running computation finish, so it never emits into a widget Qt is tearing down."""
        self._generation += 1
        thread = self._thread
        if thread is not None:
            thread.join(timeout=_JOIN_SECONDS)

    # -- controls <-> modules ----------------------------------------------------------------------------------------------------

    def _sync_controls(self) -> None:
        """Put the controls on the modules' current values, without sending anything back."""
        metric = self._settings.metric_settings()
        formula = self._roi_toolbox.detection_settings().formula_key
        self._syncing = True
        try:
            self._formula.setCurrentIndex(max(self._formula.findData(formula), 0))
            self._fit.setCurrentIndex(max(self._fit.findData(metric.fit_method), 0))
            self._order.setValue(int(metric.poly_order))
            self._order.setEnabled(metric.fit_method == "polynomial")
            self._metric.setCurrentIndex(max(self._metric.findData(metric.metric_key), 0))
            self._window_box.setEnabled(metric.fit_method != "none")
            self._window_box.setChecked(metric.fit_wl_min is not None or metric.fit_wl_max is not None)
        finally:
            self._syncing = False

    def _on_formula_chosen(self, _index: int) -> None:
        if self._syncing:
            return
        key = self._formula.currentData()
        detection = self._roi_toolbox.detection_settings()
        if key and key != detection.formula_key:
            self._roi_toolbox.set_detection_settings(replace(detection, formula_key=key))

    def _on_metric_controls_changed(self, *_args: object) -> None:
        if self._syncing:
            return
        current = self._settings.metric_settings()
        self._settings.set_metric_settings(
            replace(current, fit_method=self._fit.currentData(), poly_order=self._order.value(), metric_key=self._metric.currentData())
        )

    def _on_window_toggled(self, on: bool) -> None:
        if self._syncing:
            return
        current = self._settings.metric_settings()
        if not on:
            self._settings.set_metric_settings(replace(current, fit_wl_min=None, fit_wl_max=None))
            return
        wavelengths = self._plotted_wavelengths()
        if wavelengths is None:
            low, high = self._plot.x_range()
        else:
            low, high = wavelengths
        span = high - low
        self._settings.set_metric_settings(replace(current, fit_wl_min=low + 0.1 * span, fit_wl_max=high - 0.1 * span))

    def _on_window_dragged(self, low: float, high: float) -> None:
        current = self._settings.metric_settings()
        self._settings.set_metric_settings(replace(current, fit_wl_min=min(low, high), fit_wl_max=max(low, high)))

    def _plotted_wavelengths(self) -> tuple[float, float] | None:
        if self._last is None:
            return None
        spectra = [s for s in self._last[1] if s.wavelengths_nm.size]
        if not spectra:
            return None
        return min(float(s.wavelengths_nm.min()) for s in spectra), max(float(s.wavelengths_nm.max()) for s in spectra)

    def _on_settings_changed(self) -> None:
        self._sync_controls()
        self._schedule_redraw()

    def _on_roi_changed(self, change: object) -> None:
        if getattr(change, "reason", "") in _RELEVANT_ROI_CHANGES:
            self._sync_controls()  # the formula lives in the detection settings
            self._schedule_redraw()

    def _on_results_changed(self) -> None:
        self._generation += 1  # what was computed from the old file must not be drawn
        self._last = None
        self._schedule_redraw()

    # -- computing off the GUI thread --------------------------------------------------------------------------------------------

    def _schedule_redraw(self) -> None:
        if self._gate.defer():
            return
        self._redraw_timer.start()

    def _redraw(self) -> None:
        stored = self._scan.stored()
        roi_ids = tuple(sorted(self._selection.selected_roi_ids()))
        if stored is None:
            self._generation += 1
            self._last = None
            self._plot.clear()
            self._plot.set_fit_window(None)
            if self._scan.message():
                self._status.setText(self._scan.message())
            elif self._scan.is_loading():
                self._status.setText("Reading the scan...")
            else:
                self._status.setText("No scan yet. Run it in Workflow > Analysis.")
            return
        if not roi_ids:
            self._generation += 1
            self._last = None
            self._plot.clear()
            self._plot.set_fit_window(None)
            self._status.setText("Select one or more ROIs (ROI table or image) to see their spectra.")
            return
        request = SpectraRequest(roi_ids, int(self._selection.current_cube()), self._roi_toolbox.detection_settings().formula_key,
                                 self._settings.metric_settings())
        job = _Job(self._generation, stored, request)
        if self._busy:
            self._pending = True  # newest request wins: computed when the running one is back
            return
        self._busy = True
        self._thread = threading.Thread(target=self._compute, args=(job,), name="spectra-compute", daemon=True)
        self._thread.start()

    def _compute(self, job: _Job) -> None:
        try:
            with timed(logger, "spectra.compute", rois=len(job.request.roi_ids), cube=job.request.cube):
                results: object = compute_spectra(job.stored, job.request)
        except BaseException as exc:  # noqa: BLE001 - nothing may escape a thread silently
            logger.exception("Computing the spectra failed")
            results = exc
        self._spectra_ready.emit(job, results)

    def _on_ready(self, job: _Job, results: object) -> None:
        self._busy = False
        if isinstance(results, BaseException):
            self._status.setText(f"The spectra could not be computed: {results}")
        elif job.generation == self._generation:
            self._last = (job, results)  # type: ignore[assignment]
            self._draw_last()
        if self._pending:
            self._pending = False
            self._schedule_redraw()

    # -- drawing -------------------------------------------------------------------------------------------------------------------

    def _roi_colors(self, roi_ids: tuple[int, ...]) -> dict[int, str]:
        by_id = {roi.area_roi_id: roi for roi in self._roi_toolbox.rois()}
        colors: dict[int, str] = {}
        for index, roi_id in enumerate(roi_ids):
            roi = by_id.get(roi_id)
            chosen = roi.sample_color_hex if roi is not None else None
            if not chosen or not QColor(chosen).isValid():
                chosen = pg.intColor(index, hues=_FALLBACK_HUES).name()  # a ROI without its own colour: one per position, distinct
            colors[roi_id] = chosen
        return colors

    def _draw_last(self) -> None:
        if self._gate.defer() or self._last is None:
            return
        job, spectra = self._last
        colors = self._roi_colors(job.request.roi_ids)
        cube = job.request.cube
        current = self._scan.is_cube_current(cube)
        stale = current is False
        self._plot.set_formula(job.request.formula_key)
        self._plot.show_spectra(
            [(f"ROI {s.roi_id}", colors[s.roi_id], s) for s in spectra], error_bars=self._error_box.isChecked(), dimmed=stale
        )
        metric = job.request.metric
        has_window = metric.fit_wl_min is not None or metric.fit_wl_max is not None
        wavelengths = self._plotted_wavelengths()
        if has_window and wavelengths is not None:
            self._plot.set_fit_window((metric.fit_wl_min if metric.fit_wl_min is not None else wavelengths[0],
                                       metric.fit_wl_max if metric.fit_wl_max is not None else wavelengths[1]))
        else:
            self._plot.set_fit_window(None)
        self._status.setText(self._status_line(job, spectra, current))

    def _status_line(self, job: _Job, spectra: list[RoiSpectrum], current: bool | None) -> str:
        cube = job.request.cube
        parts = [f"Cube {cube}: {len(spectra)} ROI{'s' if len(spectra) != 1 else ''}"]
        if current is False:
            reason = self._scan.stale_reason()
            parts.append(f"OUT OF DATE ({reason}); run the scan again" if reason else "OUT OF DATE (the inputs changed since the scan); run the scan again")
        elif current is None and self._scan.is_checking():
            parts.append("checking if the scan is up to date...")
        missing = sum(1 for s in spectra if s.n_missing and s.values.size)
        if missing:
            parts.append(f"{missing} ROI{'s' if missing != 1 else ''} lack{'s' if missing == 1 else ''} some wavelengths (no valid pixel)")
        notes = [f"ROI {s.roi_id}: {s.note}" for s in spectra if s.note]
        if notes:
            parts.append("; ".join(notes[:3]) + (f" (+{len(notes) - 3} more)" if len(notes) > 3 else ""))
        return " - ".join(parts)
