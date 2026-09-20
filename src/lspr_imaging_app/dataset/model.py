"""Dataset dataclasses (sketch §7 "Dataset", §10).

Placeholder shapes only - when this module is actually built, these should
mirror whatever the current app's ``io/dataset.py`` / ``domain/models.py``
already carry (the feature inventory found that layer clean, so this is
expected to be a near-verbatim port, not a redesign). Field lists below are
not final.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AcquisitionMetadata:
    """Per-dataset acquisition metadata (pump plan, timestamps, ...). TODO: port from
    the current app's dataset/session loading once this module is built."""


@dataclass(frozen=True)
class ImageRecord:
    """One (spectral cube index, wavelength) image reference. TODO: port fields."""

    cube_index: int
    wavelength: float


@dataclass(frozen=True)
class ImageDataset:
    """A loaded dataset: source path plus everything needed to resolve an
    :class:`ImageRecord` to pixel data. TODO: port fields from ``io/dataset.py``."""

    source_path: Path
