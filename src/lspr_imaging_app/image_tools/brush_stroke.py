"""The pixels a brush stroke covers (2026-11-15; the selection brush, the Mask tab's old Draw tool moved into the selection toolbar).

Pure numpy, no Qt. A stroke is a polyline of view points (pixel ``(row, col)`` covers ``[col, col+1) x [row, row+1)``, so its centre
is ``(col + 0.5, row + 0.5)``) drawn with a round brush of a given diameter: the covered pixels are the ones whose **centre lies within
half the diameter of the polyline** (the exact union of the round stamps along it, so the speed of the mouse never changes the
result). A single point is one stamp (a click). The stroke is made once, when the drag ends, and combined into the selection as
one undo step; while it is dragged the canvas shows only its outline.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np


def stroke_region(points: Sequence[tuple[float, float]], diameter_px: float, shape: tuple[int, int]) -> np.ndarray:
    """Boolean ``shape`` array: ``True`` where a pixel's centre is within ``diameter_px / 2`` of the polyline through `points`.
    A point outside the image still paints the part of its stamp that reaches in. No points, or a diameter of zero or less, paint
    nothing."""
    height, width = int(shape[0]), int(shape[1])
    covered = np.zeros((height, width), dtype=bool)
    radius = float(diameter_px) / 2.0
    if len(points) == 0 or radius <= 0.0:
        return covered
    pts = np.asarray(points, dtype=float).reshape(-1, 2)
    segments = zip(pts[:-1], pts[1:]) if len(pts) > 1 else [(pts[0], pts[0])]
    for (x0, y0), (x1, y1) in segments:
        col0 = max(0, int(np.floor(min(x0, x1) - radius - 1.0)))
        col1 = min(width, int(np.ceil(max(x0, x1) + radius + 1.0)))
        row0 = max(0, int(np.floor(min(y0, y1) - radius - 1.0)))
        row1 = min(height, int(np.ceil(max(y0, y1) + radius + 1.0)))
        if col0 >= col1 or row0 >= row1:
            continue
        centre_x, centre_y = np.meshgrid(np.arange(col0, col1) + 0.5, np.arange(row0, row1) + 0.5)
        dx, dy = x1 - x0, y1 - y0
        length_sq = dx * dx + dy * dy
        if length_sq > 0.0:
            t = np.clip(((centre_x - x0) * dx + (centre_y - y0) * dy) / length_sq, 0.0, 1.0)
        else:
            t = np.zeros_like(centre_x)
        near_x = x0 + t * dx
        near_y = y0 + t * dy
        covered[row0:row1, col0:col1] |= (centre_x - near_x) ** 2 + (centre_y - near_y) ** 2 <= radius * radius
    return covered
