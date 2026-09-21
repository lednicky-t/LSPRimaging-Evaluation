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

from lspr_core import ImagingAcquisitionMetadata
from PyQt6.QtCore import QObject, pyqtSignal

from ..diagnostics import instrumented
from .io import dataset_get_record
from .model import ImageDataset, ImageRecord


class DatasetModule(QObject):
    """Owns dataset load/convert and acquisition metadata."""

    dataset_loaded = pyqtSignal(object)  # ImageDataset
    dataset_cleared = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._dataset: ImageDataset | None = None

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

    def wavelengths(self) -> tuple[float, ...]:
        """Every distinct wavelength in the loaded dataset, sorted - an
        empty tuple (not an error) if no dataset is loaded, since unlike
        `current_image` there's no specific key being asked for that could
        be "missing"."""
        if self._dataset is None:
            return ()
        return tuple(self._dataset.wavelengths_nm)

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

    # -- commands -----------------------------------------------------------

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
