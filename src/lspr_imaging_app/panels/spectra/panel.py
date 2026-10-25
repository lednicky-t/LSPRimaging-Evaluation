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

**The cursor.** A dashed line at the wavelength shown on the Image panel, with the Wavelength slider's circle on the x axis (``axis_cursor.py``).
It follows ``SelectionModule.wavelength_changed`` without recomputing (the spectra do not depend on the wavelength), and dragging the circle
sets the wavelength to the nearest scanned one, as the slider does. Hidden while nothing is plotted or the Image panel shows the 0 nm dark frame.

**Threads and pacing.** The formula, the fit and the metric (``analysis/scan_view.compute_spectra``) run on a plain worker thread, never on the
GUI thread (CLAUDE.md: no fit on the GUI thread, "live preview" included). A redraw is coalesced for 100 ms, only the newest request is
computed, a result from before a clear is dropped, and nothing is done while the dock is hidden (``HiddenPanelGate``).

**Status bar.** A computation that lasts more than a moment (``panel_task.PanelTask``) appears in the app's task indicator ("Spectra: fitting
3 ROIs on cube 5") with a Cancel button; a quick one leaves no trace. Cancel drops the result and clears the plot, so a plot that no longer matches
the controls is never left standing. The worker itself cannot be interrupted and runs to its end unseen.

**State.** Formula, fit method, order, metric and the fit window belong to modules (``AreaRoiDetectionSettings.formula_key``,
``AnalysisSettingsModule``) and are saved with the session; this panel only edits them through those modules. The error-bar choice is a
remembered UI choice (``ui_state``). The fit window is a pick-up menu in the Fit group: no window, a manual range (picking it sets the middle 80 % of the plotted
wavelengths; dragging the shaded band moves it), each spectrum's maximum +/- a width in nm, or +/- a multiple of its FWHM; the widths are
fields in the menu, "Same number of points" is its footer. Only the manual range is drawn on the plot (``analysis/fit_window.py``). The Metric group has a second menu,
the metric range: which part of the fitted curve the centroid is read from (whole window, the top part above a level, or a manual range; ``analysis/metric_range.py``).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, replace

import pyqtgraph as pg
from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QCheckBox, QDoubleSpinBox, QHBoxLayout, QLabel, QSpinBox, QVBoxLayout, QWidget

from lspr_ui import get_active_theme

from ...analysis.live_state import AnalysisLiveModule
from ...analysis.query import FORMULA_KEYS
from ...analysis.scan_results import ScanResultsModule
from ...analysis.scan_store import StoredScan
from ...analysis.scan_view import RoiSpectrum, SpectraRequest, compute_spectra
from ...analysis.settings import FIT_METHODS, METRIC_KEYS, MetricSettings
from ...analysis.settings_module import AnalysisSettingsModule
from ...applog import timed
from ...roi import RoiToolbox
from ...selection import SelectionModule
from ...storage.ui_state_keys import SPECTRA_RIBBON_OPEN, read
from ..live_toggle import LiveToggle
from ..ribbon_fields import style_ribbon_check, style_ribbon_number_field
from ..ribbon_row import RibbonRow, rolled_ribbon
from ..panel_task import PanelTask
from ..ui_state import UiStateStore
from ..visibility_gate import HiddenPanelGate
from .pick_menu import PickMenu, equation_picture, fit_picture, metric_picture, range_picture, window_picture
from .plot import SpectraPlot

logger = logging.getLogger(__name__)

_REDRAW_COALESCE_MS = 100  # sketch §8
_JOIN_SECONDS = 5.0
FORMULA_LABELS = {
    "absorbance": "Absorbance",
    "ratio": "Sample / reference",
    "relative_change": "Relative change",
}
# I_s, I_r: the stored sample and reference intensity (matches `analysis/query.py` `apply_formula`; _VALUE_FLOOR aside)
_IS = "<i>I</i><sub>s</sub>"
_IR = "<i>I</i><sub>r</sub>"
FORMULA_EQUATIONS = {
    "absorbance": f"<i>A</i> = log<sub>10</sub>({_IR} / {_IS})",
    "ratio": f"{_IS} / {_IR}",
    "relative_change": f"({_IR} − {_IS}) / {_IR}",
}
FORMULA_TIPS = {
    "absorbance": "Absorbance: log10 of reference over sample.",
    "ratio": "Sample intensity divided by reference intensity.",
    "relative_change": "Reference minus sample, divided by reference.",
}
FIT_LABELS = {"none": "No fit", "polynomial": "Polynomial", "gaussian": "Gaussian"}
FIT_TIPS = {
    "none": "Show the measured points only.",
    "polynomial": "Fit a polynomial through the points.",
    "gaussian": "Fit a Gaussian (bell curve) through the points.",
}
METRIC_LABELS = {"maximum": "Maximum", "centroid": "Centroid"}
METRIC_TIPS = {
    "maximum": "The wavelength of the top of the fitted spectrum.",
    "centroid": "The intensity-weighted mean wavelength of the fitted spectrum.",
}
WINDOW_LABELS = {"off": "No window", "manual": "Manual range", "around_max": "Maximum \u00b1 width", "fwhm": "Maximum \u00b1 FWHM"}
WINDOW_TIPS = {
    "off": "Fit all the points of the spectrum.",
    "manual": "One range for every spectrum. Drag the shaded band on the plot to move or resize it.",
    "around_max": "Each spectrum is fitted around its own maximum, this many nm to each side. The window follows a peak that shifts.",
    "fwhm": "Each spectrum is fitted around its own maximum, this many times its FWHM (the peak's width at half height) to each side.",
}
RANGE_LABELS = {"off": "Whole fit window", "top_fraction": "Top part of the peak", "manual": "Manual range"}
RANGE_TIPS = {
    "off": "The centroid of the whole fitted curve, baseline included.",
    "top_fraction": "The centroid of the part of the fitted curve above a level (a percentage of the peak's height above its baseline). The baseline has no weight and "
                    "the result does not depend on where the fit window ends.",
    "manual": "The centroid of the fitted curve between two wavelengths, baseline taken out.",
}
TASK_ID = "spectra.compute"
ERROR_BARS_KEY = "spectra/error_bars"
_RELEVANT_ROI_CHANGES = {"detection_settings", "session_restored", "added", "deleted", "detected"}  # a drag changes no spectrum
_FALLBACK_HUES = 9


def _window_is_on(metric: MetricSettings) -> bool:
    """A manual range is on when it has a bound, a per-spectrum window when ``window_on`` is True."""
    if metric.window_mode == "manual":
        return metric.fit_wl_min is not None or metric.fit_wl_max is not None
    return bool(metric.window_on)


def _window_choice(metric: MetricSettings) -> str:
    """The entry of the window menu for these settings: "off", or the mode."""
    return metric.window_mode if _window_is_on(metric) else "off"


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
        live: AnalysisLiveModule | None = None,
    ) -> None:
        super().__init__(parent)
        self._live = live if live is not None else AnalysisLiveModule(self)  # without an owner: always live
        self._dirty_while_paused = False
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

        self._formula = PickMenu(self, name_on_face=False)  # the equation says it all
        for key in FORMULA_KEYS:
            if key not in FORMULA_EQUATIONS:  # modified absorbance is absorbance x 1000, a unit change: calculated, not offered
                continue
            self._formula.add_choice(key, FORMULA_LABELS.get(key, key), FORMULA_TIPS.get(key, ""), equation_picture(FORMULA_EQUATIONS[key]))
        self._formula.finish()
        self._formula.setToolTip("How the stored sample and reference values are combined into one value per wavelength (Is, Ir: sample and reference intensity).")
        self._fit = PickMenu(self, name_on_face=False)
        for key in FIT_METHODS:
            self._fit.add_choice(key, FIT_LABELS.get(key, key), FIT_TIPS.get(key, ""), fit_picture(key))
        self._fit.finish()
        self._fit.setToolTip("Curve fitted through the points of each ROI's spectrum (inside the fit window, if one is set).")
        self._order = QSpinBox(self)
        self._order.setRange(1, 8)
        self._order.setPrefix("order ")
        style_ribbon_number_field(self._order, "order 8")
        self._order.setToolTip("Polynomial order (capped at half the number of points).")
        self._metric = PickMenu(self, name_on_face=False)
        for key in METRIC_KEYS:
            self._metric.add_choice(key, METRIC_LABELS.get(key, key), METRIC_TIPS.get(key, ""), metric_picture(key))
        self._metric.finish()
        self._metric.setToolTip("The number read off each fitted spectrum (marked with a diamond): what the Sensorgram will plot.")
        self._half_width = QDoubleSpinBox(self)
        self._half_width.setRange(1.0, 500.0)
        self._half_width.setDecimals(1)
        self._half_width.setSingleStep(5.0)
        self._half_width.setPrefix("\u00b1 ")
        self._half_width.setSuffix(" nm")
        self._half_width.setKeyboardTracking(False)  # a value is taken when Enter is pressed or the field is left, not on every digit
        self._half_width.setFixedWidth(96)
        self._half_width.setToolTip("Half the width of the window, in nm: the window reaches this far to each side of the maximum.")
        self._fwhm_factor = QDoubleSpinBox(self)
        self._fwhm_factor.setRange(0.1, 5.0)
        self._fwhm_factor.setDecimals(2)
        self._fwhm_factor.setSingleStep(0.05)
        self._fwhm_factor.setPrefix("\u00b1 ")
        self._fwhm_factor.setSuffix(" \u00d7 FWHM")
        self._fwhm_factor.setKeyboardTracking(False)
        self._fwhm_factor.setFixedWidth(120)
        self._fwhm_factor.setToolTip("How far the window reaches to each side of the maximum, in widths of the peak at half its height.")
        self._same_points = QCheckBox("Same number of points", self)
        self._same_points.setToolTip(
            "Every window holds the same number of points (the nearest ones to the maximum), so a point never joins or leaves the fit just because "
            "the peak moved a few nm. Near the end of the data the window takes points outside its nominal range."
        )
        self._top_fraction = QDoubleSpinBox(self)
        self._top_fraction.setRange(0.0, 95.0)
        self._top_fraction.setDecimals(0)
        self._top_fraction.setSingleStep(5.0)
        self._top_fraction.setPrefix("above ")
        self._top_fraction.setSuffix(" % of height")
        self._top_fraction.setKeyboardTracking(False)
        self._top_fraction.setFixedWidth(150)
        self._top_fraction.setToolTip("The level, as a percentage of the peak's height above its baseline: 30 % reads the top 70 % of the peak, 90 % only its tip.")
        self._range_min = QDoubleSpinBox(self)
        self._range_max = QDoubleSpinBox(self)
        for box in (self._range_min, self._range_max):
            box.setRange(0.0, 5000.0)
            box.setDecimals(1)
            box.setSingleStep(5.0)
            box.setSuffix(" nm")
            box.setKeyboardTracking(False)
            box.setFixedWidth(84)
        self._range_min.setToolTip("Lower end of the range the centroid is read from.")
        self._range_max.setToolTip("Upper end of the range the centroid is read from.")
        self._range_pair = QWidget(self)
        pair = QHBoxLayout(self._range_pair)
        pair.setContentsMargins(0, 0, 0, 0)
        pair.setSpacing(4)
        pair.addWidget(self._range_min)
        pair.addWidget(QLabel("to", self._range_pair))
        pair.addWidget(self._range_max)
        self._metric_range = PickMenu(self, name_on_face=False)
        self._metric_range.add_choice("off", RANGE_LABELS["off"], RANGE_TIPS["off"], range_picture("off"))
        self._metric_range.add_choice("top_fraction", RANGE_LABELS["top_fraction"], RANGE_TIPS["top_fraction"], range_picture("top_fraction"), self._top_fraction)
        self._metric_range.add_choice("manual", RANGE_LABELS["manual"], RANGE_TIPS["manual"], range_picture("manual"), self._range_pair)
        self._metric_range.finish()
        self._metric_range.setToolTip("Which part of the fitted curve the centroid is read from (the maximum has no range; it needs a fit).")
        self._window_mode = PickMenu(self, name_on_face=False)
        self._window_mode.add_choice("off", WINDOW_LABELS["off"], WINDOW_TIPS["off"], window_picture("off"))
        self._window_mode.add_choice("manual", WINDOW_LABELS["manual"], WINDOW_TIPS["manual"], window_picture("manual"))
        self._window_mode.add_choice("around_max", WINDOW_LABELS["around_max"], WINDOW_TIPS["around_max"], window_picture("around_max"), self._half_width)
        self._window_mode.add_choice("fwhm", WINDOW_LABELS["fwhm"], WINDOW_TIPS["fwhm"], window_picture("fwhm"), self._fwhm_factor)
        self._window_mode.add_footer(self._same_points)
        self._window_mode.finish()
        self._window_mode.setToolTip("Which points of each spectrum are fitted (and the metric is read from): all, a fixed range, or a window around each spectrum's own maximum.")
        self._error_box = QCheckBox("Show", self)
        self._error_box.setToolTip(
            "Pixel-to-pixel spread inside each aperture divided by the square root of the pixel count (standard error of the mean value), "
            "propagated through the formula. Not the reproducibility between measurements."
        )
        style_ribbon_check(self._error_box)
        self._status = QLabel("", self)
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f"color: {get_active_theme().text_dim};")
        self._plot = SpectraPlot(self)
        self.task = PanelTask(TASK_ID, "Spectra", self)  # connected to the status bar's task indicator by app_rewrite

        self._live_toggle = LiveToggle(self)
        self._live_toggle.bind(self._live)
        self._ribbon = RibbonRow(self)
        self._ribbon.add_group("Live", self._live_toggle)
        self._ribbon.add_group("Formula", self._formula)
        self._ribbon.add_group("Fit", self._fit, self._order, self._window_mode)
        self._ribbon.add_group("Metric", self._metric, self._metric_range)
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
        self._live.live_changed.connect(self._on_live_changed)
        self._formula.currentIndexChanged.connect(self._on_formula_chosen)
        self._fit.currentIndexChanged.connect(self._on_metric_controls_changed)
        self._order.valueChanged.connect(self._on_metric_controls_changed)
        self._metric.currentIndexChanged.connect(self._on_metric_controls_changed)
        self._metric_range.currentIndexChanged.connect(self._on_metric_range_chosen)
        for field in (self._top_fraction, self._range_min, self._range_max):
            field.valueChanged.connect(self._on_metric_range_params_changed)
        self._window_mode.currentIndexChanged.connect(self._on_window_mode_chosen)
        self._half_width.valueChanged.connect(self._on_window_params_changed)
        self._fwhm_factor.valueChanged.connect(self._on_window_params_changed)
        self._same_points.toggled.connect(self._on_window_params_changed)
        self._error_box.toggled.connect(lambda _on: self._draw_last())
        self._plot.fit_window_changed.connect(self._on_window_dragged)
        self._plot.wavelength_dragged.connect(self._on_cursor_dragged)

        self._selection.cube_changed.connect(lambda _cube: self._schedule_redraw())
        self._selection.wavelength_changed.connect(lambda _wavelength: self._update_cursor())
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
        for pick in (self._formula, self._fit, self._metric, self._window_mode, self._metric_range):
            pick.refresh_theme()
        self._live_toggle.refresh_theme()
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
            self._window_mode.setEnabled(metric.fit_method != "none")
            self._metric_range.setEnabled(metric.fit_method != "none" and metric.metric_key == "centroid")
            self._metric_range.setCurrentIndex(max(self._metric_range.findData(metric.metric_range), 0))
            self._top_fraction.setValue(round(100.0 * float(metric.metric_top_fraction)))
            if metric.metric_range_min is not None:
                self._range_min.setValue(float(metric.metric_range_min))
            if metric.metric_range_max is not None:
                self._range_max.setValue(float(metric.metric_range_max))
            self._window_mode.setCurrentIndex(max(self._window_mode.findData(_window_choice(metric)), 0))
            self._half_width.setValue(float(metric.window_half_width_nm))
            self._fwhm_factor.setValue(float(metric.window_fwhm_factor))
            self._same_points.setChecked(bool(metric.window_same_points))
            self._same_points.setEnabled(_window_choice(metric) in ("around_max", "fwhm"))
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

    def _on_window_mode_chosen(self, _index: int) -> None:
        """A choice in the window menu. Picking a mode turns the window on; "No window" turns it off (a manual range is forgotten, as before)."""
        if self._syncing:
            return
        current = self._settings.metric_settings()
        key = self._window_mode.currentData() or "off"
        if key == "off":
            changed = replace(current, window_on=False, fit_wl_min=None, fit_wl_max=None)
        elif key == "manual":
            manual = replace(current, window_mode="manual")
            changed = manual if _window_is_on(manual) else self._with_default_range(manual)
        else:
            changed = replace(current, window_mode=key, window_on=True)
        self._settings.set_metric_settings(changed)

    def _on_window_params_changed(self, *_args: object) -> None:
        """A width or the same-points box was edited in the menu. This never turns the window on or off."""
        if self._syncing:
            return
        current = self._settings.metric_settings()
        self._settings.set_metric_settings(replace(
            current, window_half_width_nm=self._half_width.value(), window_fwhm_factor=self._fwhm_factor.value(),
            window_same_points=self._same_points.isChecked(),
        ))

    def _default_span(self) -> tuple[float, float]:
        """The middle 80 % of the plotted wavelengths: what a newly set manual range starts as."""
        wavelengths = self._plotted_wavelengths()
        low, high = self._plot.x_range() if wavelengths is None else wavelengths
        span = high - low
        return low + 0.1 * span, high - 0.1 * span

    def _with_default_range(self, metric: MetricSettings) -> MetricSettings:
        """``metric`` with a manual fit range set (the middle 80 % of the plotted wavelengths)."""
        low, high = self._default_span()
        return replace(metric, fit_wl_min=low, fit_wl_max=high)

    def _on_metric_range_chosen(self, _index: int) -> None:
        """A choice in the metric range menu (which part of the fitted curve the centroid is read from)."""
        if self._syncing:
            return
        current = self._settings.metric_settings()
        key = self._metric_range.currentData() or "off"
        changed = replace(current, metric_range=key)
        if key == "manual" and current.metric_range_min is None and current.metric_range_max is None:
            low, high = self._default_span()
            changed = replace(changed, metric_range_min=low, metric_range_max=high)
        self._settings.set_metric_settings(changed)

    def _on_metric_range_params_changed(self, *_args: object) -> None:
        """The level or an end of the range was edited in the menu. This never changes which range is chosen."""
        if self._syncing:
            return
        current = self._settings.metric_settings()
        changed = replace(current, metric_top_fraction=self._top_fraction.value() / 100.0)
        if current.metric_range == "manual":
            low, high = self._range_min.value(), self._range_max.value()
            changed = replace(changed, metric_range_min=min(low, high), metric_range_max=max(low, high))
        self._settings.set_metric_settings(changed)

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

    # -- the cursor ------------------------------------------------------------------------------------------------------------------

    def _update_cursor(self) -> None:
        """The line and the circle follow the wavelength slider; nothing is computed."""
        if self._gate.defer():
            return
        wavelength = float(self._selection.current_wavelength())
        self._plot.set_current_wavelength(wavelength if self._last is not None and wavelength > 0.0 else None)

    def _on_cursor_dragged(self, wavelength_nm: float) -> None:
        """The circle was dragged: the nearest scanned wavelength becomes the current one (the Image panel's slider follows)."""
        stored = self._scan.stored()
        if stored is None or not stored.wavelength_nm:
            return
        self._selection.set_wavelength(min(stored.wavelength_nm, key=lambda wl: abs(wl - wavelength_nm)))

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

    def _redraw(self) -> None:
        if self._held_by_pause():  # paused after the timer was started
            return
        stored = self._scan.stored()
        roi_ids = tuple(sorted(self._selection.selected_roi_ids()))
        if stored is None:
            self._generation += 1
            self._last = None
            self._plot.clear()
            self._plot.set_fit_window(None)
            self._plot.set_current_wavelength(None)
            self.task.end("cancelled", "nothing to compute")
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
            self._plot.set_current_wavelength(None)
            self.task.end("cancelled", "nothing to compute")
            self._status.setText("Select one or more ROIs (ROI table or image) to see their spectra.")
            return
        request = SpectraRequest(roi_ids, int(self._selection.current_cube()), self._roi_toolbox.detection_settings().formula_key,
                                 self._settings.metric_settings())
        job = _Job(self._generation, stored, request)
        if self._busy:
            self._pending = True  # newest request wins: computed when the running one is back
            return
        self._busy = True
        self.task.begin(self._task_message(request))
        self._thread = threading.Thread(target=self._compute, args=(job,), name="spectra-compute", daemon=True)
        self._thread.start()

    def _task_message(self, request: SpectraRequest) -> str:
        fit = "" if request.metric.fit_method == "none" else f", {FIT_LABELS.get(request.metric.fit_method, request.metric.fit_method).lower()} fit"
        return f"{len(request.roi_ids)} ROI{'s' if len(request.roi_ids) != 1 else ''} on cube {request.cube}{fit}"

    def cancel(self) -> None:
        """Task indicator's Cancel: forget the running (or queued) computation and clear the plot. The worker thread cannot be stopped; its
        result is dropped when it arrives (it belongs to an older generation)."""
        self._redraw_timer.stop()
        self._pending = False
        self._generation += 1
        self._last = None
        self._plot.clear()
        self._plot.set_fit_window(None)
        self._plot.set_current_wavelength(None)
        self._status.setText("Cancelled. Change a setting or the selection to compute the spectra again.")
        self.task.end("cancelled")

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
            self._schedule_redraw()  # the task stays open: the newer computation continues it
            if not self._redraw_timer.isActive():  # hidden panel: nothing was started, so nothing continues it
                self.task.end("cancelled", "superseded while hidden")
        elif isinstance(results, BaseException):
            self.task.end("failed", str(results))
        else:
            self.task.end("completed")

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
        manual_min, manual_max = metric.manual_bounds()  # a per-spectrum window is not drawn: the ends of each fitted curve show it
        has_window = manual_min is not None or manual_max is not None
        wavelengths = self._plotted_wavelengths()
        if has_window and wavelengths is not None:
            self._plot.set_fit_window((manual_min if manual_min is not None else wavelengths[0],
                                       manual_max if manual_max is not None else wavelengths[1]))
        else:
            self._plot.set_fit_window(None)
        self._update_cursor()
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
