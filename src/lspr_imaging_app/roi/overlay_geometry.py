"""Qt-free geometry for drawing ROI outlines on an image (2026-10-07).

Why this is its own module: the Image panel used to build each ROI's circle
one at a time (about 0.27 ms per ROI, so 0.5 s per redraw at 2000 ROIs, on
the GUI thread) and, in doing so, put the chromatic affine on the circle
**twice** - once inside ``RoiToolbox.display_position()`` (which already
returns the transformed centre) and again inside ``transformed_circle_points``
(which transforms the circle around whatever centre it is given). The drawn
circle then sat off the measured region by roughly the affine's translation.

The rule here is the one the measurement itself follows: a ROI is a circle in
the reference frame, and the measured region at another wavelength is that
circle pushed through the wavelength's affine ``A``. Pushing the circle
``c + r*u`` through ``A`` gives ``A(c) + r * L @ u`` where ``L`` is the affine's
linear part (2x2). So the outline is **the display centre plus the circle's
shape bent by ``L``** - the translation is applied once, in the centre only.
A manual per-wavelength nudge only changes the centre, and works unchanged.
"""

from __future__ import annotations

import numpy as np

DEFAULT_CIRCLE_POINTS = 48
"""Vertices per drawn circle (closed: the last point repeats the first)."""

CELL_OFFSET = 0.5
"""A pixel's index is the middle of its cell in the view (the image's pixel ``(row, col)`` covers ``[col, col + 1) x [row, row + 1)``): the shift between
the **pixel-index coordinates** everything stored and measured is in, and the view's.

Two coordinate systems meet here, and this constant is the whole difference between them:

* *Pixel-index coordinates*: pixel ``(row, col)`` is the point ``(col, row)``. The scan (`roi/rasterize.py`, `analysis/frame_reduce.py`), the detectors,
  a ROI's stored centre (the ROI table's X / Y) and a mask's pixels are in it, and so is `RoiToolbox.display_positions` (the stored centre carried through
  the frame's affine). A ROI at X = 100, Y = 80 measures the pixels whose index is within the radius of (100, 80): pixel (80, 100) is its middle.
* *View coordinates*: what the pointer reports and what the canvas draws in; pixel ``(row, col)`` is the cell ``[col, col + 1)``, its middle at ``col + 0.5``.

Every outline drawn from a ROI centre is therefore drawn around ``display_position + CELL_OFFSET`` and every pointer position is turned into a stored
one by subtracting it first (`panels/image/roi_queries.RoiQueries.view_centers` / `display_to_reference`). Until 2026-11-18 circles and rectangles were drawn
around the stored centre as it was, half a pixel up and to the left of the pixels they measure (found while drawing masks, which need this shift
to outline their own cells: `panels/image/shape_paths_item.py`, `roi/shape_hits.py`). The shift is applied after the affine, never inside it: the affine maps
pixel-index coordinates to pixel-index coordinates. No stored number or scan result depends on it."""


def circle_points(
    display_centers_xy: np.ndarray,
    diameters_px: np.ndarray,
    affine_matrix: np.ndarray,
    *,
    n_points: int = DEFAULT_CIRCLE_POINTS,
) -> tuple[np.ndarray, np.ndarray]:
    """The vertices of many circles as two (N, n_points) arrays ``(xs, ys)``, one row per circle,
    each row closed (last vertex = first). Same geometry as `circle_outlines` (which is these
    rows joined with NaN separators), for callers that paint circles one by one."""
    centers = np.asarray(display_centers_xy, dtype=np.float64).reshape(-1, 2)
    radii = np.asarray(diameters_px, dtype=np.float64).reshape(-1) / 2.0
    if centers.shape[0] == 0:
        return np.empty((0, n_points)), np.empty((0, n_points))
    theta = np.linspace(0.0, 2.0 * np.pi, n_points, endpoint=True)
    unit = np.column_stack((np.cos(theta), np.sin(theta)))
    shape = unit @ np.asarray(affine_matrix, dtype=np.float64)[:, :2].T  # the circle's shape under the affine
    return (
        centers[:, 0:1] + radii[:, None] * shape[None, :, 0],
        centers[:, 1:2] + radii[:, None] * shape[None, :, 1],
    )


def circle_outlines(
    display_centers_xy: np.ndarray,
    diameters_px: np.ndarray,
    affine_matrix: np.ndarray,
    *,
    n_points: int = DEFAULT_CIRCLE_POINTS,
) -> tuple[np.ndarray, np.ndarray]:
    """Outlines of many circles as two flat arrays ``(xs, ys)``, circles
    separated by one NaN (so a single ``PlotDataItem(connect="finite")`` draws
    them all as disjoint curves; no NaN after the last).

    ``display_centers_xy`` is (N, 2): where each circle sits **in the displayed
    frame** (``RoiToolbox.display_positions``), not its reference-frame
    centre. ``diameters_px`` is (N,) in reference-frame pixels. Only the linear
    part of ``affine_matrix`` is applied, to each circle's offsets from its
    centre (see the module docstring). Empty input returns two empty arrays."""
    centers = np.asarray(display_centers_xy, dtype=np.float64).reshape(-1, 2)
    radii = np.asarray(diameters_px, dtype=np.float64).reshape(-1) / 2.0
    count = centers.shape[0]
    if count == 0:
        return np.empty(0), np.empty(0)
    theta = np.linspace(0.0, 2.0 * np.pi, n_points, endpoint=True)
    unit = np.column_stack((np.cos(theta), np.sin(theta)))
    shape = unit @ np.asarray(affine_matrix, dtype=np.float64)[:, :2].T  # the circle's shape under the affine
    outlines = np.full((count, n_points + 1, 2), np.nan)  # last slot of each circle stays NaN: the separator
    outlines[:, :n_points, :] = centers[:, None, :] + radii[:, None, None] * shape[None, :, :]
    return outlines[:, :, 0].ravel()[:-1], outlines[:, :, 1].ravel()[:-1]


def rectangle_points(
    display_centers_xy: np.ndarray,
    widths_px: np.ndarray,
    heights_px: np.ndarray,
    angles_deg: np.ndarray,
    affine_matrix: np.ndarray,
    *,
    offset_px: np.ndarray | float = 0.0,
    n_points: int = DEFAULT_CIRCLE_POINTS,
) -> tuple[np.ndarray, np.ndarray]:
    """The outlines of many rotated rectangles as two ``(N, n_points)`` arrays, one closed row per rectangle, in the same layout as
    :func:`circle_points` (so they go into the same items). ``offset_px`` moves the outline away from the rectangle's edge: ``0`` is the rectangle,
    a positive value the outline at that distance (straight sides joined by quarter circles, the outer edge of a border at gap + thickness),
    a negative value a smaller rectangle (the inner edge of a border that starts inside the shape; clamped at zero size).

    Geometry as for a circle: the outline is built around the **display** centre in the rectangle's own (reference-frame) pixels and bent by the
    linear part of the frame's affine, whose translation is already in the centre (see the module docstring). ``angles_deg`` turn the rectangle
    clockwise on the screen (y points down), about its centre, as ``roi_shapes.Rectangle`` says. A row holds ``4 * k`` vertices
    (``k = (n_points - 1) // 4`` per corner, 11 for the default 48) followed by the first vertex again up to ``n_points``, so every row is closed
    and the rows fit the same arrays as circles; a sharp rectangle looks the same with any ``k``."""
    centers = np.asarray(display_centers_xy, dtype=np.float64).reshape(-1, 2)
    count = centers.shape[0]
    if count == 0:
        return np.empty((0, n_points)), np.empty((0, n_points))
    per_corner = max((int(n_points) - 1) // 4, 1)
    offset = np.broadcast_to(np.asarray(offset_px, dtype=np.float64), (count,))
    half_w = np.maximum(np.asarray(widths_px, dtype=np.float64).reshape(-1) / 2.0 + np.minimum(offset, 0.0), 0.0)
    half_h = np.maximum(np.asarray(heights_px, dtype=np.float64).reshape(-1) / 2.0 + np.minimum(offset, 0.0), 0.0)
    radius = np.maximum(offset, 0.0)
    # corners clockwise on the screen: (+,+), (-,+), (-,-), (+,-); the arc of corner k turns from k * 90 degrees to (k + 1) * 90
    sign_x = np.repeat(np.array([1.0, -1.0, -1.0, 1.0]), per_corner)
    sign_y = np.repeat(np.array([1.0, 1.0, -1.0, -1.0]), per_corner)
    phi = (np.arange(4)[:, None] + np.linspace(0.0, 1.0, per_corner)[None, :]).ravel() * (np.pi / 2.0)
    local_x = sign_x[None, :] * half_w[:, None] + radius[:, None] * np.cos(phi)[None, :]
    local_y = sign_y[None, :] * half_h[:, None] + radius[:, None] * np.sin(phi)[None, :]
    angle = np.deg2rad(np.asarray(angles_deg, dtype=np.float64).reshape(-1))
    cos, sin = np.cos(angle)[:, None], np.sin(angle)[:, None]
    turned_x, turned_y = local_x * cos - local_y * sin, local_x * sin + local_y * cos
    linear = np.asarray(affine_matrix, dtype=np.float64)[:, :2]
    bent_x = turned_x * linear[0, 0] + turned_y * linear[0, 1]
    bent_y = turned_x * linear[1, 0] + turned_y * linear[1, 1]
    xs = np.empty((count, n_points))
    ys = np.empty((count, n_points))
    used = 4 * per_corner
    xs[:, :used], ys[:, :used] = centers[:, 0:1] + bent_x, centers[:, 1:2] + bent_y
    xs[:, used:], ys[:, used:] = xs[:, 0:1], ys[:, 0:1]  # closed, and padded with the first vertex again
    return xs, ys
