"""``SensorgramPanel`` (sketch §7 "Display panels" > Sensorgram, §10): the metric of the selected ROIs against time.

**Screen.** A ribbon of compact controls on top, in three tabs (since 2026-11-13) in the look of the Image panel's ribbon (captioned groups, `ribbon_row.py`): **Plot** (display mode, centre and
band for averages, smoothing, spike removal, baseline, export), **View** (how everything is drawn: `view_controls.py`, `docs/sensorgram_view_tab_2026-11-13.md`) and **Experimental plan** (editing
the plan, the evaluation, the checks). A double-click on the ribbon's empty space rolls it up to a "..." handle, and a panel
narrower than the row scrolls it with an arrow at each edge. The plot fills the rest and one status line sits below it. For the ROIs selected in the ROI table or on the image it plots, at every scanned cube, the **metric** of the
Spectra panel (the position of the fitted maximum or centroid, in nm: a peak *shifting* is the measurement). The x axis is time in seconds or the
cube number, following the app-wide Cube / Time choice. A dashed line marks the current cube; clicking the plot moves the cube slider there.

**Where the numbers come from.** ``analysis/scan.h5`` through :class:`~...analysis.scan_results.ScanResultsModule`; nothing here starts an
analysis. ``analysis/scan_trace.py`` fits every cube of every selected ROI (all cubes of a ROI at once, ``trace_fit.py``, in a helper process, ``trace_pool.py``) and then applies the statistics of
``AnalysisSettingsModule`` **per ROI** (spike removal, smoothing, baseline, in that order) and only then, for "Average all" / "Average by group",
combines the finished per-ROI traces (mean or median, band = SD or SEM). Averaging fitted values, never spectra (CLAUDE.md). Points of cubes
whose results are out of date are marked with a grey hollow diamond, never hidden (2026-11-03: the View tab's clock button hides the marks only, never the points;
it is on by default and remembered, and its tool tip says how many cubes are out of date). The View tab's mask button (2026-10-24) adds orange marks
where an ignore mask touched the ROIs of a line: a triangle for a persistent mask, a circle for an individual one. They are read from
``n_masked`` in the scan file (``analysis/scan_mask_marks.py``), so only masks that really removed pixels of a ROI's disk or ring are marked.

**Threads and pacing.** All of that runs on a plain worker thread, never the GUI thread; the fits themselves run in a helper process, because fits
in a thread took the interpreter lock from the window and froze it for seconds (``docs/sensorgram_fit_freeze_2026-10-24.md``). The traces already computed are kept (a trace depends on the
formula, fit settings and metric only, so a statistics change or another ROI selection reuses them); a new request cancels a long one between ROIs
and keeps what it finished. Selecting all 160 ROIs costs ~1 s once with the polynomial fit (a Gaussian fit ~2 s with the fast solver, ~87 s with the classic one, with a progress line). 100 ms coalescing, newest request wins, a result from an
older file is dropped, and nothing happens while the dock is hidden (``HiddenPanelGate``).

**Y axis and baseline selector (2026-10-25).** A double-click on the y axis label switches between the absolute position and the shift from the
baseline (the same switch as the Baseline group's "Subtract"). The baseline selector is the draggable band along the bottom tenth of the plot; the
Baseline group's pencil shows or hides it and its menu sets its colour and fill transparency (a pick-up menu like the Image ribbon's ROI display; all
three remembered). The selector is drawn whether or not the traces are subtracted, and dragging it only stores where it is.

**The plan bar (2026-10-26).** The bar of experiment steps along the top or bottom of the plot is controlled by the Steps and Evaluation menus of the View tab (``plan_group.py`` the controls,
``plan_bar.py`` the item, ``plan/bar_model.py`` what is drawn and where the text goes; design ``docs/experimental_plan_bar_design_2026-10-26.md``). The steps come from
``ExperimentPlanModule`` (the dataset's measured steps with the user's edits); the panel only converts them to the plot's x axis (``plan/axis_map.py``) and hands them to the
plot. They follow the Cube / Time choice, and are drawn only while traces are (an empty plot has no axis to put them on). The bar's gestures (drag a boundary, double-click to retype a note,
right-click for colour and reset) are handled by ``plan_actions.PlanBarActions``, which edits the module; everything that hangs on the steps lives in ``step_overlays.StepOverlays`` (split out of this file 2026-10-26); this panel only gives it the plot, the lines last drawn and the x axis.

**Smoothing inside steps and the Stats menu (2026-10-26).** With the "smooth inside steps" toggle on (Smoothing group) and a dataset that has steps, each step is smoothed on its own, so a
window never mixes two solutions; without steps the whole trace is smoothed. The Stats menu (View tab) writes mean +/- SD of every line in every step on the plot, level with the legend's rows
(``stats_group.py`` the controls, ``stats_table.py`` the item, ``plan/stats_table.py`` what is written). Both need the steps and the cube times, which `StepOverlays.step_inputs` turns into the two extra
inputs of the request (the pieces to smooth, the cubes of each step); the numbers are computed in the worker with the curves, so they follow smoothing, baseline and spike removal. A moved,
reset or restored boundary asks for a redraw when either is on (`StepOverlays._on_plan_changed`); a note or colour edit does not. Design and measurements: ``docs/step_statistics_backbone_2026-10-26.md``.

**Shared with the ROI table (2026-11-05).** The fitted traces and the helper process live in a `TraceStore` (``analysis/trace_store.py``) and the x axis, the baseline window and the Subtract
switch in a `SensorgramAxis` (``selection/sensorgram_axis.py``); the app gives the same two objects to the ROI / Groups table's step columns (``selection/step_table_module.py``), so a ROI fitted for
one is not fitted again for the other and both compute their numbers from the same x and the same baseline. Given none, this panel makes its own trace store (tests) and takes the settings' one axis (`SensorgramAxis.of`).

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
from dataclasses import asdict, dataclass, replace

import numpy as np
from PyQt6.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QCursor
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QMenu, QSpinBox, QToolTip, QVBoxLayout, QWidget

from lspr_ui import get_active_theme

from ...analysis.live_state import AnalysisLiveModule
from ...analysis.scan_mask_marks import mask_marks
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
from ...selection.sensorgram_axis import SensorgramAxis
from ...analysis.trace_store import TraceStore
from ...analysis.settings import (
    SENSORGRAM_AGGREGATIONS,
    SENSORGRAM_BANDS,
    SMOOTHING_METHODS,
)
from ...analysis.settings_module import AnalysisSettingsModule
from ...analysis.trace_pool import TracePool
from ...applog import timed
from ...dataset import DatasetModule
from ...image_tools.mask.module import MaskModule
from ...selection.experiment_plan_module import ExperimentPlanModule
from ...roi import RoiToolbox
from ...selection import CubeDisplayModule, SelectionModule
from ...selection.cube_display_module import MODE_CUBE, MODE_TIME
from ...storage.ui_state_keys import (
    SENSORGRAM_BASELINE_BAND_ALPHA,
    SENSORGRAM_BASELINE_BAND_COLOR,
    SENSORGRAM_BASELINE_BAND_VISIBLE,
    SENSORGRAM_ANCHOR_POINT_SCOPE,
    SENSORGRAM_RIBBON_OPEN,
    SENSORGRAM_RIBBON_TAB,
    read,
)
from ..live_toggle import LiveToggle
from ..ribbon_fields import style_ribbon_number_field
from ..image.tool_ribbon import ImageToolRibbon
from ..ribbon_drawer import RibbonDrawer
from ..ribbon_row import RibbonRow
from ..image.roi_overlay_controls import RoiOverlayControls
from ..image_first import ImageFirst
from ...analysis.export_tables import sensorgram_table, step_stats_table
from ..panel_task import PanelTask
from ..plot_export_button import ExportData, PlotExportButton
from ..ui_state import UiStateStore
from ..spectra.pick_menu import PickMenu
from ..spectra.view_menu import IconToggle
from ..visibility_gate import HiddenPanelGate
from ...analysis.baseline_drift import METHOD_ANCHORS, METHOD_WINDOW, curve_subtracted, uses_anchor_steps
from .baseline_controls import DriftBaselineControls, drift_summary, pencil_icon
from .baseline_selector_menu import DEFAULT_ALPHA, DEFAULT_COLOR, SPEC as BASELINE_BAND_SPEC, BaselineSelectorMenu, make_font_box, make_height_box
from .checks_group import ChecksControls
from .mask_marks_toggle import MaskMarksToggle
from .stale_marks_toggle import StaleMarksToggle
from .stat_icons import band_picture, center_picture, display_picture, smoothing_picture, spikes_icon, subtract_icon, within_steps_icon
from .text_link import TextSizeLink
from .view_controls import SensorgramViewControls
from .step_overlays import StepOverlays
from .plot import SensorgramPlot

logger = logging.getLogger(__name__)

TASK_ID = "sensorgram.compute"
PLOT_TAB = "Plot"  # the control row's first tab: how the traces are made (display, statistic, smoothing, baseline)
VIEW_TAB = "View"  # its second (2026-11-13): how everything is drawn: the marks, points and lines, the legend, the plan bar and the step table (`view_controls.py`)
PLAN_TAB = "Experimental plan"  # its third: the baseline / signal steps, the editing of the plan, the evaluation and the checks; the plan bar's structure tools are live only here
MASK_MARKS_KEY = "sensorgram/mask_marks"
STALE_MARKS_KEY = "sensorgram/stale_marks"
BAND_HEIGHT_KEY = "sensorgram/baseline_band_height"
BAND_FONT_KEY = "sensorgram/baseline_readout_font_size"
_REDRAW_COALESCE_MS = 100  # sketch §8
_JOIN_SECONDS = 5.0
_PROGRESS_MIN_ROIS = 8  # a status line "Fitting ROI 3 of 160" only for runs long enough to need it
DISPLAY_LABELS = {DISPLAY_INDIVIDUAL: "Individual", DISPLAY_AVERAGE_ALL: "Average all", DISPLAY_BY_GROUP: "Average by group"}
DISPLAY_ORDER = (DISPLAY_INDIVIDUAL, DISPLAY_BY_GROUP, DISPLAY_AVERAGE_ALL)  # as listed in the menu: one line per ROI, one per group, one for all
CENTER_LABELS = {"mean": "Mean", "median": "Median"}
BAND_LABELS = {"sd": "SD band", "sem": "SEM band"}
SMOOTHING_LABELS = {"none": "No smoothing", "savgol": "Savitzky-Golay", "moving_average": "Moving average"}
DISPLAY_TIPS = {
    DISPLAY_INDIVIDUAL: "One line for every selected ROI.",
    DISPLAY_BY_GROUP: "One line for every ROI group (ROIs in no group form one more): the ROIs of a group are combined at each cube.",
    DISPLAY_AVERAGE_ALL: "One line for all the selected ROIs, combined at each cube.",
}
CENTER_TIPS = {
    "mean": "The ordinary average of the ROIs at each cube; every ROI counts the same, so a wild ROI pulls it.",
    "median": "The middle value of the ROIs at each cube; a few wild ROIs hardly move it.",
}
BAND_TIPS = {
    "sd": "SD band: the line plus and minus one standard deviation of the ROIs at each cube. It shows how much the ROIs differ and does not shrink with more ROIs.",
    "sem": "SEM band: the standard error of the mean, the sample standard deviation divided by the square root of the number of ROIs. It shows how well the average is known and narrows with more ROIs.",
}
SMOOTHING_TIPS = {
    "none": "The traces are drawn as fitted.",
    "savgol": "Savitzky-Golay: a low-order polynomial fitted in a sliding window; keeps the height and width of peaks better than a plain average.",
    "moving_average": "Moving average: the mean of the points in a sliding window.",
}
WITHIN_STEPS_TIP = (
    "Smooth inside each experiment step: every step is smoothed on its own, so the smoothing window never reaches over a step boundary and one solution's level "
    "is not mixed into the next. A cube that straddles a boundary is left unsmoothed. The statistics of the steps are taken from this same curve. "
    "Off, or a dataset without steps: the whole trace is smoothed in one piece."
)
NO_STEPS_NOTE = " This dataset has no experiment steps, so the whole trace is smoothed."
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
    plan_bar_settings_changed = pyqtSignal()
    """The plan bar's display settings changed (text content and separators among them): the ROI table titles its step columns the same way."""

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
        trace_in_process: bool = True,
        mask: MaskModule | None = None,
        experiment_plan: ExperimentPlanModule | None = None,
        trace_store: TraceStore | None = None,
        axis: SensorgramAxis | None = None,
    ) -> None:
        """`image_busy`: True while the Image panel has a frame in flight; a computation then waits for it (`ImageFirst`). `trace_in_process`: the
        fits run in a helper process (`TracePool`), so they never compete with the window for the interpreter lock; False computes them in the worker
        thread (tests that spy on the fit, debugging); it applies only to the store this panel makes for itself. `mask`: tells which masks are individual (the mask marks); without it every mask counts as persistent.
        `experiment_plan`: the experiment steps for the plan bar; without it the Experimental plan group stays greyed out.
        `trace_store`: the fitted traces and the helper process, shared with the ROI / Groups table's step columns (`analysis/trace_store.py`); without one the panel makes its own and shuts it down itself.
        `axis`: the x axis and the baseline switch, shared with the same table (`selection/sensorgram_axis.py`); without one it takes the settings' own (`SensorgramAxis.of`)."""
        super().__init__(parent)
        self._mask = mask
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
        self._owns_traces = trace_store is None
        self._traces = trace_store if trace_store is not None else TraceStore(scan_results, self, in_process=trace_in_process)
        self._axis = axis if axis is not None else SensorgramAxis.of(scan_results, dataset, cube_display, analysis_settings)
        self._last: tuple[_Job, list[Series]] | None = None
        self._syncing = False

        self._display = PickMenu(self, name_on_face=False)  # pick-up menus that answer like the combo boxes they replaced
        for key in DISPLAY_ORDER:
            self._display.add_choice(key, DISPLAY_LABELS.get(key, key), DISPLAY_TIPS[key], display_picture(key))
        self._display.finish()
        self._display.setToolTip(
            "Individual: one line per selected ROI. Average all / by group: each ROI is processed alone first, then the finished traces "
            "are combined (never the spectra)."
        )
        self._center = PickMenu(self, name_on_face=False)
        for key in SENSORGRAM_AGGREGATIONS:
            self._center.add_choice(key, CENTER_LABELS.get(key, key), CENTER_TIPS[key], center_picture(key))
        self._center.finish()
        self._center.setToolTip("How the ROIs of an average are combined at each point.")
        self._band = PickMenu(self, name_on_face=False)
        for key in SENSORGRAM_BANDS:
            self._band.add_choice(key, BAND_LABELS.get(key, key), BAND_TIPS[key], band_picture(key))
        self._band.finish()
        self._band.setToolTip("Shaded band around an average: standard deviation of the ROIs, or standard error of the mean (SD / sqrt(n)).")
        self._smoothing = PickMenu(self, name_on_face=False)  # a pick-up menu that answers like the combo box it replaced
        for key in SMOOTHING_METHODS:
            self._smoothing.add_choice(key, SMOOTHING_LABELS.get(key, key), SMOOTHING_TIPS[key], smoothing_picture(key))
        self._smoothing.finish()
        self._smoothing.setToolTip("Smoothing of each ROI's trace, after spike removal.")
        self._window = QSpinBox(self)
        self._window.setRange(3, 301)
        self._window.setSingleStep(2)
        style_ribbon_number_field(self._window, "301")
        self._window.setToolTip("Smoothing window in points (made odd, never longer than the trace). Shown while a smoothing method is chosen.")
        self._within_steps = IconToggle(within_steps_icon, WITHIN_STEPS_TIP + NO_STEPS_NOTE, self)
        self._within_steps.setEnabled(False)  # until a dataset with steps is open (`_on_steps_available`)
        self._spikes = IconToggle(
            spikes_icon,
            "Remove spikes: replace single-point outliers (Hampel filter: 3.5 scaled MADs from the rolling median of 5 points) before smoothing.",
            self,
        )
        self._baseline = IconToggle(
            subtract_icon,
            "Subtract the baseline, so every trace reads as a shift from it instead of an absolute position. With \"Range\" the mean of each trace inside the "
            "baseline selector is taken off (show the selector with the pencil and drag it), or the curve through the plan steps marked as baseline anchors; with "
            "\"Anchor points\" the curve through the points clicked on the plot. A curve is drawn dashed while this is off. The same switch as a double-click on "
            "the y axis label.",
            self,
        )
        self._mask_toggle = MaskMarksToggle(self)
        self._mask_toggle.setChecked(True)
        self._stale_toggle = StaleMarksToggle(self)
        self._stale_toggle.setChecked(True)  # on by default: out-of-date results are never hidden unless the user says so
        self._view_controls = SensorgramViewControls(self)  # the View tab's band / anchors toggles, points & lines and legend menus (2026-11-13)
        self._band_visible = True  # the baseline selector: the controls hold the truth, these copies follow their signals
        self._band_color = QColor(DEFAULT_COLOR)
        self._band_alpha = DEFAULT_ALPHA
        self._band_controls = RoiOverlayControls(
            BASELINE_BAND_SPEC, visible=self._band_visible, color=self._band_color, alpha=self._band_alpha, parent=self
        )
        self._band_height = make_height_box(self)
        self._band_font = make_font_box(self)
        self._band_menu = BaselineSelectorMenu(self._band_controls, self._band_height, self._band_font, self)
        # The Range ("window") method's edit pencil (2026-11-09): shows the baseline selector band to drag or type; it replaces the show / hide toggle
        # that sat in the band menu (the band's visibility is still the remembered `SENSORGRAM_BASELINE_BAND_VISIBLE`). While it is on, a right-click on
        # a plan step also offers to make the step a baseline anchor (`_baseline_step_menu`).
        self._band_controls.set_toggle_shown(False)
        self._band_edit = IconToggle(
            pencil_icon,
            "Edit the baseline range: shows the baseline selector band on the plot, to drag it or its edges, or type its range. While this is on, "
            "right-click a step of the experimental plan to make it a baseline anchor (the band is then not used and not shown). Off hides the band; "
            "Subtract still uses the range it marks, or the anchor steps.",
            self,
        )
        self._band_edit.setChecked(self._band_visible)
        # The drift baseline (2026-11-09): method, shape and the anchor points clicked on the plot.
        self._drift = DriftBaselineControls(self, analysis_settings, lambda: tuple(sorted(self._selection.selected_roi_ids())), self._cube_axis_now)
        self._status = QLabel("", self)
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f"color: {get_active_theme().text_dim};")
        self._plot = SensorgramPlot(self)
        self._axis.set_range_fallback(self._plot.x_range)  # a default baseline window on an axis with fewer than two points goes where the plot is looking
        self._steps = StepOverlays(
            self, experiment_plan=experiment_plan, plot=self._plot, dataset=dataset, settings=analysis_settings, cube_display=cube_display, drawn=self._drawn, x_axis=self._x_axis,
            schedule_redraw=self._schedule_redraw, defer=lambda: self._gate.defer(), on_steps_available=self._on_steps_available,  # the gate is made further down
            current_cube=self._selection.current_cube,
        )
        # The legend's text size and the stats numbers' text size can be linked (2026-11-13): a chain button in each of the two menus, one state, one rule (`text_link.py`).
        self._text_link = TextSizeLink(
            self._view_controls.legend_text, self._view_controls.text_link, self._steps.stats_controls.size_box, self._steps.stats_controls.text_link, self
        )
        self.task = PanelTask(TASK_ID, "Sensorgram", self)  # connected to the status bar's task indicator by app_rewrite
        self._steps.plan_controls.settings_changed.connect(self.plan_bar_settings_changed)

        self._live_toggle = LiveToggle(self)
        self._live_toggle.bind(self._live)
        self._ribbon = RibbonRow(self)
        self._ribbon.add_group("Live", self._live_toggle)
        self._ribbon.add_group("Statistic", self._display, self._center, self._band)  # how the ROIs are shown, and how an average is made
        self._ribbon.add_group("Smoothing", self._smoothing, self._window, self._within_steps, self._spikes)
        self._ribbon.add_group("Baseline", self._baseline, *self._drift.widgets(), self._band_edit, self._band_menu)
        self.export_button = PlotExportButton("sensorgram", lambda: self._plot.plot_item(), self._export_data, self, svg_parts=self._plot.export_parts)
        self._ribbon.add_group("Export", self.export_button)
        self._ribbon.add_stretch()
        # The View tab (2026-11-13) holds every option of how the plot is drawn, as the Image panel's View tab does: what is shown (the band, the anchors, the mask and out-of-date
        # marks), the points and lines, the legend, and the two groups that were on the Experimental plan tab until now, the plan bar's and the step table's.
        self._view_ribbon = RibbonRow(self)
        self._view_ribbon.add_group("Show", self._view_controls.band, self._view_controls.anchors, self._mask_toggle, self._stale_toggle)
        self._view_ribbon.add_group("Points & lines", self._view_controls.lines_menu)
        self._view_ribbon.add_group("Legend", self._view_controls.legend_menu)
        self._view_ribbon.add_group("Steps", *self._steps.plan_controls.widgets())  # the plan bar's menu
        self._view_ribbon.add_group("Evaluation", *self._steps.plan_controls.evaluation_widgets())  # the markers B1 / S1 / Sb on the bar: shown, where, in what colour (2026-11-13)
        self._view_ribbon.add_group("Stats", *self._steps.stats_controls.widgets())  # the step table's menu
        self._view_ribbon.add_stretch()
        # The experimental plan has a tab of its own (2026-10-29): the baseline / signal steps, the editing of the plan, the evaluation and the checks. The plan bar's structure tools
        # (remove, add, unify, the roles in the right-click menu) are live only while it is the active tab, as the ROI gestures are only live on the Image ribbon's ROIs tab.
        # The Edit group's buttons are on this tab, so they are reachable only here by themselves.
        self._plan_ribbon = RibbonRow(self)
        self._plan_ribbon.add_group("Edit", *self._steps.edit_controls.widgets())  # add / remove / merge / restore steps (2026-11-10)
        self._plan_ribbon.add_group("Evaluation", *self._steps.evaluation.widgets(), *self._steps.bulk.widgets())  # the pairs, then S_bulk (2026-11-12)
        self.checks_controls = ChecksControls(self)  # the data-quality checks (2026-11-07): the button only; `checks_presenter.py` is what the app connects to it
        self._plan_ribbon.add_group("Checks", *self.checks_controls.widgets())
        self._plan_ribbon.add_stretch()
        self._ribbon_tabs = ImageToolRibbon([(PLOT_TAB, self._ribbon), (VIEW_TAB, self._view_ribbon), (PLAN_TAB, self._plan_ribbon)], self)
        self._ribbon_drawer = RibbonDrawer(self._ribbon_tabs, "Show the sensorgram controls.", self, handle_alignment=Qt.AlignmentFlag.AlignTop)
        for row in (self._ribbon, self._view_ribbon, self._plan_ribbon):
            row.roll_up_requested.connect(lambda: self._ribbon_drawer.set_expanded(False))
        self._ribbon_tabs.category_changed.connect(self._on_ribbon_tab)
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
        self._within_steps.toggled.connect(self._on_controls_changed)
        self._spikes.toggled.connect(self._on_controls_changed)
        self._baseline.toggled.connect(self._on_baseline_toggled)
        self._mask_toggle.toggled.connect(lambda _on: self._draw_last())
        self._stale_toggle.toggled.connect(lambda _on: self._draw_last())
        self._view_controls.settings_changed.connect(self._on_view_changed)
        self._plot.baseline_window_changed.connect(self._on_baseline_dragged)
        self._plot.y_label_double_clicked.connect(self._on_y_label_double_clicked)
        self._plot.x_label_double_clicked.connect(self._on_x_label_double_clicked)
        self._band_height.valueChanged.connect(self._plot.set_baseline_height)
        self._band_font.valueChanged.connect(self._plot.set_readout_font_size)
        self._plot.baseline_range_edited.connect(self._on_baseline_dragged)  # typed values are stored exactly as a dragged band's
        self._band_controls.visibility_changed.connect(self._on_band_visibility)
        self._band_edit.toggled.connect(self._band_controls.set_visible)  # the pencil is the band's show / hide
        self._band_controls.color_changed.connect(self._on_band_color)
        self._band_controls.alpha_changed.connect(self._on_band_alpha)
        self._plot.x_clicked.connect(self._on_plot_clicked)
        self._drift.edit.toggled.connect(self._plot.set_anchor_mode)
        self._plot.anchor_point_added.connect(self._drift.add_point_at)
        self._plot.anchor_point_remove_requested.connect(self._drift.remove_point_at)
        self._plot.anchor_menu_requested.connect(self._show_anchor_menu)
        if self._steps.actions is not None:
            self._steps.actions.baseline_menu = self._baseline_step_menu  # a right-click on a step with the Range pencil on
        # too few anchor steps for the shape: said at the pointer, the way the plan's notices are (the status row is not shown)
        self._drift.say = lambda text: QToolTip.showText(QCursor.pos(), text, self)
        self._drift.steps_in_plan = lambda: len(self._steps.anchor_step_indices())

        self._selection.roi_selection_changed.connect(lambda _ids: self._schedule_redraw())
        self._selection.roi_selection_changed.connect(lambda _ids: self._drift.refresh_tools())  # "apply to all ROIs" depends on the lines shown
        self._following_play = False  # `follow_play`: the cursor follows the frame on screen, not the slider
        self._play_cube: int | None = None
        self._selection.cube_changed.connect(
            lambda _cube: (None if self._following_play else self._update_cursor(), self._steps.cursor_moved())
        )
        self._settings.settings_changed.connect(lambda _change: self._on_settings_changed())
        self._roi_toolbox.geometry_changed.connect(self._on_roi_changed)
        self._roi_toolbox.cosmetic_changed.connect(lambda _change: self._schedule_redraw())
        self._axis.axis_changed.connect(self._schedule_redraw)  # Cube / Time: the x values changed (the axis object has carried the baseline window over)
        self._scan.results_changed.connect(self._on_results_changed)
        self._dataset.dataset_loaded.connect(lambda _dataset: self._plot.forget_view())  # a zoom kept over redraws means nothing in another dataset
        self._scan.staleness_changed.connect(self._draw_last)

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)
        self._sync_controls()
        self._schedule_redraw()

    # -- wiring with the owner -------------------------------------------------------------------------------------------------

    def _on_view_changed(self) -> None:
        """An option of the View tab changed (or was restored): the plot takes it and the last result is drawn again. Nothing is recomputed."""
        self._plot.set_view(self._view_controls.settings())
        self._draw_last()

    def restore_ui_state(self, store: UiStateStore) -> None:
        store.bind(MASK_MARKS_KEY, self._mask_toggle)
        store.bind(STALE_MARKS_KEY, self._stale_toggle)
        self._text_link.begin_restore()  # the legend's and the stats' text sizes are restored one after the other: no size is copied between them until both are back
        self._view_controls.restore_ui_state(store)  # also hands the restored options to the plot
        store.bind(BAND_HEIGHT_KEY, self._band_height)
        self._plot.set_baseline_height(self._band_height.value())  # a restored value arrives without a signal
        store.bind(BAND_FONT_KEY, self._band_font)
        self._steps.restore_ui_state(store)
        self._text_link.end_restore()  # both text sizes are restored now: linked, they are made equal (the Legend menu's wins)
        self._plot.set_readout_font_size(self._band_font.value())
        saved_color = read(store, SENSORGRAM_BASELINE_BAND_COLOR)
        self._band_visible = read(store, SENSORGRAM_BASELINE_BAND_VISIBLE) is not False
        self._band_color = QColor(saved_color) if isinstance(saved_color, str) and QColor(saved_color).isValid() else QColor(DEFAULT_COLOR)
        self._band_alpha = min(max(float(read(store, SENSORGRAM_BASELINE_BAND_ALPHA)), 0.0), 1.0)
        # not a widget `bind`: three values behind one control
        self._band_controls.set_state(visible=self._band_visible, color=self._band_color, alpha=self._band_alpha)
        self._band_edit.setChecked(self._band_visible)
        self._plot.set_baseline_style(self._band_color, self._band_alpha)
        self._band_controls.visibility_changed.connect(lambda on: store.set(SENSORGRAM_BASELINE_BAND_VISIBLE.key, bool(on)))
        self._band_controls.color_changed.connect(lambda color: store.set(SENSORGRAM_BASELINE_BAND_COLOR.key, color.name()))
        self._band_controls.alpha_changed.connect(lambda alpha: store.set(SENSORGRAM_BASELINE_BAND_ALPHA.key, float(alpha)))
        self._draw_last()
        self._ribbon_drawer.set_expanded(read(store, SENSORGRAM_RIBBON_OPEN) is not False, animate=False)
        self._ribbon_drawer.expanded_changed.connect(lambda open_: store.set(SENSORGRAM_RIBBON_OPEN.key, bool(open_)))
        saved_tab = read(store, SENSORGRAM_RIBBON_TAB)
        if isinstance(saved_tab, str) and self._ribbon_tabs.set_category(saved_tab):
            self._on_ribbon_tab(saved_tab)  # a restored tab is not a user change, but the tools of the tab must follow it
        self._ribbon_tabs.category_changed.connect(lambda label: store.set(SENSORGRAM_RIBBON_TAB.key, label))
        self._drift.set_point_scope(str(read(store, SENSORGRAM_ANCHOR_POINT_SCOPE)))
        self._drift.scope_changed = lambda scope: store.set(SENSORGRAM_ANCHOR_POINT_SCOPE.key, scope)

    def set_panel_visible(self, visible: bool) -> None:
        """Connected to the dock's ``visibilityChanged``: a hidden panel does no work and catches up once when shown."""
        self._gate.set_visible(visible)

    def bulk_overlays(self):
        """The step overlays (`StepOverlays`): what the S_bulk plot reads (`bulk_inputs`, `bulk_inputs_changed`, `set_numbers_for_plot`)."""
        return self._steps

    def bulk_controls(self):
        """The S_bulk button of the Evaluation group (`bulk_group.BulkControls`): its weighting is the ROI table's S_bulk column's too (`app_rewrite._follow_plan_bar`)."""
        return self._steps.bulk

    def plan_bar_settings(self):
        """The plan bar's display settings as set now (`plan.bar_model.PlanBarSettings`): the text mode and separators decide how a step's note is written in a title."""
        return self._steps.plan_controls.settings()

    def refresh_theme(self) -> None:
        self._live_toggle.refresh_theme()
        self._ribbon.refresh_theme()
        self._plan_ribbon.refresh_theme()
        self._ribbon_tabs.refresh_theme(get_active_theme())
        self._mask_toggle.refresh_theme()
        self._stale_toggle.refresh_theme()
        for menu in (self._display, self._center, self._band, self._smoothing):
            menu.refresh_theme()
        self._spikes.refresh_theme()
        self._within_steps.refresh_theme()
        self._baseline.refresh_theme()
        self._drift.refresh_theme()
        self._view_controls.refresh_theme()
        self._band_controls.refresh_theme(get_active_theme())
        self._band_edit.refresh_theme()
        self._band_menu.refresh_theme(get_active_theme())
        self._steps.refresh_theme()
        self.checks_controls.refresh_theme()
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
        if self._owns_traces:  # a store the app made is shut down by the app (the step table may still be using it)
            self._traces.shutdown()

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
            self._window.setVisible(stats.smoothing_method != "none")  # the window means nothing without a smoothing method
            self._within_steps.setChecked(bool(stats.smooth_within_steps))
            self._within_steps.setVisible(stats.smoothing_method != "none")  # nor does smoothing inside steps
            self._spikes.setChecked(bool(stats.spike_rejection_enabled))
            self._baseline.setChecked(bool(stats.baseline_enabled))
            self._drift.sync(stats)
            # a toggle with nothing to act on is greyed out; the anchors toggle also owns the dashed baseline curve, which the Range method's marked steps have too
            self._view_controls.set_available(band=averaged, anchors=stats.baseline_method == METHOD_ANCHORS or uses_anchor_steps(stats))
            for widget in (self._band_edit, self._band_menu):
                widget.setVisible(stats.baseline_method != METHOD_ANCHORS)  # the window selector means nothing to the anchors method
        finally:
            self._syncing = False

    def _on_controls_changed(self, *_args: object) -> None:
        if self._syncing:
            return
        self._settings.set_statistics_settings(replace(
            self._settings.statistics_settings(),
            sensorgram_display_mode=self._display.currentData(), sensorgram_aggregation=self._center.currentData(),
            sensorgram_band=self._band.currentData(), smoothing_method=self._smoothing.currentData(),
            smoothing_window=self._window.value(), smooth_within_steps=self._within_steps.isChecked(), spike_rejection_enabled=self._spikes.isChecked(),
        ))

    def _on_ribbon_tab(self, label: str) -> None:
        """Another control tab is shown: the plan bar's structure tools (remove / add / unify steps, the baseline and signal roles in the right-click menu) are live only on the
        Experimental plan tab, so nothing structural happens by accident while working on the traces."""
        self._steps.set_structure_tools(label == PLAN_TAB)
        if label == PLAN_TAB:
            self._drift.edit.setChecked(False)  # editing steps and editing anchor points never at the same time

    def current_ribbon_tab(self) -> str:
        return self._ribbon_tabs.current_category()

    def _on_baseline_toggled(self, on: bool) -> None:
        if self._syncing:
            return
        self._axis.set_baseline_enabled(on)

    def _on_baseline_dragged(self, low: float, high: float) -> None:
        """The selector was moved: remember where (the axis object puts a typed value on a data point as a dragged one is)."""
        self._axis.set_baseline_window(low, high)

    def _range_decimals(self) -> int:
        """Decimals of the baseline range readout: whole cubes, or tenths of a second."""
        return 1 if self._cube_display.effective_mode() == MODE_TIME else 0

    def _on_y_label_double_clicked(self) -> None:
        """Double-click on the y axis label: absolute position <-> shift from the baseline (the Subtract switch)."""
        self._on_baseline_toggled(not self._settings.statistics_settings().baseline_enabled)

    def _on_x_label_double_clicked(self) -> None:
        """Double-click on the x axis label: time <-> cube number, for the whole app (the shared cube display mode)."""
        self._cube_display.set_mode(MODE_CUBE if self._cube_display.effective_mode() == MODE_TIME else MODE_TIME)

    def _baseline_step_menu(self, menu: QMenu, steps: list) -> bool:
        """A right-click on plan steps: while the Range baseline's pencil is on (and not on the Experimental plan tab, where steps are edited), the
        baseline's items (make / remove anchor steps) go at the top of the step's menu. Returns whether they were added."""
        stats = self._settings.statistics_settings()
        if stats.baseline_method != METHOD_WINDOW or not self._band_edit.isChecked() or self.current_ribbon_tab() == PLAN_TAB:
            return False
        self._drift.fill_step_menu(menu, steps)
        return True

    def _show_anchor_menu(self, x: float, screen_pos: QPoint) -> None:
        """A right click away from every anchor square in the anchor edit: the anchor menu at the pointer (non-blocking)."""
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._drift.fill_context_menu(menu, x)
        menu.popup(screen_pos)

    def _cube_axis_now(self) -> tuple[np.ndarray, np.ndarray] | None:
        """(the plot's x of each cube, the cube number of each) of the scan shown now, None without one: what the anchor points are placed on."""
        stored = self._scan.stored()
        if stored is None:
            return None
        _label, x = self._x_axis()
        return np.asarray(x, dtype=np.float64), np.asarray(stored.cube_indices)

    def _on_band_visibility(self, on: bool) -> None:
        self._band_visible = bool(on)
        self._band_edit.setChecked(self._band_visible)
        self._draw_last()

    def _on_band_color(self, color: QColor) -> None:
        self._band_color = QColor(color)
        self._plot.set_baseline_style(self._band_color, self._band_alpha)

    def _on_band_alpha(self, alpha: float) -> None:
        self._band_alpha = float(alpha)
        self._plot.set_baseline_style(self._band_color, self._band_alpha)

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
        self._schedule_redraw()  # the shared trace store and the axis empty themselves on the same signal

    # -- the x axis -----------------------------------------------------------------------------------------------------------------

    def _x_axis(self) -> tuple[str, np.ndarray]:
        """(axis label, x per cube of the file): time in seconds, or the cube number. The shared axis object keeps it (``selection/sensorgram_axis.py``)."""
        return self._axis.label_and_x()

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

    def follow_play(self, playing: bool) -> None:
        """While the slideshow plays, the cursor follows the frame on screen (`show_play_frame`) instead of the cube slider, which is
        one step ahead of it. Back to the slider when Play stops."""
        self._play_cube = None
        self._following_play = playing
        if playing:
            self._plot.begin_play_repaints()
        else:
            self._plot.end_play_repaints()
            self._update_cursor()

    def repaint_after_play_frame(self) -> None:
        """A Play frame is shown (`ImagePanel.play_frame_shown`, before the image view repaints): the cursor's strip is repainted with it."""
        if self._following_play:
            self._plot.repaint_play_cursor()

    def show_play_frame(self, cube: int) -> None:
        """The Image panel is about to show this cube's frame during Play (`ImagePanel.play_frame_showing`): the cursor moves now, so its
        repaint goes to the screen with the image's, in one window repaint (2026-11-11: it used to take a second repaint every frame)."""
        if self._following_play:
            self._play_cube = int(cube)
            self._update_cursor()

    def _update_cursor(self) -> None:
        """The dashed line follows the cube slider (during Play: the frame on screen) without recomputing anything."""
        if self._gate.defer():
            return
        stored = self._scan.stored()
        if stored is None or self._last is None:
            self._plot.set_current_x(None)
            return
        cube = int(self._selection.current_cube()) if self._play_cube is None else self._play_cube
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
        self._steps.refresh()
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
        self._warm_up_pool()
        if not roi_ids:
            self._clear_with("Select one or more ROIs (ROI table or image) to see their sensorgram.")
            return
        _label, x = self._x_axis()
        detection = self._roi_toolbox.detection_settings()
        metric = self._settings.metric_settings()
        groups = tuple(GroupSpec(g.name, g.sample_color_hex, tuple(g.area_roi_ids)) for g in self._roi_toolbox.groups())
        segments, membership = self._steps.step_inputs(stored)
        request = SensorgramRequest(
            roi_ids, detection.formula_key, metric, self._settings.statistics_settings(), x, groups, self._roi_colors(roi_ids), segments, membership,
            cube_numbers=np.asarray(stored.cube_indices) if self._settings.statistics_settings().baseline_method == METHOD_ANCHORS else None,
            baseline_step_regions=self._steps.baseline_step_regions(stored),  # the Range baseline's marked anchor steps (None: the window's mean)
        )
        key = trace_key(stored, detection.formula_key, metric, [g.roi_ids for g in groups])
        known = self._traces.known(key)  # the traces fitted so far (any panel's); other formula / fit / metric: the store drops the old ones
        if self._job is not None:
            self._job.cancel.set()  # a running (maybe long) computation is stopped between ROIs; what it finished is kept
            self._pending = True
            return
        if self._image_first.should_wait():  # the frame the user is waiting for goes first (the fit slows its read down 10-20x)
            self._schedule_redraw()  # ask again in one coalescing step (and not at all while the dock is hidden)
            return
        job = _Job(self._generation, stored, request, key, threading.Event())
        self._job = job
        missing = sum(1 for r in roi_ids if r not in known)
        self.task.begin(f"fitting {missing} ROI{'s' if missing != 1 else ''}" if missing else "applying the statistics")
        self._thread = threading.Thread(target=self._compute, args=(job, known), name="sensorgram-compute", daemon=True)
        self._thread.start()

    def fit_pool(self) -> TracePool | None:
        """The helper process the fits run in (None: in the worker thread), owned by the shared trace store; lent to the Spectra panel."""
        return self._traces.pool()

    def start_helper_early(self) -> None:
        """The app is about to open a dataset (launch with "reopen the last dataset"): start the helper process now, while the window is still being
        built and the dataset read, instead of when the scan is first drawn (2026-10-24: it took 2.1 s to start and the first fit waited ~0.75 s of
        that). Safe to call at any time and more than once."""
        self._warm_up_pool()

    def _warm_up_pool(self) -> None:
        """A scan is loaded and this panel is showing: start the helper process now (it imports numpy / scipy, a few seconds), so the first fit
        does not wait for that. Off the GUI thread."""
        self._traces.warm_up()

    def _compute(self, job: _Job, known: dict[int, np.ndarray]) -> None:
        total_missing = sum(1 for r in job.request.roi_ids if r not in known)
        report = (lambda done, total: self._progress.emit(done, total)) if total_missing >= _PROGRESS_MIN_ROIS else None
        try:
            with timed(logger, "sensorgram.compute", rois=len(job.request.roi_ids), new=total_missing):
                series, new = compute_sensorgram(
                    job.stored, job.request, known, cancelled=job.cancel.is_set, progress=report,
                    runner=self._traces.runner(),
                )
            self._result_ready.emit(job, series, new, None)
        except BaseException as exc:  # nothing may escape a thread silently
            logger.exception("Computing the sensorgram failed")
            self._result_ready.emit(job, None, {}, exc)

    def _on_progress(self, done: int, total: int) -> None:
        if self._job is not None and not self._job.cancel.is_set():
            self._status.setText(f"Fitting ROI {done} of {total}...")
            self.task.update(done / total if total else -1.0, f"fitting ROI {done} of {total}")

    def _on_result(self, job: _Job, series: object, new: object, error: object) -> None:
        self._job = None
        if isinstance(new, dict):
            self._traces.add(job.key, new)  # a cancelled run's finished traces are kept as well (the store ignores them if the key has moved on)
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

    def _stale_note(self, stale: np.ndarray | None) -> str:
        """The out-of-date state in words, for the View button's tool tip (the status row is hidden, so a switched-off mark must still say it)."""
        if stale is not None:
            reason = self._scan.stale_reason()
            return f"{int(stale.sum())} of {len(stale)} cubes are out of date{': ' + reason if reason else ''}."
        if self._scan.stale_cubes() is not None:
            return "every cube is up to date."
        return "checking if the scan is up to date..." if self._scan.is_checking() else ""

    def _mask_marks(self, stored: StoredScan, series: list[Series]) -> dict[str, tuple[np.ndarray, np.ndarray]] | None:
        """Per line, the cubes a persistent / an individual mask touched (None while the View button is off). Cheap: it reads ``n_masked``."""
        if not self._mask_toggle.isChecked():
            return None
        individual = {} if self._mask is None else self._mask.individual_cubes()
        return {s.key: mask_marks(stored, s.member_ids, individual) for s in series}

    def _draw_last(self) -> None:
        if self._gate.defer() or self._last is None:
            return
        job, series = self._last
        stale = self._stale_mask(job.stored)
        label, x_axis = self._x_axis()
        self._plot.set_snap_grid(x_axis)
        stats = job.request.statistics
        y_label = "Centroid position (nm)" if job.request.metric.metric_key == "centroid" else "Peak position (nm)"
        if job.request.metric.uses_template:
            y_label = "Position by template shift (nm)"  # the reference's peak + the shift (analysis/template_shift.py)
        elif job.request.metric.uses_response:
            y_label = "Position by response shape (shift-equivalent nm)"  # baseline peak + response in pure-shift nm (analysis/response_shape.py)
        if stats.baseline_enabled:
            y_label = y_label.replace("position", "shift")
        self._plot.set_axis_labels(label, y_label)
        anchors_method = stats.baseline_method == METHOD_ANCHORS
        drift_shown = anchors_method or uses_anchor_steps(stats)  # a curve through anchors: anchor points, or the Range method's marked steps
        self._plot.show_series(  # the toggle hides the marks, never the points
            series, stale if self._stale_toggle.isChecked() else None, self._mask_marks(job.stored, series),
            baseline_curves=drift_shown and not stats.baseline_enabled and self._plot.view().show_anchors,  # absolute traces: the dashed curve is what Subtract would take off (View tab: it can be hidden)
            # Hollow squares only for the Anchor points method, where they ARE the anchors (clicked, removable). The Range method's marked steps show
            # their anchor icon on the plan bar instead; a square in the middle of each step's trace was a second, confusing marker (maintainer, 2026-11-11).
            anchor_dots=("subtracted" if stats.baseline_enabled else "absolute") if anchors_method and self._plot.view().show_anchors else None,  # View tab: the squares can be hidden
        )
        self._stale_toggle.set_note(self._stale_note(stale))
        uncorrected = next((s.baseline_note for s in series if s.baseline_note), "") if drift_shown else ""
        self._drift.set_fit_summary(drift_summary(series) if drift_shown else [], uncorrected)
        self._plot.set_anchors_needed(self._drift.points_needed() if anchors_method else 0)  # the number beside the edit mode's cross-hair
        if stats.baseline_window_start is not None and stats.baseline_window_end is not None:
            window = (stats.baseline_window_start, stats.baseline_window_end)
        else:
            window = self._axis.default_baseline_window()  # where Subtract would put it; stored only once dragged, typed or subtracted
        self._plot.set_baseline_window(window if self._band_visible and not drift_shown else None, self._range_decimals())
        self._update_cursor()
        self._steps.refresh()
        self._status.setText(self._status_line(job, series, stale))

    def _export_data(self) -> ExportData | None:
        """What "Save data" writes (export what is drawn): the lines as plotted with the out-of-date cubes marked, and the step statistics table
        when it is on the plot; the record holds the settings that made them."""
        if self._last is None:
            return None
        job, series = self._last
        label, _x = self._x_axis()
        tables = {"sensorgram": sensorgram_table(series, label, job.stored.cube_indices, self._stale_mask(job.stored))}
        stats_item = self._plot.stats_item()
        steps = step_stats_table(stats_item.table()) if stats_item.is_shown() else None
        if steps is not None:
            tables["step_stats"] = steps
        request = job.request
        info = {
            "x_axis": label,
            "roi_ids": [int(r) for r in request.roi_ids],
            "settings": {"formula": request.formula_key, "metric": asdict(request.metric), "statistics": asdict(request.statistics)},
            "lines": [
                {
                    "label": s.label, "key": s.key, "members": [int(m) for m in s.member_ids], "baseline_nm": s.baseline,
                    **({"anchor_rms_nm": s.anchor_rms, "baseline_note": s.baseline_note}
                       if request.statistics.baseline_method == METHOD_ANCHORS or uses_anchor_steps(request.statistics) else {}),
                }
                for s in series
            ],
        }
        return ExportData(tables, info)

    def _drawn(self) -> tuple[StoredScan, list[Series]] | None:
        """The scan and the lines last drawn (None before the first draw or after a clear): what the step overlays are laid over."""
        return None if self._last is None else (self._last[0].stored, self._last[1])

    def _on_steps_available(self, has_steps: bool) -> None:
        """Told by the step overlays at every refresh whether the dataset has experiment steps: without them there is nothing to smooth step by step, the whole trace is smoothed."""
        self._within_steps.setEnabled(has_steps)
        self._within_steps.setToolTip(WITHIN_STEPS_TIP if has_steps else WITHIN_STEPS_TIP + NO_STEPS_NOTE)

    def _status_line(self, job: _Job, series: list[Series], stale: np.ndarray | None) -> str:
        parts = [f"{len(job.request.roi_ids)} ROI{'s' if len(job.request.roi_ids) != 1 else ''}"]
        if job.request.metric.fit_method == "none":
            parts.append("metric read off the measured points (no fit)")
        elif job.request.metric.uses_template:
            first, last = job.request.metric.reference_cube_range()
            parts.append(f"template shift against the mean of cubes {first}-{last}")
        elif job.request.metric.uses_response:
            base, signal = job.request.metric.response_ranges()
            if signal is not None:
                parts.append(f"response shape learned per group from cubes {base[0]}-{base[1]} -> {signal[0]}-{signal[1]} (shift-equivalent nm)")
        if stale is not None:
            reason = self._scan.stale_reason()
            parts.append(f"{int(stale.sum())} of {len(stale)} cubes OUT OF DATE (grey diamonds{': ' + reason if reason else ''}); run the scan again")
        elif self._scan.is_checking() and self._scan.stale_cubes() is None:
            parts.append("checking if the scan is up to date...")
        stats = job.request.statistics
        if curve_subtracted(stats):
            failed = [s for s in series if s.baseline_note]
            if failed:
                parts.append(f"{len(failed)} line{'s' if len(failed) != 1 else ''} not corrected ({failed[0].baseline_note})")
        elif stats.baseline_enabled and series and not any(np.isfinite(s.baseline) for s in series):
            parts.append("the baseline band holds no points: drag it over some")
        gaps = sum(1 for s in series if not np.isfinite(s.y).any())
        if gaps:
            parts.append(f"{gaps} series without any value")
        return " - ".join(parts)
