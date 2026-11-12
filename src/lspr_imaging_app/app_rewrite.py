"""Entry point and wiring of the app (``lspri-evaluation`` console script,
``src/main.py``).

CLAUDE.md says what is built, and ``docs/rewrite_architecture_sketch_2026-09.md``
describes the structure. This file creates the modules (:mod:`lspr_imaging_app.dataset`, ``image_tools``,
``roi``, ``analysis``, ``selection``, ``plan``, ``storage``), creates the
panels, and connects them with signals: ``build_main_window`` is the one place
where that happens. Session restore, autosave, the panel layout and the
remembered UI state are all wired from here. ``main`` adds logging and the
QApplication; ``src/main.py`` (used by ``run.py`` and the Suite launcher) creates the
QApplication and the launch screen first, so the screen is up while this module is
imported. The console script calls ``main`` directly, without the launch screen.
"""

from __future__ import annotations

import base64
import logging
import sys

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import Qt, QByteArray, QPoint, QTimer, QUrl
from PyQt6.QtGui import QAction, QActionGroup, QDesktopServices, QGuiApplication, QPixmapCache
from PyQt6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMenu, QMessageBox, QStatusBar, QWidget

from lspr_core import ACKNOWLEDGEMENT_REQUEST, DISCLAIMER_POINTS, LICENSE_NAME, NOTICE_URL, citation_text
from lspr_core.perf import get_runtime
from lspr_ui import APP_ID_LSPRI_EVALUATION, app_icon, set_active_theme, set_windows_app_user_model_id, show_about_dialog

from .analysis import AnalysisSettingsModule
from .analysis.scan_jobs import ScanSource, source_from_modules
from .analysis.live_state import AnalysisLiveModule
from .analysis.roi_tuning_module import RoiTuningModule, TuningView
from .analysis.roi_tuning_task import TASK_ID as ROI_TUNING_TASK_ID
from .analysis.scan_results import ScanResultsModule
from .selection.sensorgram_axis import SensorgramAxis
from .selection.data_checks_module import DataChecksModule
from .selection.image_motion_module import ImageMotionModule
from .selection.signal_map_module import SignalMapModule
from .selection.step_table_module import StepTableModule
from .analysis.trace_store import TraceStore
from .storage.session_index import session_dir_for
from .analysis.scan_store import FILE_NAME as SCAN_FILE_NAME, describe_file as describe_scan_file
from .analysis.scan_pool import MODE_NORMAL, MODES
from .analysis.scan_task import TASK_ID as ANALYSIS_SCAN_TASK_ID, ScanTask
from .applog import LogSystem, RingBufferHandler, configure_logging, log_event
from .applog import current as current_log_system
from .frame_naming import FrameNamingScheme
from .dataset import DatasetModule
from .gil_tuning import apply_switch_interval
from .display_options import (
    RESOLUTION_FULL,
    ResolutionSizes,
    apply_saved_resolution,
    format_size,
    monitor_pixels,
    resolution_sizes,
    running_resolution,
)
from .opengl_surface import configure_opengl_surface
from .gui.app_theme import LSPRI_BRIGHT_THEME, LSPRI_DARK_THEME, apply_app_theme
from .gui.windows_titlebar import apply_windows_titlebar_color
from .image_tools.mask_scope import MaskScope
from .image_tools.chromatic_auto_task import TASK_ID as CHROMATIC_TASK_ID, ChromaticAutoDetect
from .image_tools.mask.mask_pointers import MaskPointersModule
from .selection.experiment_plan_module import ExperimentPlanModule
from .image_tools.mask_auto_task import TASK_ID as AUTO_MASK_TASK_ID
from .image_tools.mask_export_task import TASK_ID as MASK_EXPORT_TASK_ID
from .panels.feature_map_task import TASK_ID as FEATURE_MAP_TASK_ID
from .dataset.module import EXPORT_TASK_ID as ZARR_EXPORT_TASK_ID
from .image_tools.mask_scan_task import TASK_ID as SCAN_TASK_ID, TransientScanTask
from .roi.array_task import TASK_ID as ARRAY_TASK_ID
from .panels.image.chromatic_tab import ChromaticUiValues
from .image_tools import (
    ActiveToolModule,
    BackgroundModule,
    ChromaticModule,
    GeometryModule,
    MaskModule,
    MaskScopeModule,
)
from .panels.dataset_info_bar import attach_to_menu_bar
from .panels.dock_container import PanelContainer, TitleChoice
from .panels.histogram import HistogramPanel
from .panels.log import LogPanel
from .panels.image import ImagePanel
from .panels.plot_export_button import ExportData, PlotExportButton
from .analysis.signal_map import signal_map_table
from .panels.image.overlay_cache import environment_budget_mb
from .panels.image.overlay_style import OverlayStyle
from .panels.layout_presets import wire_view_menu
from .panels.panel_visibility import ensure_floating_panels_on_screen, wire_panel_visibility_menu
from .panels.preferences_dialog import Preferences, PreferencesDialog
from .panels.shortcuts_dialog import ShortcutsDialog
from .perf_setup import apply_saved_preferences as apply_saved_perf_preferences
from .perf_setup import saved_count as perf_saved_count
from .perf_setup import install_runtime as install_perf_runtime
from .perf_setup import status_text as perf_status_text
from .perf_setup import write_perf_preferences
from .panels.roi_table import RoiTablePanel
from .panels.check_report_window import CheckReportWindow
from .panels.image.analysis_tab import AnalysisTab
from .panels.image.motion_presenter import MotionPresenter
from .panels.sensorgram.checks_presenter import ChecksPresenter
from .panels.image.tuning_tab import TuningTab
from .panels.sensorgram import SensorgramPanel
from .panels.roi_table.panel import TASK_ID as ROI_TABLE_TASK_ID
from .panels.sensorgram.panel import TASK_ID as SENSORGRAM_TASK_ID
from .panels.spectra import SpectraPanel
from .panels.spectra.panel import TASK_ID as SPECTRA_TASK_ID
from .launch import LaunchSplash, StageClock
from .panels.task_indicator import TaskIndicator
from .startup_tracker import STARTUP_TASK_ID, StartupStep, StartupTracker
from .panels.ui_state import UiStateStore
from .panels.dataset_manager import DatasetManagerDialog
from .panels.file_menu import FileMenu
from .roi import RoiToolbox
from .roi_geometry_sync import RoiGeometrySync
from .selection import (
    AreaSelectionModule,
    CubeDisplayModule,
    HighlightRangeModule,
    ReferenceFrameModule,
    SelectionModule,
)
from .selection.frame_roles_module import FrameRolesModule
from .selection.reference_auto_task import TASK_ID as REFERENCE_TASK_ID, ReferenceAutoFind
from .storage.app_settings import AppSettings, load_app_settings, save_app_settings
from .storage.ui_state_keys import (
    ANALYSIS_MODE,
    CHECKS_WINDOW_NUMBERS,
    CHECKS_WINDOW_SIZE,
    MOTION_WINDOW_NUMBERS,
    MOTION_WINDOW_SIZE,
    SENSORGRAM_CHECKS_PAIR,
    PLOT_EXPORT_FIGURE_FORMAT,
    PLOT_EXPORT_FOLDER,
    CHROMATIC_ALL_WAVELENGTHS,
    CUBE_DISPLAY_MODE,
    CHROMATIC_BORDER_PERCENT,
    CHROMATIC_FEATURE_DIAMETER,
    CHROMATIC_LANDMARK_COUNT,
    CHROMATIC_MAX_STEP,
    CHROMATIC_SHOW_LANDMARKS,
    CHROMATIC_STRIDE,
    DISPLAY_RANGE,
    DISPLAY_RESOLUTION,
    RI_AGE_WARNING_DAYS,
    RI_WAVELENGTH_NM,
    HIGHLIGHT_RANGE,
    HISTOGRAM_BIN_WIDTH,
    HISTOGRAM_LINE_WIDTH,
    HISTOGRAM_LOG_Y,
    HISTOGRAM_Y_MODE,
    IMAGE_RIBBON_CATEGORY,
    IMAGE_SHOW_BACKGROUND,
    IMAGE_VIEW_RANGE,
    MASK_SCOPE,
    OVERLAY_KEYS,
    read,
    read_display_range,
    read_highlight_range,
    read_view_range,
)
from .storage.session import SessionState, session_path
from .storage.session_load_task import TASK_ID as SESSION_LOAD_TASK_ID, LoadedSession, SessionLoadTask
from .storage.session_autosave import SessionAutosave
from .storage.session_coordinator import SessionCoordinator
from .storage.session_carry_task import TASK_ID as SESSION_CARRY_TASK_ID, CarriedResult, SessionCarry
from .shortcuts import bind_action
from .undo import UndoManager, undo_manager
from .version import APP_NAME, APP_VERSION, version_string

logger = logging.getLogger(__name__)

# Bump this whenever a change reshapes the dock area *topology* - which
# splitDockWidget/addDockWidget calls build the tree, not just panel
# content (2026-09-27, found the hard way: reordering the Spectra/Image/
# ROI-table split calls to fix a resize bug - see the comments around
# those calls below - silently corrupted every existing saved layout.
# QMainWindow.restoreState() maps a saved blob's per-node sizes onto the
# *current* tree by position, not by identity; a differently-shaped tree
# still "restores successfully" (returns True) but applies old sizes to
# the wrong nodes - reproduced headlessly: one panel getting squeezed to
# ~49px, doing effectively nothing, next to another ballooning to fill
# the rest, which looks exactly like a stuck/unresizable dock, not like
# what it actually is (a stale blob). Passing an explicit, mismatched
# version to both saveState()/restoreState() makes Qt reject an
# incompatible old blob outright (restoreState returns False, nothing is
# applied) instead of silently mis-applying it - falls back to this
# function's own fresh layout instead of a corrupted one.
# Bumped to 2 (2026-09-27): tabifyDockWidget(spectra_dock, sensorgram_dock)
# replaced with a plain splitDockWidget - a saved blob from before this
# change still had Spectra/Sensorgram tabified, and restoreState() was
# reapplying that tabbed grouping on every launch regardless of what this
# function's own layout calls built, which is exactly the silent-stale-blob
# failure mode this version guard exists to catch.
# Bumped to 3 (2026-09-28): dragging one panel onto another to merge them
# into a tab group was manually done at least once (Histogram onto Image)
# before `window.setDockOptions(...)` below dropped AllowTabbedDocks and
# removed that drop target - that manual tab group got saved under version
# 2 and kept being treated as a valid, current-version blob on every
# relaunch, restoring the same unwanted tab group even after the drop
# target that created it was gone. This is the same silent-stale-blob
# pattern as the version-2 bump above, just triggered by an interactive
# drag instead of a code change - restoreState() reconstructs whatever
# topology a saved blob describes (including tab groups) regardless of
# what setDockOptions currently allows *creating* interactively.
# Bumped to 4 (2026-10-18): Workflow moved from its own LeftDockWidgetArea into
# the shared split tree (see `_build_dock_layout`). A version-3 blob puts it in
# the left area; restoring that onto the new tree is exactly the silent
# mis-mapping described above, so it is rejected instead. Cost, once: a window
# layout and any custom layout presets saved before this are dropped (the
# default layout is built, and a preset falls back to showing/hiding panels).
# Bumped to 5 (2026-11-11): the Workflow dock was removed (its actions are in the File
# menu, its displays in File -> Dataset Manager). A version-4 blob still holds a column
# for it; same one-time cost as above.
_DOCK_LAYOUT_STATE_VERSION = 5

LAUNCH_POOL_FALLBACK_MS = 20_000
"""A remembered Performance mode starts its worker processes when the startup tracker says the app has loaded, or this long after launch if
nothing was being loaded (analysis/scan_pool.py): seven processes importing at once would slow down the dataset that is still opening."""

def _build_menu_bar(window: QMainWindow) -> tuple[QMenu, QMenu, QMenu]:
    """Standard File/Edit/View/Help menus
    (``docs/rewrite_gui_shell_design_2026-09.md`` §2). File gets the dataset,
    session and metadata actions and Exit (`panels/file_menu.py`, 2026-11-11);
    View gets the panel toggles, layout presets and theme switching, Edit gets
    undo/redo (`_wire_edit_menu`) and Preferences (`_wire_preferences`), Help
    holds the Log Console and, last, About. (The Options menu was replaced by Edit ->
    Preferences on 2026-10-19.)

    Returns (File menu, Edit menu, View menu) so the caller can add the actions once the
    things they act on exist."""
    menu_bar = window.menuBar()
    file_menu = menu_bar.addMenu("&File")
    edit_menu = menu_bar.addMenu("&Edit")
    view_menu = menu_bar.addMenu("&View")
    menu_bar.addMenu("&Help")
    return file_menu, edit_menu, view_menu


def _wire_edit_menu(
    edit_menu: QMenu,
    window: QMainWindow,
    *,
    manager: UndoManager = undo_manager,
) -> tuple[QAction, QAction]:
    """Edit -> Undo / Redo (keys: `shortcuts.py`, Ctrl+Z and Ctrl+Y / Ctrl+Shift+Z). Every command already
    pushes onto the shared `undo_manager`; this is the first thing that can
    pop it.

    - The labels follow the stack ("Undo Move ROI") and the actions are
      disabled when there is nothing to do (the key then reaches whatever
      has focus - a text field keeps its own Ctrl+Z, which Qt gives the
      focused editor first).
    - Application-wide shortcut, so it also works from a floating (undocked)
      panel, which is a separate top-level window.
    - Selection is never undoable (`CLAUDE.md`), so Ctrl+Z after clicking a
      ROI undoes the last real edit, not the click."""
    undo_action = QAction("Undo", window)
    redo_action = QAction("Redo", window)
    bind_action(undo_action, "edit.undo", context=Qt.ShortcutContext.ApplicationShortcut)
    bind_action(redo_action, "edit.redo", context=Qt.ShortcutContext.ApplicationShortcut)
    edit_menu.addAction(undo_action)
    edit_menu.addAction(redo_action)

    def refresh() -> None:
        undo_action.setEnabled(manager.can_undo)
        redo_action.setEnabled(manager.can_redo)
        undo_action.setText(f"Undo {manager.undo_label}" if manager.undo_label else "Undo")
        redo_action.setText(f"Redo {manager.redo_label}" if manager.redo_label else "Redo")

    def run(verb: str) -> None:
        label = manager.undo_label if verb == "Undo" else manager.redo_label
        (manager.undo if verb == "Undo" else manager.redo)()
        if label:
            window.statusBar().showMessage(f"{'Undid' if verb == 'Undo' else 'Redid'}: {label}", 3000)

    undo_action.triggered.connect(lambda: run("Undo"))
    redo_action.triggered.connect(lambda: run("Redo"))
    manager.changed.connect(refresh)

    def forget_window() -> None:
        # The manager is shared by the whole app and `refresh` is a closure over this window's two actions, so Qt cannot drop the connection
        # when the window goes. Left connected, every later change raised "wrapped C/C++ object of type QAction has been deleted" (one
        # such listener per window ever built; in a long test run that was tens of thousands of tracebacks, 2026-10-30).
        try:
            manager.changed.disconnect(refresh)
        except (TypeError, RuntimeError):  # already disconnected
            pass

    window.destroyed.connect(forget_window)
    edit_menu.aboutToShow.connect(refresh)  # a running analysis changes no undo state, so the menu re-checks on opening
    refresh()
    return undo_action, redo_action


def _wire_theme_menu(
    view_menu: QMenu,
    window: QMainWindow,
    image_panel: ImagePanel,
    *,
    initial_theme: str = "dark",
    on_theme_changed: Callable[[str], None] | None = None,
) -> None:
    """View -> Theme: Dark / Bright, exclusive-checkable (design doc §6).

    **Persisted since 2026-09-26** via `storage/app_settings.py` - the
    caller applies `initial_theme` (a plain "dark"/"bright" string, matching
    `AppSettings.theme`) *before* this function runs (see
    `build_main_window`, which calls `set_active_theme`/`apply_app_theme`
    at the very top, before any panel reads `get_active_theme()`); this
    function only has to make the menu's checked action match that, and
    report every future change back through `on_theme_changed` so the
    caller can write it out. Every live theme switch has to explicitly
    touch three kinds of chrome, in this order, matching the stable app's
    own ``_apply_theme_styles``:

    1. The QApplication-level palette/QSS (``apply_app_theme``) - covers
       every standard Qt widget.
    2. Every ``PanelContainer``'s title bar - baked-in per-widget
       stylesheets at construction time, not QSS, so they don't pick up a
       switch on their own (see ``PanelContainer.refresh_theme``'s own
       docstring).
    3. Pyqtgraph canvases - also don't respond to QSS (see
       ``ImagePanel.refresh_theme``). Only ``ImagePanel`` has a real one
       today; Histogram/Spectra/Sensorgram get the same call once their
       real plot widgets exist (currently still ``NotImplementedError``
       stubs - see the build log) - ``getattr(..., None)`` guards each one
       so this doesn't have to change when they do.
    """
    view_menu.addSeparator()
    theme_menu = view_menu.addMenu("Theme")
    group = QActionGroup(window)
    group.setExclusive(True)

    dark_action = theme_menu.addAction("Dark")
    dark_action.setCheckable(True)
    dark_action.setChecked(initial_theme != "bright")
    group.addAction(dark_action)

    bright_action = theme_menu.addAction("Bright")
    bright_action.setCheckable(True)
    bright_action.setChecked(initial_theme == "bright")
    group.addAction(bright_action)

    def switch_theme(theme, name: str) -> None:
        set_active_theme(theme)
        app = QApplication.instance()
        if app is not None:
            apply_app_theme(app, theme)
        image_panel.refresh_theme()
        for table in window.findChildren(RoiTablePanel):
            table.refresh_theme()
        for log_panel in window.findChildren(LogPanel):
            log_panel.refresh_theme()
        for spectra in window.findChildren(SpectraPanel):
            spectra.refresh_theme()
        for sensorgram in window.findChildren(SensorgramPanel):
            sensorgram.refresh_theme()
        for dock in window.findChildren(PanelContainer):
            dock.refresh_theme()
        apply_windows_titlebar_color(window, theme)
        if on_theme_changed is not None:
            on_theme_changed(name)

    dark_action.triggered.connect(lambda checked: switch_theme(LSPRI_DARK_THEME, "dark") if checked else None)
    bright_action.triggered.connect(lambda checked: switch_theme(LSPRI_BRIGHT_THEME, "bright") if checked else None)


def analysis_scan_source(m: _Modules) -> ScanSource:
    """What an analysis scan reads from the live modules (``analysis/scan_jobs.py``). Call on the GUI thread and hand the result to
    ``m.analysis_scan.start(...)``, which snapshots it before the task thread starts."""
    return source_from_modules(
        dataset=m.dataset, frame_roles=m.frame_roles, roi_toolbox=m.roi_toolbox, geometry=m.geometry, background=m.background,
        chromatic=m.chromatic, mask=m.mask, records=m.dataset.record_map(),
    )


def _connect_roi_renumbering(roi_toolbox: RoiToolbox, selection: SelectionModule, analysis_settings: AnalysisSettingsModule | None = None) -> None:
    """Everything that holds a ROI id follows ``RoiToolbox.roi_ids_renumbered``
    (a delete closing the gap, a fresh detection). The selection holds no
    toolbox reference of its own, so this is the one place that connects
    them. Kept as a function so a test can wire exactly what the app wires.
    The analysis settings hold a ROI's own anchor points of the drift
    baseline (2026-11-09)."""
    roi_toolbox.roi_ids_renumbered.connect(selection.remap_roi_ids)
    if analysis_settings is not None:
        roi_toolbox.roi_ids_renumbered.connect(analysis_settings.remap_roi_ids)


def capture_session(
    geometry: GeometryModule,
    mask: MaskModule,
    chromatic: ChromaticModule,
    background: BackgroundModule,
    roi_toolbox: RoiToolbox,
    selection: SelectionModule,
    analysis_settings: AnalysisSettingsModule,
    frame_roles: FrameRolesModule | None = None,
    mask_pointers: MaskPointersModule | None = None,
    experiment_plan: ExperimentPlanModule | None = None,
) -> SessionState:
    """Read every module's current state into a plain, Qt-free
    :class:`SessionState` (2026-09-23).

    Lives here because this is the one place that knows about every module, and keeping the knowledge
    here leaves ``storage/session.py`` a pure data layer that a test can
    drive with dataclasses and no Qt at all.

    **Not every query below returns a copy** (corrected 2026-10-17): the
    `settings()` queries do, but `rois()`, `groups()`, `array_groups()`,
    `models()` and `landmarks()` return the live, mutable objects. The result is
    therefore only safe to hand to another thread after
    `storage.session.prepare_session` has copied it, which the autosave does on
    the GUI thread before its writer runs."""
    return SessionState(
        geometry=geometry.settings(),
        background=background.settings(),
        mask_settings=mask.settings(),
        mask_changes=mask.mask_changes(),
        chromatic_settings=chromatic.settings(),
        chromatic_models=chromatic.models(),
        chromatic_landmarks=chromatic.landmarks(),
        detection_settings=roi_toolbox.detection_settings(),
        metric_settings=analysis_settings.metric_settings(),
        statistics_settings=analysis_settings.statistics_settings(),
        rois=roi_toolbox.rois(),
        groups=roi_toolbox.groups(),
        arrays=roi_toolbox.array_groups(),
        selected_cube=selection.current_cube(),
        selected_wavelength=selection.current_wavelength(),
        selected_roi_ids=tuple(sorted(selection.selected_roi_ids())),
        **({} if frame_roles is None else {"frame_roles": frame_roles.state()}),
        **({} if mask_pointers is None else {"mask_pointers": mask_pointers.state()}),
        **({} if experiment_plan is None else {"plan_edits": experiment_plan.state(), "refractive_index": experiment_plan.ri_table()}),
    )


def apply_session(
    state: SessionState,
    geometry: GeometryModule,
    mask: MaskModule,
    chromatic: ChromaticModule,
    background: BackgroundModule,
    roi_toolbox: RoiToolbox,
    selection: SelectionModule,
    analysis_settings: AnalysisSettingsModule,
    frame_roles: FrameRolesModule | None = None,
    mask_pointers: MaskPointersModule | None = None,
    experiment_plan: ExperimentPlanModule | None = None,
) -> None:
    """Push a loaded :class:`SessionState` into every module.

    Uses each module's ``restore_*`` method rather than its command API:
    those replace state wholesale, push nothing onto the undo stack, and
    still emit, so panels redraw. **The undo stack is then cleared**, which
    is the point of not tracking the restore - Ctrl+Z straight after
    opening a dataset should do nothing at all, not rewind past the file
    that was just opened into a half-restored state that never existed.

    Selection is applied last, through its ordinary setters: it is not
    undo-tracked in the first place (see ``selection/module.py``), and
    setting it after the ROIs exist means the selected ids are real."""
    geometry.restore_settings(state.geometry)
    background.restore_settings(state.background)
    mask.restore_state(state.mask_settings, state.mask_changes)
    chromatic.restore_state(state.chromatic_settings, state.chromatic_models, state.chromatic_landmarks)
    roi_toolbox.restore_state(state.detection_settings, state.rois, state.groups, state.arrays)
    analysis_settings.restore_state(state.metric_settings, state.statistics_settings)
    if frame_roles is not None:
        frame_roles.restore_state(state.frame_roles)
    if mask_pointers is not None:
        mask_pointers.restore_state(state.mask_pointers)
    if experiment_plan is not None:
        experiment_plan.restore_state(state.plan_edits)
        experiment_plan.restore_ri_table(state.refractive_index)

    selection.set_cube(state.selected_cube)
    selection.set_wavelength(state.selected_wavelength)
    selection.set_roi_selection(set(state.selected_roi_ids))

    undo_manager.clear()


def _frame_naming(dataset: DatasetModule) -> FrameNamingScheme:
    """The dataset's own frame-tag scheme, which saving and loading a
    session must agree on or a mask PNG's filename won't be found again
    (`storage/session.py`'s `save_session`).

    Kept here rather than in ``DatasetModule`` because the dataset does not
    know about sessions."""
    cube_indices = list(dataset.spectral_cubes())
    wavelengths: list[float] = []
    for cube_index in cube_indices:
        wavelengths.extend(dataset.wavelengths_for_cube(cube_index))
    return FrameNamingScheme.for_dataset(cube_indices, wavelengths)


def _cached_frame_naming(dataset: DatasetModule) -> Callable[[], FrameNamingScheme]:
    """`_frame_naming`, worked out once per loaded dataset instead of on every save (2026-10-17): it walks every cube
    (16 ms for 314 cubes), and the autosave asks for it on the GUI thread each time it saves. The scheme depends only on the
    cubes and wavelengths, which cannot change while a dataset stays loaded; loading or clearing one forgets it."""
    cache: list[FrameNamingScheme | None] = [None]

    def forget(*_args: object) -> None:
        cache[0] = None

    dataset.dataset_loaded.connect(forget)
    dataset.dataset_cleared.connect(forget)

    def naming() -> FrameNamingScheme:
        if cache[0] is None:
            cache[0] = _frame_naming(dataset)
        return cache[0]

    return naming


def _build_session_autosave(
    dataset: DatasetModule,
    geometry: GeometryModule,
    mask: MaskModule,
    chromatic: ChromaticModule,
    background: BackgroundModule,
    roi_toolbox: RoiToolbox,
    selection: SelectionModule,
    analysis_settings: AnalysisSettingsModule,
    frame_roles: FrameRolesModule | None = None,
    mask_pointers: MaskPointersModule | None = None,
    experiment_plan: ExperimentPlanModule | None = None,
) -> SessionAutosave:
    """Build the debounce, and wire every module's own change signal to
    ``schedule()`` it (2026-09-23).

    This is the one place that knows about every module, so the knowledge of *what
    counts as a change* lives here rather than inside
    ``storage/session_autosave.py``, which stays a pure debounce.

    Every computational **and** cosmetic signal is connected. The
    cosmetic/computational split exists to tell the analysis store what may
    be stale (sketch §3); it says nothing about what is worth persisting,
    and a relabelled or recoloured ROI is exactly as worth keeping as a
    moved one.

    **Does not itself decide *where* to save** (2026-09-26) - that used to
    be `dataset_model.home` directly, wired here via a `dataset_loaded`
    handler. Now that a dataset can hold several named sessions
    (`storage/session_index.py`), *which* folder is active is
    `SessionCoordinator`'s job, not this function's - see
    `_wire_session_coordinator` below, which is what actually calls
    `autosave.set_root()`."""
    autosave = SessionAutosave(
        capture=lambda: capture_session(
            geometry, mask, chromatic, background, roi_toolbox, selection, analysis_settings, frame_roles,
            mask_pointers, experiment_plan,
        ),
        naming=_cached_frame_naming(dataset),
    )

    signals = [
        geometry.geometry_changed, geometry.cosmetic_changed,
        mask.mask_changed, mask.cosmetic_changed,
        background.background_model_changed,
        chromatic.chromatic_model_changed,
        roi_toolbox.geometry_changed, roi_toolbox.cosmetic_changed,
        roi_toolbox.roi_ids_renumbered,
        # Neither cosmetic nor computational (see AnalysisSettingsChange) -
        # but just as much part of the session: a baseline-corrected trace
        # reads as relative shift rather than absolute value, so losing the
        # setting changes what the plot means on the next open.
        analysis_settings.settings_changed,
        selection.cube_changed, selection.wavelength_changed, selection.roi_selection_changed,
    ]
    if frame_roles is not None:
        signals.append(frame_roles.roles_changed)
    if mask_pointers is not None:
        signals.append(mask_pointers.pointers_changed)  # cosmetic for the analysis, but part of the session
    if experiment_plan is not None:
        signals.append(experiment_plan.steps_changed)  # a dragged boundary, a retyped note, a colour: part of the session
        signals.append(experiment_plan.ri_changed)  # an imported RI table (RIs.txt) is part of the session too
    for signal in signals:
        signal.connect(autosave.schedule)

    return autosave


def _wire_session_coordinator(
    coordinator: SessionCoordinator,
    autosave: SessionAutosave,
    geometry: GeometryModule,
    mask: MaskModule,
    chromatic: ChromaticModule,
    background: BackgroundModule,
    roi_toolbox: RoiToolbox,
    selection: SelectionModule,
    analysis_settings: AnalysisSettingsModule,
    frame_roles: FrameRolesModule | None = None,
    mask_pointers: MaskPointersModule | None = None,
    session_load: SessionLoadTask | None = None,
    experiment_plan: ExperimentPlanModule | None = None,
) -> None:
    """Restore into every module whenever the *active session* changes -
    on a dataset load, an explicit session switch, or a new session's
    creation (2026-09-26).

    This is `restore_for`'s old body (see `_build_session_autosave`'s
    2026-09-23 history), moved here and re-keyed off
    `SessionCoordinator.active_session_changed` instead of
    `DatasetModule.dataset_loaded` directly - a session switch has to run
    the exact same restore with no new dataset load involved, so the two
    triggers need to share one implementation rather than duplicate it.
    """

    if session_load is None:
        session_load = SessionLoadTask()
    wanted: dict[str, Path | None] = {"root": None}  # the session whose load we are waiting for (None: none)

    def apply(state: SessionState | None, root: Path) -> None:
        # `state or SessionState()`, never skipped on `None`: a session with
        # no `session.json` yet is not only the harmless "first ever load"
        # case (modules already start out empty then) - it is also what a
        # brand-new session created via `SessionCoordinator.create_new()`
        # looks like *while switching away from a session that had real
        # in-memory state*. Skipping the apply there would leave the
        # previous session's ROIs/masks/settings sitting in every module,
        # silently bleeding into whatever gets saved to the new session's
        # folder next - caught by
        # `test_a_second_session_is_independent_of_the_first` (2026-09-26).
        # Restoring emits from every module it touches; without the
        # `suspended()` guard the restore would schedule a save of what was
        # just loaded/reset.
        with autosave.suspended():
            apply_session(
                state or SessionState(), geometry, mask, chromatic, background,
                roi_toolbox, selection, analysis_settings, frame_roles, mask_pointers, experiment_plan,
            )

    def restore(root: Path | None) -> None:
        # A load still running belongs to the session being left: stop it, and drop its result if it arrives anyway.
        session_load.discard()
        wanted["root"] = None
        # Flush whatever the *previous* session still had pending first,
        # while its root is still the current one - matches the ordering
        # `restore_for` used before sessions existed, for the same reason
        # (a late-arriving write must land in the session it belongs to,
        # not the new one).
        autosave.set_root(None)
        if root is None:
            return
        # The flush above is written in the background (2026-10-17). Reading `root` back before a write *to that same
        # root* has landed (reloading the session just edited) would restore the previous save; for any other root this
        # returns at once.
        autosave.wait_until_written(root)
        if not session_path(root).is_file():
            # Nothing to read (a fresh dataset, a brand-new session): blank, and saving may start now.
            apply(None, root)
            autosave.set_root(root)
            check_ri_file()
            return
        # Reading the session takes seconds when it holds hundreds of masks, so it happens off the GUI thread (2026-10-22,
        # `storage/session_load_task.py`): the modules are blank for now, autosave stays OFF for this root (a blank state must
        # never be written over the saved one), and `loaded` below fills everything in and switches autosave on.
        apply(None, root)
        wanted["root"] = root
        session_load.load(root)

    def loaded(result: LoadedSession) -> None:
        if result.root != wanted["root"]:  # the session was left meanwhile (the task drops those itself; belt and braces)
            return
        wanted["root"] = None
        apply(result.state, result.root)
        autosave.set_root(result.root)
        # The loaded masks are already on disk, in known file versions: hand those to the writer so the first
        # save does not read every PNG back to find that out (2026-10-21).
        if result.state is not None:
            autosave.seed_mask_versions(result.root, result.state)
        check_ri_file()

    def check_ri_file() -> None:
        # RIs.txt in the dataset folder: imported when the session has none, reported when changed since its import. Only once
        # autosave is on for the session (an import is saved; the blank state shown while a session loads never imports).
        if experiment_plan is not None:
            experiment_plan.check_ri_file()

    def ended(root: Path, outcome: str) -> None:
        if outcome == "completed" or root != wanted["root"]:
            return
        # `load_session` raises on a file it cannot read or does not recognise, deliberately (see its docstring) - starting
        # from defaults silently would look exactly like a session that was never set up. Autosave stays off for this root
        # so the app cannot overwrite a recoverable file with blank state; the status bar shows why (a cancel does the same).
        wanted["root"] = None
        logger.warning("The session in %s was not loaded (%s); autosave is off for it", root, outcome)
        autosave.set_root(root, enabled=False)

    session_load.completed.connect(loaded)
    session_load.ended.connect(ended)

    coordinator.active_session_changed.connect(restore)


@dataclass(frozen=True)
class _Modules:
    """Every rewrite module `build_main_window` constructs, in one place, so the
    functions below take one argument instead of a dozen (2026-10-07: the
    single 630-line function was split by what it wires)."""

    dataset: DatasetModule
    geometry: GeometryModule
    active_tool: ActiveToolModule
    mask: MaskModule
    mask_scope: MaskScopeModule
    chromatic: ChromaticModule
    background: BackgroundModule
    roi_toolbox: RoiToolbox
    roi_geometry_sync: RoiGeometrySync
    selection: SelectionModule
    reference_frame: ReferenceFrameModule
    reference_auto: ReferenceAutoFind
    frame_roles: FrameRolesModule
    experiment_plan: ExperimentPlanModule
    cube_display: CubeDisplayModule
    highlight_range: HighlightRangeModule
    area_selection: AreaSelectionModule
    analysis_settings: AnalysisSettingsModule
    session_coordinator: SessionCoordinator
    session_autosave: SessionAutosave
    chromatic_auto: ChromaticAutoDetect
    mask_pointers: MaskPointersModule
    transient_scan: TransientScanTask
    analysis_scan: ScanTask
    scan_results: ScanResultsModule
    roi_tuning: RoiTuningModule
    analysis_live: AnalysisLiveModule
    session_carry: SessionCarry
    session_load: SessionLoadTask
    trace_store: TraceStore  # the fitted metric traces and the helper process, shared by the Sensorgram and the ROI table's step columns
    sensorgram_axis: SensorgramAxis  # the Sensorgram's x axis and Subtract switch, shared with the same table
    step_table: StepTableModule  # the numbers of the ROI table's step columns
    data_checks: DataChecksModule  # the Sensorgram's data-quality checks (report, run, whether it is current)
    image_motion: ImageMotionModule  # the Image panel's image-motion check
    signal_map: SignalMapModule  # the Image panel's signal map (ROIs coloured by the step table's numbers)


class _SettingsWriter:
    """The one write path into `AppSettings` for app-level state (last
    dataset, theme, window and dock layout). Everything
    a panel's controls change goes through the `UiStateStore` instead
    (`panels/ui_state.py`), which batches its own writes."""

    def __init__(self, settings: AppSettings, on_settings_changed: Callable[[AppSettings], None] | None) -> None:
        self.settings = settings
        self._on_settings_changed = on_settings_changed

    def persist(self, **changes: object) -> None:
        for key, value in changes.items():
            setattr(self.settings, key, value)
        if self._on_settings_changed is not None:
            self._on_settings_changed(self.settings)


def _build_modules() -> _Modules:
    """Construct every module and connect them to each other (no panels yet)."""
    dataset = DatasetModule()
    session_coordinator = SessionCoordinator()
    # Connected FIRST, before any module that resets itself on `dataset_cleared` (2026-11-12): Qt calls the slots in connection order,
    # and the unbind hands the session over (writes what is pending, then points autosave at nothing). A module clearing itself
    # earlier would schedule a save of its cleared state into the still-bound session (the plan edits and RI table were lost that way).
    dataset.dataset_cleared.connect(session_coordinator.unbind)
    geometry = GeometryModule()
    # Transient UI mode (which canvas tool is on) - not undoable/persisted; see its docstring.
    active_tool = ActiveToolModule()
    mask = MaskModule()
    # Transient UI mode (which scope a *new* mask edit would land in) - shared
    # between the Workflow panel's Mask section and the Image panel's own
    # "Mask" tab; see image_tools/mask_scope.py's docstring.
    mask_scope = MaskScopeModule()
    chromatic = ChromaticModule()
    background = BackgroundModule()
    roi_toolbox = RoiToolbox()
    # Keeps existing ROI positions/masks aligned with the image whenever
    # rotation/flip/crop changes - see its own module docstring for why this
    # lives outside both `image_tools/` and `roi/`.
    roi_geometry_sync = RoiGeometrySync(geometry, roi_toolbox, dataset)
    selection = SelectionModule()
    reference_frame = ReferenceFrameModule()
    frame_roles = FrameRolesModule(dataset, reference_frame)
    experiment_plan = ExperimentPlanModule(dataset)  # the measured steps with the user's edits on top; the edits are part of the session
    highlight_range = HighlightRangeModule()
    # The pointers a Mask-tab scan finds (cosmetic: never a mask, never a recompute); part of the session.
    mask_pointers = MaskPointersModule()
    # Rectangle/lasso area selection limiting the Image panel's editing tools
    # and the Histogram plot (transient, not persisted); lives in displayed
    # pixel space, so it is dropped when rotate/flip/crop changes the grid.
    area_selection = AreaSelectionModule()
    geometry.geometry_changed.connect(lambda _change: area_selection.clear())
    analysis_settings = AnalysisSettingsModule()

    # SelectionModule holds no RoiToolbox reference of its own (CLAUDE.md,
    # "no module reaches into another's internals") - this is the one place
    # that connects RoiToolbox's roi_ids_renumbered signal to Selection's
    # remap_roi_ids(), so a delete-driven ROI renumber never leaves a stale
    # selected id behind (see roi/toolbox.py's module docstring, "Cross-
    # module consequence", and selection/module.py).
    _connect_roi_renumbering(roi_toolbox, selection, analysis_settings)

    # Which session-scoped folder the session autosave points at is
    # `SessionCoordinator`'s job, not a plain
    # `ds.home` read here (2026-09-26 - see CLAUDE.md's "Sessions" section
    # and `storage/session_index.py`). `home`, not `folder`: it is the
    # folder derived data is allowed to be written into, so a session never
    # lands inside a raw TIFF/OME-Zarr folder it doesn't own (see
    # ImageDataset.home).
    session_autosave = _build_session_autosave(
        dataset, geometry, mask, chromatic, background, roi_toolbox, selection, analysis_settings, frame_roles,
        mask_pointers, experiment_plan,
    )
    session_load = SessionLoadTask()
    _wire_session_coordinator(
        session_coordinator, session_autosave,
        geometry, mask, chromatic, background, roi_toolbox, selection, analysis_settings, frame_roles,
        mask_pointers, session_load, experiment_plan,
    )
    dataset.dataset_loaded.connect(lambda ds: session_coordinator.bind_dataset(ds.home))

    # An OME-Zarr export can take the session with it, moved into the exported pixels' coordinates (2026-10-22,
    # `storage/session_carry.py`). The Export tab calls `prepare` when the user starts an export; a finished export
    # starts the carry, a failed or cancelled one drops it.
    session_carry = SessionCarry(
        capture=lambda: capture_session(
            geometry, mask, chromatic, background, roi_toolbox, selection, analysis_settings, frame_roles, mask_pointers,
            experiment_plan,
        ),
        naming=_cached_frame_naming(dataset),
        source=lambda: (dataset.dataset_home(), session_coordinator.active_session_id()),
    )
    dataset.export_stats_ready.connect(session_carry.set_export_stats)  # emitted just before export_finished
    # The analysis scan belongs to the dataset and session it was started in: a dataset opened or cleared, or a session switched, stops it.
    analysis_scan = ScanTask()
    modules_holder: list[_Modules] = []  # the results module needs the finished module set to read the app (analysis_scan_source)
    # No look at the scan file while it is being written: by a scan, or by ROI position tuning (a trial reads a lot; Accept writes the file).
    scan_results = ScanResultsModule(
        lambda: analysis_scan_source(modules_holder[0]),
        scan_running=lambda: analysis_scan.is_running() or (bool(modules_holder) and modules_holder[0].roi_tuning.task.is_running()),
    )
    analysis_live = AnalysisLiveModule()  # Live / Paused: the Spectra and Sensorgram plots and the out-of-date check stop recomputing while paused
    analysis_live.live_changed.connect(lambda live: scan_results.set_paused(not live))
    dataset.dataset_loaded.connect(lambda _dataset: analysis_scan.invalidate())
    dataset.dataset_cleared.connect(analysis_scan.invalidate)
    session_coordinator.active_session_changed.connect(lambda _root: analysis_scan.invalidate())
    # ROI position tuning (2026-10-29): its result lives in RAM and belongs to the dataset and session it was made in.
    roi_tuning = RoiTuningModule(
        lambda: analysis_scan_source(modules_holder[0]), experiment_plan, selection, analysis_settings, dataset, roi_toolbox
    )
    dataset.dataset_loaded.connect(lambda _dataset: roi_tuning.invalidate())
    dataset.dataset_cleared.connect(roi_tuning.invalidate)
    session_coordinator.active_session_changed.connect(lambda _root: roi_tuning.invalidate())
    app = QApplication.instance()
    if app is not None:  # the thread must be stopped where the application ends, not in a widget's destructor (sip crash-on-close)
        app.aboutToQuit.connect(analysis_scan.shutdown)
        app.aboutToQuit.connect(scan_results.shutdown)
        app.aboutToQuit.connect(roi_tuning.task.shutdown)
    dataset.export_finished.connect(session_carry.start)
    dataset.export_failed.connect(lambda _message: session_carry.discard())
    cube_display = CubeDisplayModule(dataset)
    trace_store = TraceStore(scan_results)  # shut down after the panels (build_main_window), which may still be fitting
    sensorgram_axis = SensorgramAxis.of(scan_results, dataset, cube_display, analysis_settings)
    # The drift baseline is fitted against the Sensorgram's x (cube number or time); the trial fits it the same way, and redoes it when the axis is switched.
    roi_tuning.set_x_is_time(sensorgram_axis.uses_time)
    sensorgram_axis.axis_changed.connect(roi_tuning.refit_for_axis)
    step_table =StepTableModule(scan_results, roi_toolbox, dataset, analysis_settings, experiment_plan, trace_store, sensorgram_axis)
    data_checks = DataChecksModule(scan_results, roi_toolbox, dataset, analysis_settings, experiment_plan, trace_store)
    image_motion = ImageMotionModule(dataset, experiment_plan)
    if app is not None:
        app.aboutToQuit.connect(step_table.shutdown)
        app.aboutToQuit.connect(data_checks.shutdown)
        app.aboutToQuit.connect(image_motion.shutdown)
    modules = _Modules(
        dataset=dataset, geometry=geometry, active_tool=active_tool, mask=mask, mask_scope=mask_scope,
        chromatic=chromatic, background=background, roi_toolbox=roi_toolbox,
        roi_geometry_sync=roi_geometry_sync, selection=selection, reference_frame=reference_frame,
        highlight_range=highlight_range, area_selection=area_selection, analysis_settings=analysis_settings,
        session_coordinator=session_coordinator,
        session_autosave=session_autosave, chromatic_auto=ChromaticAutoDetect(chromatic),
        mask_pointers=mask_pointers, transient_scan=TransientScanTask(mask_pointers), analysis_scan=analysis_scan, scan_results=scan_results, roi_tuning=roi_tuning, analysis_live=analysis_live, session_carry=session_carry,
        session_load=session_load,
        reference_auto=ReferenceAutoFind(dataset, selection, reference_frame, frame_roles),
        frame_roles=frame_roles,
        experiment_plan=experiment_plan,
        cube_display=cube_display,
        trace_store=trace_store, sensorgram_axis=sensorgram_axis, step_table=step_table, data_checks=data_checks, image_motion=image_motion,
        signal_map=SignalMapModule(step_table, roi_toolbox, experiment_plan),
    )
    modules_holder.append(modules)
    return modules


def _wire_highlight_range_persistence(m: _Modules, ui_state: UiStateStore, settings: AppSettings) -> None:
    """Highlight range: remembered per dataset (an intensity range from one
    dataset means nothing on another), so the saved value carries the folder
    it was set on. `dataset_cleared` wipes the module's range on every load, so
    the saved one is put back when the same dataset finishes loading - before
    the Histogram would seed the full frame range."""
    highlight_range = m.highlight_range

    def _restore_highlight_range(ds) -> None:
        saved = read_highlight_range(ui_state)
        if saved is not None and saved[0] == str(ds.home) and highlight_range.current_range() is None:
            highlight_range.set_range(saved[1], saved[2])

    def _on_highlight_range_changed(range_: object) -> None:
        if range_ is None:  # cleared by a dataset load, not a user choice: keep what was saved
            return
        lo, hi = range_  # type: ignore[misc]
        ui_state.set(
            HIGHLIGHT_RANGE,
            {"dataset": settings.last_dataset_folder, "min": float(lo), "max": float(hi)},
        )

    m.dataset.dataset_loaded.connect(_restore_highlight_range)
    highlight_range.range_changed.connect(_on_highlight_range_changed)


def _wire_display_range_persistence(
    m: _Modules, ui_state: UiStateStore, settings: AppSettings, image_panel: ImagePanel
) -> None:
    """The Image panel's fixed display range: remembered per dataset, like the highlight range (an intensity
    range from one dataset means nothing on another). When a dataset finishes loading the saved range is put back
    if it was set on this same dataset; otherwise the display goes back to automatic per frame, so a range from
    another dataset can never leave the new one looking all white or all black."""

    def _restore_display_range(ds) -> None:
        saved = read_display_range(ui_state)
        if saved is not None and saved[0] == str(ds.home):
            image_panel.set_display_range(saved[1], saved[2], saved[3])
        else:
            _fixed, low, high = image_panel.display_range()
            image_panel.set_display_range(False, low, high)

    def _on_display_range_changed(fixed: bool, low: float, high: float) -> None:
        ui_state.set(
            DISPLAY_RANGE,
            {"dataset": settings.last_dataset_folder, "fixed": bool(fixed), "min": float(low), "max": float(high)},
        )

    m.dataset.dataset_loaded.connect(_restore_display_range)
    image_panel.display_range_changed.connect(_on_display_range_changed)


def _overlay_style(visible: bool, color: str | None, alpha: float, default_color: str) -> OverlayStyle:
    return OverlayStyle(visible, color or default_color, alpha)


def _build_image_panel(m: _Modules, ui_state: UiStateStore, theme_obj) -> ImagePanel:
    """The Image panel, seeded from the saved UI state and reporting every
    user-changeable display option back to it (keys: `storage/ui_state_keys.py`)."""
    mask_visible, mask_color, mask_alpha = (read(ui_state, key) for key in OVERLAY_KEYS["mask"])
    highlight_visible, highlight_color, highlight_alpha = (read(ui_state, key) for key in OVERLAY_KEYS["highlight"])
    image_panel = ImagePanel(
        m.dataset, m.geometry, m.mask, m.chromatic, m.background, m.roi_toolbox, m.selection, m.active_tool,
        m.reference_frame, m.highlight_range,
        mask_scope=m.mask_scope,
        area_selection=m.area_selection,
        chromatic_auto=m.chromatic_auto,
        mask_pointers=m.mask_pointers,
        transient_scan=m.transient_scan,
        session_carry=m.session_carry,
        cube_display=m.cube_display,
        frame_roles=m.frame_roles,
        initial_chromatic_view=(read(ui_state, CHROMATIC_SHOW_LANDMARKS), read(ui_state, CHROMATIC_ALL_WAVELENGTHS)),
        initial_chromatic_values=ChromaticUiValues(
            landmark_count=read(ui_state, CHROMATIC_LANDMARK_COUNT),
            stride=read(ui_state, CHROMATIC_STRIDE),
            border_percent=read(ui_state, CHROMATIC_BORDER_PERCENT),
            max_step_px=read(ui_state, CHROMATIC_MAX_STEP),
            feature_diameter_px=read(ui_state, CHROMATIC_FEATURE_DIAMETER),
        ),
        # `None` unless all four numbers were saved together (first launch, or a
        # partial/hand-edited value): `ImagePanel` then lets pyqtgraph auto-range
        # fit the first image, which is not an error.
        initial_view_range=read_view_range(ui_state),
        initial_mask_overlay=_overlay_style(mask_visible, mask_color, mask_alpha, theme_obj.mask_color),
        initial_highlight_overlay=_overlay_style(
            highlight_visible, highlight_color, highlight_alpha, theme_obj.highlight_color
        ),
        initial_ribbon_category=read(ui_state, IMAGE_RIBBON_CATEGORY),
        initial_show_background=read(ui_state, IMAGE_SHOW_BACKGROUND),
    )
    image_panel.background_view_changed.connect(lambda shown: ui_state.set(IMAGE_SHOW_BACKGROUND.key, bool(shown)))
    image_panel.ribbon_category_changed.connect(lambda label: ui_state.set(IMAGE_RIBBON_CATEGORY.key, label))

    def _on_overlay_style_changed(kind: str, style: OverlayStyle) -> None:
        visible_key, color_key, alpha_key = OVERLAY_KEYS[kind]
        ui_state.set(visible_key.key, style.visible)
        ui_state.set(color_key.key, style.color)
        ui_state.set(alpha_key.key, style.alpha)

    image_panel.overlay_style_changed.connect(_on_overlay_style_changed)

    def _on_chromatic_settings_applied(values) -> None:
        ui_state.set(CHROMATIC_LANDMARK_COUNT.key, values.landmark_count)
        ui_state.set(CHROMATIC_STRIDE.key, values.stride)
        ui_state.set(CHROMATIC_BORDER_PERCENT.key, values.border_percent)
        ui_state.set(CHROMATIC_MAX_STEP.key, values.max_step_px)
        ui_state.set(CHROMATIC_FEATURE_DIAMETER.key, values.feature_diameter_px)

    image_panel.chromatic_settings_applied.connect(_on_chromatic_settings_applied)

    def _on_chromatic_view_changed(show: bool, every: bool) -> None:
        ui_state.set(CHROMATIC_SHOW_LANDMARKS.key, bool(show))
        ui_state.set(CHROMATIC_ALL_WAVELENGTHS.key, bool(every))

    image_panel.chromatic_view_changed.connect(_on_chromatic_view_changed)
    image_panel.view_range_changed.connect(
        lambda x_min, x_max, y_min, y_max: ui_state.set(
            IMAGE_VIEW_RANGE, [[float(x_min), float(x_max)], [float(y_min), float(y_max)]]
        )
    )
    return image_panel


def _build_histogram_panel(m: _Modules, image_panel: ImagePanel, ui_state: UiStateStore) -> HistogramPanel:
    histogram_panel = HistogramPanel(
        image_panel, m.geometry, m.mask, m.chromatic, m.roi_toolbox, m.highlight_range,
        initial_y_mode=read(ui_state, HISTOGRAM_Y_MODE),
        initial_log_y=read(ui_state, HISTOGRAM_LOG_Y),
        initial_bin_width=read(ui_state, HISTOGRAM_BIN_WIDTH),
        initial_line_width=read(ui_state, HISTOGRAM_LINE_WIDTH),
        selection=m.selection,
    )

    def _on_display_settings_changed(y_mode: str, log_y: bool, bin_width_px: float, line_width_px: float) -> None:
        ui_state.set(HISTOGRAM_Y_MODE.key, y_mode)
        ui_state.set(HISTOGRAM_LOG_Y.key, bool(log_y))
        ui_state.set(HISTOGRAM_BIN_WIDTH.key, float(bin_width_px))
        ui_state.set(HISTOGRAM_LINE_WIDTH.key, float(line_width_px))

    histogram_panel.display_settings_changed.connect(_on_display_settings_changed)
    return histogram_panel


def _wire_file_menu(file_menu: QMenu, window: QMainWindow, m: _Modules, ui_state: UiStateStore) -> FileMenu:
    """File menu + the Dataset Manager window (2026-11-11; they replace the Workflow panel). The manager is built now,
    hidden, so it hears the first `dataset_loaded` (`panels/dataset_manager/dialog.py`). The saved RI settings
    (Preferences) are applied here, before any dataset or session opens."""
    manager = DatasetManagerDialog(
        m.dataset, m.selection, m.session_coordinator, cube_display=m.cube_display, ui_state=ui_state, parent=window,
        experiment_plan=m.experiment_plan,
    )
    actions = FileMenu(file_menu, window, m.dataset, m.session_coordinator, manager, experiment_plan=m.experiment_plan)
    _apply_ri_settings(m, actions, float(read(ui_state, RI_WAVELENGTH_NM)), int(read(ui_state, RI_AGE_WARNING_DAYS)))
    return actions


def _apply_ri_settings(m: _Modules, file_actions: FileMenu, wavelength_nm: float, age_warning_days: int) -> None:
    """Preferences -> Refractive indices: the wavelength is stamped on the next import (0 = not given); the age limit only colours the table."""
    m.experiment_plan.ri_wavelength_nm = float(wavelength_nm) if wavelength_nm > 0 else None
    if file_actions.manager.refractive is not None:
        file_actions.manager.refractive.set_age_warning_days(int(age_warning_days))


def _wire_analysis_scan(m: _Modules, row: AnalysisTab, ui_state: UiStateStore, startup: StartupTracker | None = None) -> None:
    """The Image panel's Analysis tab (Scan / Fast scan / Scan file) and the scan task: the tab only emits requests and shows text; this
    decides where the file goes, starts the task on the GUI thread and turns its outcome into the tab's status line.
    `ui_state` holds the remembered analysis mode (Normal / Performance, 2026-10-27); a remembered Performance mode starts its worker processes
    once the application has finished starting up (`startup`; or 20 s after launch when nothing is being loaded), not while the dataset loads."""
    row.bind_live(m.analysis_live)  # not remembered: a pause is a working state, the app always starts live

    remembered = read(ui_state, ANALYSIS_MODE)
    mode = remembered if remembered in MODES else MODE_NORMAL
    row.set_mode(mode)  # shown, not announced
    m.analysis_scan.set_mode(mode, start_pool=False)

    def mode_picked(picked: str) -> None:
        ui_state.set(ANALYSIS_MODE.key, picked)
        m.analysis_scan.set_mode(picked)  # Performance: the workers start now, on a thread of their own

    row.mode_changed.connect(mode_picked)
    m.analysis_scan.pool_state_changed.connect(row.set_pool_state)  # a bound method of the tab: delivered on the GUI thread
    # A remembered Performance mode's workers are started by `_wire_startup_calculation`: after the start-up calculation of the ROI / Groups
    # table, which they used to slow down tenfold (2026-11-11).

    def scan_file() -> Path | None:
        home, session_id = m.dataset.dataset_home(), m.session_coordinator.active_session_id()
        if home is None or session_id is None:
            return None
        return session_dir_for(home, session_id) / "analysis" / SCAN_FILE_NAME

    def show_saved_scan(*_args: object) -> None:
        path = scan_file()
        line = describe_scan_file(path) if path is not None else None
        row.set_status(line or "No scan yet.")
        refresh_file_button()

    def refresh_file_button(*_args: object) -> None:
        path = scan_file()
        row.set_scan_file_available(path is not None and path.is_file())

    def open_scan_folder() -> None:
        path = scan_file()
        if path is None or not path.is_file():
            row.set_scan_file_available(False)
            row.set_status("There is no scan file to show.", announce=True)
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent))):
            row.set_status(f"Could not open the folder {path.parent}.", announce=True)

    def run(only_stale: bool) -> None:  # True = the Fast scan button, False = the Scan button (every cube)
        path = scan_file()
        if path is None:
            row.set_status("Open a dataset first: the scan is saved in its session.", announce=True)
            return
        if m.session_load.is_running():
            row.set_status("The session is still loading; run the scan when it has finished.", announce=True)
            return
        try:
            source = analysis_scan_source(m)
        except RuntimeError as exc:
            row.set_status(str(exc), announce=True)
            return
        row.set_status("Starting...")
        m.analysis_scan.start(source, store_path=path, only_stale=only_stale, dataset_label=m.dataset.dataset_home().name)

    def finished(_task_id: str, outcome: str, message: str) -> None:
        if outcome == "cancelled":
            row.set_status(message or "Stopped. Cubes that were finished are saved; run again to continue.", announce=True)
        else:
            row.set_status(message, announce=True)
        refresh_file_button()

    results = m.scan_results
    results.set_scan_file(scan_file())
    m.session_coordinator.active_session_changed.connect(lambda _root: results.set_scan_file(scan_file()))
    m.dataset.dataset_cleared.connect(lambda: results.set_scan_file(None))
    # The file changed under the results module: a scan finished (also a stopped or failed one: the finished cubes are saved).
    m.analysis_scan.completed.connect(lambda summary: results.reload() if summary.cubes else results.request_check())
    m.analysis_scan.cancelled.connect(results.reload)
    m.analysis_scan.failed.connect(lambda _message: results.reload())
    # Anything that can change a stored number: look again at which cubes are out of date (debounced, off the GUI thread).
    # Formula / fit / metric settings are deliberately not here: they never make a stored cell stale.
    for changed in (
        m.geometry.geometry_changed, m.mask.mask_changed, m.chromatic.chromatic_model_changed, m.background.background_model_changed,
        m.roi_toolbox.geometry_changed, m.roi_toolbox.roi_ids_renumbered, m.frame_roles.roles_changed,
    ):
        changed.connect(lambda *_args: results.request_check())
    row.run_requested.connect(run)
    row.stop_requested.connect(m.analysis_scan.cancel)
    m.analysis_scan.running_changed.connect(row.set_running)
    m.analysis_scan.task_finished.connect(finished)
    m.analysis_scan.failed.connect(lambda message: row.set_status(message, announce=True))
    row.open_folder_requested.connect(open_scan_folder)
    results.results_changed.connect(refresh_file_button)  # a scan file came, was reloaded or went
    m.dataset.dataset_cleared.connect(refresh_file_button)
    m.session_coordinator.active_session_changed.connect(show_saved_scan)
    show_saved_scan()


def _wire_data_checks(window: QMainWindow, m: _Modules, sensorgram_panel: SensorgramPanel, ui_state: UiStateStore) -> ChecksPresenter:
    """The Sensorgram's "Checks" button, its report window and the data-checks module (2026-11-07): the button opens the window, the window runs the checks. The chosen
    evaluation pair is remembered. Returns the presenter (the status bar shows its runs)."""
    report_window = CheckReportWindow(
        "Data checks", size_key=CHECKS_WINDOW_SIZE, numbers_key=CHECKS_WINDOW_NUMBERS, ui_state=ui_state, with_pairs=True, parent=window,
    )
    saved_pair = read(ui_state, SENSORGRAM_CHECKS_PAIR)
    m.data_checks.set_pair(saved_pair if isinstance(saved_pair, int) else None)
    m.data_checks.pair_changed.connect(lambda number: ui_state.set(SENSORGRAM_CHECKS_PAIR.key, number))
    return ChecksPresenter(m.data_checks, sensorgram_panel.checks_controls, report_window, window)


def _wire_image_motion(window: QMainWindow, m: _Modules, row: AnalysisTab, ui_state: UiStateStore) -> MotionPresenter:
    """The Analysis tab's "Motion" button, its compact report window and the image-motion module (2026-11-07; 2026-11-10: no wavelength box, the plane of the image on screen
    is measured; the window opens only when a step moves). Returns the presenter."""
    report_window = CheckReportWindow(
        "Image motion", size_key=MOTION_WINDOW_SIZE, numbers_key=MOTION_WINDOW_NUMBERS, ui_state=ui_state, compact=True, parent=window,
    )
    return MotionPresenter(m.image_motion, row, report_window, m.selection.current_wavelength, window)


def _ask_tuning_export_path(parent: QWidget, default: Path) -> Path | None:
    chosen, _filter = QFileDialog.getSaveFileName(parent, "Export the tuning numbers", str(default), "CSV table (*.csv)")
    return Path(chosen) if chosen else None


def _wire_signal_map(m: _Modules, image_panel: ImagePanel, ui_state: UiStateStore) -> PlotExportButton:
    """The Analysis tab's Signal map group, the `SignalMapModule` and the Image panel's drawing of it (2026-11-10). Restores the remembered controls (and hands
    them to the module), passes the user's choices on, hands the module's view to the panel, and makes the group's export button (the Image plot as a figure, the
    map's values as CSV), which is returned so it gets the same export context as the other plots.

    The on / off switch itself is **not** remembered: switching the map on calculates, and the app calculates only when asked (the analysis rule)."""
    module, group, display = m.signal_map, image_panel.analysis_tab.signal_map, image_panel.signal_map
    tab = image_panel.analysis_tab
    for key, widget in (
        ("signal_map/show", group.show_combo), ("signal_map/pair", group.pair_combo), ("signal_map/per", group.per_combo),
        ("signal_map/scheme", group.scheme_combo), ("signal_map/range", group.range_combo), ("signal_map/low", group.low_spin),
        ("signal_map/high", group.high_spin), ("signal_map/legend", group.legend_button), ("signal_map/hover", group.hover_button),
    ):
        ui_state.bind(key, widget)
    module.set_show(group.show_key())
    module.set_pair(group.pair())
    module.set_per(group.per())
    module.set_scheme(group.scheme())
    module.set_range(group.range_mode(), *group.fixed_range())
    display.set_legend_visible(group.legend_button.isChecked())
    display.set_hover_enabled(group.hover_button.isChecked())

    def refresh_steps(*_args: object) -> None:
        group.step_picker.set_steps(m.experiment_plan.steps(), None, group.step_picker.current_ref())

    def announce_if_blocked() -> None:
        reason = module.blocked_reason()
        if reason:
            tab.status_message.emit(reason)

    def enabled(on: bool) -> None:
        module.set_enabled(on)
        if on:
            announce_if_blocked()

    def refresh() -> None:
        if not module.calculate():
            tab.status_message.emit(module.status())

    def state(*_args: object) -> None:
        group.set_state(module.status(), module.needs_refresh())
        tab.info_changed.emit()

    refresh_steps()
    m.experiment_plan.steps_changed.connect(refresh_steps)
    group.enabled_toggled.connect(enabled)
    group.show_changed.connect(module.set_show)
    group.pair_changed.connect(module.set_pair)
    group.step_changed.connect(module.set_step)
    group.per_changed.connect(module.set_per)
    group.scheme_changed.connect(module.set_scheme)
    group.range_changed.connect(module.set_range)
    group.refresh_requested.connect(refresh)
    group.legend_toggled.connect(display.set_legend_visible)
    group.hover_toggled.connect(display.set_hover_enabled)
    module.view_changed.connect(display.set_view)
    module.status_changed.connect(state)
    state()

    def edit_bound(which: str, where: QPoint) -> None:  # a click on a legend number: the other end keeps the value shown now
        view = module.view()
        if view is not None:
            group.popup_bound_editor(which, view.low, view.high, view.decimals(), where)

    # Cube signal follows the Image panel's cube; Subtract baseline is the Sensorgram's switch, shown and changed here too.
    module.set_cube(m.selection.current_cube())
    m.selection.cube_changed.connect(module.set_cube)
    group.set_baseline_checked(module.baseline_enabled())
    m.analysis_settings.settings_changed.connect(lambda _change: group.set_baseline_checked(module.baseline_enabled()))
    group.baseline_toggled.connect(module.set_baseline_enabled)

    display.legend.scheme_clicked.connect(group.popup_scheme_menu)
    display.legend.context_requested.connect(group.popup_context_menu)
    display.legend.bound_clicked.connect(edit_bound)

    def data() -> ExportData | None:
        view = module.view()
        if view is None:
            return None
        return ExportData({"signal_map": signal_map_table(view, module.roi_ids())}, module.export_info())

    button = PlotExportButton("signal_map", display.plot, data, group)
    group.set_export_button(button)
    return button


def _wire_roi_tuning(
    m: _Modules,
    tab: TuningTab,
    ui_state: UiStateStore,
    show_view: Callable[[TuningView | None], None] | None = None,
    choose_export_path: Callable[[QWidget, Path], Path | None] = _ask_tuning_export_path,
) -> None:
    """The Image panel's Tuning tab and the ROI position tuning module (2026-10-29): the tab only emits requests and shows text; this restores its remembered
    controls (and hands the values to the module), starts a trial on the GUI thread, tells the tab what to say, hands the module's drawing data to the Image
    panel (`show_view` = `ImagePanel.set_tuning_view`: crosses, ROIs at their best centres), and marks a result out of date when something it was made from
    changes (the same signals that make a scan cell stale). `choose_export_path` is the file dialog (a test passes its own)."""
    tuning = m.roi_tuning
    for key, widget in (
        ("tuning/lattice_size", tab.size_combo), ("tuning/pitch_px", tab.pitch_spin), ("tuning/objective", tab.objective_combo),
        ("tuning/show_crosses", tab.crosses_button), ("tuning/show_best", tab.best_button), ("tuning/interpolate", tab.interpolate_button),
    ):
        ui_state.bind(key, widget)
    tuning.set_lattice(tab.lattice_size(), tab.pitch_px())  # restored values go to the module before the tab can announce changes
    tuning.set_objective(tab.objective_key())
    tuning.set_show_crosses(tab.crosses_button.isChecked())
    tuning.set_show_best(tab.best_button.isChecked())
    tuning.set_interpolate(tab.interpolate_button.isChecked())

    def refresh(*_args: object) -> None:
        tab.set_state(tuning.state(), tuning.preview(), tuning.can_accept())
        tab.set_status(tuning.message() or "No trial yet.")

    def run() -> None:
        if m.analysis_scan.is_running():
            tab.set_status("An analysis scan is running; try positions when it has finished.", announce=True)
        elif m.session_load.is_running():
            tab.set_status("The session is still loading; try positions when it has finished.", announce=True)
        elif not tuning.run():
            tab.set_status(tuning.message(), announce=True)

    def accept() -> None:
        if m.analysis_scan.is_running():  # both write the scan file
            tab.set_status("An analysis scan is running; accept when it has finished.", announce=True)
            return
        if m.session_load.is_running():
            tab.set_status("The session is still loading; accept when it has finished.", announce=True)
            return
        home, session_id = m.dataset.dataset_home(), m.session_coordinator.active_session_id()
        store_path = session_dir_for(home, session_id) / "analysis" / SCAN_FILE_NAME if home is not None and session_id is not None else None
        tuning.accept(store_path, home.name if home is not None else "")
        tab.set_status(tuning.message(), announce=True)  # a refusal, or "Moved n ROIs ..." (the outcome of saving their numbers follows from the task)

    def export() -> None:
        home, session_id = m.dataset.dataset_home(), m.session_coordinator.active_session_id()
        folder = session_dir_for(home, session_id) / "analysis" if home is not None and session_id is not None else Path.home()
        path = choose_export_path(tab, folder / "roi_tuning.csv")
        if path is not None:
            tuning.export_csv(path)

    tab.run_requested.connect(run)
    tab.stop_requested.connect(tuning.cancel)
    tab.lattice_changed.connect(lambda size, pitch: tuning.set_lattice(size, pitch))
    tab.objective_changed.connect(tuning.set_objective)
    tab.crosses_toggled.connect(tuning.set_show_crosses)
    tab.best_toggled.connect(tuning.set_show_best)
    tab.interpolate_toggled.connect(tuning.set_interpolate)
    tab.accept_requested.connect(accept)
    tab.clear_requested.connect(tuning.clear)
    tab.export_requested.connect(export)
    # Accept wrote cells into scan.h5 (or could not): the scan results module reads the file again and compares it with the moved ROIs.
    tuning.seeded.connect(lambda report: m.scan_results.reload() if report.written else m.scan_results.request_check())
    tuning.export_finished.connect(
        lambda path, error: tab.set_status(f"Could not write {path}: {error}" if error else f"Wrote the tuning numbers to {path}.", announce=True)
    )
    tuning.task.task_finished.connect(lambda _task_id, _outcome, _message: tab.set_status(tuning.message() or "No trial yet.", announce=True))
    # Anything that can alter a scan number: the result no longer says where the best centres are (the scan results module watches the same signals).
    for changed in (
        m.geometry.geometry_changed, m.mask.mask_changed, m.chromatic.chromatic_model_changed, m.background.background_model_changed,
        m.roi_toolbox.geometry_changed, m.roi_toolbox.roi_ids_renumbered, m.frame_roles.roles_changed,
    ):
        changed.connect(lambda *_args: tuning.mark_stale())
    # What a press of Try would do depends on the selection, the two plan steps and the lattice: keep the tab's tooltip current.
    for changed in (m.selection.roi_selection_changed, m.experiment_plan.steps_changed, tuning.state_changed):
        changed.connect(refresh)
    refresh()
    if show_view is not None:
        for changed in (tuning.matrix_changed, tuning.view_changed):
            changed.connect(lambda: show_view(tuning.view()))
        show_view(tuning.view())


def _build_startup_tracker(
    m: _Modules, image_panel: ImagePanel, spectra_panel: SpectraPanel, sensorgram_panel: SensorgramPanel
) -> StartupTracker:
    """The status-bar "Starting up" entry for a launch that reopens the last dataset (2026-10-24, `startup_tracker.py`). Idle until
    `_auto_reopen_last_dataset` calls `start()`. The steps are asked in this order; each says what the app is waiting for."""
    first_image = {"drawn": False}
    image_panel.image_rendered.connect(lambda *_: first_image.update(drawn=True))
    scan = m.scan_results

    def scan_settled() -> bool:
        # No file, or a file that has been read *and* compared with the current setup (stale_cubes() is None until that check ran).
        if scan.is_loading() or scan.is_checking():
            return False
        return scan.stored() is None or scan.stale_cubes() is not None or bool(scan.message())

    tracker = StartupTracker([
        StartupStep("dataset", "opening the dataset", lambda: m.dataset.dataset_home() is not None and not m.dataset.is_loading()),
        # The autosave root is set only once the session has been applied (or found not to exist), see _wire_session_coordinator.
        StartupStep("session", "reading the session", lambda: m.session_autosave.root() is not None and not m.session_load.is_running()),
        StartupStep("first image", "drawing the first image", lambda: first_image["drawn"]),
        StartupStep("scan results", "reading the scan results", scan_settled),
        StartupStep("panels", "Spectra / Sensorgram calculating", lambda: not (spectra_panel.task.is_active() or sensorgram_panel.task.is_active())),
    ])
    # Both signals come from the loader thread; connecting to the tracker's own slots makes Qt deliver them on the GUI thread.
    m.dataset.dataset_load_failed.connect(tracker.fail)
    m.dataset.dataset_choice_needed.connect(tracker.on_choice_needed)
    return tracker


def _wire_startup_calculation(startup: StartupTracker, roi_table_panel: RoiTablePanel, start_pool: Callable[[], None]) -> None:
    """The ROI / Groups table's step columns are calculated once after a launch that reopened the last dataset (2026-11-06, maintainer's request). The moment is the tracker's
    "completed": dataset, session (ROIs, groups, plan), first image, scan results and the Spectra / Sensorgram fits are all settled, so the numbers are not out of date as
    they arrive, and the fits the Sensorgram already made are reused. A failed or cancelled start-up, or a dataset opened later by hand, calculates nothing.

    `start_pool` (the analysis scan's Performance-mode workers; does nothing in Normal mode or when they run) is called only once that calculation has ended, or at once
    when there is none: seven worker processes starting (~1,000 imports each, ~8 s) took the CPU from the fit helper, which runs at low priority, and the calculation
    took 11 s instead of ~1.2 s (2026-11-11). The 20 s fallback for a launch that opens no dataset waits in the same way."""

    def start_pool_when_free() -> None:
        if not roi_table_panel.task.is_active():  # otherwise the calculation's `ended` calls this again
            start_pool()

    def on_finished(_task_id: str, outcome: str, _message: str) -> None:
        if outcome == "completed":
            roi_table_panel.calculate_at_startup()
        start_pool_when_free()

    startup.task_finished.connect(on_finished)
    roi_table_panel.task.ended.connect(lambda _outcome: start_pool_when_free())
    QTimer.singleShot(LAUNCH_POOL_FALLBACK_MS, start_pool_when_free)


def _build_status_bar(
    window: QMainWindow,
    m: _Modules,
    file_actions: FileMenu,
    image_panel: ImagePanel,
    roi_table_panel: RoiTablePanel,
    spectra_panel: SpectraPanel,
    sensorgram_panel: SensorgramPanel,
    startup: StartupTracker,
    check_presenters: tuple = (),
) -> None:
    """`check_presenters`: the data-checks and image-motion presenters (each has a `task` and a `cancel`), whose long runs join the task indicator like the rest."""
    status_bar = QStatusBar(window)
    window.setStatusBar(status_bar)
    # App-wide task indicator (spinner, progress, elapsed/ETA, Cancel). Every
    # heavy task joins it with the same three connects below: its
    # `task_progress` -> report, its `task_finished` -> finish, and the
    # indicator's `cancel_requested` -> that task's own cancel.
    task_indicator = TaskIndicator(status_bar)
    status_bar.addPermanentWidget(task_indicator, 1)  # stretch: takes the free width, text clips instead of overlapping
    m.reference_auto.task_progress.connect(task_indicator.report)
    m.reference_auto.task_finished.connect(task_indicator.finish)
    m.chromatic_auto.task_progress.connect(task_indicator.report)
    m.chromatic_auto.task_finished.connect(task_indicator.finish)
    m.transient_scan.task_progress.connect(task_indicator.report)
    m.transient_scan.task_finished.connect(task_indicator.finish)
    m.analysis_scan.task_progress.connect(task_indicator.report)
    m.analysis_scan.task_finished.connect(task_indicator.finish)
    m.roi_tuning.task.task_progress.connect(task_indicator.report)
    m.roi_tuning.task.task_finished.connect(task_indicator.finish)
    image_panel.mask_export.task_progress.connect(task_indicator.report)
    image_panel.mask_export.task_finished.connect(task_indicator.finish)
    image_panel.mask_auto.task_progress.connect(task_indicator.report)
    image_panel.mask_auto.task_finished.connect(task_indicator.finish)
    image_panel.feature_map.task_progress.connect(task_indicator.report)
    image_panel.feature_map.task_finished.connect(task_indicator.finish)
    image_panel.array_action.task_progress.connect(task_indicator.report)
    image_panel.array_action.task_finished.connect(task_indicator.finish)
    m.dataset.task_progress.connect(task_indicator.report)  # OME-Zarr export (Image panel, Export tab)
    m.dataset.task_finished.connect(task_indicator.finish)
    m.session_carry.task_progress.connect(task_indicator.report)  # the session that goes with that export
    m.session_carry.task_finished.connect(task_indicator.finish)
    m.session_load.task_progress.connect(task_indicator.report)  # reading the session when a dataset opens
    m.session_load.task_finished.connect(task_indicator.finish)
    roi_table_panel.task.task_progress.connect(task_indicator.report)  # the ROI table's step columns being calculated (fitting many ROIs)
    roi_table_panel.task.task_finished.connect(task_indicator.finish)
    spectra_panel.task.task_progress.connect(task_indicator.report)  # a Spectra recompute that lasts (metric, fit, formula)
    spectra_panel.task.task_finished.connect(task_indicator.finish)
    sensorgram_panel.task.task_progress.connect(task_indicator.report)  # a Sensorgram recompute that lasts (fitting many ROIs)
    sensorgram_panel.task.task_finished.connect(task_indicator.finish)
    for presenter in check_presenters:
        presenter.task.task_progress.connect(task_indicator.report)
        presenter.task.task_finished.connect(task_indicator.finish)
    startup.task_progress.connect(task_indicator.report)  # "Starting up": the launch is not finished until the dataset and all that follows is in
    startup.task_finished.connect(task_indicator.finish)
    cancel_by_task = {
        STARTUP_TASK_ID: startup.cancel,  # stops the status-bar entry only; loading is not interrupted
        SPECTRA_TASK_ID: spectra_panel.cancel,
        SENSORGRAM_TASK_ID: sensorgram_panel.cancel,
        ROI_TABLE_TASK_ID: roi_table_panel.cancel,
        SESSION_LOAD_TASK_ID: m.session_load.cancel,
        ZARR_EXPORT_TASK_ID: m.dataset.cancel_export,
        SESSION_CARRY_TASK_ID: m.session_carry.cancel,
        CHROMATIC_TASK_ID: m.chromatic_auto.cancel,
        SCAN_TASK_ID: m.transient_scan.cancel,
        ANALYSIS_SCAN_TASK_ID: m.analysis_scan.cancel,
        ROI_TUNING_TASK_ID: m.roi_tuning.cancel,
        AUTO_MASK_TASK_ID: image_panel.mask_auto.cancel,
        MASK_EXPORT_TASK_ID: image_panel.mask_export.cancel,
        FEATURE_MAP_TASK_ID: image_panel.feature_map.cancel,
        ARRAY_TASK_ID: image_panel.array_action.cancel,
        REFERENCE_TASK_ID: m.reference_auto.cancel,
        **{presenter.task.task_id: presenter.cancel for presenter in check_presenters},
    }
    task_indicator.cancel_requested.connect(lambda task_id: cancel_by_task[task_id]())
    # The File menu's state text ("Loading dataset from ...") shows as a transient
    # message on the bar's left side; the task indicator above is permanent, on the
    # right, so neither covers the other.
    file_actions.status_message.connect(status_bar.showMessage)
    m.experiment_plan.ri_notice.connect(lambda message: status_bar.showMessage(message, 15000))  # RIs.txt imported / changed (15 s: easy to miss)
    m.roi_geometry_sync.status_changed.connect(status_bar.showMessage)
    roi_table_panel.status_message.connect(status_bar.showMessage)
    # A failed session write must reach the user, not only the log (30 s: long enough to be seen,
    # and it is shown again on every failed retry).
    m.session_autosave.save_failed.connect(lambda message: status_bar.showMessage(message, 30000))
    # A canvas tool's live status (e.g. the rotate tool's angle readout
    # while placing point 2) - 2026-09-29, replacing an always-in-layout
    # text row under the Image panel with a permanent info icon there for
    # the *static* controls; the *live* per-gesture text belongs here, on
    # the same bar every other panel's transient status already uses.
    image_panel.tool_status_changed.connect(status_bar.showMessage)
    # Play shows its live fps on the bar; a finished task's result (kept until dismissed) would cover it (2026-11-11).
    image_panel.playback_changed.connect(lambda playing: task_indicator.dismiss_result() if playing else None)


def _wire_session_carry_prompt(window: QMainWindow, m: _Modules) -> None:
    """When a session has been carried into an exported dataset, offer to open that dataset (2026-10-22). Opening it loads the
    Zarr and makes the carried session the active one, the same as choosing it by hand; the dataset that is open now, and its
    session, are left exactly as they are. The box does not block (``open``, not ``exec``): the user may keep working."""

    def _ask(result: CarriedResult) -> None:
        box = QMessageBox(window)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Session carried")
        box.setText(f"The export is finished, and your session was carried into {result.destination.name}.")
        lines = [] if result.export_stats is None else [result.export_stats.describe(), ""]
        lines += [f"Session ({result.seconds:.1f} s): {result.report.summary()}", "", "Open the exported dataset now?"]
        box.setInformativeText("\n".join(lines))
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.Yes)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        def _answered(button: int) -> None:
            if button == QMessageBox.StandardButton.Yes.value:
                m.dataset.load_dataset_from_folder(result.destination)

        box.finished.connect(_answered)
        box.open()

    m.session_carry.carried.connect(_ask)


def _follow_plan_bar(sensorgram_panel: SensorgramPanel, step_table: StepTableModule, roi_table_panel: RoiTablePanel) -> None:
    """The step columns of the ROI table look as the Sensorgram's plan bar looks, now and whenever the user changes it there: the same part of each step's note as the title
    (whole note or the solution before the separator, and which separators), and the same fill (the step's colour at the bar's opacity)."""

    def apply() -> None:
        settings = sensorgram_panel.plan_bar_settings()
        step_table.set_title_style(settings.text_mode, settings.separators)
        roi_table_panel.set_step_tint_alpha(settings.alpha)

    sensorgram_panel.plan_bar_settings_changed.connect(apply)
    apply()


def _wire_roi_table_title(dock: PanelContainer, panel: RoiTablePanel) -> None:
    """Make the "ROI / Groups" dock title the switch of the table's Groups-only view (2026-10-24): "Groups" highlighted = Groups-only
    (double-click it); "ROI" = every ROI (click it).

    The title must **start from the panel's real state**. The panel restores its saved Groups-only view before the dock exists, so a
    title that started on "ROI" while the table was locked would ignore a click on "ROI" (already the current word) and a double
    click on "Groups" would change nothing (the table is already in that view): the user was stuck (found 2026-11-05)."""
    dock.set_title_choices(
        (
            TitleChoice("ROI", "Show the ROIs of every group (leaves the Groups-only view)."),
            TitleChoice("Groups", "Double-click: Groups-only view, every group stays collapsed.", on_double_click=True),
        ),
        current=1 if panel.group_only() else 0,
    )
    dock.title_choice_picked.connect(lambda index: panel.set_group_only(index == 1))
    panel.group_only_changed.connect(lambda on: dock.set_title_current(1 if on else 0))


def _build_dock_layout(
    window: QMainWindow,
    image_panel: ImagePanel,
    histogram_panel: HistogramPanel,
    roi_table_panel: RoiTablePanel,
    spectra_panel: SpectraPanel,
    sensorgram_panel: SensorgramPanel,
) -> dict[str, PanelContainer]:
    """Dock-wrap every panel and build the default arrangement. Returns the
    docks by their panel name (the order is the View menu's order and its
    Ctrl+1..6 order).

    Each panel is dock-wrapped via the shared PanelContainer (undock/float/
    maximize/close - see panels/dock_container.py), matching how the stable
    app already docks every panel. This default arrangement is just a
    starting point, not a preset: named, user-editable presets (design doc
    §5) are applied on top of it.
    (The Workflow dock, the left column until 2026-11-11, was retired: its
    actions are in the File menu, its displays in File -> Dataset Manager.)"""
    image_dock = PanelContainer("Image", image_panel, window)
    histogram_dock = PanelContainer("Histogram", histogram_panel, window)
    roi_table_dock = PanelContainer("ROI / Groups", roi_table_panel, window)
    _wire_roi_table_title(roi_table_dock, roi_table_panel)
    # The counts (ROIs, groups, selected) and the panel's notices are fields of the dock's title bar, like the Image panel's frame
    # status (2026-11-06, maintainer request: the row under the table is gone). Seeded once: the panel built its first fields before
    # this connection existed.
    roi_table_panel.title_columns_changed.connect(roi_table_dock.set_subtitle_columns)
    roi_table_dock.set_subtitle_columns(roi_table_panel.title_columns())
    spectra_dock = PanelContainer("Spectra", spectra_panel, window)
    sensorgram_dock = PanelContainer("Sensorgram", sensorgram_panel, window)

    # The frame status ("Cube X", "550 nm", time, comment) shown in the Image
    # dock's own title bar (2026-09-30, maintainer request) rather than in a
    # row under the canvas - see `ImagePanel.frame_status_changed`'s docstring
    # for why render errors are deliberately not routed here too. Since
    # 2026-10-16 as left-aligned fields at fixed places (`frame_columns_changed`
    # -> `set_subtitle_columns`), so a role word or a comment appearing never
    # shifts the others. Seeded once explicitly: `image_panel` already emitted
    # its initial "No dataset loaded." during its own construction, before
    # this connection existed to hear it.
    image_panel.frame_columns_changed.connect(image_dock.set_subtitle_columns)
    image_dock.set_subtitle_columns(image_panel.frame_columns())

    # Every panel lives in ONE nested split tree inside RightDockWidgetArea
    # (2026-10-18): inside the tree, a window resize is shared out among the
    # panels, `resizeDocks` is exact, and every separator drags. A panel that
    # owned an area of its own got 100% of any extra window width (measured
    # with the old Workflow dock: a 1900 px window gave it 798 px).
    window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, image_dock)
    # Split order matters here, found 2026-09-27 debugging a "can't resize
    # the docks' height" report: this establishes the top-row/Spectra
    # split *before* subdividing the top row into columns, not after.
    #
    # The earlier version called `addDockWidget(Bottom, spectra_dock)`
    # instead of a split, which looked equivalent (Spectra still ended up
    # spanning the full width, visually) but isn't: it put Spectra in a
    # *separate* outer Qt dock area (Bottom) from the nested split tree
    # Image/Histogram/ROI table live in (built entirely from
    # `splitDockWidget` calls anchored on image_dock). Confirmed headlessly
    # (precise simulated mouse drags, not just reading the code) that a
    # QMainWindowLayout separator sitting *between* two different outer
    # areas - one of which contains further nested splits - resizes only
    # one direction reliably: dragging to shrink the top row worked, but
    # dragging to grow it (shrink Spectra) silently did nothing, even
    # though Spectra had hundreds of spare pixels to give up.
    #
    # Splitting Spectra off image_dock instead makes it part of the same
    # nested tree, which resizes correctly in both directions - *and*,
    # done in this order (before roi_table_dock/histogram_dock split image_
    # dock further), Spectra still ends up a sibling of the whole top-row
    # group rather than nested inside just one of its columns, so it still
    # spans the full width below Image *and* ROI table, not just below
    # Image. Reordering the two splits below (roi_table_dock/histogram_dock
    # first, this split after) reproduces the full-width look but brings
    # back the one-directional resize bug - both properties depend on this
    # exact order, confirmed by testing each ordering directly.
    window.splitDockWidget(image_dock, spectra_dock, Qt.Orientation.Vertical)
    window.splitDockWidget(image_dock, roi_table_dock, Qt.Orientation.Horizontal)
    window.splitDockWidget(image_dock, histogram_dock, Qt.Orientation.Vertical)
    # Spectra and Sensorgram were tabified here until 2026-09-27: Qt's own
    # tabify mechanism draws the tab strip as a separate native QTabBar row
    # sitting *above* each dock's own PanelContainer title bar, so a
    # tabified pair always showed two stacked header rows (the native tab
    # strip, then whichever panel's own title bar/buttons) - not fixable by
    # styling alone, since Qt has no public way to put buttons inside its
    # own tab strip. A plain vertical split keeps both panels permanently
    # visible instead, each with its own single-row header exactly like
    # every other panel here, at the cost of splitting height between them
    # rather than only showing one at a time (maintainer's explicit choice
    # over rebuilding this as one merged panel with a custom tab strip).
    window.splitDockWidget(spectra_dock, sensorgram_dock, Qt.Orientation.Vertical)

    # Order here fixes each panel's place in the View menu and its Ctrl+1..5
    # shortcut (2026-10-19, maintainer: ROI / Groups sits right under
    # Histogram; 2026-11-11: Workflow, Ctrl+1 until then, was retired).
    return {
        "Image": image_dock,
        "Histogram": histogram_dock,
        "ROI / Groups": roi_table_dock,
        "Spectra": spectra_dock,
        "Sensorgram": sensorgram_dock,
    }


def _restore_window_state(
    window: QMainWindow,
    settings: AppSettings,
    panel_docks: dict[str, PanelContainer],
    layout_preset_manager,
) -> None:
    if settings.layout_presets:
        layout_preset_manager.load_custom_blobs({
            name: QByteArray(base64.b64decode(blob))
            for name, blob in settings.layout_presets.items()
        })
    if settings.main_window_geometry:
        try:
            window.restoreGeometry(QByteArray(base64.b64decode(settings.main_window_geometry)))
        except Exception:
            logger.exception("Could not restore the saved window geometry")
    if settings.main_window_state:
        try:
            window.restoreState(
                QByteArray(base64.b64decode(settings.main_window_state)), _DOCK_LAYOUT_STATE_VERSION
            )
        except Exception:
            logger.exception("Could not restore the saved window layout")
    # Must run after restoreState() (which is what applies a floating
    # panel's saved geometry) - a panel left floating on a monitor that's
    # since been unplugged/reordered would otherwise reopen off-screen and
    # be unreachable, the second half of the "undocked and now I can't see
    # it" report this module fixes (panels/panel_visibility.py).
    ensure_floating_panels_on_screen(panel_docks, window)


def _wire_quit_persistence(
    window: QMainWindow,
    m: _Modules,
    writer: _SettingsWriter,
    ui_state: UiStateStore,
    layout_preset_manager,
) -> None:
    # Parented now that there is a window to own it, so it dies with the
    # window rather than living on as an orphan QObject holding a timer.
    m.session_autosave.setParent(window)
    # On the application, not on `closeEvent`: the same reasoning ImagePanel
    # documents for its render thread - `closeEvent` reaches only top-level
    # windows, and `aboutToQuit` is where a normal quit actually arrives.
    # Unsaved edits inside the debounce window would otherwise be lost every
    # time the app is closed within 2.5 s of the last edit.
    app = QApplication.instance()
    if app is None:
        return
    app.aboutToQuit.connect(m.session_autosave.flush)

    def _persist_on_quit() -> None:
        ui_state.flush()
        # Window geometry/state and layout-preset blobs only make sense
        # to capture here, at quit - unlike theme/last-dataset
        # above, there is no single "the user just changed this" moment
        # to write on; every dock drag would otherwise mean a write.
        writer.persist(
            main_window_geometry=base64.b64encode(bytes(window.saveGeometry())).decode("ascii"),
            main_window_state=base64.b64encode(
                bytes(window.saveState(_DOCK_LAYOUT_STATE_VERSION))
            ).decode("ascii"),
            layout_presets={
                name: base64.b64encode(bytes(blob)).decode("ascii")
                for name, blob in layout_preset_manager.custom_blobs().items()
            },
            active_layout_preset=layout_preset_manager.current(),
        )

    app.aboutToQuit.connect(_persist_on_quit)


def _auto_reopen_last_dataset(dataset: DatasetModule, settings: AppSettings, startup: StartupTracker | None = None) -> None:
    """Auto-reopen the last dataset (2026-09-26, on by default - see the
    "Reopen Last Dataset on Launch" toggle). Safe on a fresh/test
    `AppSettings()` (no `on_settings_changed` given): `last_dataset_folder`
    is `None` until a real settings file has actually recorded one, so
    this is a no-op for every existing zero-argument caller.

    `startup` (2026-10-24) is the status-bar "Starting up" entry: started here, only when a
    dataset is really being reopened, and ended by the tracker itself once everything is loaded."""
    if not (settings.auto_reopen_last_dataset and settings.last_dataset_folder):
        return
    last_folder = Path(settings.last_dataset_folder)
    if last_folder.is_dir():
        if startup is not None:
            startup.start()
        try:
            dataset.load_dataset_from_folder(last_folder)
        except RuntimeError:
            logger.exception("Could not auto-reopen the last dataset at %s", last_folder)
            if startup is not None:
                startup.fail(f"could not reopen {last_folder.name}")
    else:
        logger.info("Last dataset folder %s no longer exists - skipping auto-reopen", last_folder)


_fallback_log_ring: RingBufferHandler | None = None


def _log_ring() -> tuple[RingBufferHandler, LogSystem | None]:
    """The ring buffer the Log panel reads. Normally the one `main()` configured; a
    window built without `configure_logging` (tests, embedding) gets a private ring
    attached to the root logger once, so the panel still works (without a log file)."""
    global _fallback_log_ring
    system = current_log_system()
    if system is not None:
        return system.ring, system
    if _fallback_log_ring is None:
        _fallback_log_ring = RingBufferHandler()
        logging.getLogger().addHandler(_fallback_log_ring)
    return _fallback_log_ring, None


def _wire_shortcuts_dialog(window: QMainWindow, ui_state: UiStateStore) -> QAction | None:
    """Help -> Keyboard shortcuts (F1; 2026-10-30, `docs/keyboard_shortcut_registry_design_2026-10-30.md`). One non-modal dialog, kept
    and reused: F1 (or the menu entry) while it is open closes it, otherwise it opens with an empty search. Call it before
    `_wire_log_console` so it is the first entry of the Help menu."""
    help_menu = next((action.menu() for action in window.menuBar().actions() if action.text() == "&Help"), None)
    if help_menu is None:
        return None
    holder: dict[str, ShortcutsDialog] = {}
    action = help_menu.addAction("Keyboard shortcuts")
    action.setToolTip("Every keyboard shortcut and mouse gesture of the app.")
    bind_action(action, "help.shortcuts", context=Qt.ShortcutContext.ApplicationShortcut)

    def toggle() -> None:
        dialog = holder.get("dialog")
        if dialog is None:
            dialog = holder["dialog"] = ShortcutsDialog(ui_state, window)
        if dialog.isVisible():
            dialog.close()
            return
        dialog.reset_search()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    action.triggered.connect(toggle)
    return action


APP_TITLE = "LSPRimaging Evaluation"  # the name the About dialog and the citation use


def _wire_about(window: QMainWindow) -> None:
    """Help -> About, the last Help entry: the version, how to cite the software and its licence. The dialog is shared by the apps
    (`lspr_ui.about_dialog`); the wording comes from `lspr_core.citation`."""
    help_menu = next((action.menu() for action in window.menuBar().actions() if action.text() == "&Help"), None)
    if help_menu is None:
        return
    help_menu.addSeparator()
    help_menu.addAction(f"&About {APP_TITLE}...", lambda: _show_about(window))


def _show_about(window: QMainWindow) -> None:
    show_about_dialog(
        window,
        title=f"About {APP_TITLE}",
        app_line=f"{APP_TITLE} {APP_VERSION}",
        description="Evaluation of LSPR imaging data: ROIs, masks, chromatic correction and sensorgrams.",
        citation=citation_text(APP_TITLE, APP_VERSION),
        acknowledgement=ACKNOWLEDGEMENT_REQUEST,
        license_line=f"{LICENSE_NAME}, Copyright (c) 2026 Tomáš Lednický. Free to use, also commercially.",
        notice_url=NOTICE_URL,
        disclaimer=DISCLAIMER_POINTS,
    )


def _wire_log_console(window: QMainWindow, ui_state: UiStateStore, image_panel: ImagePanel | None = None) -> PanelContainer:
    """Help -> Log Console (Ctrl+Shift+L) and Help -> Open Log Folder (2026-10-14,
    docs/logging_design_2026-10-14.md). The Log panel is a normal dock, created floating
    and hidden, and deliberately not part of the workspace panels: it is not among the View
    menu's panel entries, Show/Hide all, or the layout presets (those take explicit dicts of docks).
    Created before the window state is restored so its saved position comes back too."""
    ring, system = _log_ring()
    panel = LogPanel(ring, system, ui_state)
    if image_panel is not None:
        image_panel.playback_changed.connect(panel.set_busy)  # refresh slowly while Play runs
    dock = PanelContainer("Log", panel, window)
    dock.setObjectName("LogConsolePanel")
    window.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
    dock.setFloating(True)
    dock.resize(1000, 460)
    dock.hide()
    help_menu = next((action.menu() for action in window.menuBar().actions() if action.text() == "&Help"), None)
    if help_menu is not None:
        toggle = dock.toggleViewAction()
        toggle.setText("Log Console")
        bind_action(toggle, "help.log_console", context=Qt.ShortcutContext.ApplicationShortcut)
        toggle.setToolTip("Show the application log: messages, timings and errors.")
        help_menu.addAction(toggle)
        folder_action = help_menu.addAction("Open Log Folder")
        folder_action.setEnabled(system is not None)
        folder_action.triggered.connect(panel.open_log_folder)
    return dock


def build_main_window(
    initial_settings: AppSettings | None = None,
    on_settings_changed: Callable[[AppSettings], None] | None = None,
    progress: Callable[[str], None] | None = None,
) -> QMainWindow:
    """Construct every rewrite module and wire the panels to them, per the
    module boundaries in CLAUDE.md / sketch §7. No module reaches into
    another's internals here - this function only connects the public,
    already-defined constructor seams. It is a sequence of named steps; each
    step lives in its own function above (split from one 630-line function,
    2026-10-07, with no behaviour change).

    **`initial_settings`/`on_settings_changed` (2026-09-26)** - the app-level
    settings layer (`storage/app_settings.py`): last dataset, theme, window
    geometry, layout presets. Injected the same way `SessionAutosave` takes callables instead of reading global state
    directly - `main()` is the only real caller that passes a loaded
    `AppSettings` and a callback that actually writes to disk; every
    existing test that calls `build_main_window()` with no arguments keeps
    doing zero settings-file I/O and no auto-reopen (`AppSettings()`'s
    defaults have `last_dataset_folder=None`, so the auto-reopen guard
    is always a no-op without a real settings file behind it).

    **`progress` (2026-10-24)**: called with a short message at the main steps (the launch screen shows it, `launch.py`). Every step is
    also timed (`app.build_stage` DEBUG events, `stages.done(...)` below): add a `done` after a new step that may be slow."""
    stages = StageClock(progress)
    settings = initial_settings if initial_settings is not None else AppSettings()
    writer = _SettingsWriter(settings, on_settings_changed)

    # Applied before any panel is constructed - several read `get_active_
    # theme()` at construction time (e.g. `DatasetFolderRow`), so the right
    # theme has to already be active, not just switched on afterward.
    theme_obj = LSPRI_BRIGHT_THEME if settings.theme == "bright" else LSPRI_DARK_THEME
    set_active_theme(theme_obj)
    app_for_theme = QApplication.instance()
    if app_for_theme is not None:
        apply_app_theme(app_for_theme, theme_obj)

    # Small UI choices (toggles, picks, Export options...) - panels/ui_state.py.
    ui_state = UiStateStore(settings.ui_state, on_changed=lambda values: writer.persist(ui_state=values))
    apply_saved_perf_preferences(ui_state)  # parallel work: automatic / fixed number, cores kept free, low priority (perf_setup.py)
    stages.done("theme and saved settings", "Creating the modules...")

    m = _build_modules()
    # The cube unit (number / start time) is one app-wide display choice, remembered across restarts.
    saved_cube_mode = read(ui_state, CUBE_DISPLAY_MODE)
    if saved_cube_mode in ("cube", "time"):
        m.cube_display.set_mode(str(saved_cube_mode))
    m.cube_display.mode_changed.connect(lambda _mode: ui_state.set(CUBE_DISPLAY_MODE.key, m.cube_display.mode()))
    # A Highlight range selected against one dataset's intensity scale is
    # meaningless for whatever gets opened next - same reasoning as the
    # reference-frame reset just above.
    m.dataset.dataset_cleared.connect(m.highlight_range.clear_range)
    # Remembers the last-opened dataset for the next launch's auto-reopen
    # (see the end of this function) - a dataset open is already a
    # deliberate, infrequent action, so this writes immediately rather than
    # going through the session autosave's debounce. Connected *before* the
    # highlight-range restore below, which records `last_dataset_folder`
    # alongside the range it puts back.
    m.dataset.dataset_loaded.connect(lambda ds: writer.persist(last_dataset_folder=str(ds.home)))
    stages.done("modules", "Building the Image panel...")

    image_panel = _build_image_panel(m, ui_state, theme_obj)
    _wire_highlight_range_persistence(m, ui_state, settings)
    _wire_display_range_persistence(m, ui_state, settings, image_panel)
    stages.done("Image panel", "Building the panels...")
    histogram_panel = _build_histogram_panel(m, image_panel, ui_state)
    image_panel.restore_ui_state(ui_state)
    histogram_panel.restore_ui_state(ui_state)
    stages.done("Histogram panel")
    try:
        m.mask_scope.set_scope(MaskScope(ui_state.get(MASK_SCOPE.key)))
    except ValueError:  # nothing saved yet
        pass
    m.mask_scope.scope_changed.connect(lambda scope: ui_state.set(MASK_SCOPE.key, scope.value))
    roi_table_panel = RoiTablePanel(m.roi_toolbox, m.selection, m.geometry, edit_target=image_panel.edit_target,
        display_style=image_panel.roi_display, step_table=m.step_table,
    )
    roi_table_panel.restore_ui_state(ui_state)
    spectra_panel = SpectraPanel(m.scan_results, m.roi_toolbox, m.selection, m.analysis_settings, live=m.analysis_live)
    spectra_panel.restore_ui_state(ui_state)
    sensorgram_panel = SensorgramPanel(
        m.scan_results, m.roi_toolbox, m.dataset, m.selection, m.analysis_settings, m.cube_display, image_busy=image_panel.render_pending,
        live=m.analysis_live, mask=m.mask, experiment_plan=m.experiment_plan, trace_store=m.trace_store, axis=m.sensorgram_axis,
    )
    sensorgram_panel.restore_ui_state(ui_state)
    # During Play the Sensorgram cursor marks the frame on screen and moves first, so image and cursor reach the screen in one repaint.
    image_panel.playback_changed.connect(sensorgram_panel.follow_play)
    image_panel.play_frame_showing.connect(sensorgram_panel.show_play_frame)
    image_panel.play_frame_shown.connect(sensorgram_panel.repaint_after_play_frame)  # the cursor strip, just before the image view repaints
    _follow_plan_bar(sensorgram_panel, m.step_table, roi_table_panel)  # the table's step columns are titled and coloured as the Sensorgram's plan bar titles and colours its steps
    app_instance = QApplication.instance()
    if app_instance is not None:  # after the panels' own shutdowns (connected in their constructors): nothing may still be fitting when the helper process ends
        app_instance.aboutToQuit.connect(m.trace_store.shutdown)
    spectra_panel.set_fit_pool(sensorgram_panel.fit_pool())  # Spectra fits in the same helper process (never competing with the window)

    def export_context() -> tuple[Path | None, dict[str, object]]:
        """For the plots' export buttons: the dialogs open where the last export was saved (remembered across launches) while that folder exists,
        else in `<session>/exports` (made on first use); the record names the dataset and session."""
        session_root = m.session_autosave.root()
        dataset_home = m.dataset.dataset_home()
        last = read(ui_state, PLOT_EXPORT_FOLDER)
        if last and Path(str(last)).is_dir():
            folder: Path | None = Path(str(last))
        else:
            folder = None if session_root is None else Path(session_root) / "exports"
        return (
            folder,
            {"dataset": None if dataset_home is None else str(dataset_home), "session": None if session_root is None else str(session_root)},
        )

    def remember_export_folder(folder: Path) -> None:
        ui_state.set(PLOT_EXPORT_FOLDER.key, str(folder))

    def remember_export_format(kind: str) -> None:
        ui_state.set(PLOT_EXPORT_FIGURE_FORMAT.key, kind)

    signal_map_export = _wire_signal_map(m, image_panel, ui_state)
    for button in (sensorgram_panel.export_button, spectra_panel.export_button, histogram_panel.export_button(), roi_table_panel.export_button, signal_map_export):
        button.set_context(
            export_context, remember_export_folder, lambda: str(read(ui_state, PLOT_EXPORT_FIGURE_FORMAT)), remember_export_format
        )
    stages.done("ROI table, Spectra and Sensorgram panels", "Assembling the window...")

    window = QMainWindow()
    window.setWindowTitle(version_string())
    window.resize(1400, 900)
    # Qt's default dock options include AllowTabbedDocks, which is what let
    # a plain drag of one panel's title bar onto another's create a tab
    # group interactively - the exact behavior the maintainer asked to
    # remove (2026-09-27), not just the one `tabifyDockWidget` call this
    # function used to make in code. AnimatedDocks/AllowNestedDocks are
    # Qt's other two defaults, kept so ordinary dragging and the nested
    # Image/Histogram/ROI-table split tree built below still work; leaving
    # AllowTabbedDocks out means dropping a panel onto another's center now
    # simply isn't offered as a target - only the split zones are.
    window.setDockOptions(QMainWindow.DockOption.AnimatedDocks | QMainWindow.DockOption.AllowNestedDocks)
    file_menu, edit_menu, view_menu = _build_menu_bar(window)
    file_actions = _wire_file_menu(file_menu, window, m, ui_state)
    attach_to_menu_bar(window.menuBar(), m.dataset)  # dataset path, type / size / cubes, and the Time / Plan checks
    startup = _build_startup_tracker(m, image_panel, spectra_panel, sensorgram_panel)
    _wire_startup_calculation(startup, roi_table_panel, m.analysis_scan.start_pool)
    check_presenters = (
        _wire_data_checks(window, m, sensorgram_panel, ui_state),
        _wire_image_motion(window, m, image_panel.analysis_tab, ui_state),
    )
    _build_status_bar(window, m, file_actions, image_panel, roi_table_panel, spectra_panel, sensorgram_panel, startup, check_presenters)
    _wire_analysis_scan(m, image_panel.analysis_tab, ui_state, startup)
    _wire_roi_tuning(m, image_panel.tuning_tab, ui_state, show_view=image_panel.set_tuning_view)
    _wire_session_carry_prompt(window, m)
    _wire_edit_menu(edit_menu, window)
    stages.done("menus and status bar")
    panel_docks = _build_dock_layout(
        window, image_panel, histogram_panel, roi_table_panel, spectra_panel, sensorgram_panel
    )
    stages.done("dock layout")
    _wire_shortcuts_dialog(window, ui_state)
    log_dock = _wire_log_console(window, ui_state, image_panel)
    _wire_about(window)  # after the other Help entries: it is the last one
    stages.done("Log console")
    # A display panel does no work while its dock is hidden (closed, behind a tab, window minimised) and
    # catches up once when it is shown again (panels/visibility_gate.py).
    for dock_name, hidden_aware_panel in (("Histogram", histogram_panel), ("ROI / Groups", roi_table_panel), ("Spectra", spectra_panel),
                                             ("Sensorgram", sensorgram_panel)):
        panel_docks[dock_name].visibilityChanged.connect(hidden_aware_panel.set_panel_visible)

    # View menu, top to bottom (the order of these calls is the menu order):
    # Show/Hide all + one checkable, Ctrl+1..5-shortcut toggleViewAction()
    # per dock (panels/panel_visibility.py) - the recovery path a
    # closed-and-lost panel had no way back from before this (found
    # 2026-09-27: an undocked panel, once lost, had no fix short of
    # restarting) - then Panel Presets (wire_view_menu below), then Theme.
    # Until 2026-10-19 the panels sat in a "Panels" submenu under Theme.
    wire_panel_visibility_menu(view_menu, panel_docks)

    # Named panel presets (design doc §5). A never-customized preset is
    # not applied at startup - every dock stays visible until the user picks
    # one. A preset slot that *was* customized and saved does get its real
    # geometry back, but only via the raw `window.restoreState()` call in
    # `_restore_window_state` (the most recent on-screen arrangement,
    # whichever preset produced it) - not by re-`apply()`-ing the preset
    # itself, which would restore that slot's own blob from whenever it was
    # last explicitly saved, possibly older. (Applying a preset when the
    # Workflow stage changed went with the Workflow panel, 2026-11-11.)
    layout_preset_manager = wire_view_menu(
        view_menu,
        window,
        dict(panel_docks),
        state_version=_DOCK_LAYOUT_STATE_VERSION,
    )
    _wire_theme_menu(
        view_menu, window, image_panel,
        initial_theme=settings.theme,
        on_theme_changed=lambda name: writer.persist(theme=name),
    )
    stages.done("View menu and layout presets")
    _restore_window_state(window, settings, panel_docks, layout_preset_manager)
    ensure_floating_panels_on_screen({"Log": log_dock}, window)
    stages.done("saved window layout")

    # Edit -> Preferences...: reopen-last-dataset, the overlay cache size and the rest of the app-wide settings (2026-10-19;
    # they were in the Options menu, which is gone).
    _wire_preferences(
        edit_menu, window, image_panel, writer, ui_state,
        apply_ri_settings=lambda wavelength_nm, age_days: _apply_ri_settings(m, file_actions, wavelength_nm, age_days),
    )

    _wire_quit_persistence(window, m, writer, ui_state, layout_preset_manager)
    stages.done("preferences and quit wiring", "Opening the last dataset...")
    _auto_reopen_last_dataset(m.dataset, settings, startup)
    if m.dataset.is_loading():  # a dataset is being reopened, so a Sensorgram fit will be asked for: the helper process starts in parallel
        sensorgram_panel.start_helper_early()
    apply_windows_titlebar_color(window, theme_obj)
    stages.done("dataset reopen start and title bar")
    stages.finish()
    return window


def _screen_resolution_sizes(window: QMainWindow) -> ResolutionSizes | None:
    """The real and the reduced pixel size of the screen the window is on (Preferences -> Screen resolution), or None when not known.

    The reduced size is Qt's size of the screen; the real one comes from Windows (a 96-dpi run cannot get it from Qt). Windows only.
    """
    screen = window.screen()
    if screen is None:
        return None
    geometry = screen.geometry()
    real = monitor_pixels(int(window.winId())) if QGuiApplication.platformName() == "windows" else None  # winId is no HWND on other platforms
    return resolution_sizes(
        (geometry.width(), geometry.height()), running_resolution(), screen.devicePixelRatio(), real
    )


def _wire_preferences(
    edit_menu: QMenu,
    window: QMainWindow,
    image_panel: ImagePanel,
    writer: _SettingsWriter,
    ui_state: UiStateStore,
    apply_ri_settings: Callable[[float, int], None] = lambda _wavelength_nm, _age_days: None,
) -> QAction:
    """Edit -> Preferences... (Ctrl+, anywhere in the app): the app-wide settings that belong to no panel
    (`panels/preferences_dialog.py`; replaced the Options menu on 2026-10-19).

    - The dialog is shown with `open()` (window-modal, does not block the event loop) and applies nothing until OK. OK
      applies only what changed, through the setters the Options menu used: `AppSettings` via the writer (reopen last
      dataset; the "arrange panels when the Workflow stage changes" toggle went with the Workflow panel, 2026-11-11) and
      `ImagePanel.set_overlay_cache_mb` (applied at once, saved with the other UI state). The parallel-work choices
      (2026-10-22) go to the `lspr_core.perf` runtime, which the next export or scan reads, and into the UI-state store.
    - A second press of Ctrl+, while the dialog is open just brings it forward."""
    edit_menu.addSeparator()
    action = edit_menu.addAction("Preferences...")
    bind_action(action, "edit.preferences", context=Qt.ShortcutContext.ApplicationShortcut)

    def current() -> Preferences:
        return Preferences(
            reopen_last_dataset=bool(writer.settings.auto_reopen_last_dataset),
            ri_wavelength_nm=float(read(ui_state, RI_WAVELENGTH_NM)),
            ri_age_warning_days=int(read(ui_state, RI_AGE_WARNING_DAYS)),
            overlay_cache_mb=image_panel.overlay_cache_mb,
            gpu_drawing=image_panel.gpu_drawing,
            screen_resolution=str(read(ui_state, DISPLAY_RESOLUTION)),  # the saved choice (applied at the next start)
            perf=get_runtime().preferences,
        )

    def apply(old: Preferences, new: Preferences) -> None:
        changed: list[str] = []
        if new.reopen_last_dataset != old.reopen_last_dataset:
            writer.persist(auto_reopen_last_dataset=new.reopen_last_dataset)
            changed.append("reopen_last_dataset")
        if (new.ri_wavelength_nm, new.ri_age_warning_days) != (old.ri_wavelength_nm, old.ri_age_warning_days):
            ui_state.set(RI_WAVELENGTH_NM.key, float(new.ri_wavelength_nm))
            ui_state.set(RI_AGE_WARNING_DAYS.key, int(new.ri_age_warning_days))
            apply_ri_settings(new.ri_wavelength_nm, new.ri_age_warning_days)
            changed.append("refractive_index")
        if new.overlay_cache_mb != old.overlay_cache_mb:
            image_panel.set_overlay_cache_mb(new.overlay_cache_mb)
            changed.append("overlay_cache_mb")
        restart_reasons: list[str] = []  # what only a new start completes: one box for all of them
        if new.gpu_drawing != old.gpu_drawing:
            image_panel.set_gpu_drawing(new.gpu_drawing)  # stays off (WARNING in the log) where OpenGL is not available
            changed.append("gpu_drawing")
            if not new.gpu_drawing:  # switching on works at once; off leaves the window an OpenGL window until the app starts again
                restart_reasons.append(
                    "Drawing the image with the processor is complete only after the app starts again "
                    "(until then the window still goes through the graphics card)."
                )
        if new.screen_resolution != old.screen_resolution:
            ui_state.set(DISPLAY_RESOLUTION.key, new.screen_resolution)  # read before the next start's QApplication (display_options.py)
            changed.append("screen_resolution")
            if new.screen_resolution != running_resolution():  # a choice that goes back to what this run already has needs no restart
                sizes = _screen_resolution_sizes(window)
                size_text = ""
                if sizes is not None:
                    size_text = f" ({format_size(sizes.full if new.screen_resolution == RESOLUTION_FULL else sizes.reduced)})"
                restart_reasons.append(f"The new screen resolution{size_text} is used the next time the app starts.")
        if new.perf != old.perf:
            get_runtime().set_preferences(new.perf)  # takes effect for the next export or scan; a running one keeps its own
            changed.extend(f"perf_{name}" for name in write_perf_preferences(ui_state, new.perf))
        if changed:
            log_event(logger, "app.preferences_changed", logging.INFO, changed=",".join(changed))
        if restart_reasons:
            ask_restart(restart_reasons)

    def ask_restart(reasons: list[str]) -> None:
        """Some choices are only complete after a new start (the screen resolution is fixed when the program starts, `display_options.py`;
        switching the graphics card off leaves an OpenGL window, `gpu_view.py`): say which, and offer to close the app now.

        Not a relaunch: the Suite launcher starts the app as a process it tracks (output, "already running", crash report), and a copy started
        by the app itself would escape that. The person starts the app again as usual. Shown with `open()` (does not block the event loop).
        """
        text = "\n".join(reasons) + "\n\nClose the app now, then start it again?"
        box = QMessageBox(QMessageBox.Icon.Question, "Restart needed", text, QMessageBox.StandardButton.NoButton, window)
        close_button = box.addButton("Close the app now", QMessageBox.ButtonRole.AcceptRole)
        later_button = box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(later_button)  # closing the app is the one choice that must be made on purpose
        box.setEscapeButton(later_button)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        close_button.clicked.connect(lambda: QTimer.singleShot(0, lambda: window.close()))  # after the box is gone; the normal close path
        box.open()

    def forget_saved_counts() -> None:
        removed = get_runtime().forget_learned()
        log_event(logger, "app.perf_saved_counts_forgotten", logging.INFO, removed=removed)

    def open_dialog() -> None:
        shown = window.findChild(PreferencesDialog)
        if shown is not None and shown.isVisible():
            shown.raise_()
            shown.activateWindow()
            return
        old = current()
        dialog = PreferencesDialog(
            old,
            overlay_cache_status=image_panel.overlay_cache_status(),
            cache_set_by_environment=environment_budget_mb() is not None,
            perf_status=perf_status_text(),
            core_count=get_runtime().last_system().logical_cores,
            perf_saved_count=perf_saved_count(),
            running_resolution=running_resolution(),
            resolution_sizes=_screen_resolution_sizes(window),
            resolution_available=sys.platform == "win32",
            parent=window,
        )
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.accepted.connect(lambda: apply(old, dialog.values()))
        dialog.accepted.connect(lambda: forget_saved_counts() if dialog.forget_saved_requested else None)
        dialog.open()

    action.triggered.connect(open_dialog)
    return action


_GC_SUMMARY_MS = 5000

# Qt keeps the plots' cached items (axes with their grid, the Sensorgram's plan bar and step table) as screen-sized pictures
# in one shared pixmap cache, 10 MB by default. One axis of a 2400 x 1000 px plot is ~9 MB, so on a large screen the pictures
# pushed each other out at every frame of Play and were all drawn again (91 vs 10 ms per cursor move, offscreen, 2026-11-11;
# ~26 % of the GUI thread in Play). A limit, not an allocation: memory is used only by pictures actually cached.
_PIXMAP_CACHE_KB = 256 * 1024


def main(app: QApplication | None = None, splash: LaunchSplash | None = None) -> None:
    """`app` / `splash`: `src/main.py` creates the QApplication and the launch screen first, so the screen is up while the
    modules above are imported; `splash` is closed once the window is shown. Both default to None (own QApplication, no screen)."""
    system = configure_logging(app_version=version_string())
    apply_switch_interval()  # before any worker thread exists; see gil_tuning.py
    if app is None:
        set_windows_app_user_model_id(APP_ID_LSPRI_EVALUATION)  # before any window; `src/main.py` does it for the launch-screen start
        configure_opengl_surface()  # before the QApplication, as `src/main.py` does (opengl_surface.py)
        apply_saved_resolution()  # likewise (display_options.py)
        app = QApplication(sys.argv)
    QPixmapCache.setCacheLimit(max(QPixmapCache.cacheLimit(), _PIXMAP_CACHE_KB))
    if splash is not None:
        splash.show("Starting...")
    log_event(
        logger, "app.start", logging.INFO,
        version=version_string(), log_file=str(system.log_path),
        screens=len(app.screens()), platform=sys.platform,
    )
    app.aboutToQuit.connect(lambda: log_event(logger, "app.quit", logging.INFO))
    if system.gc_trace is not None:  # one `gc.summary` DEBUG record per 5 s of garbage-collector activity (applog/gc_trace.py)
        gc_timer = QTimer(app)
        gc_timer.setInterval(_GC_SUMMARY_MS)
        gc_timer.timeout.connect(system.gc_trace.flush)
        gc_timer.start()
        app.aboutToQuit.connect(system.gc_trace.flush)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName("LSPRi Evaluation")
    app.setApplicationVersion(version_string())
    app.setWindowIcon(app_icon())

    initial_settings = load_app_settings()
    runtime = install_perf_runtime(initial_settings.ui_state)  # learned worker counts + saved choices; never in tests (perf_setup.py)
    log_event(logger, "perf.machine", logging.INFO, machine=runtime.probe().describe(), fingerprint=runtime.last_system().fingerprint())
    window = build_main_window(initial_settings, on_settings_changed=save_app_settings, progress=splash.show if splash is not None else None)
    window.show()
    if splash is not None:
        splash.finish(window)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
