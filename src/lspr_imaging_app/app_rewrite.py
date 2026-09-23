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
from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow, QVBoxLayout, QWidget

from lspr_ui import app_icon, set_active_theme, GRAY_DARK_THEME

from .analysis import AnalysisEngine, AnalysisSettingsModule
from .analysis.provenance import FrameNamingScheme
from .dataset import DatasetModule
from .gui.app_theme import apply_app_theme
from .image_tools import BackgroundModule, ChromaticModule, GeometryModule, MaskModule
from .panels.histogram import HistogramPanel
from .panels.image import ImagePanel
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


def _not_functional_banner() -> QWidget:
    banner = QLabel(
        "Scaffold preview only - nothing here loads data or computes anything yet.\n"
        "Every panel is real code wired to real modules, but every module method\n"
        "still raises NotImplementedError until it's actually built."
    )
    banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
    banner.setWordWrap(True)
    banner.setStyleSheet("padding: 10px; font-weight: 600;")
    return banner


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
    workflow.addTab(image_panel, "Image")
    workflow.addTab(histogram_panel, "Histogram")
    workflow.addTab(roi_table_panel, "ROI / Groups")
    workflow.addTab(spectra_panel, "Spectra")
    workflow.addTab(sensorgram_panel, "Sensorgram")

    central = QWidget()
    layout = QVBoxLayout(central)
    layout.addWidget(_not_functional_banner())
    layout.addWidget(workflow)

    window = QMainWindow()
    window.setWindowTitle(rewrite_version_string())
    window.setCentralWidget(central)
    window.resize(1100, 720)

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
