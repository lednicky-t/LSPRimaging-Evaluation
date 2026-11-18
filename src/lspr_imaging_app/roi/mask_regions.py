"""Pixel regions of a selection, as the masks and centres of new ROIs (phase 3 of ``docs/roi_shapes_and_linking_plan_2026-11-18.md``). Pure: numpy and scipy only.

An area selection (lasso, wand, brush, rectangle) is a bool plane. A **pixel-mask ROI** (`roi_shapes.MaskShape`) is made from it either as **one region
holding every selected pixel** (``together=True``; the blobs need not touch) or as **one region per separate blob** (``together=False``; blobs that touch
by an edge or a corner are one blob). Each region becomes a cropped `RoiMask` in the plane's own pixel-index coordinates (the space ROI masks are stored in)
and a centre, the mean of its pixel indices, which is where the ROI is labelled and grouped.

The order of the blobs is the reading order of their first pixel (top to bottom, then left to right), so ROI ids follow what the eye sees.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from ..domain.roi_shapes import RoiMask

_EIGHT_CONNECTED = np.ones((3, 3), dtype=bool)


def mask_regions(plane: np.ndarray, *, together: bool) -> list[tuple[RoiMask, float, float]]:
    """``[(mask, centre_x, centre_y), ...]`` for the selected (``True``) pixels of ``plane``; ``[]`` for an empty plane. See the module docstring."""
    selected = np.asarray(plane, dtype=bool)
    if selected.ndim != 2 or not selected.any():
        return []
    if together:
        labels, count = selected.astype(np.uint8), 1
        slices = ndimage.find_objects(labels)
    else:
        labels, count = ndimage.label(selected, structure=_EIGHT_CONNECTED)
        slices = ndimage.find_objects(labels)
    regions: list[tuple[RoiMask, float, float]] = []
    for number in range(1, count + 1):
        window = slices[number - 1]
        if window is None:
            continue
        pixels = (labels[window] == number) if not together else selected[window]
        rows, cols = np.nonzero(pixels)
        y0, x0 = window[0].start, window[1].start
        regions.append((RoiMask(int(x0), int(y0), np.ascontiguousarray(pixels, dtype=bool)), float(x0 + cols.mean()), float(y0 + rows.mean())))
    # reading order of the first pixel of each blob: top to bottom, then left to right (label() numbers them in that order already)
    return regions
