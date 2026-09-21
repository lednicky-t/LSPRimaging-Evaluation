"""Pure mask-file I/O: read/write a mask as a black-and-white raster image
(PNG/BMP/TIFF), ported from the old app's `MaskController.read_mask_image`/
`save_mask_to_file` (`gui/mask_controller.py`, `develop`/`main`).

Deliberately scoped to just the read/write logic, not the interactive file
picker (`QFileDialog`) that chooses `path` in the old app - that stays
panel-layer work, same as the async candidate-computation dispatch flagged
in `mask/module.py`'s own docstring (both are UI/orchestration concerns,
not something this pure-math/IO layer should own). A future panel command
calls these two functions with an already-chosen path.

No Qt import allowed in this file (AGENTS.md testing rule).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image


def read_mask_image(path: Path, expected_shape: tuple[int, int]) -> np.ndarray:
    """Read a mask image file as a boolean array - a pixel is masked
    (`True`) if its grayscale value is `>= 128`, matching the 0/255
    black-and-white encoding `write_mask_image` writes.

    Raises `ValueError` if the file's pixel dimensions don't match
    `expected_shape` (`(height, width)`, the same convention as
    `np.ndarray.shape`) - this app's masks are pixel-registered to one
    specific raw image, so a size mismatch means the file belongs to a
    different dataset/image, not something to silently resize."""
    with Image.open(path) as image:
        mask = np.array(image.convert("L"), dtype=np.uint8)
    if mask.shape != tuple(expected_shape):
        raise ValueError(
            f"Mask size {mask.shape[1]} x {mask.shape[0]} px does not match the current image "
            f"{expected_shape[1]} x {expected_shape[0]} px."
        )
    return mask >= 128


def write_mask_image(mask: np.ndarray, path: Path) -> None:
    """Write `mask` as an 8-bit grayscale image - `True` becomes 255
    (white, masked/excluded), `False` becomes 0 (black, kept). Creates
    `path`'s parent directory if it doesn't exist yet (masks live under a
    dataset's `analysis/masks/` folder, which may not exist until the first
    save - see the old app's `_resolve_sidecar_path`).

    The 0/255 encoding (not 0/1) matters: `read_mask_image` thresholds at
    `>= 128`, so this must stay consistent with that, not an arbitrary
    boolean-to-int cast."""
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = np.where(np.asarray(mask, dtype=bool), 255, 0).astype(np.uint8)
    Image.fromarray(encoded, mode="L").save(path)
