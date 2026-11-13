"""A smooth curve through measured points, for the Sensorgram's "Spline" line style (2026-11-13). Display only: no Qt, and nothing that is stored, fitted or exported
as a number is ever read from it.

The curve is a **PCHIP** (monotone piecewise cubic Hermite) interpolation: a cubic spline whose slope at every point is chosen so that the curve never leaves the range of the
two points around it. A natural cubic spline would invent wiggles between noisy points (measured on 400 points with 0.3 nm noise: it left the range of its neighbours in 18 % of
the drawn samples, by up to 0.15 nm; PCHIP in none). It passes through every measured point exactly, so the dots drawn on the points still sit on the curve.

`smooth_run` makes one stretch of points denser; `smooth_line` does it for a whole line, stretch by stretch, and keeps a gap (a NaN in the data) a gap.

Cost (scratch measurements 2026-11-13, not kept; 400 points per line, 6 drawn points per interval): the interpolation alone is 0.3 ms per line, about 45 ms for 160 lines. A whole redraw
of the plot (`show_series` plus a full paint) goes from 66 ms (straight) to 204 ms (spline) with 160 lines, from 31 to 51 ms with 40 lines, and does not change with 5. The cheaper
ways, if ever needed: one `PchipInterpolator` call for all the lines that share an x (8.6 ms instead of 45 ms for the interpolation) or fewer drawn points per interval.
"""

from __future__ import annotations

import numpy as np
from scipy.interpolate import PchipInterpolator

SUBDIVISIONS = 6  # drawn points per interval between two measured points


def smooth_run(x: np.ndarray, y: np.ndarray, per: int = SUBDIVISIONS) -> tuple[np.ndarray, np.ndarray]:
    """`x`, `y` of one stretch of finite points -> the same stretch with `per` points per interval, on the PCHIP curve through them (every measured point is one of them).
    Fewer than two points, or an `x` that does not strictly increase (the curve would not be a function of x): returned unchanged, drawn straight as before."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    if x.size < 2 or per < 2 or not np.all(np.diff(x) > 0.0):
        return x, y
    steps = np.arange(per, dtype=np.float64) / per
    xs = np.append((x[:-1, None] + np.diff(x)[:, None] * steps).ravel(), x[-1])
    return xs, PchipInterpolator(x, y)(xs)


def finite_stretches(ok: np.ndarray) -> list[tuple[int, int]]:
    """[start, stop) of every run of True in `ok` (a bool array)."""
    edges = np.flatnonzero(np.diff(np.concatenate(([0], np.asarray(ok, dtype=np.int8), [0]))))
    return list(zip(edges[::2].tolist(), edges[1::2].tolist()))


def smooth_line(x: np.ndarray, y: np.ndarray, per: int = SUBDIVISIONS) -> tuple[np.ndarray, np.ndarray]:
    """A whole line: each stretch of points where both x and y are finite is smoothed on its own, and a NaN is put between two stretches so that a gap in the data stays a
    gap in the line (draw it with ``connect="finite"``). A line without gaps comes back as one stretch with no NaN."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.all():
        return smooth_run(x, y, per)
    pieces_x: list[np.ndarray] = []
    pieces_y: list[np.ndarray] = []
    for start, stop in finite_stretches(ok):
        xs, ys = smooth_run(x[start:stop], y[start:stop], per)
        if pieces_x:
            pieces_x.append(np.array([np.nan]))
            pieces_y.append(np.array([np.nan]))
        pieces_x.append(xs)
        pieces_y.append(ys)
    if not pieces_x:
        return np.empty(0), np.empty(0)
    return np.concatenate(pieces_x), np.concatenate(pieces_y)
