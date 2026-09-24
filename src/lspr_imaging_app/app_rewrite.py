"""Entry point for the rewrite-preview build (``lspri-evaluation-rewrite``
console script, ``src/main_rewrite.py``).

**Not the real app.** This exists so the maintainer can open a real window
and watch the rewrite's module/panel skeleton take shape while it's being
built, alongside the still-fully-functional stable app (``app.py`` /
``lspri-evaluation``) - see AGENTS.md and
``docs/rewrite_architecture_sketch_2026-09.md``. Every panel currently
shows an empty placeholder: the modules behind them
(:mod:`lspr_imaging_app.dataset`, :mod:`lspr_imaging_app.image_tools`,
:mod:`lspr_imaging_app.roi`, :mod:`lspr_imaging_app.analysis`,
:mod:`lspr_imaging_app.selection`) construct without error, but every
method that would actually load data, render an image, or compute anything
still raises ``NotImplementedError`` - this window exists to prove the
pieces wire together, not to be used for real evaluation work.

Deliberately skips almost everything ``app.py`` does (startup splash,
dataset restore flow, panel-layout persistence, window-state restore) -
none of that exists yet in the new architecture, and reproducing it here
would just be more code to throw away once the real shell (sketch §7
"Workflow shell") replaces this file.
"""

from __future__ import annotations

import logging
import sys

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QActionGroup
from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow, QMenu, QStatusBar, QWidget

from lspr_ui import app_icon, set_active_theme, BRIGHT_THEME, GRAY_DARK_THEME

from .analysis import AnalysisEngine, AnalysisSettingsModule
from .analysis.provenance import FrameNamingScheme
from .dataset import DatasetModule
from .gui.app_theme import apply_app_theme
from .image_tools import BackgroundModule, ChromaticModule, GeometryModule, MaskModule
from .panels.dock_container import PanelContainer
from .panels.histogram import HistogramPanel
from .panels.image import ImagePanel
from .panels.layout_presets import wire_view_menu
from .panels.roi_table import RoiTablePanel
from .panels.sensorgram import SensorgramPanel
from .panels.spectra import SpectraPanel
from .panels.workflow import WorkflowPanel
from .roi import RoiToolbox
from .selection import SelectionModule
from .storage.session import SessionState, load_session
from .storage.session_autosave import SessionAutosave
from .undo import undo_manager
from .version_rewrite import rewrite_version_string

logger = logging.getLogger(__name__)


def _not_functional_reminder() -> QWidget:
    """A permanent status-bar widget, not a central banner - the window's
    central area is dock widgets now (see ``build_main_window``), so there's
    no fixed-position banner slot left to eat screen space. Permanent (added
    via ``QStatusBar.addPermanentWidget``) so ``WorkflowPanel``'s transient
    stage-status messages (left side of the bar) never cover it."""
    label = QLabel(
        "Scaffold preview - every panel is real code wired to real modules, "
        "but every module method still raises NotImplementedError until it's actually built."
    )
    label.setStyleSheet("padding: 0 8px; font-weight: 600;")
    return label


def _build_menu_bar(window: QMainWindow) -> tuple[QMenu, QMenu]:
    """Standard File/Edit/View/Options/Help menus
    (``docs/rewrite_gui_shell_design_2026-09.md`` §2). Only File->Exit is
    real so far - View gets theme switching and layout presets (below),
    Options gets the presets' auto-apply toggle (below - standing in for a
    not-yet-built Preferences dialog), Edit gets undo/redo
    (``undo.undo_manager`` already exists and has nothing wired to it yet),
    Help is an empty placeholder. Adding them now, even empty, keeps the
    menu *bar* itself - not just its contents - something later work fills
    in rather than builds from scratch.

    Returns (View menu, Options menu) so the caller can add the theme/
    preset actions once the panels those actions need to act on actually
    exist."""
    menu_bar = window.menuBar()
    file_menu = menu_bar.addMenu("&File")
    file_menu.addAction("E&xit", window.close)
    menu_bar.addMenu("&Edit")
    view_menu = menu_bar.addMenu("&View")
    options_menu = menu_bar.addMenu("&Options")
    menu_bar.addMenu("&Help")
    return view_menu, options_menu


def _wire_theme_menu(view_menu: QMenu, window: QMainWindow, image_panel: ImagePanel) -> None:
    """View -> Theme: Dark / Bright, exclusive-checkable (design doc §6).

    No persistence yet (there is no settings/Preferences layer in the
    rewrite to persist into - the stable app's equivalent,
    ``MainWindow._set_ui_theme``, writes to ``QSettings``). Every live
    theme switch has to explicitly touch three kinds of chrome, in this
    order, matching the stable app's own ``_apply_theme_styles``:

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
    theme_menu = view_menu.addMenu("Theme")
    group = QActionGroup(window)
    group.setExclusive(True)

    dark_action = theme_menu.addAction("Dark")
    dark_action.setCheckable(True)
    dark_action.setChecked(True)  # matches set_active_theme(GRAY_DARK_THEME) at startup (main())
    group.addAction(dark_action)

    bright_action = theme_menu.addAction("Bright")
    bright_action.setCheckable(True)
    group.addAction(bright_action)

    def switch_theme(theme) -> None:
        set_active_theme(theme)
        app = QApplication.instance()
        if app is not None:
            apply_app_theme(app, theme)
        image_panel.refresh_theme()
        for dock in window.findChildren(PanelContainer):
            dock.refresh_theme()

    dark_action.triggered.connect(lambda checked: switch_theme(GRAY_DARK_THEME) if checked else None)
    bright_action.triggered.connect(lambda checked: switch_theme(BRIGHT_THEME) if checked else None)


def _build_analysis_engine(
    dataset: DatasetModule,
    geometry: GeometryModule,
    mask: MaskModule,
    chromatic: ChromaticModule,
    background: BackgroundModule,
    roi_toolbox: RoiToolbox,
    analysis_settings: AnalysisSettingsModule | None = None,
) -> AnalysisEngine:
    """Gather the engine's narrow per-module reads into the callables it
    takes at construction (2026-09-23 - previously ``AnalysisEngine()`` with
    no arguments at all, i.e. every action method raised).

    The engine takes callables rather than module references on purpose:
    it is the one component that needs to read from *every* other module,
    and holding six module references would make it the god object this
    rewrite exists to avoid (see
    ``docs/rewrite_feature_inventory_2026-09.md``). Naming each read
    explicitly here keeps the full list of what analysis depends on visible
    in one place - and keeps the engine unit-testable with plain fakes, no
    Qt modules required.

    No storage root is passed: the store lives beside the dataset, which
    isn't loaded yet at this point. ``build_main_window`` connects
    ``dataset_loaded`` to ``set_storage_root``.
    """
    return AnalysisEngine(
        load_plane=dataset.load_plane,
        # Per-cube, never DatasetModule.wavelengths() - that is the union
        # across every cube, and a cube short one wavelength (an aborted
        # acquisition) would make the engine ask for a plane that isn't
        # there.
        cube_indices=dataset.spectral_cubes,
        wavelengths_for_cube=dataset.wavelengths_for_cube,
        rois=roi_toolbox.rois,
        geometry_settings=geometry.settings,
        background_settings=background.settings,
        chromatic_affine=lambda cube_index, wavelength_nm: chromatic.affine_for((cube_index, wavelength_nm)),
        chromatic_affine_between=chromatic.affine_between,
        # Handed through as authored, unwarped - compute_cell does the
        # re-registration, in processed space (see tasks.py's
        # _mask_for_compute).
        resolve_mask=lambda cube_index, wavelength_nm: mask.resolve_mask_source((cube_index, wavelength_nm)),
        reduction_method=lambda: roi_toolbox.detection_settings().reduction_method,
        default_reference_radii=lambda: (
            roi_toolbox.detection_settings().reference_inner_radius_px,
            roi_toolbox.detection_settings().reference_outer_radius_px,
        ),
        # The whole object, for apply_preprocessing's `mask_settings` - what
        # makes `flatten_background_exclude_mask` actually exclude the ignore
        # mask from the background estimate (2026-09-23; see analysis/tasks.py).
        detection_settings=roi_toolbox.detection_settings,
        # The query layer's fit/metric half. Optional, unlike the reads
        # above: without a settings module the engine answers `get_metric`
        # using MetricSettings' defaults rather than raising, because these
        # can never make a stored cell wrong - only derive it differently.
        **(
            {}
            if analysis_settings is None
            else {"metric_settings": analysis_settings.metric_settings}
        ),
    )


def capture_session(
    geometry: GeometryModule,
    mask: MaskModule,
    chromatic: ChromaticModule,
    background: BackgroundModule,
    roi_toolbox: RoiToolbox,
    selection: SelectionModule,
    analysis_settings: AnalysisSettingsModule,
) -> SessionState:
    """Read every module's current state into a plain, Qt-free
    :class:`SessionState` (2026-09-23).

    Lives here for the same reason ``_build_analysis_engine`` does: this is
    the one place that knows about every module, and keeping the knowledge
    here leaves ``storage/session.py`` a pure data layer that a test can
    drive with dataclasses and no Qt at all.

    Every query below already returns a defensive copy, so the captured
    state cannot be mutated out from under the caller by continued use of
    the app while it is being written."""
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

    selection.set_cube(state.selected_cube)
    selection.set_wavelength(state.selected_wavelength)
    selection.set_roi_selection(set(state.selected_roi_ids))

    undo_manager.clear()


def _frame_naming(dataset: DatasetModule) -> FrameNamingScheme:
    """The dataset's own frame-tag scheme, which saving and loading a
    session must agree on or a mask PNG's filename won't be found again
    (`storage/session.py`'s `save_session`).

    Same derivation as ``AnalysisEngine._naming``. Two copies rather than
    one shared helper because the two sit on opposite sides of the module
    boundary and neither owns the dataset - worth collapsing into
    ``DatasetModule`` itself if a third caller ever appears."""
    cube_indices = list(dataset.spectral_cubes())
    wavelengths: list[float] = []
    for cube_index in cube_indices:
        wavelengths.extend(dataset.wavelengths_for_cube(cube_index))
    return FrameNamingScheme.for_dataset(cube_indices, wavelengths)


def _build_session_autosave(
    dataset: DatasetModule,
    geometry: GeometryModule,
    mask: MaskModule,
    chromatic: ChromaticModule,
    background: BackgroundModule,
    roi_toolbox: RoiToolbox,
    selection: SelectionModule,
    analysis_settings: AnalysisSettingsModule,
) -> SessionAutosave:
    """Give ``save_session``/``load_session`` the triggers they were built
    without (2026-09-23): load when a dataset opens, autosave while it is
    open, flush before anything replaces it.

    Same shape and reasoning as ``_build_analysis_engine`` above - this is
    the one place that knows about every module, so the knowledge of *what
    counts as a change* lives here rather than inside
    ``storage/session_autosave.py``, which stays a pure debounce.

    Every computational **and** cosmetic signal is connected. The
    cosmetic/computational split exists to tell the analysis store what may
    be stale (sketch §3); it says nothing about what is worth persisting,
    and a relabelled or recoloured ROI is exactly as worth keeping as a
    moved one."""
    autosave = SessionAutosave(
        capture=lambda: capture_session(
            geometry, mask, chromatic, background, roi_toolbox, selection, analysis_settings
        ),
        naming=lambda: _frame_naming(dataset),
    )

    def restore_for(dataset_model: object) -> None:
        # `set_root(None)` first: it flushes whatever the *previous* dataset
        # still had pending, while that dataset's root is still the current
        # one. Doing it after the switch would write the old dataset's edits
        # into the new dataset's folder.
        root = Path(dataset_model.home)
        autosave.set_root(None)
        try:
            state = load_session(root)
        except Exception:
            # `load_session` raises on a file it cannot read or does not
            # recognise, deliberately (see its docstring) - starting from
            # defaults silently would look exactly like a dataset that was
            # never set up. Autosave stays off for this root so the app
            # cannot overwrite a recoverable file with blank state.
            logger.exception("Could not read the session for %s - continuing with defaults", root)
            autosave.set_root(root, enabled=False)
            return
        if state is not None:
            # Restoring emits from every module it touches; without this the
            # restore would schedule a save of what was just loaded.
            with autosave.suspended():
                apply_session(
                    state, geometry, mask, chromatic, background,
                    roi_toolbox, selection, analysis_settings,
                )
        autosave.set_root(root)

    dataset.dataset_loaded.connect(restore_for)
    dataset.dataset_cleared.connect(lambda: autosave.set_root(None))

    for signal in (
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
    ):
        signal.connect(autosave.schedule)

    return autosave


def build_main_window() -> QMainWindow:
    """Construct every rewrite module and wire the panels to them, per the
    module boundaries in AGENTS.md / sketch §7. No module reaches into
    another's internals here - this function only connects the public,
    already-defined constructor seams."""
    dataset = DatasetModule()
    geometry = GeometryModule()
    mask = MaskModule()
    chromatic = ChromaticModule()
    background = BackgroundModule()
    roi_toolbox = RoiToolbox()
    selection = SelectionModule()
    analysis_settings = AnalysisSettingsModule()
    analysis_engine = _build_analysis_engine(
        dataset, geometry, mask, chromatic, background, roi_toolbox, analysis_settings
    )

    # SelectionModule holds no RoiToolbox reference of its own (AGENTS.md,
    # "no module reaches into another's internals") - this is the one place
    # that connects RoiToolbox's roi_ids_renumbered signal to Selection's
    # remap_roi_ids(), so a delete-driven ROI renumber never leaves a stale
    # selected id behind (see roi/toolbox.py's module docstring, "Cross-
    # module consequence", and selection/module.py).
    roi_toolbox.roi_ids_renumbered.connect(selection.remap_roi_ids)

    # The analysis store lives beside the dataset, so it can only be located
    # once one is loaded - see AnalysisEngine.set_storage_root for why the
    # engine is re-pointed rather than rebuilt. `home`, not `folder`: it is
    # the folder derived data is allowed to be written into, so an analysis
    # never lands inside a raw TIFF/OME-Zarr folder it doesn't own (see
    # ImageDataset.home).
    dataset.dataset_loaded.connect(lambda ds: analysis_engine.set_storage_root(ds.home))
    dataset.dataset_cleared.connect(lambda: analysis_engine.set_storage_root(None))

    # Connected after the engine's own dataset hooks above, so a restored
    # session's signals land on panels that are already looking at the right
    # store. Qt calls slots in connection order.
    session_autosave = _build_session_autosave(
        dataset, geometry, mask, chromatic, background, roi_toolbox, selection, analysis_settings
    )

    # AnalysisScope.SELECTED_ROIS means "whatever is selected right now".
    # Setting it never triggers computation (sketch §7) - it only decides
    # what the *next* explicitly-requested run covers.
    # sorted(): roi_selection_changed carries a set, whose iteration order is
    # not meaningful - the engine's scope tuple should be stable run to run.
    selection.roi_selection_changed.connect(
        lambda roi_ids: analysis_engine.set_selected_rois(tuple(sorted(roi_ids)))
    )

    image_panel = ImagePanel(dataset, geometry, mask, chromatic, background, roi_toolbox, selection)
    histogram_panel = HistogramPanel(image_panel)
    roi_table_panel = RoiTablePanel(roi_toolbox)
    spectra_panel = SpectraPanel(analysis_engine, roi_toolbox, selection)
    sensorgram_panel = SensorgramPanel(analysis_engine, roi_toolbox, dataset, selection)
    workflow = WorkflowPanel()

    window = QMainWindow()
    window.setWindowTitle(rewrite_version_string())
    window.resize(1400, 900)
    view_menu, options_menu = _build_menu_bar(window)
    _wire_theme_menu(view_menu, window, image_panel)

    status_bar = QStatusBar(window)
    window.setStatusBar(status_bar)
    status_bar.addPermanentWidget(_not_functional_reminder())
    # WorkflowPanel.set_status() (state/performance text, no hover-hints -
    # design doc §2) shows as a transient message on the bar's left side;
    # the reminder above is permanent, on the right, so neither covers the
    # other.
    workflow.status_requested.connect(status_bar.showMessage)

    # Each panel dock-wrapped via the shared PanelContainer (undock/float/
    # maximize/close - see panels/dock_container.py), matching how the
    # stable app already docks every panel. This default arrangement is
    # just a starting point, not a preset: named, user-editable presets
    # (design doc §5) replace it once built - for now the user can drag
    # panels anywhere via PanelContainer's own controls. Closing a panel
    # (its title bar's close button) currently has no way back short of
    # restarting - a View-menu "show panel" toggle is design doc §5/§6
    # territory, not yet built.
    # collapsible=True - the only panel this applies to (design doc §4).
    workflow_dock = PanelContainer("Workflow", workflow, window, collapsible=True)
    image_dock = PanelContainer("Image", image_panel, window)
    histogram_dock = PanelContainer("Histogram", histogram_panel, window)
    roi_table_dock = PanelContainer("ROI / Groups", roi_table_panel, window)
    spectra_dock = PanelContainer("Spectra", spectra_panel, window)
    sensorgram_dock = PanelContainer("Sensorgram", sensorgram_panel, window)

    window.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, workflow_dock)
    window.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, image_dock)
    window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, roi_table_dock)
    window.splitDockWidget(image_dock, histogram_dock, Qt.Orientation.Vertical)
    window.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, spectra_dock)
    window.tabifyDockWidget(spectra_dock, sensorgram_dock)
    spectra_dock.raise_()
    # Fixed-ish width (design doc §4) - an initial size, not a hard clamp;
    # the user can still drag it wider/narrower (§1, "give the user real
    # freedom to rearrange").
    window.resizeDocks([workflow_dock], [320], Qt.Orientation.Horizontal)

    # Named panel presets (design doc §5). Deliberately not applied here at
    # startup - every dock stays visible until the user explicitly picks a
    # preset (View -> Panel Presets) or turns on the Options menu's
    # auto-apply toggle, which only then starts reacting to stage changes.
    # Forcing a preset at launch while that toggle defaults to off would
    # contradict "manual application always available, auto-apply is
    # opt-in" (§5).
    wire_view_menu(
        view_menu,
        options_menu,
        window,
        workflow,
        workflow_dock,
        {
            "Image": image_dock,
            "Histogram": histogram_dock,
            "ROI / Groups": roi_table_dock,
            "Spectra": spectra_dock,
            "Sensorgram": sensorgram_dock,
        },
    )

    # Parented now that there is a window to own it, so it dies with the
    # window rather than living on as an orphan QObject holding a timer.
    session_autosave.setParent(window)
    # On the application, not on `closeEvent`: the same reasoning ImagePanel
    # documents for its render thread - `closeEvent` reaches only top-level
    # windows, and `aboutToQuit` is where a normal quit actually arrives.
    # Unsaved edits inside the debounce window would otherwise be lost every
    # time the app is closed within 2.5 s of the last edit.
    app = QApplication.instance()
    if app is not None:
        app.aboutToQuit.connect(session_autosave.flush)
    return window


def main() -> None:
    logging.basicConfig(level=logging.DEBUG)
    app = QApplication(sys.argv)
    app.setApplicationName("LSPR Imaging (Rewrite Preview)")
    app.setApplicationVersion(rewrite_version_string())
    app.setWindowIcon(app_icon())
    set_active_theme(GRAY_DARK_THEME)
    apply_app_theme(app)

    window = build_main_window()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
