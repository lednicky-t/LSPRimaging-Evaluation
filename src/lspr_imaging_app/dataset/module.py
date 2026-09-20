"""``DatasetModule`` - the Dataset stage owner (sketch §7 "Dataset", §10).

Owns the current :class:`~lspr_imaging_app.dataset.model.ImageDataset` and
acquisition metadata. Emits ``dataset_loaded``/``dataset_cleared``; exposes a
read-only query interface. No other module may read dataset state any other
way (AGENTS.md, "Module boundaries").
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from ..diagnostics import instrumented
from .model import AcquisitionMetadata, ImageDataset, ImageRecord


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
        Not yet implemented - scaffolding only."""
        raise NotImplementedError

    def wavelengths(self) -> tuple[float, ...]:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError

    def spectral_cubes(self) -> tuple[int, ...]:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError

    def acquisition_metadata(self) -> AcquisitionMetadata | None:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError

    # -- commands -----------------------------------------------------------

    @instrumented("DatasetModule.load_dataset")
    def load_dataset(self, dataset: ImageDataset) -> None:
        """Load ``dataset`` and emit :attr:`dataset_loaded`. Not yet
        implemented - scaffolding only."""
        raise NotImplementedError

    @instrumented("DatasetModule.clear_dataset")
    def clear_dataset(self) -> None:
        """Clear the current dataset and emit :attr:`dataset_cleared`. Not
        yet implemented - scaffolding only."""
        raise NotImplementedError
