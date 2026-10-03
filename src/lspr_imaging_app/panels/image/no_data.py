"""Pure helpers for showing pixels that have no value (NaN, e.g. the corners
a rotation creates). No Qt import.

Such a pixel is drawn as a neutral grey checker, never black and never a
colormap colour, so it cannot be mistaken for any data value. The image item
itself leaves NaN pixels transparent (pyqtgraph does that natively and also
computes its contrast levels from finite pixels only); the checker sits
underneath it.
"""

from __future__ import annotations

import numpy as np

_CHECKER_PX = 8
_LIGHT = 150
_DARK = 105
NO_DATA_TEXT = "no data"


def no_data_overlay_rgba(image: np.ndarray) -> np.ndarray | None:
    """`(H, W, 4)` uint8 checker, opaque exactly where `image` is not finite,
    transparent elsewhere. `None` when every pixel has a value (nothing to
    draw, nothing allocated)."""
    invalid = ~np.isfinite(image)
    if not invalid.any():
        return None
    rows, cols = np.indices(image.shape[:2])
    light = ((rows // _CHECKER_PX + cols // _CHECKER_PX) % 2) == 0
    grey = np.where(light, _LIGHT, _DARK).astype(np.uint8)
    rgba = np.empty(image.shape[:2] + (4,), dtype=np.uint8)
    rgba[..., 0] = rgba[..., 1] = rgba[..., 2] = grey
    rgba[..., 3] = np.where(invalid, 255, 0)
    return rgba


def format_pixel_value(value: float) -> str:
    """The displayed intensity, or "no data" for a pixel without a value."""
    if not np.isfinite(value):
        return NO_DATA_TEXT
    return f"{value:.1f}"
