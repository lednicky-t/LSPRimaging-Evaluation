"""``AnalysisEngine`` (sketch §7 "Analysis Engine", §10).

Owns the store (§5), the recompute planner (§6), background workers.
Confirmed (2026-09-20, sketch §7): analysis is only ever run by explicit
user action - ``run_analysis(scope)`` is the *only* entry point that
triggers real computation. Selecting/deselecting ROIs or navigating between
panels never implicitly triggers computation.

**Built against injected callables, not live module references.** The engine
is the one component that reads from every other module; taking each read as a
narrow callable (gathered in `app_rewrite._build_analysis_engine`) keeps the full
list of what analysis depends on in one place, keeps it from becoming a god
object holding eleven module references, and lets a test supply plain fakes
with no Qt modules. **All eleven reads are required** (2026-10-07): they used
to default to an "unwired" raiser left over from the scaffold phase, which let
a half-wired engine construct and made every attribute's type `object`.

**The query layer (layers 2-3) was added 2026-09-23** - `formula_spectrum`/
`get_fit`/`get_metric`/`metric_trace` below, over `query.py`'s pure
functions. They re-derive from what is already in memory: a formula, fit or
metric change never invalidates a stored cell, never reads a pixel, and
never triggers a run (sketch §6's last bullet). The fit is memoized per
cell against a settings fingerprint, because a sensorgram asks for one per
cube per ROI and a gaussian fit is ~1 ms - see `request_metric_traces` for
the background path a panel is expected to use. Pillar II (smoothing,
baseline, group aggregation - `statistics.py`) deliberately does **not**
live here: it is cheap, purely visual, and belongs to whichever panel is
displaying (see `docs/analysis_pipeline_layers.md`).

**`get_metric`/`get_spectrum`/`status_summary` are backed by an in-memory
store, `data.h5`-persisted as of 2026-09-22 - not a per-query file read**:
`data.h5` now exists (`store.py`), but every read still goes through the
in-memory `_results`/`InMemoryProvenanceStore`, rehydrated from `data.h5`
once at construction (`read_all_cells`) rather than read per-query - see
`store.py`'s own module docstring for why (HDF5 doesn't support safe
concurrent cross-thread read/write, and this sidesteps that by
construction rather than adding locking). `run_analysis` writes each
computed cell to both the in-memory store and `data.h5` together, so a
restart now genuinely recovers prior results instead of starting empty.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from ..dataset.model import is_dark_frame_wavelength
from ..diagnostics import instrumented
from ..image_tools.background.model import BackgroundSettings
from ..image_tools.geometry.model import GeometrySettings
from ..roi.model import AreaRoi, AreaRoiDetectionSettings
from .planner import AnalysisScope, CurrentInputs, plan_recompute
from .reduction import DEFAULT_TRIMMED_MEAN_FRACTION
from .query import FormulaSpectrum, FitResult, fit_spectrum, formula_spectrum, metric_value
from .settings import MetricSettings
from .provenance import (
    DEFAULT_REFERENCE_EXCLUSION_MODE,
    FrameNamingScheme,
    InMemoryProvenanceStore,
    SettingsSnapshot,
    background_exclusion_digest,
    mask_scope_tag,
    persist_chromatic_snapshot,
    resolve_mask_snapshot_ref,
    roi_geometry_fingerprint_fields,
    sample_exclusion_digest,
)
from .store import read_all_cells, remap_cell_roi_ids, write_cell
from .tasks import (
    DEFAULT_COVERAGE_THRESHOLDS,
    CellResult,
    CoverageThresholds,
    WavelengthComputeInput,
    compute_cell,
)
from .worker import AnalysisWorker

logger = logging.getLogger(__name__)

MaskResolution = tuple[tuple[int, float], np.ndarray, str]  # (authored_frame, mask_array, scope)

_RENUMBER_JOIN_TIMEOUT_SECONDS = 60.0
"""How long `remap_roi_ids` waits for a cancelled run to stop; see there."""


@dataclass(frozen=True)
class AnalysisStatus:
    """What's actually in the store, for the proposed "what's actually in
    the HDF5" indicator (sketch §5's "Restore semantics" reader). Real
    shape, backed by the in-memory store (see module docstring) - will need
    revisiting once `data.h5` exists, since a real file's status shouldn't
    require the whole thing to already be loaded in memory to summarize."""

    computed_cells: int
    total_cells_in_scope: int


class AnalysisEngine(QObject):
    """Owns the analysis store and drives (re)computation. Never reacts to
    a ``ComputationalChange`` signal by launching computation itself - it
    only tracks that inputs are stale *for the next run the user asks for*
    (sketch §7)."""

    analysis_progress = pyqtSignal(float)  # batched/coalesced (§8), not per-cube
    store_updated = pyqtSignal()
    analysis_complete = pyqtSignal()
    metric_traces_ready = pyqtSignal(object)  # dict[int, np.ndarray], see request_metric_traces

    def __init__(
        self,
        *,
        load_plane: Callable[[int, float], np.ndarray],
        rois: Callable[[], tuple[AreaRoi, ...]],
        rois_at: Callable[[int], tuple[AreaRoi, ...]] | None = None,
        has_timeline: Callable[[], bool] = lambda: False,
        cube_indices: Callable[[], tuple[int, ...]],
        wavelengths_for_cube: Callable[[int], tuple[float, ...]],
        geometry_settings: Callable[[], GeometrySettings],
        background_settings: Callable[[], BackgroundSettings],
        chromatic_affine: Callable[[int, float], np.ndarray],
        chromatic_affine_between: Callable[[tuple[int, float], tuple[int, float]], np.ndarray] | None = None,
        resolve_mask: Callable[[int, float], MaskResolution | None],
        reduction_method: Callable[[], str],
        default_reference_diameters: Callable[[], tuple[float, float]],
        detection_settings: Callable[[], AreaRoiDetectionSettings],
        metric_settings: Callable[[], MetricSettings] = MetricSettings,
        reference_exclusion_mode: Callable[[], str] = lambda: DEFAULT_REFERENCE_EXCLUSION_MODE,
        storage_root: Path | None = None,
        trimmed_mean_fraction: float = DEFAULT_TRIMMED_MEAN_FRACTION,
        coverage_thresholds: Callable[[], CoverageThresholds] = lambda: DEFAULT_COVERAGE_THRESHOLDS,
        parent: QObject | None = None,
    ) -> None:
        """Every callable parameter is a narrow read from a real module,
        gathered by whoever constructs this engine (`app.py`, once wired) -
        see module docstring for why this is callables rather than direct
        module references today.

        - ``load_plane(cube_index, wavelength_nm) -> raw image`` -
          ``DatasetModule.load_plane()``.
        - ``wavelengths_for_cube(cube_index)`` -
          ``DatasetModule.wavelengths_for_cube()``. **Not**
          ``DatasetModule.wavelengths()``, which is the union across every
          cube - see that method's own docstring.
        - ``chromatic_affine(cube_index, wavelength_nm) -> 2x3 matrix`` -
          ``ChromaticModule.affine_for()``.
        - ``chromatic_affine_between(from_key, to_key) -> 2x3 matrix`` -
          ``ChromaticModule.affine_between()``. Used only to re-register an
          ignore mask authored at a *different* frame; `compute_cell`
          applies it, in processed space, after its own spatial transform
          (see `tasks.py`'s `_mask_for_compute`). Optional: omitted, masks
          are used as authored, which is correct whenever no chromatic
          model has been fitted.
        - ``resolve_mask(cube_index, wavelength_nm) -> (authored_frame,
          mask_array, scope) | None`` - ``MaskModule.resolve_mask_source()``
          directly, no wrapping. It returns the mask **as authored**; this
          engine never warps it and never calls into Chromatic or Mask
          itself, matching the module boundary rule every other module
          already follows.
        - ``reduction_method`` / ``default_reference_diameters`` /
          ``detection_settings`` - all three read
          ``RoiToolbox.detection_settings()``: the first two pull one field
          each (`reduction_method`, and `reference_inner/outer_diameter_px`
          for ROIs that don't override them), while the third hands the
          whole object to `compute_cell` for `apply_preprocessing`'s
          `mask_settings` (added 2026-09-23 with the background-exclusion
          fix - see `tasks.py`'s module docstring). Three reads of one
          object is redundant and worth collapsing into one the next time
          this constructor is touched; left alone here so the fix didn't
          also reshape a just-tested public surface.

        ``storage_root`` is the folder this analysis writes under - its
        `analysis/` subfolder holds `data.h5` plus the mask/chromatic/
        settings snapshot files. `None` (the default) means "no dataset
        loaded yet", in which case nothing is read or written at all;
        `set_storage_root()` points it at the real dataset once one is
        loaded. See that method for why this can't just be a constructor
        argument in practice.

        **Every callable above is required.** `chromatic_affine_between` is the
        one optional read (omitted, masks are used as authored).

        ``metric_settings`` - ``AnalysisSettingsModule.metric_settings()``,
        the fit/metric half of the query layer (`query.py`). Like
        ``reference_exclusion_mode`` below it gets a real default rather
        than a required argument, because an engine with no settings
        module attached should still answer `get_metric` using the
        documented defaults instead of raising - these settings can never
        make a stored cell wrong, only re-derive it differently. The
        *formula* half is read from ``detection_settings().formula_key``,
        where the old app already keeps it.

        ``reference_exclusion_mode`` is the one exception to that rule -
        it gets a real default rather than a required argument, because
        unlike the reads above it isn't module state that has to be read
        from somewhere: it's a plain analysis setting. See
        `provenance.REFERENCE_EXCLUSION_MODES` for what the modes mean.
        """
        super().__init__(parent)
        self._load_plane = load_plane
        self._rois = rois
        # ROI geometry can differ by cube (docs/roi_timeline_design_2026-10-08.md): `rois_at(cube)` gives every ROI with
        # the geometry valid on that cube. Without it (or while no ROI has a timeline) every cube sees `rois()`.
        self._rois_at = rois_at if rois_at is not None else (lambda _cube: self._rois())
        self._has_timeline = has_timeline
        self._cube_indices = cube_indices
        self._wavelengths_for_cube = wavelengths_for_cube
        self._geometry_settings = geometry_settings
        self._background_settings = background_settings
        self._chromatic_affine = chromatic_affine
        self._chromatic_affine_between = chromatic_affine_between
        self._resolve_mask = resolve_mask
        self._reduction_method = reduction_method
        self._default_reference_diameters = default_reference_diameters
        self._detection_settings = detection_settings
        self._reference_exclusion_mode = reference_exclusion_mode
        self._metric_settings = metric_settings
        self._trimmed_mean_fraction = trimmed_mean_fraction
        self._coverage_thresholds = coverage_thresholds

        self._worker = AnalysisWorker()
        # A second worker, not the one above: `run_analysis` holds that one
        # for the length of a run, and a panel asking for a sensorgram trace
        # must not have to wait for an analysis to finish (or, worse, get a
        # RuntimeError from `submit` because one is in flight).
        self._derived_worker = AnalysisWorker()
        self._metric_cache: dict[tuple[int, int], tuple[tuple, float | None]] = {}
        self._store = InMemoryProvenanceStore()
        self._results: dict[tuple[int, int], CellResult] = {}
        # Bumped whenever ROI ids are renumbered (`remap_roi_ids`). A derived
        # value computed on the other worker thread across a renumber was
        # computed against the old ids and must not be cached or shown.
        self._id_epoch = 0
        self._state_lock = threading.Lock()
        self._selected_roi_ids: tuple[int, ...] = ()
        self._storage_root: Path | None = None
        self.set_storage_root(storage_root)

    # -- where this analysis is stored --------------------------------------

    @property
    def _analysis_dir(self) -> Path:
        """Every snapshot/`data.h5` path is derived from this.

        Falls back to a relative `analysis/` when no storage root is set.
        No real run ever reaches that fallback (`run_analysis` refuses
        without a root, see below); it only keeps the path properties total,
        so an engine with no dataset loaded can still be inspected."""
        if self._storage_root is None:
            return Path("analysis")
        return self._storage_root / "analysis"

    @property
    def _masks_dir(self) -> Path:
        return self._analysis_dir / "masks"

    @property
    def _chromatic_dir(self) -> Path:
        return self._analysis_dir / "chromatic"

    @property
    def _settings_dir(self) -> Path:
        return self._analysis_dir / "settings"

    @property
    def _data_h5_path(self) -> Path:
        return self._analysis_dir / "data.h5"

    def storage_root(self) -> Path | None:
        """The dataset folder this engine reads/writes its analysis under,
        or `None` if no dataset is loaded."""
        return self._storage_root

    def set_storage_root(self, root: Path | None) -> None:
        """Point this engine at a dataset's own folder and rehydrate from
        whatever that folder already has stored, discarding any previous
        dataset's results.

        **Why this isn't simply a constructor argument** (found 2026-09-23,
        while wiring the engine to real modules): the store belongs beside
        the dataset (`ImageDataset.data_root`), but the engine is
        constructed at application start, when no dataset is loaded yet.
        Baking the path in at construction would force either rebuilding
        the engine on every dataset load - tearing down and re-`connect()`-
        ing every panel with it - or writing one dataset's results into
        another dataset's folder. Re-pointing a live engine is the only
        version of this that stays correct across a dataset switch.

        Rehydrates from the new root's `data.h5` if present (sketch §5's
        "Restore semantics": "HDF5 present -> every cell's own provenance is
        already known"). A missing or empty file is the ordinary
        fresh-dataset case, not an error (`store.read_all_cells`'s own
        contract). Emits `store_updated` either way, so panels redraw -
        including the empty case, where the correct redraw is "clear what
        the previous dataset left on screen"."""
        self._storage_root = None if root is None else Path(root)
        self._results = {}
        self._store = InMemoryProvenanceStore()
        # Derived values belong to the dataset they were derived from; a
        # (roi_id, cube_index) key means something different under a
        # different dataset, so keeping them would show one dataset's
        # sensorgram against another's.
        self._metric_cache.clear()
        if self._storage_root is not None:
            for (roi_id, cube_index), result in read_all_cells(self._data_h5_path).items():
                self._results[(roi_id, cube_index)] = result
                self._store.record(roi_id, cube_index, result.provenance)
        self.store_updated.emit()

    # -- following ROI renumbering ------------------------------------------

    def is_running(self) -> bool:
        """Whether an analysis run is in flight. A panel offering a command
        that renumbers ROIs (reorder, delete) should disable it while this is
        true: `remap_roi_ids` would have to cancel the run."""
        return self._worker.is_running()

    @instrumented("AnalysisEngine.remap_roi_ids")
    def remap_roi_ids(self, id_map: dict[int, int]) -> None:
        """Make stored results follow their ROIs when ROI ids are
        renumbered - connected to `RoiToolbox.roi_ids_renumbered`.

        ``id_map`` is ``{old_id: new_id}`` and covers **every ROI that still
        exists**: an old id absent from it is a ROI that is gone (deleted, or
        replaced by a fresh detection), and its results are dropped. An empty
        map therefore means "no old ROI survives".

        Results are filed under the ROI's id (in memory and as
        ``/cells/roi_<id>`` in ``data.h5``). Without this, after deleting
        ROI 3 the survivor that became ROI 3 would be shown the deleted ROI's
        spectrum. A result's validity check ignores the id (see
        `roi_geometry_fingerprint_fields`), so a result that follows its ROI
        stays valid and nothing recomputes.

        **A run in flight is cancelled and waited for first**, because its
        worker writes cells under the old ids. A cancelled run stops between
        cells, so the wait is short. If it has not stopped after
        `_RENUMBER_JOIN_TIMEOUT_SECONDS`, every result held in memory is
        dropped instead of risking wrong attribution (the file is left alone,
        and the error is logged). Callers should avoid renumbering during a
        run (`is_running`); this is the backstop.

        Emits `store_updated` so panels redraw and ask again; a
        `request_metric_traces` still running across the renumber discards its
        answer rather than emitting it under the old numbering."""
        id_map = {int(old): int(new) for old, new in id_map.items()}
        if len(set(id_map.values())) != len(id_map):
            logger.error("remap_roi_ids: two ROI ids map to one target (%s); dropping every stored result", id_map)
            id_map = {}
        touch_file = True
        if self._worker.is_running():
            logger.warning("ROI ids were renumbered during an analysis run; cancelling the run")
            self._worker.cancel()
            if not self._worker.join(_RENUMBER_JOIN_TIMEOUT_SECONDS):
                logger.error(
                    "Analysis run did not stop within %.0f s after a ROI renumber; dropping the in-memory results "
                    "rather than showing them under the wrong ROI (data.h5 was not touched)",
                    _RENUMBER_JOIN_TIMEOUT_SECONDS,
                )
                id_map, touch_file = {}, False
        if not any(old not in id_map or id_map[old] != old for old, _cube in self._results):
            return  # nothing stored is affected
        with self._state_lock:
            self._id_epoch += 1
            self._results = {
                (id_map[roi_id], cube_index): result
                for (roi_id, cube_index), result in self._results.items()
                if roi_id in id_map
            }
            self._store.remap_roi_ids(id_map)
            self._metric_cache = {
                (id_map[roi_id], cube_index): cached
                for (roi_id, cube_index), cached in self._metric_cache.items()
                if roi_id in id_map
            }
        try:
            if touch_file and self._storage_root is not None:
                remap_cell_roi_ids(self._data_h5_path, id_map)
        finally:
            self.store_updated.emit()

    # -- query interface ------------------------------------------------

    def get_spectrum(self, roi_id: int, cube_index: int) -> CellResult | None:
        """Returns the stored `CellResult` (wavelengths + raw sample/
        reference pairs + provenance), or `None` if not yet analyzed."""
        return self._results.get((roi_id, cube_index))

    # -- the query layer: layers 2-3, derived, never stored ------------------
    #
    # Everything below re-derives from what `get_spectrum` already holds.
    # None of it can invalidate a stored cell, none of it reads a pixel, and
    # none of it may trigger a run - a cell nobody has analyzed answers
    # `None`, and a panel shows "needs analysis" rather than quietly
    # computing it (sketch §7).

    def _query_fingerprint(self) -> tuple:
        """Every setting that changes a derived value, as one comparable
        tuple - the cache key's other half.

        Comparing this on read is why nothing has to *subscribe* to a
        settings change to stay correct: a changed setting simply misses
        the cache. A subscription would be a second thing to keep in step
        with the settings that actually matter, and forgetting to update it
        would show a stale plot with no error."""
        metric = self._metric_settings()
        return (
            str(self._detection_settings().formula_key),
            str(metric.fit_method), int(metric.poly_order),
            metric.fit_wl_min, metric.fit_wl_max, str(metric.metric_key),
        )

    def formula_spectrum(self, roi_id: int, cube_index: int) -> FormulaSpectrum | None:
        """Layer 2: this ROI's own spectrum under the current formula, or
        `None` if the cell isn't analyzed.

        Not cached: it is one vectorized expression over arrays already in
        memory, so caching it would cost more in bookkeeping than it
        saves - unlike the fit below."""
        result = self._results.get((roi_id, cube_index))
        if result is None:
            return None
        return formula_spectrum(
            result.wavelengths_nm, result.sample_values, result.reference_values,
            self._detection_settings().formula_key,
        )

    def get_fit(self, roi_id: int, cube_index: int) -> FitResult | None:
        """The fitted curve for one cell, for the Spectra panel to draw
        over the measured points. `None` when the cell isn't analyzed *or*
        when the fit method is `"none"` - a panel wanting to tell those
        apart checks `formula_spectrum` for the first."""
        spectrum = self.formula_spectrum(roi_id, cube_index)
        if spectrum is None:
            return None
        metric = self._metric_settings()
        return fit_spectrum(
            spectrum, metric.fit_method, poly_order=metric.poly_order,
            wl_min=metric.fit_wl_min, wl_max=metric.fit_wl_max,
        )

    def get_metric(self, roi_id: int, cube_index: int) -> float | None:
        """Layer 3: one number per (ROI, cube) - the metric *wavelength*,
        which is what a sensorgram plots, since a shifting peak is the
        measurement. `None` if the cell isn't analyzed or the fit didn't
        converge.

        Memoized per cell against `_query_fingerprint`, because this is the
        expensive one: a gaussian `curve_fit` is ~1 ms, and a sensorgram
        asks for one per cube per ROI."""
        cached = self._metric_cache.get((roi_id, cube_index))
        fingerprint = self._query_fingerprint()
        if cached is not None and cached[0] == fingerprint:
            return cached[1]

        epoch = self._id_epoch
        spectrum = self.formula_spectrum(roi_id, cube_index)
        if spectrum is None:
            return None
        metric = self._metric_settings()
        wavelength, _value = metric_value(
            spectrum, metric.fit_method, metric.metric_key,
            poly_order=metric.poly_order, wl_min=metric.fit_wl_min, wl_max=metric.fit_wl_max,
        )
        # Not cached if ROI ids were renumbered meanwhile (this can run on the
        # derived worker thread): the value belongs to the old numbering.
        with self._state_lock:
            if epoch == self._id_epoch:
                self._metric_cache[(roi_id, cube_index)] = (fingerprint, wavelength)
        return wavelength

    def metric_trace(self, roi_id: int, cube_indices: tuple[int, ...] | None = None) -> np.ndarray:
        """One ROI's sensorgram trace: its metric per cube, in cube order,
        `NaN` where nothing is stored.

        **NaN rather than a short array**: every ROI's trace has to stay
        aligned to the same x axis for `statistics.aggregate_traces` to
        stack them, and a missing cube is a real, ordinary state (that cell
        has not been analyzed, or its fit didn't converge) - dropping the
        point would silently shift every later point left.

        Synchronous, and safe to call from the GUI thread only for a trace
        that is already cached. Use `request_metric_traces` otherwise."""
        cubes = self._cube_indices() if cube_indices is None else cube_indices
        return np.asarray(
            [
                value if (value := self.get_metric(roi_id, cube_index)) is not None else np.nan
                for cube_index in cubes
            ],
            dtype=np.float64,
        )

    def request_metric_traces(self, roi_ids: tuple[int, ...]) -> None:
        """Compute `metric_trace` for each of `roi_ids` off the GUI thread,
        then emit `metric_traces_ready` with `{roi_id: trace}`.

        This is the entry point a panel uses. CLAUDE.md forbids fitting on
        the GUI thread, and the arithmetic backs that up: 160 ROIs x 300
        cubes is 48,000 fits, ~48 s with a gaussian - a freeze, not a
        stutter. Once warm the cache answers instantly, so the cost is paid
        once per settings change rather than once per redraw.

        Silently does nothing if a previous request is still running: this
        is a display query, so the newest answer is the only one that
        matters and the in-flight one is about to produce it anyway. Unlike
        `run_analysis`, dropping a request here loses nothing - nothing is
        stored from it."""
        if self._derived_worker.is_running():
            return

        def run() -> None:
            epoch = self._id_epoch
            traces = {roi_id: self.metric_trace(roi_id) for roi_id in roi_ids}
            if epoch != self._id_epoch:
                return  # ROI ids were renumbered meanwhile; `store_updated` already told panels to ask again
            self.metric_traces_ready.emit(traces)

        self._derived_worker.submit(run)

    def status_summary(self) -> AnalysisStatus:
        all_cells = tuple(
            (roi.area_roi_id, cube_index)
            for roi in self._rois()
            for cube_index in self._cube_indices()
        )
        return AnalysisStatus(
            computed_cells=sum(1 for cell in all_cells if cell in self._results),
            total_cells_in_scope=len(all_cells),
        )

    def set_selected_rois(self, roi_ids: tuple[int, ...]) -> None:
        """What `AnalysisScope.SELECTED_ROIS` means for the next
        `run_analysis`/`preview_recompute` call - set by whichever panel
        owns ROI selection (not built yet). Never triggers computation
        itself (sketch §7: selection changes must never implicitly
        recompute anything)."""
        self._selected_roi_ids = tuple(roi_ids)

    # -- planning (no computation, no persistence) ---------------------------

    def preview_recompute(self, scope: AnalysisScope = AnalysisScope.ALL_ROIS):
        """Returns the `RecomputePlan` `run_analysis` would act on, without
        computing or persisting anything - lets a panel show "N cells will
        be recomputed" before the user commits (useful specifically
        because a chromatic refit or background change invalidates every
        cell at once). See `planner.py`'s module docstring for the one
        known imperfection: this can still write a small settings-snapshot
        JSON as a side effect of comparing fingerprints - never a mask/
        background/chromatic file, only that small reference JSON."""
        return plan_recompute(
            self._store,
            self._gather_current_inputs(),
            scope,
            all_roi_ids=tuple(roi.area_roi_id for roi in self._rois()),
            selected_roi_ids=self._selected_roi_ids,
        )

    def _gather_current_inputs(self) -> CurrentInputs:
        geometry = self._geometry_settings()
        background = self._background_settings()
        reduction_method = self._reduction_method()
        naming = self._naming()
        exclusion_mode = self._reference_exclusion_mode()
        coverage_thresholds = self._coverage_thresholds()
        all_rois = self._rois()
        timeline = bool(self._has_timeline())
        # Must match what compute_cell records, or every cell would look
        # stale the moment it's compared against its own stored fingerprint.
        # With a geometry timeline the digests differ by cube (computed per cube below).
        exclusion_digest = (
            sample_exclusion_digest(all_rois)
            if exclusion_mode == "exclude_all_sample_rois" and all_rois
            else None
        )
        # Same requirement for the background estimate's own ROI/mask
        # exclusion (2026-09-23). Both digests are pure geometry and
        # settings - no pixel is read to build them, which is what keeps
        # `preview_recompute` cheap enough to answer "how many cells will
        # recompute" without estimating a background for every frame in the
        # dataset (the design trap recorded in the same build log entry).
        background_digest = background_exclusion_digest(
            all_rois, background, self._detection_settings()
        )
        cube_settings: dict[int, dict[float, SettingsSnapshot]] = {}
        cube_rois: dict[int, tuple[AreaRoi, ...]] = {}
        for cube_index in self._cube_indices():
            wavelength_settings: dict[float, SettingsSnapshot] = {}
            if timeline:
                cube_rois[cube_index] = self._rois_at(cube_index)
                exclusion_digest = (
                    sample_exclusion_digest(cube_rois[cube_index])
                    if exclusion_mode == "exclude_all_sample_rois" and cube_rois[cube_index]
                    else None
                )
                background_digest = background_exclusion_digest(
                    cube_rois[cube_index], background, self._detection_settings()
                )
            for wavelength_nm in self._wavelengths_for_cube(cube_index):
                # The dark/background frame (0.0 nm, when present) is real
                # data but not a spectral sample point - excluded here, and
                # identically in `_gather_wavelength_inputs` below, so the
                # planning-time fingerprint and the compute-time snapshot
                # dict this feeds always agree on which wavelengths exist.
                # See `dataset.model.is_dark_frame_wavelength`.
                if is_dark_frame_wavelength(wavelength_nm):
                    continue
                mask_resolution = self._resolve_mask(cube_index, wavelength_nm)
                mask_ref = None
                if mask_resolution is not None:
                    (authored_cube, authored_wl), mask_array, scope = mask_resolution
                    # Planning still writes no mask file (the design doc's
                    # "written lazily, only when actually analyzed" rule) -
                    # but it must resolve the *real* version, not a
                    # placeholder, or the fingerprint it builds can never
                    # match the one compute_cell records. See
                    # resolve_mask_snapshot_ref's docstring for the bug this
                    # replaced. The same as-authored mask compute_cell
                    # persists, so both sides hash identical content.
                    mask_8bit = np.asarray(mask_array, dtype=bool).astype(np.uint8) * 255
                    mask_ref, _is_new = resolve_mask_snapshot_ref(
                        self._masks_dir, mask_8bit,
                        cube_index=authored_cube, wavelength_nm=authored_wl,
                        tag=mask_scope_tag(scope), naming=naming,
                    )
                chromatic_ref = persist_chromatic_snapshot(
                    self._chromatic_dir, self._chromatic_affine(cube_index, wavelength_nm),
                    cube_index=cube_index, wavelength_nm=wavelength_nm,
                    naming=naming,
                )
                wavelength_settings[wavelength_nm] = SettingsSnapshot(
                    geometry=asdict(geometry), mask=mask_ref, chromatic=chromatic_ref,
                    background=asdict(background), reduction_method=reduction_method,
                    reference_exclusion_mode=exclusion_mode, sample_exclusion=exclusion_digest,
                    background_exclusion=background_digest,
                    coverage_thresholds=coverage_thresholds.as_dict(),
                )
            cube_settings[cube_index] = wavelength_settings
        if timeline:
            roi_geometries = {
                (roi.area_roi_id, cube_index): roi_geometry_fingerprint_fields(roi)
                for cube_index, rois_here in cube_rois.items()
                for roi in rois_here
            }
        else:
            roi_geometries = {roi.area_roi_id: roi_geometry_fingerprint_fields(roi) for roi in all_rois}
        return CurrentInputs(
            reduction_method=reduction_method, cube_settings=cube_settings,
            roi_geometries=roi_geometries, settings_dir=self._settings_dir,
        )

    def _naming(self) -> FrameNamingScheme:
        cube_indices = list(self._cube_indices())
        wavelengths: list[float] = []
        for cube_index in cube_indices:
            # Excluded for the same reason as `_gather_current_inputs`: the
            # dark frame never gets a mask/chromatic snapshot written
            # through this pipeline (post-exclusion, nothing analyzes it),
            # so it shouldn't influence the filename precision derived here.
            wavelengths.extend(
                wl for wl in self._wavelengths_for_cube(cube_index) if not is_dark_frame_wavelength(wl)
            )
        return FrameNamingScheme.for_dataset(cube_indices, wavelengths)

    # -- the only entry point that triggers computation ---------------------

    @instrumented("AnalysisEngine.run_analysis")
    def run_analysis(self, scope: AnalysisScope = AnalysisScope.ALL_ROIS) -> None:
        """Plan and dispatch recompute for ``scope``. The only method in
        this module (or anywhere else) allowed to trigger real computation
        (CLAUDE.md, "What NOT to do without checking in again first").

        Dispatches via `AnalysisWorker` (a real background thread, never
        `QThreadPool` - CLAUDE.md's zarr rule), so this returns immediately;
        results arrive via `store_updated`/`analysis_complete`, emitted
        from the worker thread (safe - Qt queues a cross-thread `.emit()`
        automatically, see `worker.py`'s module docstring). Progress is
        emitted once per completed cell, not batched into a timer yet
        (sketch §8's redraw-pacing idea applies to *display panels*
        consuming this signal, not to this engine emitting it - a panel
        should coalesce on its own end).

        Raises `RuntimeError` if no storage root has been set - running
        would otherwise write `data.h5` and every snapshot file into
        whatever the process's working directory happens to be, which is
        silently wrong rather than obviously wrong (the results would
        compute fine and simply never be found again)."""
        if self._storage_root is None:
            raise RuntimeError("No storage root is set - load a dataset before running analysis.")
        rois_by_id = {roi.area_roi_id: roi for roi in self._rois()}
        current_inputs = self._gather_current_inputs()
        plan = plan_recompute(
            self._store, current_inputs, scope,
            all_roi_ids=tuple(rois_by_id.keys()), selected_roi_ids=self._selected_roi_ids,
        )
        naming = self._naming()
        reduction_method = current_inputs.reduction_method
        default_inner, default_outer = self._default_reference_diameters()
        cancel_event = self._worker.cancel_event
        exclusion_mode = self._reference_exclusion_mode()
        coverage_thresholds = self._coverage_thresholds()
        all_rois = tuple(rois_by_id.values())
        # The ROIs as they are on each cube of the plan (they differ only when a geometry timeline exists). Resolved
        # here, on the GUI thread, never from the worker.
        rois_by_cube: dict[int, tuple[dict[int, AreaRoi], tuple[AreaRoi, ...]]] = {}
        if self._has_timeline():
            for _roi_id, cube_index in plan.to_recompute:
                if cube_index not in rois_by_cube:
                    at_cube = self._rois_at(cube_index)
                    rois_by_cube[cube_index] = ({r.area_roi_id: r for r in at_cube}, tuple(at_cube))
        # Read once here, on the GUI thread, not per cell from the worker:
        # the modules are only ever touched from the GUI thread (the same
        # convention `panels/image/render.py`'s RenderRequest follows), and
        # a run must in any case compute every cell against one consistent
        # set of settings rather than picking up an edit halfway through.
        detection = self._detection_settings()
        # One cache for this whole run: the sample-exclusion union is
        # identical for every cell at a given (cube, wavelength), so this
        # turns O(ROIs x cells) rasterizations into O(ROIs). Scoped to the
        # run rather than held on self so it can't go stale against a later
        # ROI edit - a new run builds a fresh one.
        sample_exclusion_cache: dict[tuple[int, float], np.ndarray] = {}

        def run() -> None:
            # `finally`, so a failure partway through still tells whoever is
            # waiting that the run is over (2026-09-23). Without it, a
            # raising cell left `analysis_complete` unemitted forever and a
            # panel showing "analyzing..." with no way back - and in a
            # packaged build the traceback went to a stderr nobody reads.
            # `AnalysisWorker` now logs it; this half makes sure the UI
            # recovers. Cells already computed stay in the store: each is
            # written whole, so a partial run is valid, just incomplete.
            try:
                total = len(plan.to_recompute)
                for completed, (roi_id, cube_index) in enumerate(plan.to_recompute, start=1):
                    if cancel_event.is_set():
                        break
                    by_id_here, all_rois_here = rois_by_cube.get(cube_index, (rois_by_id, all_rois))
                    roi = by_id_here.get(roi_id)
                    if roi is None:
                        continue
                    wavelength_inputs = self._gather_wavelength_inputs(cube_index)
                    result = compute_cell(
                        roi, cube_index, wavelength_inputs,
                        reduction_method=reduction_method, trimmed_mean_fraction=self._trimmed_mean_fraction,
                        default_reference_inner_diameter_px=default_inner, default_reference_outer_diameter_px=default_outer,
                        masks_dir=self._masks_dir, chromatic_dir=self._chromatic_dir, settings_dir=self._settings_dir,
                        naming=naming, all_rois=all_rois_here, detection_settings=detection,
                        reference_exclusion_mode=exclusion_mode,
                        sample_exclusion_cache=sample_exclusion_cache, cancel_event=cancel_event,
                        coverage_thresholds=coverage_thresholds,
                    )
                    if result is not None:
                        self._results[(roi_id, cube_index)] = result
                        self._store.record(roi_id, cube_index, result.provenance)
                        write_cell(self._data_h5_path, roi_id, cube_index, result)
                        # The settings fingerprint hasn't changed, but the
                        # numbers underneath it have - the one invalidation
                        # case `_query_fingerprint` cannot detect on its own.
                        # A single-key pop, never an iteration: the derived
                        # worker may be reading this dict from its own thread
                        # (see request_metric_traces), and under the GIL a
                        # keyed get/set/pop is safe where iterating while
                        # another thread mutates is not.
                        self._metric_cache.pop((roi_id, cube_index), None)
                        self.store_updated.emit()
                    self.analysis_progress.emit(completed / max(total, 1))
            finally:
                self.analysis_complete.emit()

        self._worker.submit(run)

    def _gather_wavelength_inputs(self, cube_index: int) -> dict[float, WavelengthComputeInput]:
        """Per-wavelength compute inputs for `compute_cell`, one cube's
        worth. Excludes the dark/background frame (0.0 nm, when present):
        it is real, ordinarily-acquired data, but not a spectral sample
        point, so it must never become an entry in a stored `CellResult` -
        which `formula_spectrum`/`fit_spectrum`/`metric_from_spectrum`
        (`query.py`) would then treat as a genuine point in the spectrum,
        eligible to be fit or picked as a "maximum"/"centroid" metric. See
        `dataset.model.is_dark_frame_wavelength` and
        `DatasetModule.wavelengths_for_cube`'s docstring for the domain fact
        this enforces."""
        geometry = self._geometry_settings()
        background = self._background_settings()
        inputs: dict[float, WavelengthComputeInput] = {}
        for wavelength_nm in self._wavelengths_for_cube(cube_index):
            if is_dark_frame_wavelength(wavelength_nm):
                continue
            mask_resolution = self._resolve_mask(cube_index, wavelength_nm)
            resolved_mask = None
            mask_authored_frame = None
            mask_scope = None
            mask_warp_affine = None
            if mask_resolution is not None:
                mask_authored_frame, resolved_mask, mask_scope = mask_resolution
                frame = (int(cube_index), float(wavelength_nm))
                # Only when the mask came from a different frame than the one
                # being computed - `affine_between` would return identity
                # anyway, but skipping it also skips a pointless warp of a
                # full-image mask per wavelength.
                if self._chromatic_affine_between is not None and mask_authored_frame != frame:
                    mask_warp_affine = self._chromatic_affine_between(mask_authored_frame, frame)
            inputs[wavelength_nm] = WavelengthComputeInput(
                wavelength_nm=wavelength_nm,
                raw_image=self._load_plane(cube_index, wavelength_nm),
                geometry_settings=geometry, background_settings=background,
                chromatic_affine=self._chromatic_affine(cube_index, wavelength_nm),
                resolved_mask=resolved_mask, mask_authored_frame=mask_authored_frame, mask_scope=mask_scope,
                mask_warp_affine=mask_warp_affine,
            )
        return inputs
