"""``AnalysisEngine`` (sketch §7 "Analysis Engine", §10).

Owns the store (§5), the recompute planner (§6), background workers.
Confirmed (2026-09-20, sketch §7): analysis is only ever run by explicit
user action - ``run_analysis(scope)`` is the *only* entry point that
triggers real computation. Selecting/deselecting ROIs or navigating between
panels never implicitly triggers computation.

**Built against injected callables, not live module references (2026-09-22)
- a deliberate, flagged gap, not a design preference**: gathering real
per-wavelength compute input needs pixel data, but `DatasetModule`
(`dataset/module.py`) only exposes 4 narrow query methods - none of them
actually load pixels (`dataset_load_plane` needs the full `ImageDataset`,
which `DatasetModule` deliberately never exposes, per its own "no other
module may read dataset state any other way" rule). `DatasetModule` needs a
5th method (e.g. `load_plane(cube_index, wavelength_nm) -> np.ndarray`)
before this engine can be wired to the real module - not guessed at here.
Building against injected callables instead keeps this file's own
orchestration logic real, complete, and unit-testable today (with fake
callables standing in for the not-yet-wired real modules) rather than
leaving the whole class NotImplementedError pending that one method. Once
`DatasetModule.load_plane` (and equivalent real accessors for the other
callables) exist, wiring this up is a small change at construction time -
nothing about the logic below needs to change.

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

from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from ..diagnostics import instrumented
from ..image_tools.background.model import BackgroundSettings
from ..image_tools.geometry.model import GeometrySettings
from ..roi.model import AreaRoi
from .planner import AnalysisScope, CurrentInputs, plan_recompute
from .reduction import DEFAULT_TRIMMED_MEAN_FRACTION
from .provenance import (
    DEFAULT_REFERENCE_EXCLUSION_MODE,
    FrameNamingScheme,
    InMemoryProvenanceStore,
    SettingsSnapshot,
    mask_scope_tag,
    persist_chromatic_snapshot,
    resolve_mask_snapshot_ref,
    roi_geometry_fingerprint_fields,
    sample_exclusion_digest,
)
from .store import read_all_cells, write_cell
from .tasks import CellResult, WavelengthComputeInput, compute_cell
from .worker import AnalysisWorker

MaskResolution = tuple[tuple[int, float], np.ndarray, str]  # (authored_frame, mask_array, scope)


def _unwired(name: str) -> Callable[..., object]:
    """A placeholder for a constructor callable that wasn't supplied -
    raises only if actually *called*, matching every other module's
    "constructs fine, action methods raise" scaffold contract (see
    `__init__`'s own docstring)."""

    def _raise(*_args: object, **_kwargs: object) -> object:
        raise NotImplementedError(f"AnalysisEngine.{name} was not supplied at construction")

    return _raise


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

    def __init__(
        self,
        *,
        load_plane: Callable[[int, float], np.ndarray] | None = None,
        rois: Callable[[], tuple[AreaRoi, ...]] | None = None,
        cube_indices: Callable[[], tuple[int, ...]] | None = None,
        wavelengths_for_cube: Callable[[int], tuple[float, ...]] | None = None,
        geometry_settings: Callable[[], GeometrySettings] | None = None,
        background_settings: Callable[[], BackgroundSettings] | None = None,
        chromatic_affine: Callable[[int, float], np.ndarray] | None = None,
        chromatic_affine_between: Callable[[tuple[int, float], tuple[int, float]], np.ndarray] | None = None,
        resolve_mask: Callable[[int, float], MaskResolution | None] | None = None,
        reduction_method: Callable[[], str] | None = None,
        default_reference_radii: Callable[[], tuple[float, float]] | None = None,
        reference_exclusion_mode: Callable[[], str] = lambda: DEFAULT_REFERENCE_EXCLUSION_MODE,
        storage_root: Path | None = None,
        trimmed_mean_fraction: float = DEFAULT_TRIMMED_MEAN_FRACTION,
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
        - ``reduction_method`` / ``default_reference_radii`` - both read
          ``RoiToolbox.detection_settings()`` (`reduction_method`, and
          `reference_inner/outer_radius_px` for ROIs that don't override
          them).

        ``storage_root`` is the folder this analysis writes under - its
        `analysis/` subfolder holds `data.h5` plus the mask/chromatic/
        settings snapshot files. `None` (the default) means "no dataset
        loaded yet", in which case nothing is read or written at all;
        `set_storage_root()` points it at the real dataset once one is
        loaded. See that method for why this can't just be a constructor
        argument in practice.

        **Every callable defaults to `None`, in which case calling it
        raises `NotImplementedError`** - matches this scaffold's
        established contract (every other module in `app_rewrite.py`
        constructs without error; only their *action* methods raise until
        actually wired). `AnalysisEngine()` with no arguments constructs
        fine; `run_analysis()`/`preview_recompute()` raise until real
        callables are supplied.

        ``reference_exclusion_mode`` is the one exception to that rule -
        it gets a real default rather than an `_unwired` raiser, because
        unlike the others it isn't module state that has to be read from
        somewhere: it's a plain analysis setting. See
        `provenance.REFERENCE_EXCLUSION_MODES` for what the modes mean.
        """
        super().__init__(parent)
        self._load_plane = load_plane or _unwired("load_plane")
        self._rois = rois or _unwired("rois")
        self._cube_indices = cube_indices or _unwired("cube_indices")
        self._wavelengths_for_cube = wavelengths_for_cube or _unwired("wavelengths_for_cube")
        self._geometry_settings = geometry_settings or _unwired("geometry_settings")
        self._background_settings = background_settings or _unwired("background_settings")
        self._chromatic_affine = chromatic_affine or _unwired("chromatic_affine")
        self._chromatic_affine_between = chromatic_affine_between
        self._resolve_mask = resolve_mask or _unwired("resolve_mask")
        self._reduction_method = reduction_method or _unwired("reduction_method")
        self._default_reference_radii = default_reference_radii or _unwired("default_reference_radii")
        self._reference_exclusion_mode = reference_exclusion_mode
        self._trimmed_mean_fraction = trimmed_mean_fraction

        self._worker = AnalysisWorker()
        self._store = InMemoryProvenanceStore()
        self._results: dict[tuple[int, int], CellResult] = {}
        self._selected_roi_ids: tuple[int, ...] = ()
        self._storage_root: Path | None = None
        self.set_storage_root(storage_root)

    # -- where this analysis is stored --------------------------------------

    @property
    def _analysis_dir(self) -> Path:
        """Every snapshot/`data.h5` path is derived from this.

        Falls back to a relative `analysis/` when no storage root is set.
        No real run ever reaches that fallback (`run_analysis` refuses
        without a root, see below), but keeping the path properties total
        means a scaffold-only `AnalysisEngine()` still constructs and can be
        inspected without raising - the contract every other module in
        `app_rewrite.py` follows."""
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
        if self._storage_root is not None:
            for (roi_id, cube_index), result in read_all_cells(self._data_h5_path).items():
                self._results[(roi_id, cube_index)] = result
                self._store.record(roi_id, cube_index, result.provenance)
        self.store_updated.emit()

    # -- query interface ------------------------------------------------

    def get_metric(self, roi_id: int, cube_index: int) -> tuple[float, ...] | None:
        """Raw reduced (sample, reference) values are what's stored - see
        `tasks.py`'s module docstring for why formula math isn't baked in
        here (a query-time concern, not yet built - there is no `Formula`/
        `Fit`/`Metric` layer above this yet, so this currently returns the
        stored per-wavelength *pairs*, not a single derived scalar, which
        is a real gap against the sketch's `get_metric() -> float | None`
        signature, flagged rather than faked with a wrong number).
        Returns ``None`` if not yet analyzed - never computes on read
        (sketch §7)."""
        result = self._results.get((roi_id, cube_index))
        if result is None:
            return None
        return result.sample_values + result.reference_values

    def get_spectrum(self, roi_id: int, cube_index: int) -> CellResult | None:
        """Returns the stored `CellResult` (wavelengths + raw sample/
        reference pairs + provenance), or `None` if not yet analyzed."""
        return self._results.get((roi_id, cube_index))

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
        all_rois = self._rois()
        # Must match what compute_cell records, or every cell would look
        # stale the moment it's compared against its own stored fingerprint.
        exclusion_digest = (
            sample_exclusion_digest(all_rois)
            if exclusion_mode == "exclude_all_sample_rois" and all_rois
            else None
        )
        cube_settings: dict[int, dict[float, SettingsSnapshot]] = {}
        for cube_index in self._cube_indices():
            wavelength_settings: dict[float, SettingsSnapshot] = {}
            for wavelength_nm in self._wavelengths_for_cube(cube_index):
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
                )
            cube_settings[cube_index] = wavelength_settings
        roi_geometries = {roi.area_roi_id: roi_geometry_fingerprint_fields(roi) for roi in all_rois}
        return CurrentInputs(
            reduction_method=reduction_method, cube_settings=cube_settings,
            roi_geometries=roi_geometries, settings_dir=self._settings_dir,
        )

    def _naming(self) -> FrameNamingScheme:
        cube_indices = list(self._cube_indices())
        wavelengths: list[float] = []
        for cube_index in cube_indices:
            wavelengths.extend(self._wavelengths_for_cube(cube_index))
        return FrameNamingScheme.for_dataset(cube_indices, wavelengths)

    # -- the only entry point that triggers computation ---------------------

    @instrumented("AnalysisEngine.run_analysis")
    def run_analysis(self, scope: AnalysisScope = AnalysisScope.ALL_ROIS) -> None:
        """Plan and dispatch recompute for ``scope``. The only method in
        this module (or anywhere else) allowed to trigger real computation
        (AGENTS.md, "What NOT to do without checking in again first").

        Dispatches via `AnalysisWorker` (a real background thread, never
        `QThreadPool` - AGENTS.md's zarr rule), so this returns immediately;
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
        default_inner, default_outer = self._default_reference_radii()
        cancel_event = self._worker.cancel_event
        exclusion_mode = self._reference_exclusion_mode()
        all_rois = tuple(rois_by_id.values())
        # One cache for this whole run: the sample-exclusion union is
        # identical for every cell at a given (cube, wavelength), so this
        # turns O(ROIs x cells) rasterizations into O(ROIs). Scoped to the
        # run rather than held on self so it can't go stale against a later
        # ROI edit - a new run builds a fresh one.
        sample_exclusion_cache: dict[tuple[int, float], np.ndarray] = {}

        def run() -> None:
            total = len(plan.to_recompute)
            for completed, (roi_id, cube_index) in enumerate(plan.to_recompute, start=1):
                if cancel_event.is_set():
                    break
                roi = rois_by_id.get(roi_id)
                if roi is None:
                    continue
                wavelength_inputs = self._gather_wavelength_inputs(cube_index)
                result = compute_cell(
                    roi, cube_index, wavelength_inputs,
                    reduction_method=reduction_method, trimmed_mean_fraction=self._trimmed_mean_fraction,
                    default_reference_inner_radius_px=default_inner, default_reference_outer_radius_px=default_outer,
                    masks_dir=self._masks_dir, chromatic_dir=self._chromatic_dir, settings_dir=self._settings_dir,
                    naming=naming, all_rois=all_rois, reference_exclusion_mode=exclusion_mode,
                    sample_exclusion_cache=sample_exclusion_cache, cancel_event=cancel_event,
                )
                if result is not None:
                    self._results[(roi_id, cube_index)] = result
                    self._store.record(roi_id, cube_index, result.provenance)
                    write_cell(self._data_h5_path, roi_id, cube_index, result)
                    self.store_updated.emit()
                self.analysis_progress.emit(completed / max(total, 1))
            self.analysis_complete.emit()

        self._worker.submit(run)

    def _gather_wavelength_inputs(self, cube_index: int) -> dict[float, WavelengthComputeInput]:
        geometry = self._geometry_settings()
        background = self._background_settings()
        inputs: dict[float, WavelengthComputeInput] = {}
        for wavelength_nm in self._wavelengths_for_cube(cube_index):
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
