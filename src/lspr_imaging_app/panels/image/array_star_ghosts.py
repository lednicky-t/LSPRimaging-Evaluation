"""Outlines of the copies the Array* pop-out would make, as the canvas draws them (pure numpy, no Qt).

One closed outline per copy, in **view coordinates** of the frame on screen: the copy's stored centre (reference frame, pixel-index) goes through the frame's
chromatic affine and gets the half-cell shift, exactly as `RoiQueries.view_centers` does for real ROIs; circles and rectangles are bent by the affine's linear
part (`roi.overlay_geometry`). A circle and a rectangle are drawn as they are; a pixel map is drawn as a circle of its nominal diameter (a preview, not its pixels).
"""

from __future__ import annotations

import numpy as np

from ...domain.roi_shapes import Circle, Rectangle, nominal_diameter
from ...image_tools.chromatic.affine import apply_affine_to_points
from ...roi.array_layout import ArrayLayout
from ...roi.clipboard import RoiClip
from ...roi.overlay_geometry import CELL_OFFSET, DEFAULT_CIRCLE_POINTS, circle_points, rectangle_points


OUTLINE_FLOOR = 0.1
"""The faintest outline (the wedge at its left end): still there, so the settings can be judged."""
DEFAULT_FILL = 0.25
"""The fill's opacity with the wedge in the middle."""


def preview_alphas(position: float) -> tuple[float, float]:
    """``(outline, fill)`` opacity of the preview spots for the wedge at ``position`` (0 - 1; the middle, 0.5, is the usual look).

    Left of the middle everything fades: the outline from `OUTLINE_FLOOR` up to full, the fill with it (a quarter of the outline). Right of the middle the
    outline stays full and the fill grows from `DEFAULT_FILL` to solid, so the spots become more and more opaque."""
    t = min(max(float(position), 0.0), 1.0)
    if t <= 0.5:
        outline = OUTLINE_FLOOR + (1.0 - OUTLINE_FLOOR) * (t / 0.5)
        return outline, DEFAULT_FILL * outline
    return 1.0, DEFAULT_FILL + (1.0 - DEFAULT_FILL) * ((t - 0.5) / 0.5)


def ghost_outlines(clip: RoiClip, layout: ArrayLayout, affine: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """``(xs, ys)``, each ``(copies, DEFAULT_CIRCLE_POINTS)``: the outlines of every copy the layout would add (the seed node is left out), node by node, each
    node in the clip's order. Empty arrays when there is nothing to add."""
    members = len(clip)
    new_xy = layout.new_member_xy().reshape(-1, members, 2)
    if new_xy.shape[0] == 0:
        return np.empty((0, DEFAULT_CIRCLE_POINTS)), np.empty((0, DEFAULT_CIRCLE_POINTS))
    matrix = np.asarray(affine, dtype=np.float64)
    nodes = new_xy.shape[0]
    xs = np.empty((nodes, members, DEFAULT_CIRCLE_POINTS))
    ys = np.empty_like(xs)
    for index, item in enumerate(clip.rois):
        centers = apply_affine_to_points(new_xy[:, index, :], matrix) + CELL_OFFSET
        shape = item.geometry.shape
        if isinstance(shape, Rectangle):
            size = np.full(nodes, 1.0)
            x, y = rectangle_points(centers, size * shape.width_px, size * shape.height_px, size * shape.angle_deg, matrix)
        else:
            diameter = shape.diameter_px if isinstance(shape, Circle) else nominal_diameter(shape, item.geometry.center_x, item.geometry.center_y)
            x, y = circle_points(centers, np.full(nodes, float(diameter)), matrix)
        xs[:, index, :], ys[:, index, :] = x, y
    return xs.reshape(-1, DEFAULT_CIRCLE_POINTS), ys.reshape(-1, DEFAULT_CIRCLE_POINTS)
