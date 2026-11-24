"""The rim thickness that gives a rim the same area as its sample ROI (2026-11-24, maintainer's request: "change rim width to have the same area as the sample ROI").

Pure geometry, no Qt, no image: the rim keeps its **offset** (gap from the shape's edge, `Border.gap_px`) and only its **thickness** is solved, so that

    area(rim) = area(sample)        (px^2, the shape's own area: `roi_shapes.shape_area_px`)

* **Circle** (radius R, inner rim radius r = max(R + gap, 0)): the ring's area is pi (outer^2 - r^2), so outer = sqrt(R^2 + r^2). Exact.
* **Rectangle** (w x h): the pixels within distance s of the rectangle have the area ``wh + 2(w + h) s + pi s^2`` for s >= 0 (outer corners rounded, as
  `rasterize._signed_distance_rectangle` has them). The rim holds ``A(gap + t) - A(gap)``; setting that to ``wh`` gives a quadratic in s = gap + t. Exact
  (for a rim that reaches the sample's edge, i.e. gap + t >= 0, which is always the case here: the rim's area is at least ``wh``).
* **Pixel map**: the rim is counted in pixels the way the scan finds them (`rasterize._mask_band`: every blob has a rim of its own, a pixel of another blob is never
  in it), and the thickness is searched until the count is as near the map's pixel count as it can get. A pixel count is a staircase in the thickness, so the nearest
  count is taken (it can differ from the sample's by a few pixels, and for a one-pixel-wide shape by a lot: the count jumps from 0 to twice its own, and an
  empty rim is never chosen), and the thickness is rounded to 0.01 px where that does not change the count.

The area is the shape's, **not clipped by the image edge** (a rim that runs off the image has fewer measured pixels) and **not reduced by the pixels of neighbouring ROIs**; a
chromatic stretch of a few tenths of a percent at other wavelengths changes the pixel counts of sample and rim alike.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import ndimage

from ..domain.roi_shapes import Circle, MaskShape, Rectangle, Shape, shape_area_px
from .mask_regions import EIGHT_CONNECTED

_SEARCH_STEPS = 30
"""Halvings of the thickness search for a pixel map: 2 px / 2^30, far below the 0.01 px the result is rounded to."""
_ROUND_PX = 0.01


def equal_area_thickness(shape: Shape, gap_px: float) -> float:
    """The rim thickness (px, >= 0) whose rim, ``gap_px`` from the edge of ``shape``, has the same area as ``shape``. Raises ``ValueError`` for an empty or degenerate shape
    or a gap that is not finite."""
    gap = float(gap_px)
    if not math.isfinite(gap):
        raise ValueError(f"the rim's offset must be a finite number, got {gap_px!r}")
    if isinstance(shape, Circle):
        radius = float(shape.diameter_px) / 2.0
        if not radius > 0.0:
            raise ValueError("the ROI has no area.")
        inner = max(radius + gap, 0.0)  # as `roi_shapes.ring_radii`
        return math.sqrt(radius * radius + inner * inner) - inner
    if isinstance(shape, Rectangle):
        width, height = float(shape.width_px), float(shape.height_px)
        if not (width > 0.0 and height > 0.0):
            raise ValueError("the ROI has no area.")
        # points within distance s of the rectangle: A(s) = wh + 2(w + h) s + pi s^2 (s >= 0); A(gap + t) - A(gap) = wh
        inside = _rectangle_area_within(width, height, gap)
        target = inside + width * height
        edge = (-(width + height) + math.sqrt((width + height) ** 2 + math.pi * (target - width * height))) / math.pi
        return max(edge - gap, 0.0)
    if isinstance(shape, MaskShape):
        return _mask_equal_area_thickness(shape, gap)
    raise TypeError(f"unknown shape {type(shape).__name__}")


def _rectangle_area_within(width: float, height: float, s: float) -> float:
    """The area of the points whose signed distance from the rectangle's edge is at most ``s`` (outwards positive): the shrunk rectangle for s < 0, the rounded-corner one for s >= 0."""
    if s >= 0.0:
        return width * height + 2.0 * (width + height) * s + math.pi * s * s
    shrunk_w, shrunk_h = width + 2.0 * s, height + 2.0 * s
    return shrunk_w * shrunk_h if shrunk_w > 0.0 and shrunk_h > 0.0 else 0.0


def _mask_equal_area_thickness(shape: MaskShape, gap: float) -> float:
    wanted = shape_area_px(shape)
    if not wanted > 0.0:
        raise ValueError("the ROI has no pixels.")
    count_at = _mask_rim_counter(shape, gap, upper_px=abs(gap) + math.sqrt(wanted) + 2.0)
    low, high = 0.0, abs(gap) + math.sqrt(wanted) + 2.0
    if count_at(high) < wanted:
        raise ValueError("no rim thickness reaches the area of the ROI.")
    if count_at(low) >= wanted:
        return 0.0
    for _ in range(_SEARCH_STEPS):  # count(low) < wanted <= count(high); the count only grows with the thickness
        middle = (low + high) / 2.0
        if count_at(middle) >= wanted:
            high = middle
        else:
            low = middle
    low_count, high_count = count_at(low), count_at(high)
    if low_count == 0 or abs(high_count - wanted) <= abs(low_count - wanted):  # (never an empty rim: a thin shape's count can jump from 0 to twice its own)
        keep, count, rounded = high, high_count, math.ceil(high / _ROUND_PX) * _ROUND_PX
    else:
        keep, count, rounded = low, low_count, math.floor(low / _ROUND_PX) * _ROUND_PX
    return rounded if count_at(rounded) == count else keep


def _mask_rim_counter(shape: MaskShape, gap: float, *, upper_px: float):
    """``count(thickness)``: how many pixels the rim of the pixel map has at that thickness (the pixels `rasterize._mask_band` finds, counted without its cache, since a search
    asks for dozens of thicknesses that nothing else will ever ask for). The distance maps are made once."""
    pad = int(math.ceil(max(gap + upper_px, 0.0))) + 2
    padded = np.pad(np.ascontiguousarray(shape.mask.mask, dtype=bool), pad, constant_values=False)
    labels, _count = ndimage.label(padded, structure=EIGHT_CONNECTED)
    blobs = []
    for number, window in enumerate(ndimage.find_objects(labels), start=1):
        if window is None:
            continue
        rows = slice(max(window[0].start - pad, 0), min(window[0].stop + pad, padded.shape[0]))
        cols = slice(max(window[1].start - pad, 0), min(window[1].stop + pad, padded.shape[1]))
        blob = labels[rows, cols] == number
        sdf = np.where(blob, 0.5 - ndimage.distance_transform_edt(blob), ndimage.distance_transform_edt(~blob) - 0.5)
        blobs.append((rows, cols, sdf, padded[rows, cols] & ~blob))  # (the pixels of other blobs are never this blob's rim)

    def count_at(thickness: float) -> int:
        band = np.zeros(padded.shape, dtype=bool)
        for rows, cols, sdf, foreign in blobs:
            band[rows, cols] |= (sdf >= gap) & (sdf <= gap + thickness) & ~foreign
        return int(np.count_nonzero(band))

    return count_at
