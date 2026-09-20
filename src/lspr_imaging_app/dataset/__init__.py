"""Dataset module (docs/rewrite_architecture_sketch_2026-09.md §7, §10).

Owns ``ImageDataset``, acquisition metadata, format load/convert. Per the
feature inventory this layer is already clean in the current app
(``io/dataset.py``) - ``io_tiff.py``/``io_ome_zarr.py`` are meant to port it
largely as-is, not redesign it.
"""

from .module import DatasetModule

__all__ = ["DatasetModule"]
