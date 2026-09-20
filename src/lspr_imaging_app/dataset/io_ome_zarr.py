"""OME-Zarr loading/export (sketch §10: "ports current io/dataset.py's
OME-Zarr loading/export, largely as-is").

Not yet ported. Current implementation lives in
``lspr_imaging_app/io/dataset.py`` on the ``develop``/``main`` branches.
Reminder (AGENTS.md non-negotiable invariant): OME-Zarr reads must never be
touched by a ``QThreadPool`` worker, even indirectly via a blocking wait -
use plain ``threading.Thread`` for anything that calls into this module from
a background thread.
"""

from __future__ import annotations

from pathlib import Path

from .model import ImageDataset


def load_ome_zarr(path: Path) -> ImageDataset:
    """Load an OME-Zarr dataset from ``path``. Not yet implemented -
    scaffolding only."""
    raise NotImplementedError


def export_ome_zarr(dataset: ImageDataset, path: Path) -> None:
    """Export ``dataset`` to OME-Zarr at ``path``. Not yet implemented -
    scaffolding only."""
    raise NotImplementedError
