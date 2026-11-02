"""Which pixels ROI detection and the background estimate must not use.

Moved here from `roi/detection.py` on 2026-11-02 (layering: the background estimate, in `image_tools`, needs it and `roi` sits above
`image_tools`). It reads one field of the detection settings and nothing else, so it sits next to the ROI data classes.
"""

from __future__ import annotations

import numpy as np

from .roi_model import AreaRoiDetectionSettings


def ignored_pixel_mask(
    image: np.ndarray,
    settings: AreaRoiDetectionSettings,
    external_mask: np.ndarray | None = None,
) -> np.ndarray:
    """Pixels detection and the background estimate must not use.

    Pixels with no value (non-finite, e.g. NaN created by rotation) are
    always excluded, regardless of `ignore_marked_pixels` - there is no
    scenario where counting them is correct. `external_mask` (the user's
    painted/histogram/relative/local-contrast ignore mask) is a selection
    only and counts when `ignore_marked_pixels` is on - an explicit, tested
    behavior (see TestDetectRoisIgnoreMask). Neither changes `image`.
    """
    image_shape = image.shape[:2]
    combined_mask = ~np.isfinite(image) if image.ndim == 2 else np.zeros(image_shape, dtype=bool)
    if image.size == 0:
        return combined_mask
    if settings.ignore_marked_pixels:
        combined_mask = combined_mask | _normalized_external_mask(image_shape, external_mask)
    return combined_mask


def _normalized_external_mask(
    image_shape: tuple[int, int],
    external_mask: np.ndarray | None,
) -> np.ndarray:
    if external_mask is None:
        return np.zeros(image_shape, dtype=bool)
    mask = np.asarray(external_mask, dtype=bool)
    if mask.shape != image_shape:
        return np.zeros(image_shape, dtype=bool)
    return mask
