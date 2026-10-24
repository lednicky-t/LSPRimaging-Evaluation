"""``DatasetModule`` - the Dataset stage owner (sketch §7 "Dataset", §10).

Owns the current :class:`~lspr_imaging_app.dataset.model.ImageDataset` and
acquisition metadata. Emits ``dataset_loaded``/``dataset_cleared``; exposes a
read-only query interface. No other module may read dataset state any other
way (CLAUDE.md, "Module boundaries").

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
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from lspr_core import ImagingAcquisitionMetadata
from PyQt6.QtCore import QObject, pyqtSignal

from ..analysis.worker import AnalysisWorker
from ..applog.events import log_event, timed
from ..diagnostics import instrumented
from ..storage.workspace import save_acquisition_metadata_sidecar
from .export_stats import ExportStats
from .io import (
    dataset_record_map,
    DatasetLoadChoice,
    acquisition_metadata_sidecar_path,
    dataset_get_record,
    export_ome_zarr_dataset,
    load_dataset,
    load_image_array,
    load_image_array_uncached,
    load_image_shape,
    dataset_file_stats,
    ome_zarr_chunk_shape,
)
from .model import (
    CubeIntervalStats,
    ImageDataset,
    ImageRecord,
    compact_dataset_image_timings,
    cube_interval_stats,
    mean_frame_interval_seconds,
    rehydrated_acquisition_metadata,
)

logger = logging.getLogger(__name__)

EXPORT_TASK_ID = "zarr_export"
EXPORT_TASK_LABEL = "Export to OME-Zarr"


@dataclass(frozen=True)
class ChunkGridPreview:
    """The export chunk grid to draw on the Image panel: lines at ``offset + k * chunk`` in raw pixels."""

    enabled: bool = False
    chunk_w: int = 64
    chunk_h: int = 64
    offset_x: int = 0
    offset_y: int = 0
    processed: bool = False
    """True: the grid lies in processed space (the export applies rotation/flip/crop, 2026-10-20), so it is drawn
    straight on the displayed image; False: in raw pixels, mapped onto the displayed image (tilted under rotation)."""


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
    # What the export did (`dataset/export_stats.py`: time, images, bytes, workers), emitted just BEFORE `export_finished` from the
    # same thread, so a receiver of both sees the numbers first (2026-10-22).
    export_stats_ready = pyqtSignal(object)  # ExportStats
    export_failed = pyqtSignal(str)  # also covers a cancelled export - see
    # export_to_ome_zarr's docstring for why there's no separate signal
    # The status bar's task indicator (2026-10-20, with the move of the export to the Image panel's Export tab):
    # same shapes as the other tasks (CLAUDE.md "Heavy work reports progress"); `export_*` above stay for the tab.
    task_progress = pyqtSignal(str, str, float, str)  # task_id, label, fraction 0..1, message
    task_finished = pyqtSignal(str, str, str)  # task_id, outcome, message; once per run
    # Added 2026-09-26 for the Export section's chunk-grid preview toggle
    # (since 2026-10-20 the Image panel's Export tab, `panels/image/export_tab.py`): lives here, not on the section
    # itself, because `ImagePanel` (the only thing that can draw it) may
    # only read state from a module, never from a sibling panel - see
    # module docstring's "no other module may read dataset state any other
    # way" rule. Cosmetic-only, like `MaskModule.cosmetic_changed` - it
    # never touches `_dataset` itself.
    chunk_grid_preview_changed = pyqtSignal()
    # The View tab's grid of the loaded OME-Zarr's own chunks (2026-10-22), cosmetic like the export preview above.
    # Independent of it: both grids can be shown at once.
    source_chunk_grid_changed = pyqtSignal()
    # The size / date of the loaded dataset's files are known (2026-10-24: they are counted on a background thread, an OME-Zarr walk
    # took ~3.5 s on the GUI thread). Carries the dataset they belong to; emitted on the GUI thread.
    file_stats_ready = pyqtSignal(object)  # ImageDataset
    _file_stats_done = pyqtSignal(object, object)  # (dataset, stats | None): thread -> GUI thread hand-off

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._dataset: ImageDataset | None = None
        # t = 0 of the experimental time (`_time_anchor_ms`); computed once, reset whenever the dataset or its metadata changes.
        self._time_anchor_ms_value: int | None = None
        self._time_anchor_known = False
        self._mean_interval_cache: tuple[ImageDataset, object, int, float | None] | None = None
        self._chunk_grid_preview = ChunkGridPreview()
        self._source_chunk_grid_visible = False
        self._file_stats_cache: tuple[ImageDataset, tuple[int, float | None]] | None = None
        self._file_stats_wanted: ImageDataset | None = None  # the dataset whose count is running or finished; None after a clear
        self._file_stats_done.connect(self._on_file_stats_done)
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

    def record_map(self) -> dict[tuple[int, float], ImageRecord]:
        """``(cube, wavelength) -> record`` for every image, built in one pass. For a caller that looks up many frames:
        ``current_image`` searches the record list for each call, which is quadratic over a whole dataset."""
        if self._dataset is None:
            raise RuntimeError("No dataset is loaded.")
        return dataset_record_map(self._dataset)

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

    def load_plane_uncached(self, cube_index: int, wavelength: float) -> np.ndarray:
        """`load_plane`, but the plane is not kept in the shared read cache and the array is the caller's own (2026-10-16).
        For a task that reads thousands of planes once each (the Mask tab's transient scan): going through the cache would
        evict the frames the Image panel is showing. Safe to call from a worker thread."""
        record = self.current_image(cube_index, wavelength)
        return load_image_array_uncached(str(record.path))

    def dataset_file_stats(self, dataset: ImageDataset | None = None) -> tuple[int, float | None] | None:
        """(total bytes, oldest file mtime) of `dataset` (default: the loaded one), or **None while they are not known yet**
        (and without a dataset). Never blocks: the first ask starts the count on a plain `threading.Thread` and `file_stats_ready`
        (carrying the dataset) is emitted when it is in; every ask after that returns the cached value. Counted once per dataset
        (the Dataset summary and the menu bar's info row both ask on every load or metadata import).

        2026-10-24: this used to count on the calling (GUI) thread, 3.3-3.7 s for an OME-Zarr of 8905 files
        (`docs/startup_gui_thread_audit_2026-10-24.md`). Plain thread, not `QThreadPool`: the work is `os.scandir` only, but it
        is dataset-adjacent (CLAUDE.md invariant)."""
        target = dataset if dataset is not None else self._dataset
        if target is None:
            return None
        if self._file_stats_cache is not None and self._file_stats_cache[0] is target:
            return self._file_stats_cache[1]
        if self._file_stats_wanted is not target:  # not counting it yet (a count of another dataset is dropped when it ends)
            self._file_stats_wanted = target

            def count() -> None:
                started = time.perf_counter()
                try:
                    stats: tuple[int, float | None] | None = dataset_file_stats(target)
                except Exception:  # noqa: BLE001 - nothing may escape a thread silently; a size is only displayed
                    logger.exception("Counting the dataset's files failed")
                    stats = (0, None)
                log_event(logger, "dataset.file_stats", logging.INFO, ms=round((time.perf_counter() - started) * 1000))
                self._file_stats_done.emit(target, stats)

            threading.Thread(target=count, name="dataset-file-stats", daemon=True).start()
        return None

    def _on_file_stats_done(self, dataset: object, stats: object) -> None:
        if dataset is not self._file_stats_wanted or not isinstance(stats, tuple):
            return  # the dataset was cleared or replaced while counting
        self._file_stats_cache = (dataset, stats)  # type: ignore[assignment]
        self.file_stats_ready.emit(dataset)

    def timed_image_count(self) -> int:
        """How many images have an acquisition time recorded (0 without metadata). Does not compact the timings: the
        Experimental plan section counts `metadata.image_timings` itself and compaction empties that list."""
        metadata = None if self._dataset is None else self._dataset.acquisition_metadata
        if metadata is None:
            return 0
        if metadata.image_timings:
            return len(metadata.image_timings)
        return len(compact_dataset_image_timings(self._dataset))

    def comment_event_count(self) -> int:
        """How many comment / pump-plan step events the loaded acquisition metadata holds (0 without any)."""
        metadata = None if self._dataset is None else self._dataset.acquisition_metadata
        return 0 if metadata is None else len(metadata.comment_events)

    def raw_plane_shape(self) -> tuple[int, int] | None:
        """The raw (height, width) every frame in the loaded dataset shares -
        an assumption already made elsewhere in this app (e.g. chunked
        OME-Zarr export assumes one shape for the whole dataset). `None` if
        no dataset is loaded. Reads only one image's header
        (`dataset.io.load_image_shape`), never a full plane, unlike
        `load_plane` - added for `RoiGeometrySync`, which needs a raw shape
        to remap ROI positions/masks on a rotation/flip/crop edit but has no
        reason to hold a whole frame in memory to get it."""
        if self._dataset is None or not self._dataset.records:
            return None
        return load_image_shape(str(self._dataset.records[0].path))

    def wavelengths(self) -> tuple[float, ...]:
        """Every distinct wavelength in the loaded dataset, sorted - an
        empty tuple (not an error) if no dataset is loaded, since unlike
        `current_image` there's no specific key being asked for that could
        be "missing".

        **`0.0`, if present, is not a spectral wavelength** - see
        `wavelengths_for_cube`'s docstring below for the canonical
        statement of why."""
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
        makes an ordinary occurrence rather than a corrupt-data case.

        **`0.0`, if present, is the dataset's dark/background frame (LED
        off, no illumination), not a spectral sample point** - a real,
        ordinarily-acquired image (so it belongs in this list, gets a real
        `ImageRecord`, and is a genuine, selectable index everywhere this
        list feeds a UI control), but its job is estimating the sensor's
        own dark-current offset for subtraction from the real wavelengths'
        pixel values, not contributing a point to a spectrum. Confirmed,
        not assumed - the stable app already has a real, working consumer
        of exactly this convention: `gui/analysis_worker_mixin.py`'s
        `dark_frame_pixel_impact()` (`dark_mask = wavelengths == 0.0`) uses
        the 0 nm entry to simulate dark-current subtraction and measure its
        effect on a computed formula value (e.g. absorbance); `docs/
        row_banding_artifact_analysis_2026-09.md` independently refers to
        the same thing as "`WL0`, LED off". **Any code that treats "all
        wavelengths" as spectral data (a fit, a spectrum plot, an average
        across wavelength) must exclude `0.0` explicitly, via
        `dataset.model.is_dark_frame_wavelength`** - it is real data, just
        not spectral data, and nothing in this query surface filters it out
        automatically. `AnalysisEngine` (`analysis/engine.py`) is the real
        enforcement point today - `_gather_wavelength_inputs`/
        `_gather_current_inputs`/`_naming` all exclude it before building
        `compute_cell` inputs, settings-snapshot fingerprints, and the
        per-dataset filename scheme, so a dark frame never becomes a point
        in a stored spectrum, fit, or metric (fixed 2026-09-30 - see the
        build log for the gap this closed)."""
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

    def cube_interval_stats(self) -> CubeIntervalStats | None:
        """Mean/min/max/std of the dataset's time between consecutive
        cubes' start times, in seconds - see `dataset.model.
        cube_interval_stats`'s docstring for the full reasoning behind
        "cube interval" as the term, "earliest frame" as a cube's fixed
        time definition, and mean-with-spread over a single median. `None`
        if no dataset is loaded, the dataset has no acquisition timing, or
        fewer than two cubes have timing recorded.

        Goes through `compact_dataset_image_timings` (lazy, idempotent -
        see that function's own docstring), matching `rehydrated_
        acquisition_metadata`'s pattern above rather than reading
        `_dataset.compact_image_timings` directly."""
        if self._dataset is None:
            return None
        return cube_interval_stats(compact_dataset_image_timings(self._dataset))

    def cube_start_seconds(self, cube_index: int) -> float | None:
        """Seconds from the acquisition start to the cube's *first* frame -
        the Time-mode label of a cube (ported from the stable app's
        `AnalysisController._cube_timestamp_ms_by_cube_index` "first" rule
        and `_sensorgram_time_anchor_ms`). t = 0 is the recorded
        `started_at_utc` if parseable, else the earliest recorded frame.
        `None` if there is no dataset, no timing data, or no timing for this
        cube - the caller then falls back to the plain cube number."""
        if self._dataset is None or self._dataset.acquisition_metadata is None:
            return None
        compact = compact_dataset_image_timings(self._dataset)
        cube_ms = compact.earliest_ms_by_cube.get(int(cube_index))
        anchor_ms = self._time_anchor_ms()
        if cube_ms is None or anchor_ms is None:
            return None
        return (cube_ms - anchor_ms) / 1000.0

    def frame_start_seconds(self, cube_index: int, wavelength: float) -> float | None:
        """Seconds from the acquisition start to *this frame* - the experimental time of one image (a cube's own
        time, `cube_start_seconds`, is only its first frame's). Same t = 0 as the cube time. `None` when the
        frame has no timing (or there is no dataset / acquisition metadata): the caller then shows no time."""
        if self._dataset is None or self._dataset.acquisition_metadata is None:
            return None
        frame_ms = compact_dataset_image_timings(self._dataset).per_frame_ms.get((int(cube_index), float(wavelength)))
        anchor_ms = self._time_anchor_ms()
        if frame_ms is None or anchor_ms is None:
            return None
        return (frame_ms - anchor_ms) / 1000.0

    def frame_times_unix_ms(self) -> dict[tuple[int, float], int]:
        """``(cube, wavelength) -> acquisition time`` in absolute Unix milliseconds for every timed frame; empty without a dataset or
        without acquisition timing. A copy: the caller may keep it (the analysis scan stores it with its results)."""
        if self._dataset is None or self._dataset.acquisition_metadata is None:
            return {}
        return dict(compact_dataset_image_timings(self._dataset).per_frame_ms)

    def _time_anchor_ms(self) -> int | None:
        """t = 0 of the experimental time, in unix ms: the recorded `started_at_utc` if parseable, else the
        earliest recorded frame; `None` without any timing. Computed once (and reset by `load_dataset`,
        `set_acquisition_metadata` and `clear_dataset`): it runs on every slideshow step, and the earliest-frame
        fallback scans every frame."""
        if self._time_anchor_known:
            return self._time_anchor_ms_value
        anchor_ms: int | None = None
        if self._dataset is not None and self._dataset.acquisition_metadata is not None:
            compact = compact_dataset_image_timings(self._dataset)
            started = self._dataset.acquisition_metadata.started_at_utc
            if started:
                try:
                    anchor_ms = int(datetime.fromisoformat(started.replace("Z", "+00:00")).timestamp() * 1000)
                except ValueError:
                    anchor_ms = None
            if anchor_ms is None and compact.per_frame_ms:
                anchor_ms = min(compact.per_frame_ms.values())
        self._time_anchor_ms_value, self._time_anchor_known = anchor_ms, True
        return anchor_ms

    def mean_frame_interval_seconds(self) -> float | None:
        """Average time between two consecutive images of one cube, in seconds
        (what "1x" means in the Image panel's slideshow). `None` without
        acquisition timing (math: `dataset.model.mean_frame_interval_seconds`)."""
        if self._dataset is None or self._dataset.acquisition_metadata is None:
            return None
        # Once per dataset (testing10, 2026-10-14): the slideshow asks on every step, and the sum over all 8478 frames' timings
        # was 4 % of the GUI thread while playing. The key is the dataset object, its metadata object and its record count.
        dataset, metadata, count = self._dataset, self._dataset.acquisition_metadata, len(self._dataset.records)
        cached = self._mean_interval_cache
        if cached is not None and cached[0] is dataset and cached[1] is metadata and cached[2] == count:
            return cached[3]
        value = mean_frame_interval_seconds(compact_dataset_image_timings(dataset).per_frame_ms)
        self._mean_interval_cache = (dataset, metadata, count, value)
        return value

    def frame_comment(self, cube_index: int, wavelength: float) -> tuple[bool, str | None]:
        """`(linked, comment)` for one frame. `linked` is False when the frame
        cannot be tied to the experiment record at all (no acquisition
        metadata, or no timing for this frame); `comment` is then None. With
        `linked` True, `comment` is the comment active at the frame's
        acquisition time, or None if none was recorded yet."""
        if self._dataset is None or self._dataset.acquisition_metadata is None:
            return (False, None)
        compact = compact_dataset_image_timings(self._dataset)
        timing_ms = compact.per_frame_ms.get((int(cube_index), float(wavelength)))
        if timing_ms is None:
            return (False, None)
        return (True, self._dataset.acquisition_metadata.comment_at(timing_ms))

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
                with timed(logger, "dataset.scan", logging.INFO, folder=folder.name):
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
        cancel_event = self._export_worker.cancel_event

        def progress(percent: int, message: str) -> None:
            self.export_progress.emit(percent, message)
            self.task_progress.emit(EXPORT_TASK_ID, EXPORT_TASK_LABEL, max(0.0, min(1.0, percent / 100.0)), message)

        def task() -> None:
            self.task_progress.emit(EXPORT_TASK_ID, EXPORT_TASK_LABEL, 0.0, "Starting export...")
            stats = ExportStats()
            started = time.perf_counter()
            try:
                result = export_ome_zarr_dataset(
                    dataset,
                    destination,
                    progress_callback=progress,
                    cancel_event=cancel_event,
                    export_stats=stats,
                    **kwargs,
                )
            except Exception as exc:  # noqa: BLE001 - reported via signal, not re-raised
                if cancel_event.is_set():
                    self.task_finished.emit(EXPORT_TASK_ID, "cancelled", "")
                else:
                    logger.error("The OME-Zarr export failed", exc_info=exc)
                    self.task_finished.emit(EXPORT_TASK_ID, "failed", str(exc))
                self.export_failed.emit(str(exc))
                return
            stats.seconds = time.perf_counter() - started
            log_event(
                logger, "dataset.export_done", logging.INFO, seconds=round(stats.seconds, 1), planes=stats.planes,
                mb_written=round(stats.bytes_written / 1e6, 1), planes_per_s=round(stats.planes_per_second, 1), workers=stats.workers, worker_mode=stats.worker_mode,
            )
            self.export_stats_ready.emit(stats)
            self.task_finished.emit(EXPORT_TASK_ID, "completed", f"written to {Path(result).name} in {stats.headline()}")
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
        self._time_anchor_known = False
        self._log_loaded(dataset)
        self.dataset_loaded.emit(dataset)

    @staticmethod
    def _log_loaded(dataset: ImageDataset) -> None:
        """The `dataset.loaded` log event. Logging must never be able to break a load, so every
        field is read defensively (a test double or a half-built dataset just gets fewer fields)."""
        try:
            log_event(
                logger, "dataset.loaded", logging.INFO,
                folder=Path(str(getattr(dataset, "home", ""))).name, format=getattr(dataset, "source_format", None),
                planes=len(getattr(dataset, "records", ())), cubes=len(getattr(dataset, "spectral_cube_indices", ())),
                wavelengths=len(getattr(dataset, "wavelengths_nm", ())),
            )
        except Exception:  # noqa: BLE001 - diagnostics only
            logger.debug("dataset.loaded event could not be built", exc_info=True)

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
        self._time_anchor_known = False
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
        self._time_anchor_known = False
        self._mean_interval_cache = None
        self._file_stats_cache = None
        self._file_stats_wanted = None
        self.dataset_cleared.emit()

    # -- chunk-grid preview (Export section <-> Image panel) ----------------

    def chunk_grid_preview(self) -> ChunkGridPreview:
        """The Export section's chunk-grid preview overlay. `ImagePanel` reads this to decide whether/how to draw the
        grid; it never reads the Export section's widgets directly. The grid is in raw pixels, or in processed space
        when the export applies rotation/flip/crop (`ChunkGridPreview.processed`)."""
        return self._chunk_grid_preview

    def set_chunk_grid_preview(
        self, enabled: bool, chunk_w: int, chunk_h: int, offset_x: int = 0, offset_y: int = 0, processed: bool = False
    ) -> None:
        """Set by the Export section on toggle, and again on every chunk-size or grid-position change while the
        preview is on. No-op-skip if nothing actually changed, matching this module's other setters."""
        preview = ChunkGridPreview(bool(enabled), int(chunk_w), int(chunk_h), int(offset_x), int(offset_y), bool(processed))
        if preview == self._chunk_grid_preview:
            return
        self._chunk_grid_preview = preview
        self.chunk_grid_preview_changed.emit()

    # -- the loaded OME-Zarr's own chunk grid (View tab <-> Image panel) ----

    def source_chunk_shape(self) -> tuple[int, int] | None:
        """(height, width) of the loaded OME-Zarr's chunks; None for a TIFF stack (no chunks) or no dataset."""
        return ome_zarr_chunk_shape(self._dataset)

    def source_chunk_grid_visible(self) -> bool:
        """The View tab's toggle. Stays True across a TIFF load (nothing to draw then) so the remembered choice survives."""
        return self._source_chunk_grid_visible

    def set_source_chunk_grid_visible(self, visible: bool) -> None:
        visible = bool(visible)
        if visible == self._source_chunk_grid_visible:
            return
        self._source_chunk_grid_visible = visible
        self.source_chunk_grid_changed.emit()
