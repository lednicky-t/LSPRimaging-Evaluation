"""Entry point for the rewrite-preview build (``lspri-evaluation-rewrite``
console script, ``src/main_rewrite.py``).

**Not the real app.** This exists so the maintainer can open a real window
and watch the rewrite's module/panel skeleton take shape while it's being
built, alongside the still-fully-functional stable app (``app.py`` /
``lspri-evaluation``) - see CLAUDE.md and
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

import base64
import logging
import sys

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QTimer, Qt, QByteArray
from PyQt6.QtGui import QActionGroup
from PyQt6.QtWidgets import QApplication, QMainWindow, QMenu, QStatusBar

from lspr_ui import app_icon, set_active_theme

from .analysis import AnalysisEngine, AnalysisSettingsModule
from .analysis.provenance import FrameNamingScheme
from .dataset import DatasetModule
from .gui.app_theme import LSPRI_BRIGHT_THEME, LSPRI_DARK_THEME, apply_app_theme
from .gui.windows_titlebar import apply_windows_titlebar_color
from .image_tools.mask_scope import MaskScope
from .image_tools.chromatic_auto_task import TASK_ID as CHROMATIC_TASK_ID, ChromaticAutoDetect
from .panels.image.chromatic_tab import ChromaticUiValues
from .image_tools import (
    ActiveToolModule,
    BackgroundModule,
    ChromaticModule,
    GeometryModule,
    MaskModule,
    MaskScopeModule,
)
from .panels.dock_container import PanelContainer
from .panels.fixed_width_separator_guard import FixedWidthSeparatorGuard
from .panels.histogram import HistogramPanel
from .panels.image import ImagePanel
from .panels.image.overlay_style import OverlayStyle
from .panels.layout_presets import wire_view_menu
from .panels.panel_visibility import ensure_floating_panels_on_screen, wire_panel_visibility_menu
from .panels.roi_table import RoiTablePanel
from .panels.sensorgram import SensorgramPanel
from .panels.spectra import SpectraPanel
from .panels.task_indicator import TaskIndicator
from .panels.ui_state import UiStateStore
from .panels.workflow import WorkflowPanel, WorkflowStage
from .panels.workflow.collapsible_section import CollapsibleSection
from .roi import RoiToolbox
from .roi_geometry_sync import RoiGeometrySync
from .selection import AreaSelectionModule, HighlightRangeModule, ReferenceFrameModule, SelectionModule
from .storage.app_settings import AppSettings, load_app_settings, save_app_settings
from .storage.session import SessionState, load_session
from .storage.session_autosave import SessionAutosave
from .storage.session_coordinator import SessionCoordinator
from .undo import undo_manager
from .version_rewrite import rewrite_version_string

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
_DOCK_LAYOUT_STATE_VERSION = 3


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
    4. Every ``CollapsibleSection`` inside the Workflow panel's stage tabs
       (2026-09-24, design doc §4a) - same reason as #2: baked-in
       stylesheets, not QSS.
    """
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
        for dock in window.findChildren(PanelContainer):
            dock.refresh_theme()
        for section in window.findChildren(CollapsibleSection):
            section.refresh_theme()
        apply_windows_titlebar_color(window, theme)
        if on_theme_changed is not None:
            on_theme_changed(name)

    dark_action.triggered.connect(lambda checked: switch_theme(LSPRI_DARK_THEME, "dark") if checked else None)
    bright_action.triggered.connect(lambda checked: switch_theme(LSPRI_BRIGHT_THEME, "bright") if checked else None)


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
        default_reference_diameters=lambda: (
            roi_toolbox.detection_settings().reference_inner_diameter_px,
            roi_toolbox.detection_settings().reference_outer_diameter_px,
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


def _connect_roi_renumbering(
    roi_toolbox: RoiToolbox, selection: SelectionModule, analysis_engine: AnalysisEngine
) -> None:
    """Everything that holds a ROI id follows ``RoiToolbox.roi_ids_renumbered``
    (a delete closing the gap, a fresh detection). Neither module holds a
    toolbox reference of its own, so this is the one place that connects
    them. Kept as a function so a test can wire exactly what the app wires."""
    roi_toolbox.roi_ids_renumbered.connect(selection.remap_roi_ids)
    roi_toolbox.roi_ids_renumbered.connect(analysis_engine.remap_roi_ids)


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
    """Build the debounce, and wire every module's own change signal to
    ``schedule()`` it (2026-09-23).

    Same shape and reasoning as ``_build_analysis_engine`` above - this is
    the one place that knows about every module, so the knowledge of *what
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
            geometry, mask, chromatic, background, roi_toolbox, selection, analysis_settings
        ),
        naming=lambda: _frame_naming(dataset),
    )

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


def _wire_session_coordinator(
    coordinator: SessionCoordinator,
    autosave: SessionAutosave,
    analysis_engine: AnalysisEngine,
    geometry: GeometryModule,
    mask: MaskModule,
    chromatic: ChromaticModule,
    background: BackgroundModule,
    roi_toolbox: RoiToolbox,
    selection: SelectionModule,
    analysis_settings: AnalysisSettingsModule,
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
    Also now repoints `AnalysisEngine.set_storage_root()` (previously wired
    directly to `dataset_loaded` in `build_main_window`), so the engine and
    the session always agree on which folder is active."""

    def restore(root: Path | None) -> None:
        # Flush whatever the *previous* session still had pending first,
        # while its root is still the current one - matches the ordering
        # `restore_for` used before sessions existed, for the same reason
        # (a late-arriving write must land in the session it belongs to,
        # not the new one).
        autosave.set_root(None)
        if root is None:
            analysis_engine.set_storage_root(None)
            return
        analysis_engine.set_storage_root(root)
        try:
            state = load_session(root)
        except Exception:
            # `load_session` raises on a file it cannot read or does not
            # recognise, deliberately (see its docstring) - starting from
            # defaults silently would look exactly like a session that was
            # never set up. Autosave stays off for this root so the app
            # cannot overwrite a recoverable file with blank state.
            logger.exception("Could not read the session for %s - continuing with defaults", root)
            autosave.set_root(root, enabled=False)
            return
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
                roi_toolbox, selection, analysis_settings,
            )
        autosave.set_root(root)

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
    highlight_range: HighlightRangeModule
    area_selection: AreaSelectionModule
    analysis_settings: AnalysisSettingsModule
    session_coordinator: SessionCoordinator
    analysis_engine: AnalysisEngine
    session_autosave: SessionAutosave
    chromatic_auto: ChromaticAutoDetect


class _SettingsWriter:
    """The one write path into `AppSettings`: `persist` writes now, `persist_soon`
    batches. Sliders (overlay opacity) and drags (highlight range) fire many
    times a second; the settings file is written at most once per pause."""

    _PAUSE_MS = 400

    def __init__(self, settings: AppSettings, on_settings_changed: Callable[[AppSettings], None] | None) -> None:
        self.settings = settings
        self._on_settings_changed = on_settings_changed
        self._pending: dict[str, object] = {}
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.setInterval(self._PAUSE_MS)
        self._timer.timeout.connect(lambda: self.flush_pending())

    def persist(self, **changes: object) -> None:
        for key, value in changes.items():
            setattr(self.settings, key, value)
        if self._on_settings_changed is not None:
            self._on_settings_changed(self.settings)

    def persist_soon(self, **changes: object) -> None:
        self._pending.update(changes)
        self._timer.start()

    def flush_pending(self) -> None:
        if self._pending:
            changes = dict(self._pending)
            self._pending.clear()
            self.persist(**changes)


def _build_modules() -> _Modules:
    """Construct every module and connect them to each other (no panels yet)."""
    dataset = DatasetModule()
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
    highlight_range = HighlightRangeModule()
    # Rectangle/lasso area selection limiting the Image panel's editing tools
    # and the Histogram plot (transient, not persisted); lives in displayed
    # pixel space, so it is dropped when rotate/flip/crop changes the grid.
    area_selection = AreaSelectionModule()
    geometry.geometry_changed.connect(lambda _change: area_selection.clear())
    analysis_settings = AnalysisSettingsModule()
    session_coordinator = SessionCoordinator()
    analysis_engine = _build_analysis_engine(
        dataset, geometry, mask, chromatic, background, roi_toolbox, analysis_settings
    )

    # SelectionModule holds no RoiToolbox reference of its own (CLAUDE.md,
    # "no module reaches into another's internals") - this is the one place
    # that connects RoiToolbox's roi_ids_renumbered signal to Selection's
    # remap_roi_ids(), so a delete-driven ROI renumber never leaves a stale
    # selected id behind (see roi/toolbox.py's module docstring, "Cross-
    # module consequence", and selection/module.py).
    _connect_roi_renumbering(roi_toolbox, selection, analysis_engine)

    # Which session-scoped folder the analysis store and the session
    # autosave both point at is `SessionCoordinator`'s job, not a plain
    # `ds.home` read here (2026-09-26 - see CLAUDE.md's "Sessions" section
    # and `storage/session_index.py`). `home`, not `folder`: it is the
    # folder derived data is allowed to be written into, so a session never
    # lands inside a raw TIFF/OME-Zarr folder it doesn't own (see
    # ImageDataset.home).
    session_autosave = _build_session_autosave(
        dataset, geometry, mask, chromatic, background, roi_toolbox, selection, analysis_settings
    )
    _wire_session_coordinator(
        session_coordinator, session_autosave, analysis_engine,
        geometry, mask, chromatic, background, roi_toolbox, selection, analysis_settings,
    )
    dataset.dataset_loaded.connect(lambda ds: session_coordinator.bind_dataset(ds.home))
    dataset.dataset_cleared.connect(session_coordinator.unbind)

    # AnalysisScope.SELECTED_ROIS means "whatever is selected right now".
    # Setting it never triggers computation (sketch §7) - it only decides
    # what the *next* explicitly-requested run covers.
    # sorted(): roi_selection_changed carries a set, whose iteration order is
    # not meaningful - the engine's scope tuple should be stable run to run.
    selection.roi_selection_changed.connect(
        lambda roi_ids: analysis_engine.set_selected_rois(tuple(sorted(roi_ids)))
    )
    return _Modules(
        dataset=dataset, geometry=geometry, active_tool=active_tool, mask=mask, mask_scope=mask_scope,
        chromatic=chromatic, background=background, roi_toolbox=roi_toolbox,
        roi_geometry_sync=roi_geometry_sync, selection=selection, reference_frame=reference_frame,
        highlight_range=highlight_range, area_selection=area_selection, analysis_settings=analysis_settings,
        session_coordinator=session_coordinator, analysis_engine=analysis_engine,
        session_autosave=session_autosave, chromatic_auto=ChromaticAutoDetect(chromatic),
    )


class _ReferenceFramePersistence:
    """A manual reference frame must never silently outlive the dataset it was
    captured against - see `ReferenceFrameModule.reset`'s docstring. The user's
    own choice is remembered per dataset (`ui_state`): put back when the *same*
    dataset is opened again. While this class resets or restores the module it
    ignores the module's own change signal, so a reset is never saved as if the
    user had chosen "Auto"."""

    def __init__(self, dataset: DatasetModule, reference_frame: ReferenceFrameModule, ui_state: UiStateStore,
                 settings: AppSettings) -> None:
        self._reference_frame = reference_frame
        self._ui_state = ui_state
        self._settings = settings
        self._internal_change = False
        # Closures, not bound methods: PyQt holds a bound method of a plain
        # (non-QObject) instance only weakly, and nothing else keeps this
        # object alive - the connections would silently vanish.
        dataset.dataset_loaded.connect(lambda ds: self._on_dataset_loaded(ds))
        dataset.dataset_cleared.connect(lambda: self._reset())
        reference_frame.reference_frame_changed.connect(lambda: self._on_changed())

    def _reset(self) -> None:
        self._internal_change = True
        try:
            self._reference_frame.reset()
        finally:
            self._internal_change = False

    def _on_dataset_loaded(self, ds) -> None:
        self._reset()
        saved = self._ui_state.get("reference_frame")
        if (
            isinstance(saved, dict)
            and saved.get("dataset") == str(ds.home)
            and saved.get("mode") == "manual"
            and saved.get("cube") is not None
            and saved.get("wavelength") is not None
        ):
            self._internal_change = True
            try:
                self._reference_frame.set_manual_frame(int(saved["cube"]), float(saved["wavelength"]))
            finally:
                self._internal_change = False

    def _on_changed(self) -> None:
        if self._internal_change or not self._settings.last_dataset_folder:
            return
        frame = self._reference_frame.manual_frame()
        self._ui_state.set("reference_frame", {
            "dataset": self._settings.last_dataset_folder,
            "mode": self._reference_frame.mode(),
            "cube": frame[0] if frame else None,
            "wavelength": frame[1] if frame else None,
        })


def _wire_highlight_range_persistence(m: _Modules, writer: _SettingsWriter) -> None:
    """Highlight range: remembered per dataset (an intensity range from one
    dataset means nothing on another). `dataset_cleared` wipes the module's
    range on every load, so the saved one is put back when the same dataset
    finishes loading - before the Histogram would seed the full frame range."""
    settings = writer.settings
    highlight_range = m.highlight_range

    def _restore_highlight_range(ds) -> None:
        if (
            settings.highlight_range_dataset == str(ds.home)
            and settings.highlight_range_min is not None
            and settings.highlight_range_max is not None
            and highlight_range.current_range() is None
        ):
            highlight_range.set_range(settings.highlight_range_min, settings.highlight_range_max)

    def _on_highlight_range_changed(range_: object) -> None:
        if range_ is None:  # cleared by a dataset load, not a user choice: keep what was saved
            return
        lo, hi = range_  # type: ignore[misc]
        writer.persist_soon(
            highlight_range_min=float(lo), highlight_range_max=float(hi),
            highlight_range_dataset=settings.last_dataset_folder,
        )

    m.dataset.dataset_loaded.connect(_restore_highlight_range)
    highlight_range.range_changed.connect(_on_highlight_range_changed)


def _overlay_style(visible: bool, color: str | None, alpha: float, default_color: str) -> OverlayStyle:
    return OverlayStyle(visible, color or default_color, alpha)


def _build_image_panel(m: _Modules, writer: _SettingsWriter, theme_obj) -> ImagePanel:
    """The Image panel, seeded from `AppSettings` and reporting every
    user-changeable display option back through `writer`."""
    settings = writer.settings
    # `None` unless all four were actually saved together (first-ever launch,
    # or an older settings file from before this field existed, both leave
    # them at the dataclass default of `None`) - see `ImagePanel.__init__`'s
    # own docstring for why a missing saved range just means "let pyqtgraph's
    # default auto-range fit the first image", not an error.
    saved_view_range = (
        settings.image_view_x_min, settings.image_view_x_max,
        settings.image_view_y_min, settings.image_view_y_max,
    )
    initial_view_range = (
        ((saved_view_range[0], saved_view_range[1]), (saved_view_range[2], saved_view_range[3]))
        if None not in saved_view_range else None
    )
    image_panel = ImagePanel(
        m.dataset, m.geometry, m.mask, m.chromatic, m.background, m.roi_toolbox, m.selection, m.active_tool,
        m.reference_frame, m.highlight_range,
        mask_scope=m.mask_scope,
        area_selection=m.area_selection,
        chromatic_auto=m.chromatic_auto,
        initial_chromatic_view=(settings.chromatic_show_landmarks, settings.chromatic_landmarks_all_wavelengths),
        initial_chromatic_values=ChromaticUiValues(
            landmark_count=settings.chromatic_landmark_count,
            stride=settings.chromatic_stride,
            border_percent=settings.chromatic_border_percent,
            max_step_px=settings.chromatic_max_step_px,
            feature_diameter_px=settings.chromatic_feature_diameter_px,
        ),
        initial_view_range=initial_view_range,
        initial_mask_overlay=_overlay_style(
            settings.mask_overlay_visible, settings.mask_overlay_color, settings.mask_overlay_alpha, theme_obj.mask_color
        ),
        initial_highlight_overlay=_overlay_style(
            settings.highlight_overlay_visible, settings.highlight_overlay_color,
            settings.highlight_overlay_alpha, theme_obj.highlight_color,
        ),
        initial_ribbon_category=settings.image_ribbon_category,
        initial_show_background=settings.show_background,
    )
    image_panel.background_view_changed.connect(lambda shown: writer.persist(show_background=bool(shown)))
    image_panel.ribbon_category_changed.connect(lambda label: writer.persist(image_ribbon_category=label))

    def _on_overlay_style_changed(kind: str, style: OverlayStyle) -> None:
        writer.persist_soon(**{
            f"{kind}_overlay_visible": style.visible,
            f"{kind}_overlay_color": style.color,
            f"{kind}_overlay_alpha": style.alpha,
        })

    image_panel.overlay_style_changed.connect(_on_overlay_style_changed)
    image_panel.chromatic_settings_applied.connect(
        lambda values: writer.persist(
            chromatic_landmark_count=values.landmark_count,
            chromatic_stride=values.stride,
            chromatic_border_percent=values.border_percent,
            chromatic_max_step_px=values.max_step_px,
            chromatic_feature_diameter_px=values.feature_diameter_px,
        )
    )
    image_panel.chromatic_view_changed.connect(
        lambda show, every: writer.persist(
            chromatic_show_landmarks=bool(show), chromatic_landmarks_all_wavelengths=bool(every)
        )
    )
    image_panel.view_range_changed.connect(
        lambda x_min, x_max, y_min, y_max: writer.persist(
            image_view_x_min=x_min, image_view_x_max=x_max, image_view_y_min=y_min, image_view_y_max=y_max,
        )
    )
    return image_panel


def _build_histogram_panel(m: _Modules, image_panel: ImagePanel, writer: _SettingsWriter) -> HistogramPanel:
    settings = writer.settings
    histogram_panel = HistogramPanel(
        image_panel, m.geometry, m.mask, m.chromatic, m.roi_toolbox, m.highlight_range,
        initial_y_mode=settings.histogram_y_mode,
        initial_log_y=settings.histogram_log_y,
        initial_bin_width=settings.histogram_bin_width_px,
        initial_line_width=settings.histogram_line_width_px,
    )
    histogram_panel.display_settings_changed.connect(
        lambda y_mode, log_y, bin_width_px, line_width_px: writer.persist(
            histogram_y_mode=y_mode,
            histogram_log_y=log_y,
            histogram_bin_width_px=float(bin_width_px),
            histogram_line_width_px=float(line_width_px),
        )
    )
    return histogram_panel


def _build_workflow_panel(m: _Modules, writer: _SettingsWriter, ui_state: UiStateStore) -> WorkflowPanel:
    settings = writer.settings
    # `WorkflowStage[...]` raises KeyError/TypeError for anything that isn't
    # a live member name - a settings file from a build with different stage
    # names, hand-edited JSON, or simply no saved value yet (`None`) all fall
    # back the same way: `initial_stage=None`, which makes WorkflowPanel use
    # its own hardcoded default (Dataset open) exactly as before this field
    # existed.
    try:
        initial_stage: WorkflowStage | None = (
            WorkflowStage[settings.active_workflow_stage] if settings.active_workflow_stage else None
        )
    except KeyError:
        initial_stage = None
    # The Workflow panel's "Transforms"/"Mask" subsections were removed
    # (2026-10-02, maintainer request: fully covered by the Image panel's own
    # ribbon now), so `WorkflowPanel` no longer needs the modules that only
    # backed those two - see `panels/workflow/panel.py`'s
    # `_build_image_tools_section` docstring.
    workflow = WorkflowPanel(
        m.dataset,
        m.selection,
        m.reference_frame,
        m.session_coordinator,
        initial_stage=initial_stage,
        initial_subsections=settings.expanded_subsections,
        geometry=m.geometry,
        ui_state=ui_state,
    )
    # Immediate persist-on-change, same pattern as theme/auto_apply - switching
    # the open stage is a deliberate, occasional click, not a continuous drag
    # (unlike window geometry, which is batched to quit instead - see
    # `_wire_quit_persistence`).
    workflow.stage_changed.connect(lambda stage: writer.persist(active_workflow_stage=stage.name))

    def _persist_subsection(key: str, expanded: bool) -> None:
        updated = dict(settings.expanded_subsections)
        updated[key] = expanded
        writer.persist(expanded_subsections=updated)

    workflow.subsection_expanded_changed.connect(_persist_subsection)
    return workflow


def _build_status_bar(
    window: QMainWindow,
    m: _Modules,
    workflow: WorkflowPanel,
    image_panel: ImagePanel,
    roi_table_panel: RoiTablePanel,
) -> None:
    status_bar = QStatusBar(window)
    window.setStatusBar(status_bar)
    # App-wide task indicator (spinner, progress, elapsed/ETA, Cancel). Every
    # heavy task joins it with the same three connects below: its
    # `task_progress` -> report, its `task_finished` -> finish, and the
    # indicator's `cancel_requested` -> that task's own cancel.
    task_indicator = TaskIndicator(status_bar)
    status_bar.addPermanentWidget(task_indicator, 1)  # stretch: takes the free width, text clips instead of overlapping
    m.chromatic_auto.task_progress.connect(task_indicator.report)
    m.chromatic_auto.task_finished.connect(task_indicator.finish)
    cancel_by_task = {CHROMATIC_TASK_ID: m.chromatic_auto.cancel}
    task_indicator.cancel_requested.connect(lambda task_id: cancel_by_task[task_id]())
    # WorkflowPanel.set_status() (state/performance text, no hover-hints -
    # design doc §2) shows as a transient message on the bar's left side;
    # the reminder above is permanent, on the right, so neither covers the
    # other.
    workflow.status_requested.connect(status_bar.showMessage)
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


def _build_dock_layout(
    window: QMainWindow,
    workflow: WorkflowPanel,
    image_panel: ImagePanel,
    histogram_panel: HistogramPanel,
    roi_table_panel: RoiTablePanel,
    spectra_panel: SpectraPanel,
    sensorgram_panel: SensorgramPanel,
) -> dict[str, PanelContainer]:
    """Dock-wrap every panel and build the default arrangement. Returns the
    docks by their panel name (the order is the View menu's Ctrl+1..6 order).

    Each panel is dock-wrapped via the shared PanelContainer (undock/float/
    maximize/close - see panels/dock_container.py), matching how the stable
    app already docks every panel. This default arrangement is just a
    starting point, not a preset: named, user-editable presets (design doc
    §5) are applied on top of it.
    Workflow gets collapsible=True and a real fixed_width (design doc §4):
    unlike the five display panels, it's a tool panel (settings ordered by
    workflow stage), not a data view - not draggable down to a width that
    clips its own controls. 340px matches the stable app's own
    workflow_panel minimum width (layout_builder.py:1744), reused here
    rather than inventing a new number."""
    workflow_dock = PanelContainer("Workflow", workflow, window, collapsible=True, fixed_width=340)
    image_dock = PanelContainer("Image", image_panel, window)
    histogram_dock = PanelContainer("Histogram", histogram_panel, window)
    roi_table_dock = PanelContainer("ROI / Groups", roi_table_panel, window)
    spectra_dock = PanelContainer("Spectra", spectra_panel, window)
    sensorgram_dock = PanelContainer("Sensorgram", sensorgram_panel, window)

    # "Cube X, wl nm" shown centered in the Image dock's own title bar
    # (2026-09-30, maintainer request) rather than in a row under the
    # canvas - see `ImagePanel.frame_status_changed`'s docstring for why
    # render errors are deliberately not routed here too. Seeded once
    # explicitly: `image_panel` already emitted its initial "No dataset
    # loaded." during its own construction, before this connection existed
    # to hear it.
    image_panel.frame_status_changed.connect(image_dock.set_subtitle)
    image_dock.set_subtitle("No dataset loaded.")

    # Workflow is added to LeftDockWidgetArea *alone* - every other panel
    # goes into RightDockWidgetArea/BottomDockWidgetArea instead of being
    # split off from workflow_dock, so Workflow's column is a Qt dock area
    # of its own rather than sharing one with Image/Histogram. That
    # separation is what setCorner (below) needs to give Workflow real full
    # -height without also forcing Image/Histogram/ROI table to reserve
    # full height and pushing Spectra/Sensorgram out from under them.
    #
    # Found and fixed 2026-09-24: an earlier version of this function added
    # both workflow_dock and image_dock to LeftDockWidgetArea, which made Qt
    # stack them vertically in one shared column (Workflow/Image/Histogram
    # on top of each other, each getting only a sliver of the window's
    # height) instead of side by side - confirmed by inspecting real dock
    # geometries headlessly, not just reading the code (see design doc §4).
    window.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, workflow_dock)
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
    # Qt's default BottomLeftCorner ownership belongs to BottomDockWidgetArea,
    # which is what let Spectra/Sensorgram extend under the left column in
    # the first place (confirmed headlessly). Reassigning it to
    # LeftDockWidgetArea makes Workflow's column - and only Workflow's,
    # since nothing else lives in that area - reserve the full window
    # height; Spectra/Sensorgram still extend under Image/Histogram/ROI
    # table exactly as before. Still needed even though Spectra no longer
    # goes through `addDockWidget(Bottom, ...)` above - corner ownership
    # governs how the *outer* Left/Right/Bottom areas relate to each other
    # regardless of how a dock ended up nested within one of them.
    window.setCorner(Qt.Corner.BottomLeftCorner, Qt.DockWidgetArea.LeftDockWidgetArea)

    # Workflow's separator to Image can never actually resize anything
    # (Workflow is fixed_width=340 above) - without this, Qt's own hover
    # highlight/drag still activate there anyway, since QMainWindow::
    # separator has no notion of "this segment is a no-op" (see
    # panels/fixed_width_separator_guard.py for why an event filter, not a
    # painted overlay, is what fixes this). Kept alive on the window
    # itself - installEventFilter does not keep the Python wrapper alive
    # on its own.
    window._workflow_separator_guard = FixedWidthSeparatorGuard(window, workflow_dock, window)
    window.installEventFilter(window._workflow_separator_guard)

    # Order here fixes each panel's View -> Panels shortcut: it matches the
    # stable app's own Ctrl+1..5 assignment (Workflow/Image/Histogram/
    # Spectra/Sensorgram) so existing muscle memory carries over, plus
    # Ctrl+6 for ROI / Groups (unbound in the stable app).
    return {
        "Workflow": workflow_dock,
        "Image": image_dock,
        "Histogram": histogram_dock,
        "Spectra": spectra_dock,
        "Sensorgram": sensorgram_dock,
        "ROI / Groups": roi_table_dock,
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
        writer.flush_pending()
        ui_state.flush()
        # Window geometry/state and layout-preset blobs only make sense
        # to capture here, at quit - unlike theme/last-dataset/auto-apply
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


def _auto_reopen_last_dataset(dataset: DatasetModule, settings: AppSettings) -> None:
    """Auto-reopen the last dataset (2026-09-26, on by default - see the
    "Reopen Last Dataset on Launch" toggle). Safe on a fresh/test
    `AppSettings()` (no `on_settings_changed` given): `last_dataset_folder`
    is `None` until a real settings file has actually recorded one, so
    this is a no-op for every existing zero-argument caller."""
    if not (settings.auto_reopen_last_dataset and settings.last_dataset_folder):
        return
    last_folder = Path(settings.last_dataset_folder)
    if last_folder.is_dir():
        try:
            dataset.load_dataset_from_folder(last_folder)
        except RuntimeError:
            logger.exception("Could not auto-reopen the last dataset at %s", last_folder)
    else:
        logger.info("Last dataset folder %s no longer exists - skipping auto-reopen", last_folder)


def build_main_window(
    initial_settings: AppSettings | None = None,
    on_settings_changed: Callable[[AppSettings], None] | None = None,
) -> QMainWindow:
    """Construct every rewrite module and wire the panels to them, per the
    module boundaries in CLAUDE.md / sketch §7. No module reaches into
    another's internals here - this function only connects the public,
    already-defined constructor seams. It is a sequence of named steps; each
    step lives in its own function above (split from one 630-line function,
    2026-10-07, with no behaviour change).

    **`initial_settings`/`on_settings_changed` (2026-09-26)** - the app-level
    settings layer (`storage/app_settings.py`): last dataset, theme, window
    geometry, layout presets. Injected the same way `AnalysisEngine`/
    `SessionAutosave` take callables instead of reading global state
    directly - `main()` is the only real caller that passes a loaded
    `AppSettings` and a callback that actually writes to disk; every
    existing test that calls `build_main_window()` with no arguments keeps
    doing zero settings-file I/O and no auto-reopen (`AppSettings()`'s
    defaults have `last_dataset_folder=None`, so the auto-reopen guard
    is always a no-op without a real settings file behind it)."""
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

    m = _build_modules()
    _ReferenceFramePersistence(m.dataset, m.reference_frame, ui_state, settings)
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

    image_panel = _build_image_panel(m, writer, theme_obj)
    _wire_highlight_range_persistence(m, writer)
    histogram_panel = _build_histogram_panel(m, image_panel, writer)
    image_panel.restore_ui_state(ui_state)
    histogram_panel.restore_ui_state(ui_state)
    try:
        m.mask_scope.set_scope(MaskScope(ui_state.get("mask/scope")))
    except ValueError:  # nothing saved yet
        pass
    m.mask_scope.scope_changed.connect(lambda scope: ui_state.set("mask/scope", scope.value))
    roi_table_panel = RoiTablePanel(m.roi_toolbox, m.selection, m.geometry, m.analysis_engine)
    roi_table_panel.restore_ui_state(ui_state)
    spectra_panel = SpectraPanel(m.analysis_engine, m.roi_toolbox, m.selection)
    sensorgram_panel = SensorgramPanel(m.analysis_engine, m.roi_toolbox, m.dataset, m.selection)
    workflow = _build_workflow_panel(m, writer, ui_state)

    window = QMainWindow()
    window.setWindowTitle(rewrite_version_string())
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
    view_menu, options_menu = _build_menu_bar(window)
    _wire_theme_menu(
        view_menu, window, image_panel,
        initial_theme=settings.theme,
        on_theme_changed=lambda name: writer.persist(theme=name),
    )
    _build_status_bar(window, m, workflow, image_panel, roi_table_panel)
    panel_docks = _build_dock_layout(
        window, workflow, image_panel, histogram_panel, roi_table_panel, spectra_panel, sensorgram_panel
    )

    # View -> Panels: Show/Hide all + one checkable, Ctrl+1..6-shortcut
    # toggleViewAction() per dock (panels/panel_visibility.py) - the
    # recovery path a closed-and-lost panel had no way back from before
    # this (found 2026-09-27: undocking Workflow, then losing it, had no
    # fix short of restarting).
    wire_panel_visibility_menu(view_menu, panel_docks)

    # Named panel presets (design doc §5). A never-customized preset is
    # still not applied here at startup - every dock stays visible until the
    # user explicitly picks one, or the auto-apply toggle (now itself
    # persisted, see below) reacts to a stage change. Forcing a preset at
    # launch while that toggle happens to be off would contradict "manual
    # application always available, auto-apply is opt-in" (§5). A preset
    # slot that *was* customized and saved does get its real geometry back,
    # but only via the raw `window.restoreState()` call in
    # `_restore_window_state` (the most recent on-screen arrangement,
    # whichever preset produced it) - not by re-`apply()`-ing the preset
    # itself, which would restore that slot's own blob from whenever it was
    # last explicitly saved, possibly older.
    layout_preset_manager = wire_view_menu(
        view_menu,
        options_menu,
        window,
        workflow,
        panel_docks["Workflow"],
        {
            "Image": panel_docks["Image"],
            "Histogram": panel_docks["Histogram"],
            "ROI / Groups": panel_docks["ROI / Groups"],
            "Spectra": panel_docks["Spectra"],
            "Sensorgram": panel_docks["Sensorgram"],
        },
        initial_auto_apply=settings.auto_apply_preset_on_stage_change,
        on_auto_apply_changed=lambda checked: writer.persist(auto_apply_preset_on_stage_change=bool(checked)),
        state_version=_DOCK_LAYOUT_STATE_VERSION,
    )
    _restore_window_state(window, settings, panel_docks, layout_preset_manager)

    # "Reopen Last Dataset on Launch" (2026-09-26) - stands in for a real
    # Preferences dialog exactly like the auto-apply-preset toggle above
    # (neither has one yet).
    reopen_action = options_menu.addAction("Reopen Last Dataset on Launch")
    reopen_action.setCheckable(True)
    reopen_action.setChecked(settings.auto_reopen_last_dataset)
    reopen_action.toggled.connect(lambda checked: writer.persist(auto_reopen_last_dataset=bool(checked)))

    _wire_quit_persistence(window, m, writer, ui_state, layout_preset_manager)
    _auto_reopen_last_dataset(m.dataset, settings)
    apply_windows_titlebar_color(window, theme_obj)
    return window


def main() -> None:
    logging.basicConfig(level=logging.DEBUG)
    app = QApplication(sys.argv)
    app.setApplicationName("LSPR Imaging (Rewrite Preview)")
    app.setApplicationVersion(rewrite_version_string())
    app.setWindowIcon(app_icon())

    initial_settings = load_app_settings()
    window = build_main_window(initial_settings, on_settings_changed=save_app_settings)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
