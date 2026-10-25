"""The metric range (2026-10-25): which part of the FITTED curve the centroid is read from. Pure numpy, no Qt.

**Why.** The centroid used to be taken over the whole fit window with the curve's baseline counting as weight. The baseline pulls the result towards
the middle of the window, so a peak that moves by 1 nm moved the centroid by only 0.2-0.5 nm (measured with real spectra shifted by known amounts),
and the window's edges cut the curve at a non-zero height, so a narrow or moving window made the centroid jump (``docs/spectra_analysis_methods.md``,
sections 4-6). Reading the centroid from part of the curve, with the baseline taken out, removes both. The range is applied to the fitted curve, which
is smooth and continuous, so the 10 nm spacing of the measured wavelengths plays no part.

**The curve.** ``curves`` ``[n, K]`` are fitted curves sampled on one grid ``x`` ``[K]`` (the fit's own grid: ``linspace(first, last window point,
max(400, n_points))``). ``base`` ``[n]`` is each curve's baseline: the fitted offset of a Gaussian, the lowest point of a polynomial's curve.

**Weights ``w``** and the centroid ``sum(w x) / sum(w)`` (trapezoid rule, clipped to the curve's wavelength range; NaN when the weights add up to nothing):

- ``top_fraction`` (``f``): ``level = base + f (top - base)``, ``w = max(curve - level, 0)``, with ``top`` the highest point of the curve. Only the part of the
  peak above the level counts and each point counts by how far it is above the level, so the weights go to zero at the edges of that part: where the
  edges are cannot move the result.
- ``manual``: ``w = max(curve - base, 0)`` for ``metric_range_min <= x <= metric_range_max`` (a missing bound is the end of the curve), zero elsewhere. The
  baseline carries no weight, the edges still cut the curve at a non-zero height.

One implementation: the one-spectrum route (``query.metric_from_fit_in_range``) calls it with ``n = 1``, the batch route (``trace_fit``) with many rows.
"""

from __future__ import annotations

import numpy as np

from .settings import MetricSettings

_MIN_AREA = 1.0e-12  # weights that add up to less than this have no centroid (as for the other centroids)


def curve_base(fit_method: str, coefficients: np.ndarray | None, curves: np.ndarray) -> np.ndarray:
    """The baseline of every curve ``[n]``: a Gaussian's fitted offset (``coefficients`` ``[n, 4]``: amplitude, centre, sigma, offset), else the lowest
    point of the curve."""
    if str(fit_method).strip().lower() == "gaussian" and coefficients is not None and coefficients.shape[-1] == 4:
        return np.asarray(coefficients, dtype=np.float64)[:, 3]
    return np.min(curves, axis=1)


def range_centroid(x: np.ndarray, curves: np.ndarray, base: np.ndarray, metric: MetricSettings) -> np.ndarray:
    """The centroid ``[n]`` of every curve over the part of it the metric range selects (see the module doc). ``metric.metric_range`` must be one of
    ``top_fraction`` and ``manual``."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(curves, dtype=np.float64)
    base = np.asarray(base, dtype=np.float64)
    with np.errstate(all="ignore"):
        if metric.metric_range == "top_fraction":
            fraction = min(max(float(metric.metric_top_fraction), 0.0), 1.0)
            level = base + fraction * (np.max(y, axis=1) - base)
            weights = np.clip(y - level[:, None], 0.0, None)
        else:
            keep = np.ones(x.size, dtype=bool)
            if metric.metric_range_min is not None:
                keep &= x >= float(metric.metric_range_min)
            if metric.metric_range_max is not None:
                keep &= x <= float(metric.metric_range_max)
            weights = np.clip(y - base[:, None], 0.0, None) * keep[None, :]
        area = np.trapezoid(weights, x, axis=1)
        centroid = np.trapezoid(weights * x[None, :], x, axis=1) / area
    out = np.full(y.shape[0], np.nan)
    ok = np.isfinite(area) & (area > _MIN_AREA) & np.isfinite(centroid)
    out[ok] = np.clip(centroid[ok], x[0], x[-1])
    return out
