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
