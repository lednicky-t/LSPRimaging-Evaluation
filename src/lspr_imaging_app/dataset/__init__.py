"""Dataset module (docs/rewrite_architecture_sketch_2026-09.md §7, §10).

Owns ``ImageDataset``, acquisition metadata, format load/convert. Per the
feature inventory this layer is already clean in the current app
(``io/dataset.py``) - ported largely as-is into ``io.py``.

**Correction to sketch §10 (2026-09-20)**: the sketch proposed splitting
this into ``io_tiff.py``/``io_ome_zarr.py``. The real file doesn't support
that split - format-agnostic dispatch, caching, and discovery code
(``dataset_load_plane_roi``, ``dataset_get_record``, ``discover_dataset_
candidates``, ``load_image_array``, ...) is mixed throughout rather than
separable per format, and one function (``dataset_load_plane_roi``) branches
between a scoped zarr-chunk read and a full-TIFF-load-then-crop internally
for a real performance reason. Kept as one ``io.py``, matching the file's
actual shape; format-specific internals (``_load_tiff_stack_dataset``,
``load_ome_zarr_dataset``, etc.) stay private functions in that one file.
"""

from .module import DatasetModule

__all__ = ["DatasetModule"]
