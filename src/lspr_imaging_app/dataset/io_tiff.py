"""TIFF-stack loading (sketch §10: "ports current io/dataset.py's TIFF-stack
loading, largely as-is").

Not yet ported. Current implementation lives in
``lspr_imaging_app/io/dataset.py`` on the ``develop``/``main`` branches.
"""

from __future__ import annotations

from pathlib import Path

from .model import ImageDataset


def load_tiff_stack(path: Path) -> ImageDataset:
    """Load a TIFF-stack dataset from ``path``. Not yet implemented -
    scaffolding only."""
    raise NotImplementedError
