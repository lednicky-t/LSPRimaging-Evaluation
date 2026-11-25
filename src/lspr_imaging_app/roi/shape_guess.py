"""Which ROI shape describes a blob of pixels: a circle, a rectangle, or only the pixels themselves (pure: numpy, no Qt).

Used by the array finder when the guide is an area selection (`panels/image/array_find.py`): the blob is fitted as a circle and as a rectangle (`shape_fit`) and the
better of the two is taken **if it covers the blob well enough**, else the blob stays a pixel map. "Well enough" is the overlap (intersection over union of the
shape's pixels and the blob's); `DEFAULT_MIN_OVERLAP` = 0.85 (not higher: the fitted rectangle is the smallest one that holds every pixel square, so it overshoots a turned blob by about a pixel on each side, which alone costs ~10 % of the overlap on a 16 px box).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..domain.roi_shapes import MaskShape, Shape
from .mask_regions import mask_regions
from .shape_fit import fit_circle, fit_rectangle

__all__ = ["BlobGuess", "DEFAULT_MIN_OVERLAP", "guess_shape"]

DEFAULT_MIN_OVERLAP = 0.85


@dataclass(frozen=True, slots=True)
class BlobGuess:
    shape: Shape
    center_x: float
    center_y: float
    overlap: float
    """Intersection over union of the shape and the blob (1 = the same pixels); for a pixel map, 1."""
    kind: str
    """``"circle"``, ``"rectangle"`` or ``"mask"``."""


def guess_shape(blob: np.ndarray, *, min_overlap: float = DEFAULT_MIN_OVERLAP) -> BlobGuess:
    """The simplest shape that covers ``blob`` (a bool plane holding one blob) at least ``min_overlap``: the better of a circle and a rectangle, else its pixels.
    Raises ``ValueError`` unless the plane holds exactly one blob."""
    pixels = np.asarray(blob, dtype=bool)
    circle, rectangle = fit_circle(pixels), fit_rectangle(pixels)
    rows, cols = np.nonzero(pixels)
    y0, y1, x0, x1 = rows.min() - 3, rows.max() + 4, cols.min() - 3, cols.max() + 4
    yy, xx = np.mgrid[y0:y1, x0:x1]
    inside = np.zeros(yy.shape, dtype=bool)
    inside[rows - y0, cols - x0] = True
    candidates: list[tuple[float, BlobGuess]] = []
    r = circle.shape.diameter_px / 2.0
    disk = (xx - circle.center_x) ** 2 + (yy - circle.center_y) ** 2 <= r * r
    candidates.append((_overlap(disk, inside), BlobGuess(circle.shape, circle.center_x, circle.center_y, 0.0, "circle")))
    shape = rectangle.shape
    c, s = math.cos(math.radians(shape.angle_deg)), math.sin(math.radians(shape.angle_deg))
    u = (xx - rectangle.center_x) * c + (yy - rectangle.center_y) * s
    v = -(xx - rectangle.center_x) * s + (yy - rectangle.center_y) * c
    box = (np.abs(u) <= shape.width_px / 2.0) & (np.abs(v) <= shape.height_px / 2.0)
    candidates.append((_overlap(box, inside), BlobGuess(shape, rectangle.center_x, rectangle.center_y, 0.0, "rectangle")))
    best_overlap, best = max(candidates, key=lambda item: item[0])
    if best_overlap >= min_overlap:
        return BlobGuess(best.shape, best.center_x, best.center_y, best_overlap, best.kind)
    region = mask_regions(pixels, together=True)[0]
    return BlobGuess(MaskShape(region[0]), float(region[1]), float(region[2]), 1.0, "mask")


def _overlap(shape: np.ndarray, blob: np.ndarray) -> float:
    union = int((shape | blob).sum())
    return float((shape & blob).sum()) / union if union else 0.0
