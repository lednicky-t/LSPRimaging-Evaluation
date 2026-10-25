"""The fit window found per spectrum (2026-10-25). Pure numpy, no Qt: the Spectra panel, the Sensorgram batch fit and the caches all use it.

A fixed window ("manual") is the same wavelength range for every ROI at every cube. A peak that shifts during a measurement drifts out of it, so two
more modes give **each spectrum its own window**, centred on that spectrum's own maximum:

- ``around_max``: maximum +/- a width in nm;
- ``fwhm``: maximum +/- ``k`` x the spectrum's FWHM (full width at half maximum: the peak's width at half its height).

**How the centre and the width are found** (chosen on real data, ``docs/fit_window_modes_test_2026-10-25.md``: the quick FWHM below and a Gaussian
refit gave the same window in 88-98 % of spectra, the refit cost 74x more time):

1. The spectrum is smoothed lightly (a 3-point moving average; an end point repeats its own value outside the data), so one noisy point cannot
   pull the window away from the peak.
2. The centre is the top of a parabola through the highest smoothed point and its two neighbours (uneven spacing handled), not the grid point
   itself: with a 10 nm wavelength step the grid maximum jumps 10 nm at a time and so would the window. A maximum at either end of the data keeps
   the grid point.
3. FWHM: the level halfway between the smoothed minimum (the baseline) and the smoothed maximum; the two crossings left and right of the maximum are
   found by linear interpolation. If one side never drops to the level (the peak is wider than the data) the FWHM is twice the other side's
   half-width; if neither does, there is no window (all points are fitted). A flat spectrum has no window either.

**Same number of points** (``window_same_points``): the range is converted to a point count, ``round(2 h / step) + 1`` with ``h`` the half-width and
``step`` the median wavelength spacing, and the window is that many neighbouring points closest to the centre. A window that would reach past the
end of the data is moved inwards, so it still holds that many points (the nearest points are taken even though they lie outside the nominal
range). A window then never gains or loses a point because the peak moved a few nm.

Everything is computed from one spectrum's own points, never from other spectra, so a ROI's window does not depend on which other ROIs are selected.
The window comes out as a wavelength range (low, high); the points inside are chosen with the same inclusive comparisons ``query._windowed`` uses, so
the batch path and the one-spectrum path select identical points. Vectorized only: the one-spectrum case is a batch of one.
"""

from __future__ import annotations

import numpy as np

from .settings import MetricSettings

MIN_POINTS = 4
"""Fewer valid points than this: no window is found (all points are fitted). A Gaussian needs four, and a peak is not told from three."""


def smooth3(y: np.ndarray) -> np.ndarray:
    """3-point moving average of every row of ``y`` ``[n, L]`` (L >= 2). The end points repeat their own value outside the data (an average of
    only two points would lean towards the edge, where the neighbour is missing)."""
    out = np.empty_like(y, dtype=np.float64)
    out[:, 1:-1] = (y[:, :-2] + y[:, 1:-1] + y[:, 2:]) / 3.0
    out[:, 0] = (2.0 * y[:, 0] + y[:, 1]) / 3.0
    out[:, -1] = (y[:, -2] + 2.0 * y[:, -1]) / 3.0
    return out


def peak_centre(x: np.ndarray, s: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """For every row of ``s`` ``[n, L]`` (smoothed values against the ascending ``x``): the index of its highest point and the wavelength of the top of
    the parabola through that point and its two neighbours. The parabola's top is kept between the neighbours; at an end of the data, or where the
    three points do not curve downwards, the centre is the grid point."""
    n, length = s.shape
    rows = np.arange(n)
    top = np.argmax(s, axis=1)
    centre = x[top].astype(np.float64)
    inner = (top > 0) & (top < length - 1)
    if inner.any():
        r, i = rows[inner], top[inner]
        x0, x1, x2 = x[i - 1], x[i], x[i + 1]
        y0, y1, y2 = s[r, i - 1], s[r, i], s[r, i + 1]
        denominator = (x1 - x0) * (y1 - y2) - (x1 - x2) * (y1 - y0)
        with np.errstate(all="ignore"):
            vertex = x1 - 0.5 * ((x1 - x0) ** 2 * (y1 - y2) - (x1 - x2) ** 2 * (y1 - y0)) / denominator
        ok = np.isfinite(vertex) & (denominator != 0.0)
        centre[inner] = np.where(ok, np.clip(vertex, x0, x2), x1)
    return top, centre


def half_max_width(x: np.ndarray, s: np.ndarray, top: np.ndarray, centre: np.ndarray) -> np.ndarray:
    """The FWHM of every row of ``s``; ``top`` and ``centre`` come from ``peak_centre``. The module doc says what the baseline is and what happens
    when a side never reaches the half level. NaN where there is no FWHM (a flat row, or a peak wider than the data on both sides)."""
    n, length = s.shape
    rows = np.arange(n)
    base = s.min(axis=1)
    peak = s[rows, top]
    level = base + 0.5 * (peak - base)
    index = np.arange(length)[None, :]
    below = s <= level[:, None]
    left = np.where(below & (index < top[:, None]), index, -1).max(axis=1)  # the last point at or below the level, left of the maximum
    right = np.where(below & (index > top[:, None]), index, length).min(axis=1)  # the first one, right of it
    has_left, has_right = left >= 0, right < length
    left_i = np.clip(left, 0, length - 2)
    right_i = np.clip(right, 1, length - 1)
    with np.errstate(all="ignore"):
        t_left = (level - s[rows, left_i]) / (s[rows, left_i + 1] - s[rows, left_i])
        x_left = x[left_i] + t_left * (x[left_i + 1] - x[left_i])
        t_right = (s[rows, right_i - 1] - level) / (s[rows, right_i - 1] - s[rows, right_i])
        x_right = x[right_i - 1] + t_right * (x[right_i] - x[right_i - 1])
    width = np.full(n, np.nan)
    both = has_left & has_right
    width[both] = (x_right - x_left)[both]
    only_left = has_left & ~has_right
    width[only_left] = 2.0 * (centre - x_left)[only_left]
    only_right = has_right & ~has_left
    width[only_right] = 2.0 * (x_right - centre)[only_right]
    flat = (peak - base) <= 1.0e-9 * np.maximum(np.abs(peak), np.abs(base))  # rounding alone separates the top and the bottom of a flat row
    width[flat | ~(peak > base)] = np.nan  # a flat row has no peak
    return width


def _dense_bounds(x: np.ndarray, y: np.ndarray, metric: MetricSettings) -> tuple[np.ndarray, np.ndarray]:
    """(low, high) of the window of every row of ``y`` ``[n, L]``; the rows share the ascending ``x`` and have no gaps. -inf / +inf: no window."""
    n, length = y.shape
    s = smooth3(y)
    top, centre = peak_centre(x, s)
    if metric.window_mode == "fwhm":
        half = float(metric.window_fwhm_factor) * half_max_width(x, s, top, centre)
    else:
        half = np.full(n, float(metric.window_half_width_nm))
    half = np.maximum(half, 0.0)
    low, high = centre - half, centre + half
    if metric.window_same_points:
        step = float(np.median(np.diff(x)))
        with np.errstate(all="ignore"):
            count = np.clip(np.rint(2.0 * half / step) + 1.0, 2.0, float(length))
        count = np.where(np.isfinite(count), count, length).astype(np.int64)
        position = np.interp(centre, x, np.arange(length, dtype=np.float64))  # the centre as a fractional index
        first = np.clip(np.floor(position - (count - 1) / 2.0 + 0.5).astype(np.int64), 0, length - count)
        low, high = x[first], x[first + count - 1]
    nothing = ~np.isfinite(half)  # no FWHM: fit everything
    low = np.where(nothing, -np.inf, low)
    high = np.where(nothing, np.inf, high)
    return low, high


def window_bounds(x: np.ndarray, values: np.ndarray, valid: np.ndarray, metric: MetricSettings) -> tuple[np.ndarray, np.ndarray]:
    """The relative window of every spectrum: ``(low, high)``, two ``float64 [n]`` arrays in nm, -inf / +inf where no window is found.

    ``x`` is ``[L]`` ascending; ``values`` and ``valid`` are ``[n, L]`` (``valid``: the points that count; only they are looked at). Rows with the
    same valid points are handled together. Not for the manual mode: ask ``metric.relative_window`` first."""
    n = values.shape[0]
    low, high = np.full(n, -np.inf), np.full(n, np.inf)
    if n == 0 or not metric.relative_window:
        return low, high
    patterns, inverse = np.unique(valid, axis=0, return_inverse=True)
    inverse = np.ravel(inverse)
    for g, pattern in enumerate(patterns):
        if int(pattern.sum()) < MIN_POINTS:
            continue
        rows = np.flatnonzero(inverse == g)
        columns = np.flatnonzero(pattern)
        low[rows], high[rows] = _dense_bounds(x[columns], values[np.ix_(rows, columns)], metric)
    return low, high


def restrict_to_window(x: np.ndarray, values: np.ndarray, valid: np.ndarray, metric: MetricSettings) -> np.ndarray:
    """``valid`` ``[n, L]`` reduced to the points inside each row's window (the manual range, when that is the mode, is applied too)."""
    if metric.relative_window:
        low, high = window_bounds(x, values, valid, metric)
        return valid & (x[None, :] >= low[:, None]) & (x[None, :] <= high[:, None])
    wl_min, wl_max = metric.manual_bounds()
    if wl_min is not None:
        valid = valid & (x >= float(wl_min))[None, :]
    if wl_max is not None:
        valid = valid & (x <= float(wl_max))[None, :]
    return valid


def spectrum_window(x: np.ndarray, y: np.ndarray, metric: MetricSettings) -> tuple[float | None, float | None]:
    """``(wl_min, wl_max)`` for ONE spectrum, in the form ``query.fit_spectrum`` takes (None: no bound on that side). ``x`` and ``y`` are the
    spectrum's finite points (any order)."""
    if not metric.relative_window:
        return metric.manual_bounds()
    finite = np.isfinite(x) & np.isfinite(y)
    order = np.argsort(x[finite], kind="stable")
    xs, ys = np.asarray(x, dtype=np.float64)[finite][order], np.asarray(y, dtype=np.float64)[finite][order]
    low, high = window_bounds(xs, ys[None, :], np.ones((1, xs.size), dtype=bool), metric)
    return (float(low[0]) if np.isfinite(low[0]) else None), (float(high[0]) if np.isfinite(high[0]) else None)
