"""Dataset dataclasses (sketch §7 "Dataset", §10).

Ported near-verbatim from ``domain/models.py`` on ``develop``/``main`` -
only ``ImageKey``, ``ImageRecord``, ``CompactImageTimings``, ``ImageDataset``
and their two companion functions belong to the Dataset stage; the rest of
that file's dataclasses (ROI, chromatic, mask, analysis models) belong to
other modules and are ported separately when those modules are built. No
logic changed from the original - see the original's own docstrings
(preserved here) for why ``CompactImageTimings`` exists (a documented
PyQt6-sip crash-correlation mitigation, not a style choice).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lspr_core import ImagingAcquisitionMetadata, ImagingCubeTiming


@dataclass(slots=True, frozen=True)
class ImageKey:
    wavelength_nm: float
    spectral_cube_index: int


@dataclass(slots=True)
class ImageRecord:
    key: ImageKey
    path: Path


@dataclass(slots=True)
class CompactImageTimings:
    """Memory-light substitute for holding thousands of live
    `lspr_core.ImagingCubeTiming` pydantic `BaseModel` instances (one per
    (spectral_cube_index, wavelength_nm) image) for a whole session - a
    large legacy CSV metadata import can produce 8,500+ of them. Stores
    exactly the same per-image timing information, but as plain dict
    entries (int/tuple/int) instead of one validated pydantic instance per
    row - see `compact_dataset_image_timings`'s docstring for why.

    Built once, right after a dataset's `acquisition_metadata` is set (see
    `compact_dataset_image_timings`); timing-lookup methods should read this
    directly instead of scanning `acquisition_metadata.image_timings`
    (which is emptied once this exists - see that function).
    """

    per_frame_ms: dict[tuple[int, float], int]
    earliest_ms_by_cube: dict[int, int]
    latest_ms_by_cube: dict[int, int]

    @classmethod
    def from_timings(cls, timings: list[ImagingCubeTiming]) -> "CompactImageTimings":
        per_frame_ms: dict[tuple[int, float], int] = {}
        earliest_ms_by_cube: dict[int, int] = {}
        latest_ms_by_cube: dict[int, int] = {}
        for timing in timings:
            cube_index = int(timing.spectral_cube_index)
            wavelength_nm = float(timing.wavelength_nm)
            acquired_ms = int(timing.acquired_at_unix_ms)
            per_frame_ms[(cube_index, wavelength_nm)] = acquired_ms
            earliest = earliest_ms_by_cube.get(cube_index)
            if earliest is None or acquired_ms < earliest:
                earliest_ms_by_cube[cube_index] = acquired_ms
            latest = latest_ms_by_cube.get(cube_index)
            if latest is None or acquired_ms > latest:
                latest_ms_by_cube[cube_index] = acquired_ms
        return cls(per_frame_ms=per_frame_ms, earliest_ms_by_cube=earliest_ms_by_cube, latest_ms_by_cube=latest_ms_by_cube)

    def to_timings(self) -> list[ImagingCubeTiming]:
        """Rebuilds real `ImagingCubeTiming` objects - a lossless inverse
        of `from_timings`, only ever called transiently right before a
        `model_dump()`/export call (see `rehydrated_acquisition_metadata`).
        The rebuilt list is never stored back onto a dataset."""
        return [
            ImagingCubeTiming(spectral_cube_index=cube_index, wavelength_nm=wavelength_nm, acquired_at_unix_ms=acquired_ms)
            for (cube_index, wavelength_nm), acquired_ms in self.per_frame_ms.items()
        ]

    def __len__(self) -> int:
        return len(self.per_frame_ms)


@dataclass(slots=True)
class ImageDataset:
    folder: Path
    records: list[ImageRecord]
    source_format: str = "image_stack"
    acquisition_metadata: ImagingAcquisitionMetadata | None = None
    compact_image_timings: CompactImageTimings | None = None
    home_folder: Path | None = None
    """The folder this dataset was loaded *from*, when that differs from
    `folder` (the actual TIFF/OME-Zarr location) - e.g. loading discovering
    the data one level below the folder it was pointed at (see
    `dataset/io.py`'s `discover_dataset_candidates`).
    `None` when they're the same (the common case: pointing directly at the
    data). Use the `home` property rather than this field directly."""

    @property
    def home(self) -> Path:
        """Where this app's own state (analysis/, sessions, ROI table, masks,
        acquisition metadata sidecar) is saved - `home_folder` if set,
        otherwise `folder`. Kept separate from `folder` so that data never
        gets written into a raw TIFF/OME-Zarr folder it doesn't own."""
        return self.home_folder if self.home_folder is not None else self.folder

    @property
    def wavelengths_nm(self) -> list[float]:
        return sorted({record.key.wavelength_nm for record in self.records})

    @property
    def spectral_cube_indices(self) -> list[int]:
        return sorted({record.key.spectral_cube_index for record in self.records})

    def wavelengths_for_cube(self, spectral_cube_index: int) -> list[float]:
        """The wavelengths *this one cube* actually has records for, sorted.

        **New on the `rewrite` branch (2026-09-23), not part of `domain/
        models.py`'s verbatim port** - added while wiring `AnalysisEngine`
        to real modules. `wavelengths_nm` above is dataset-*global* (the
        union across every cube); using it per-cube would silently pretend
        a cube has a wavelength it never recorded, and the analysis engine
        would then try to load a plane that doesn't exist. A real risk
        rather than a hypothetical one: an aborted or partially-failed
        acquisition is exactly how a cube ends up short a wavelength.
        """
        return sorted(
            {
                record.key.wavelength_nm
                for record in self.records
                if record.key.spectral_cube_index == int(spectral_cube_index)
            }
        )

    @property
    def is_ome_zarr(self) -> bool:
        return str(self.source_format).lower() in {"ome_zarr", "ome-zarr", "zarr"}

    @property
    def is_image_stack(self) -> bool:
        return not self.is_ome_zarr

    @property
    def format_label(self) -> str:
        return "OME-Zarr" if self.is_ome_zarr else "ImageStack"


def compact_dataset_image_timings(dataset: ImageDataset) -> CompactImageTimings:
    """Returns `dataset.compact_image_timings`, building it from
    `dataset.acquisition_metadata.image_timings` first if that hasn't
    happened yet - lazy and idempotent, so **every** reader should call
    this (never read `dataset.compact_image_timings` directly) rather than
    relying on some earlier assignment site having remembered to call it.
    Deliberately not "call once after assigning acquisition_metadata,
    trust it from then on" - that shape was tried first and proven fragile
    by real test failures on the original app: anything that constructs/
    reloads an `ImageDataset` with `acquisition_metadata` already set (a
    test fixture, a loader, a future code path) bypassed any such eager
    call site silently, with no error, just quietly empty timing data for
    a direct `dataset.acquisition_metadata.image_timings` reader, or a
    stale-looking-empty result for one that hadn't loaded yet.

    Detects "there's real, not-yet-compacted data" by checking
    `metadata.image_timings` truthiness - once compacted, that list is
    empty, so a *second* call is just a cache read (returns the existing
    `dataset.compact_image_timings`, does no work). If
    `dataset.acquisition_metadata` is later reassigned to a fresh object
    with its own real `image_timings` (e.g. a re-import), that reassignment
    naturally makes this function compact again on the next call, since the
    truthiness check is against the *current* `acquisition_metadata`, not a
    one-time flag.

    Why this exists: a large legacy CSV import can leave 8,500+ live
    `ImagingCubeTiming` pydantic instances referenced by
    `dataset.acquisition_metadata.image_timings` for the rest of a
    session. This correlates (not proven causally) with a PyQt6-sip
    native crash-on-close - see
    `apps/LSPRi/eva/docs/lspri_pyqt6_sip_crash_on_close.md` (or the
    equivalent incident doc on `develop`/`main`). Dropping the reference
    here lets CPython's refcounting free those objects promptly instead of
    holding them until app close, on the chance that's what the sip
    binding layer's teardown code trips over. `CompactImageTimings`
    preserves every reader's actual need (per-frame/per-cube lookups,
    counts) without holding onto the heavier form -
    `rehydrated_acquisition_metadata` rebuilds a fully-populated,
    export-ready metadata object on demand for the few call sites that
    still need one. Uses `model_copy` rather than mutating `image_timings`
    in place, so a background thread concurrently holding the same
    `acquisition_metadata` reference (e.g. an in-flight OME-Zarr export)
    never observes a half-updated object - export call sites use their own
    local `metadata`/`result.metadata` variable for the actual
    `model_dump()` call, never `dataset.acquisition_metadata` at that
    point, so they're unaffected regardless of exactly when compaction
    happens relative to them.
    """
    metadata = dataset.acquisition_metadata
    if metadata is not None and metadata.image_timings:
        dataset.compact_image_timings = CompactImageTimings.from_timings(metadata.image_timings)
        dataset.acquisition_metadata = metadata.model_copy(update={"image_timings": []})
    if dataset.compact_image_timings is not None:
        return dataset.compact_image_timings
    return CompactImageTimings({}, {}, {})


def rehydrated_acquisition_metadata(dataset: ImageDataset) -> ImagingAcquisitionMetadata | None:
    """`dataset.acquisition_metadata` with `image_timings` repopulated from
    `dataset.compact_image_timings` (if `compact_dataset_image_timings` has
    already emptied it) - for the handful of call sites that still need a
    fully-populated `ImagingAcquisitionMetadata` to serialize (sidecar
    JSON export, OME-Zarr `.zattrs` embed). Returns a new object via
    `model_copy` rather than mutating the live one, so the rebuilt list is
    never stored back onto the dataset - it exists only for the caller's
    own `model_dump()` call and is garbage the moment that returns."""
    metadata = dataset.acquisition_metadata
    if metadata is None:
        return None
    if dataset.compact_image_timings is None or metadata.image_timings:
        return metadata
    return metadata.model_copy(update={"image_timings": dataset.compact_image_timings.to_timings()})
