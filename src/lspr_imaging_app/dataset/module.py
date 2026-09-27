"""``DatasetModule`` - the Dataset stage owner (sketch §7 "Dataset", §10).

Owns the current :class:`~lspr_imaging_app.dataset.model.ImageDataset` and
acquisition metadata. Emits ``dataset_loaded``/``dataset_cleared``; exposes a
read-only query interface. No other module may read dataset state any other
way (AGENTS.md, "Module boundaries").

**Built 2026-09-21** - the state-owner itself, the one piece of the Dataset
stage left unbuilt after the 2026-09-20 `dataset/io.py`/`model.py` port
(those are the pure IO/dataclass layer this module wraps, not this
`QObject`). Found while auditing every package for leftover
`NotImplementedError` stubs during an unrelated MaskModule/ChromaticModule
session - not previously flagged in any prior build-log entry's "still
open" list, so recorded here rather than assumed already done.

**The four-method query surface (`current_image`/`wavelengths`/
`spectral_cubes`/`acquisition_metadata`) is deliberately narrower than the
raw `ImageDataset` dataclass** - confirmed sufficient, not just assumed,
by checking every `dataset/io.py` pure function a caller would actually
need: `dataset_load_plane_roi` already accepts an optional pre-looked-up
`record` parameter specifically to avoid re-deriving it from the full
dataset, and `dataset_load_plane`/`dataset_plane_shape` need nothing from
`ImageDataset` beyond the one `ImageRecord` a lookup already resolves to.
So `current_image()` alone is enough for a caller to reach every one of
those functions without this module ever handing out the raw dataset
object - matching its own "no other module may read dataset state any
other way" rule instead of quietly working around it.

**Real correction (2026-09-22): "sufficient" turned out to mean "enough to
reach the loading functions," not "enough to actually load pixels."**
Found while wiring `AnalysisEngine` to real data: none of the four query
methods return actual pixel data, and every `dataset/io.py` loading
function needs the full `ImageDataset` as a parameter - which this module
still correctly never hands out. Added a fifth method, `load_plane()`,
that does the loading *internally* (reusing `current_image()`'s own record
resolution rather than re-deriving it) and returns pixels only - the "no
other module reads dataset state any other way" rule is unchanged, this
is one more narrow read, not a loosening of it.

**Second correction, same cause (2026-09-23)**: `wavelengths()` is
dataset-*global*, and `AnalysisEngine` needs per-cube wavelengths - driving
a per-cube loop off the global union would ask for planes a cube may not
have. Added `wavelengths_for_cube()` as a sixth query rather than letting
the engine iterate the global list and swallow the resulting `KeyError`s.

**`clear_dataset()` is deliberately minimal** - it clears only this
module's own `_dataset` reference and emits `dataset_cleared`, unlike the
old app's `DatasetController.clear_dataset` (`gui/dataset_controller.py`),
which resets a dozen *other* pieces of window state in the same method
(record maps, mask state, sensorgram caches, image caches, UI widgets).
That single method touching everything is exactly the entanglement
pattern this rewrite exists to undo (see
`docs/rewrite_feature_inventory_2026-09.md`) - here, every other module
that holds dataset-derived state is expected to subscribe to
`dataset_cleared` and reset *itself*, not have this module reach into it.
No subscriber exists yet (the panel layer isn't built), so this is
currently unverified end-to-end - flagged for whoever wires the first
subscriber, not assumed correct.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from lspr_core import ImagingAcquisitionMetadata
from PyQt6.QtCore import QObject, pyqtSignal

from ..analysis.worker import AnalysisWorker
from ..diagnostics import instrumented
from ..storage.workspace import save_acquisition_metadata_sidecar
from .io import (
    DatasetLoadChoice,
    acquisition_metadata_sidecar_path,
    dataset_get_record,
    export_ome_zarr_dataset,
    load_dataset,
    load_image_array,
)
from .model import ImageDataset, ImageRecord, rehydrated_acquisition_metadata

logger = logging.getLogger(__name__)


class DatasetModule(QObject):
    """Owns dataset load/convert and acquisition metadata."""

    dataset_loaded = pyqtSignal(object)  # ImageDataset
    dataset_cleared = pyqtSignal()
    # Both added 2026-09-24 alongside load_dataset_from_folder() - see that
    # method's docstring.
    dataset_load_failed = pyqtSignal(str)
    dataset_choice_needed = pyqtSignal(object)  # DatasetLoadChoice
    # Added 2026-09-24 alongside export_to_ome_zarr() - see that method's
    # docstring.
    export_progress = pyqtSignal(int, str)  # percent, message
    export_finished = pyqtSignal(object)  # Path
    export_failed = pyqtSignal(str)  # also covers a cancelled export - see
    # export_to_ome_zarr's docstring for why there's no separate signal
    # Added 2026-09-26 for the Export section's chunk-grid preview toggle
    # (`panels/workflow/dataset_export.py`): lives here, not on the section
    # itself, because `ImagePanel` (the only thing that can draw it) may
    # only read state from a module, never from a sibling panel - see
    # module docstring's "no other module may read dataset state any other
    # way" rule. Cosmetic-only, like `MaskModule.cosmetic_changed` - it
    # never touches `_dataset` itself.
    chunk_grid_preview_changed = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._dataset: ImageDataset | None = None
        self._chunk_grid_preview_enabled = False
        self._chunk_grid_preview_chunk_px = 64
        self._worker = AnalysisWorker()
        # Separate from `_worker` (load) rather than shared - a load and an
        # export are independent operations with no reason to serialize
        # against each other's `AnalysisWorker.submit`'s "only one task at a
        # time" contract, matching `AnalysisEngine`'s own two-worker pattern
        # (`_worker`/`_derived_worker`) for the same reason.
        self._export_worker = AnalysisWorker()

    # -- query interface --------------------------------------------------

    def current_image(self, cube_index: int, wavelength: float) -> ImageRecord:
        """Resolve one (cube, wavelength) pair to an :class:`ImageRecord`.

        Raises `RuntimeError` if no dataset is loaded at all (a caller
        asking before any load is a caller bug, not a normal "not found"),
        or `KeyError` if a dataset is loaded but has no record at this
        exact key - the same message shape `dataset.io.dataset_load_plane`
        already raises for the identical underlying miss."""
        if self._dataset is None:
            raise RuntimeError("No dataset is loaded.")
        record = dataset_get_record(self._dataset, int(cube_index), float(wavelength))
        if record is None:
            raise KeyError(f"No record found for spectral_cube_index={cube_index}, wavelength={wavelength}")
        return record

    def load_plane(self, cube_index: int, wavelength: float) -> np.ndarray:
        """Load the full (cube_index, wavelength) plane's pixel data -
        the pixel-loading counterpart to `current_image()`'s metadata-only
        lookup (added 2026-09-22, see module docstring's "real correction"
        note). Same `RuntimeError`/`KeyError` semantics as `current_image`,
        since this calls it directly rather than re-deriving the record via
        `dataset.io.dataset_load_plane` (which would redundantly repeat the
        same lookup internally).

        The returned array is read-only (`dataset.io.load_image_array`'s
        own caching contract - see that function's docstring) - safe for
        every existing caller in this codebase, which all only ever read
        from a loaded image, never mutate it in place; a caller that
        genuinely needs to mutate the result must copy it first."""
        record = self.current_image(cube_index, wavelength)
        return load_image_array(str(record.path))

    def wavelengths(self) -> tuple[float, ...]:
        """Every distinct wavelength in the loaded dataset, sorted - an
        empty tuple (not an error) if no dataset is loaded, since unlike
        `current_image` there's no specific key being asked for that could
        be "missing"."""
        if self._dataset is None:
            return ()
        return tuple(self._dataset.wavelengths_nm)

    def wavelengths_for_cube(self, cube_index: int) -> tuple[float, ...]:
        """The wavelengths *one* cube actually has records for, sorted -
        empty tuple if no dataset is loaded (or if that cube has no records
        at all), same "no specific key was asked for" reasoning as
        `wavelengths()`.

        **Use this, not `wavelengths()`, for anything that then loads a
        plane** (added 2026-09-23 while wiring `AnalysisEngine`).
        `wavelengths()` is the union across every cube, so feeding it to a
        per-cube loop would ask for a (cube, wavelength) pair that may not
        exist - `load_plane` would raise `KeyError` for a cube that is
        merely short one wavelength, which a partially-failed acquisition
        makes an ordinary occurrence rather than a corrupt-data case."""
        if self._dataset is None:
            return ()
        return tuple(self._dataset.wavelengths_for_cube(int(cube_index)))

    def spectral_cubes(self) -> tuple[int, ...]:
        """Every distinct spectral cube index in the loaded dataset,
        sorted - empty tuple if no dataset is loaded."""
        if self._dataset is None:
            return ()
        return tuple(self._dataset.spectral_cube_indices)

    def acquisition_metadata(self) -> ImagingAcquisitionMetadata | None:
        """The loaded dataset's acquisition metadata - `None` if no dataset
        is loaded, or if the loaded dataset never had an acquisition
        sidecar (a plain image-stack folder with no metadata). Returned as
        currently stored, potentially already timing-compacted (see
        `dataset.model.CompactImageTimings`) - a caller that specifically
        needs per-frame timing data should go through
        `dataset.model.compact_dataset_image_timings`, not read `image_
        timings` off whatever this returns directly."""
        if self._dataset is None:
            return None
        return self._dataset.acquisition_metadata

    def dataset_home(self) -> Path | None:
        """Where this dataset's own derived state is saved (`ImageDataset.
        home`) - `None` if no dataset is loaded. Added 2026-09-25 as a
        narrow, legitimate query need: a file dialog's starting directory
        and the acquisition-metadata sidecar's path both need it, and
        neither is "dataset state" in the sense the module-boundary rule
        exists to protect (it's just a folder path, not pixel/record
        data) - unlike `current_image`/`load_plane`, exposing it doesn't
        let a caller work around any other query."""
        if self._dataset is None:
            return None
        return self._dataset.home

    def rehydrated_acquisition_metadata(self) -> ImagingAcquisitionMetadata | None:
        """The loaded dataset's acquisition metadata with `image_timings`
        expanded back to real per-frame entries (`dataset.model.
        rehydrated_acquisition_metadata`) - `None` under the same
        conditions as `acquisition_metadata()`.

        **Added 2026-09-25** for two real callers that need real per-frame
        data, not `acquisition_metadata()`'s possibly-already-compacted
        version: exporting metadata to a standalone file (a compacted
        export would silently lose per-image timestamps), and looking up
        one specific frame's timing (`ImagingAcquisitionMetadata.
        timing_for`) for a live comment/step preview - see
        `panels/workflow/dataset_experimental_plan.py`."""
        if self._dataset is None:
            return None
        return rehydrated_acquisition_metadata(self._dataset)

    # -- commands -----------------------------------------------------------

    def is_loading(self) -> bool:
        return self._worker.is_running()

    @instrumented("DatasetModule.load_dataset_from_folder")
    def load_dataset_from_folder(self, folder: Path) -> None:
        """Scan `folder` (`dataset.io.load_dataset` - auto-detects
        TIFF-stack vs OME-Zarr, searches one level deep) on a background
        thread, then load the result. Emits exactly one of:

        - `dataset_loaded` (via `load_dataset()`) - a single candidate was
          found and is now loaded.
        - `dataset_choice_needed` - more than one candidate was found under
          `folder`; the caller must resolve it (a GUI layer, since this
          needs to prompt the user - see `panels/workflow/dataset_summary.py`
          for the reference implementation) and call `load_dataset()` with
          the chosen one itself.
        - `dataset_load_failed` - the folder doesn't exist, holds no
          recognizable dataset, or any other error.

        Raises `RuntimeError` if a load is already in flight - matches
        `AnalysisWorker.submit`'s own "only one task at a time" contract,
        surfaced here instead of letting it escape from a background thread.

        **Plain `threading.Thread` via `AnalysisWorker`, deliberately not
        `QThreadPool`** (2026-09-24, ported from the stable app's
        `DatasetController.load_dataset_from_folder`, which does use a
        `QThreadPool`-based `FunctionWorker` there - not copied here on
        purpose). `dataset.io.load_dataset` probes for OME-Zarr candidates,
        and `analysis/worker.py` documents a since-learned, non-negotiable
        invariant in this rewrite: never let a `QThreadPool` worker touch,
        even indirectly, an OME-Zarr read (root-caused to a native
        `STATUS_HEAP_CORRUPTION` crash). `AnalysisWorker` already exists for
        exactly this reason - reused here rather than duplicated."""
        if self._worker.is_running():
            raise RuntimeError("A dataset is already loading.")

        def task() -> None:
            try:
                result = load_dataset(folder)
            except Exception as exc:  # noqa: BLE001 - reported via signal, not re-raised
                self.dataset_load_failed.emit(str(exc))
                return
            if isinstance(result, DatasetLoadChoice):
                self.dataset_choice_needed.emit(result)
            else:
                self.load_dataset(result)

        self._worker.submit(task)

    def is_exporting(self) -> bool:
        return self._export_worker.is_running()

    @instrumented("DatasetModule.export_to_ome_zarr")
    def export_to_ome_zarr(self, destination: Path, **kwargs: object) -> None:
        """Export the currently loaded dataset to `destination` as OME-Zarr
        (`dataset.io.export_ome_zarr_dataset` - accepts the same keyword
        options: `chunk_size_px`, `compression_enabled`, `shard_mode`, ...)
        on a background thread. Emits `export_progress` repeatedly, then
        exactly one of `export_finished` (the actual destination path,
        which may differ from `destination` - see
        `export_ome_zarr_dataset`'s own normalization) or `export_failed`.

        Raises `RuntimeError` immediately (not via a signal) if no dataset
        is loaded, or if an export is already running - both are caller
        bugs (the UI should disable the control), not normal outcomes.

        **No separate "cancelled" signal.** `export_ome_zarr_dataset`
        itself raises `RuntimeError("OME-Zarr export cancelled.")` when
        `cancel_export()`'s event fires - that already reaches
        `export_failed` through the same path as any other failure, and
        reads correctly there ("failed: ... cancelled"), so a second signal
        would only duplicate it.

        Same `AnalysisWorker`-not-`QThreadPool` reasoning as
        `load_dataset_from_folder` - this writes zarr chunks directly."""
        if self._dataset is None:
            raise RuntimeError("No dataset is loaded.")
        if self._export_worker.is_running():
            raise RuntimeError("An export is already running.")
        dataset = self._dataset

        def task() -> None:
            try:
                result = export_ome_zarr_dataset(
                    dataset,
                    destination,
                    progress_callback=lambda percent, message: self.export_progress.emit(percent, message),
                    cancel_event=self._export_worker.cancel_event,
                    **kwargs,
                )
            except Exception as exc:  # noqa: BLE001 - reported via signal, not re-raised
                self.export_failed.emit(str(exc))
                return
            self.export_finished.emit(result)

        self._export_worker.submit(task)

    def cancel_export(self) -> None:
        self._export_worker.cancel()

    @instrumented("DatasetModule.load_dataset")
    def load_dataset(self, dataset: ImageDataset) -> None:
        """Load `dataset` and emit `dataset_loaded`, unconditionally
        replacing any previously-loaded dataset - matches the old app's own
        load flow (a fresh load always fully replaces `window._state.
        dataset`, no "merge" concept). No no-op check: a reload is a
        real, deliberate user action even when the newly-scanned content
        happens to be identical to what was already loaded."""
        self._dataset = dataset
        self.dataset_loaded.emit(dataset)

    @instrumented("DatasetModule.set_acquisition_metadata")
    def set_acquisition_metadata(self, metadata: ImagingAcquisitionMetadata) -> None:
        """Replace the loaded dataset's acquisition metadata - the command
        an explicit "Import metadata" action needs (added 2026-09-25;
        previously the only way metadata got attached was automatically,
        inside `dataset.io.load_dataset`, at load time - see that
        function's docstring). Raises `RuntimeError` if no dataset is
        loaded (matches `current_image`'s "asking before any load is a
        caller bug" convention).

        Also saves the `analysis/acquisition_metadata.json` sidecar
        (matching the stable app's `MetadataController.import_metadata`),
        so an explicit import survives the next load - a failed sidecar
        write is logged, not raised, matching the source's own
        non-fatal-warning treatment of the same failure.

        Re-emits `dataset_loaded` rather than a new signal - every existing
        subscriber (this module's own callers, `dataset_summary.py`'s
        stats display, etc.) already reacts to that correctly, since the
        dataset object is the same one, just mutated; a second signal
        would only duplicate what subscribers already listen to."""
        if self._dataset is None:
            raise RuntimeError("No dataset is loaded.")
        self._dataset.acquisition_metadata = metadata
        try:
            save_acquisition_metadata_sidecar(acquisition_metadata_sidecar_path(self._dataset.home), metadata)
        except OSError:
            logger.exception("Could not save the acquisition-metadata sidecar for %s", self._dataset.home)
        self.dataset_loaded.emit(self._dataset)

    @instrumented("DatasetModule.clear_dataset")
    def clear_dataset(self) -> None:
        """Clear the current dataset and emit `dataset_cleared` - a no-op
        (no signal) if nothing is loaded, matching this codebase's
        no-op-skip convention elsewhere. See module docstring for why this
        deliberately does nothing beyond dropping this module's own
        reference."""
        if self._dataset is None:
            return
        self._dataset = None
        self.dataset_cleared.emit()

    # -- chunk-grid preview (Export section <-> Image panel) ----------------

    def chunk_grid_preview(self) -> tuple[bool, int]:
        """Current (enabled, chunk_size_px) for the Export section's
        chunk-grid preview overlay. `ImagePanel` reads this to decide
        whether/how to draw the grid; it never reads the Export section's
        widgets directly."""
        return self._chunk_grid_preview_enabled, self._chunk_grid_preview_chunk_px

    def set_chunk_grid_preview(self, enabled: bool, chunk_size_px: int) -> None:
        """Set by the Export section on toggle, and again on every chunk-size
        edit while the preview is on. No-op-skip if nothing actually
        changed, matching this module's other setters."""
        if enabled == self._chunk_grid_preview_enabled and chunk_size_px == self._chunk_grid_preview_chunk_px:
            return
        self._chunk_grid_preview_enabled = enabled
        self._chunk_grid_preview_chunk_px = chunk_size_px
        self.chunk_grid_preview_changed.emit()
