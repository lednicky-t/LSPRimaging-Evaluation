"""Outlines of what the array finder shows on the canvas (pure numpy, no Qt): the ROIs it would make, the empty nodes, the similar spots that are off the lattice.

Like `array_ghosts`: stored centres (reference frame, pixel-index) go through the chromatic affine of the frame on screen and get the half-cell shift
(`roi.overlay_geometry`). A circle is drawn as a circle of its diameter, a rectangle copy as the rectangle (the seed's, scaled), a pixel map as a circle of its nominal
diameter (a preview, not its pixels).
"""

from __future__ import annotations

import numpy as np

from ...domain.roi_shapes import Rectangle, Shape
from ...image_tools.chromatic.affine import apply_affine_to_points
from ...roi.array_finder import FinderResult
from ...roi.overlay_geometry import CELL_OFFSET, DEFAULT_CIRCLE_POINTS, circle_points, rectangle_points

MADE, EMPTY, STRAY = "made", "empty", "stray"
"""What an outline stands for: a ROI Apply would make, a node of the array where no spot was found, a similar spot that is not on the lattice."""


def found_outlines(result: FinderResult, template: Shape | None, seed_diameter: float, affine: np.ndarray) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """``(xs, ys, kinds)``: ``(N, DEFAULT_CIRCLE_POINTS)`` closed outlines and, per outline, what it stands for. The seeds themselves are not drawn (they are real ROIs)."""
    matrix = np.asarray(affine, dtype=np.float64)
    groups: list[tuple[np.ndarray, np.ndarray, str]] = []
    new = ~result.is_seed
    groups.append((result.member_xy[new], result.member_diameter[new], MADE))
    if len(result.missing_xy):
        reference = float(np.median(result.member_diameter)) if result.count else 0.0
        groups.append((result.missing_xy, np.full(len(result.missing_xy), reference), EMPTY))
    if len(result.stray_xy):
        groups.append((result.stray_xy, result.stray_diameter, STRAY))
    xs_all, ys_all, kinds = [], [], []
    for xy, diameter, kind in groups:
        if len(xy) == 0:
            continue
        centres = apply_affine_to_points(np.asarray(xy, dtype=np.float64), matrix) + CELL_OFFSET
        if isinstance(template, Rectangle) and kind == MADE and seed_diameter > 0:
            ratio = diameter / seed_diameter
            xs, ys = rectangle_points(centres, template.width_px * ratio, template.height_px * ratio, np.full(len(xy), template.angle_deg), matrix)
        else:
            xs, ys = circle_points(centres, np.asarray(diameter, dtype=np.float64), matrix)
        xs_all.append(xs)
        ys_all.append(ys)
        kinds += [kind] * len(xy)
    if not xs_all:
        return np.empty((0, DEFAULT_CIRCLE_POINTS)), np.empty((0, DEFAULT_CIRCLE_POINTS)), []
    return np.vstack(xs_all), np.vstack(ys_all), kinds
