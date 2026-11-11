"""Pure helpers for showing pixels that have no value (NaN, e.g. the corners
a rotation creates). No Qt import.

Such a pixel is drawn as a neutral grey checker, never black and never a
colormap colour, so it cannot be mistaken for any data value. The image item
itself leaves NaN pixels transparent (pyqtgraph does that natively and also
computes its contrast levels from finite pixels only); the checker sits
underneath it.
"""

from __future__ import annotations

import threading

import cv2
import numpy as np

_CHECKER_PX = 8
_LIGHT = 150
_DARK = 105
NO_DATA_TEXT = "no data"


# Measured 2026-10-14 on the maintainer's data (900x1300, NaN borders from a rotation): building the
# checker for every frame cost ~33 ms of the GUI thread per frame during Play. The grey pattern depends
# only on the image shape, and the NaN mask is the same for every wavelength, so both are kept.
_grey_cache: dict[tuple[int, int], np.ndarray] = {}
_last: tuple[np.ndarray, np.ndarray] | None = None  # (invalid mask, rgba built from it)
_lock = threading.Lock()  # the render thread builds it during Play (display_picture.py), the GUI thread otherwise


def _grey_for(shape: tuple[int, int]) -> np.ndarray:
    grey = _grey_cache.get(shape)
    if grey is None:
        rows, cols = np.indices(shape)
        light = ((rows // _CHECKER_PX + cols // _CHECKER_PX) % 2) == 0
        grey = np.where(light, _LIGHT, _DARK).astype(np.uint8)
        _grey_cache.clear()  # one shape at a time is enough (a crop or rotation changes it for good)
        _grey_cache[shape] = grey
    return grey


def no_data_overlay_rgba(image: np.ndarray) -> np.ndarray | None:
    """`(H, W, 4)` uint8 checker, opaque exactly where `image` is not finite,
    transparent elsewhere. `None` when every pixel has a value (nothing to
    draw, nothing allocated). **The same array object is returned again while the
    invalid-pixel mask is unchanged**, so a caller can skip re-uploading it."""
    global _last
    invalid = ~np.isfinite(image)
    if not invalid.any():
        return None
    with _lock:
        if _last is not None and _last[0].shape == invalid.shape and np.array_equal(_last[0], invalid):
            return _last[1]
        grey = _grey_for(image.shape[:2])
        rgba = np.empty(image.shape[:2] + (4,), dtype=np.uint8)
        rgba[..., 0] = rgba[..., 1] = rgba[..., 2] = grey
        rgba[..., 3] = np.where(invalid, 255, 0)
        _last = (invalid, rgba)
        return rgba


_last_valid: tuple[np.ndarray, np.ndarray] | None = None  # (has-value mask, rgba built from it): `no_data_overlay_from_valid`


def no_data_overlay_from_valid(has_value: np.ndarray) -> np.ndarray:
    """The same checker as `no_data_overlay_rgba`, from the render thread's own `(H, W)` uint8 mask (255 where the pixel has a value,
    0 where it is NaN; at least one 0). One OpenCV call while the mask is unchanged (2026-11-11): during Play every native call on the
    render thread waits for the interpreter lock again, so the four numpy calls of `no_data_overlay_rgba` cost ~10 ms there.
    The same array object is returned again while the mask is unchanged. The caller must not change `has_value` afterwards."""
    global _last_valid
    with _lock:
        last = _last_valid
        if last is not None and last[0].shape == has_value.shape and cv2.norm(last[0], has_value, cv2.NORM_INF) == 0:
            return last[1]
        grey = _grey_for(has_value.shape)
        rgba = cv2.merge([grey, grey, grey, cv2.bitwise_not(has_value)])
        _last_valid = (has_value, rgba)
        return rgba


def format_pixel_value(value: float) -> str:
    """The displayed intensity, or "no data" for a pixel without a value."""
    if not np.isfinite(value):
        return NO_DATA_TEXT
    return f"{value:.0f}"
