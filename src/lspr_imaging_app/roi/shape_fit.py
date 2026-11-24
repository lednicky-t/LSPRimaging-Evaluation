"""Fit a circle or a rectangle to one blob of pixels (2026-11-24; the right-click menu's "Circle / Rectangle fitted to selection"). Pure: numpy and scipy only.

A blob is a bool plane with one 8-connected region (`mask_regions.EIGHT_CONNECTED`). Coordinates are **pixel-index** coordinates (pixel ``(row, col)`` is the
point ``(col, row)``; a pixel covers ``col - 0.5 .. col + 0.5``), the space ROI centres are stored in.

* **Circle**: the centre is the mean of the blob's pixel indices; the diameter is the one of the circle with **the same area** (``2 sqrt(A / pi)``). So the circle
  holds about as many pixels as the blob does, and a round blob is matched at its edge. (A circle that encloses the blob would be bigger and would take in
  background around any blob that is not round.)
* **Rectangle**: the **smallest-area rectangle that contains the blob** (every pixel's whole square, so a blob that is itself a rectangle is matched exactly). It
  is found from the convex hull: the smallest box has a side along one of the hull's edges. The angle follows `roi_shapes.Rectangle` (the rectangle's own x axis
  points along ``(cos a, sin a)``; the image y axis points down, so a positive angle turns clockwise on the screen) and is given in ``(-45, 45]`` degrees, the
  longer or shorter side being the width as the turn needs, so a blob that is not turned gives angle 0.

Neither fit says anything about the rim; the caller gives the ROI the default gap and the equal-area thickness (`rim_area.equal_area_thickness`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.spatial import ConvexHull, QhullError

from ..domain.roi_shapes import Circle, Rectangle
from .mask_regions import blob_count

MIN_SIDE_PX = 2.0
"""The smallest side or diameter a fitted shape gets (the toolbox's `MIN_SAMPLE_DIAMETER_PX`): a one-pixel speck still gives a ROI that can be edited."""


@dataclass(frozen=True, slots=True)
class FittedShape:
    """``shape`` with its centre ``(center_x, center_y)`` in pixel-index coordinates, ``blob_pixels`` the blob it was fitted to."""

    shape: Circle | Rectangle
    center_x: float
    center_y: float
    blob_pixels: int


def has_single_blob(plane: np.ndarray) -> bool:
    """The ``True`` pixels of ``plane`` form exactly one blob."""
    pixels = np.asarray(plane, dtype=bool)
    return pixels.ndim == 2 and bool(pixels.any()) and blob_count(pixels) == 1


def _pixel_indices(plane: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    pixels = np.asarray(plane, dtype=bool)
    if pixels.ndim != 2 or not pixels.any():
        raise ValueError("there is no selected pixel to fit a shape to.")
    if blob_count(pixels) != 1:
        raise ValueError("the selection is not one blob: fitting needs exactly one separate blob.")
    rows, cols = np.nonzero(pixels)
    return cols.astype(np.float64), rows.astype(np.float64)


def fit_circle(plane: np.ndarray) -> FittedShape:
    """The circle with the blob's centre of mass and the blob's area. Raises ``ValueError`` unless the plane holds exactly one blob."""
    xs, ys = _pixel_indices(plane)
    diameter = max(2.0 * math.sqrt(xs.size / math.pi), MIN_SIDE_PX)
    return FittedShape(Circle(float(diameter)), float(xs.mean()), float(ys.mean()), int(xs.size))


def fit_rectangle(plane: np.ndarray) -> FittedShape:
    """The smallest rectangle (any angle) that contains every pixel square of the blob. Raises ``ValueError`` unless the plane holds exactly one blob."""
    xs, ys = _pixel_indices(plane)
    corners = np.concatenate(
        [np.column_stack((xs + dx, ys + dy)) for dx in (-0.5, 0.5) for dy in (-0.5, 0.5)]
    )
    try:
        hull = corners[ConvexHull(corners).vertices]
    except QhullError:  # a one-pixel-wide row or column: the hull has no area, the box is the pixels' own
        hull = corners
    edges = np.roll(hull, -1, axis=0) - hull
    angles = np.unique(np.round(np.arctan2(edges[:, 1], edges[:, 0]) % (math.pi / 2.0), 12))  # a box repeats every 90 degrees
    best: tuple[float, float, float, float, float, float] | None = None  # area, angle, width, height, centre u, centre v
    for angle in angles:
        c, s = math.cos(angle), math.sin(angle)
        u = hull[:, 0] * c + hull[:, 1] * s  # along the candidate's x axis
        v = -hull[:, 0] * s + hull[:, 1] * c  # along its y axis
        width, height = float(u.max() - u.min()), float(v.max() - v.min())
        area = width * height
        if best is None or area < best[0] - 1e-9:
            best = (area, float(angle), width, height, float((u.max() + u.min()) / 2.0), float((v.max() + v.min()) / 2.0))
    assert best is not None  # (a hull always has an edge)
    _area, angle, width, height, centre_u, centre_v = best
    c, s = math.cos(angle), math.sin(angle)
    centre_x, centre_y = centre_u * c - centre_v * s, centre_u * s + centre_v * c
    degrees = math.degrees(angle)
    if degrees > 45.0:  # the same box, its axes swapped: keep the turn within (-45, 45]
        degrees -= 90.0
        width, height = height, width
    if abs(degrees) < 1e-6:
        degrees = 0.0
    return FittedShape(Rectangle(max(width, MIN_SIDE_PX), max(height, MIN_SIDE_PX), float(degrees)), float(centre_x), float(centre_y), int(xs.size))
