"""Bulk sensitivity (2026-11-12): the slope of a ROI's step means against the refractive index of the steps' solutions. Pure, no Qt, no files.

Screen purpose (of the numbers): how far the resonance moves per refractive index unit (RIU) for one ROI or group, from the steps the user put in the S_bulk set
(``ExperimentPlanModule``, "Use for S_bulk"). Unit: the trace's unit per RIU (nm/RIU for a wavelength metric, eV/RIU for an energy one).

**Points.** One per step of the set whose solution has an imported RI: x = the RI (``refractive_index.SolutionRi.ri``, measured at the wavelength stamped at import), y = the
step's mean of the line as drawn (the same number as the ROI / Groups table's step column, `step_table.RowNumbers`). Each step is its own point; steps of the same solution are
never pooled. A baseline subtracted from the line moves every y by the same amount and so changes the intercept, never the slope.

**Fit** (`fit_bulk`), maintainer's choice 2026-11-12:

- *Weighted* (default): least squares with weights ``w_i = 1 / s_i^2``, where ``s_i`` is the uncertainty of point i (the step's SEM, or its SD; the caller picks).
  When the RI file gives an uncertainty of the RI (its Std column, ``u_ri``), it enters as an *effective variance* ``s_i^2 = u_y^2 + S^2 u_ri^2`` (S = the slope; the
  RI error converted to the y unit), re-fitted until S settles (Orear 1982).
- *Equal*: ordinary least squares, every step counts the same; the point uncertainties are only propagated into the slope's uncertainty.

**Uncertainty of the slope** (one number, ``u``):

- ``u_data``: what the points' own uncertainties give. Weighted: ``1 / sqrt(sum w_i (x_i - x_w)^2)``. Equal: ``sqrt(sum c_i^2 s_i^2)`` with
  ``c_i = (x_i - x_mean) / Sxx`` (the slope is ``sum c_i y_i``).
- ``chi2_red`` = ``sum ((y_i - fit_i) / s_i)^2 / (n - 2)``: do the points scatter around the line as much as their uncertainties say (about 1), or more (a bent line, drift,
  a disturbed step)? Needs three points or more.
- Weighted: ``u = u_data * max(1, sqrt(chi2_red))`` (the Birge ratio; the same as scipy ``curve_fit(absolute_sigma=False)`` when the scatter is larger). Nothing is added
  twice: the scatter already holds the measurement noise, so it only *raises* ``u`` when the points disagree with the line more than their error bars allow, never lowers it.
- Equal: ``u = max(u_data, u_scatter)``, ``u_scatter`` = the ordinary least-squares standard error ``sqrt(sum r_i^2 / (n - 2) / Sxx)``. (The Birge factor on an unweighted
  line over-states ``u``: the large-error points inflate chi2; measured 88 % coverage instead of 68 %.)
- With two points there is no scatter to judge: ``u = u_data`` and ``chi2_red`` is NaN.

The SEM counts every cube as independent; smoothing makes neighbouring cubes alike, so ``u_data`` from SEMs is then a little small, and ``chi2_red`` comes out large: the scatter
then takes over. Validated on synthetic data with a known slope (``tests/unit/test_lspri_bulk_sensitivity.py``; 5 steps, 4000 runs): the true slope lies within ``+/- u`` in
73 % (weighted) / 71 % (equal) of the runs for honest error bars; with error bars 3x too small, 61 % (weighted; u_data alone: 27 %). Coverage below 68 % there is the price
of judging the scatter from 3 degrees of freedom.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

WEIGHT_INVERSE_VARIANCE = "inverse_variance"
WEIGHT_EQUAL = "equal"
WEIGHTINGS = (WEIGHT_INVERSE_VARIANCE, WEIGHT_EQUAL)

_EFFECTIVE_VARIANCE_ROUNDS = 8  # the slope settles in two or three rounds; the rest is a safe margin
_RELATIVE_TOLERANCE = 1e-12


@dataclass(frozen=True)
class BulkFit:
    """The fitted line ``y = slope * RI + intercept`` and how well the slope is known. NaN where a number cannot be given; ``reason`` then says why."""

    slope: float  # trace unit per RIU
    intercept: float  # trace unit, the line at RI = 0 (an extrapolation: only for drawing the line)
    u: float  # the slope's standard uncertainty, ``u_data * max(1, sqrt(chi2_red))``
    u_data: float  # from the points' uncertainties alone
    chi2_red: float  # NaN with fewer than three points
    n_points: int  # points used
    n_distinct_ri: int
    weighting: str
    reason: str = ""  # empty when a slope was found

    @property
    def ok(self) -> bool:
        return math.isfinite(self.slope)


def _nan_fit(n: int, distinct: int, weighting: str, reason: str) -> BulkFit:
    nan = float("nan")
    return BulkFit(nan, nan, nan, nan, nan, n, distinct, weighting, reason)


def fit_bulk(
    ri: Sequence[float],
    y: Sequence[float],
    y_u: Sequence[float],
    ri_u: Sequence[float] | None = None,
    *,
    weighting: str = WEIGHT_INVERSE_VARIANCE,
) -> BulkFit:
    """The bulk sensitivity of one ROI or group from its points (RI, step mean, uncertainty of the step mean, uncertainty of the RI or NaN).

    A point is used when its RI and mean are finite; for the weighted fit also its uncertainty must be finite and > 0 (a point with no uncertainty cannot be weighted, and is
    left out rather than given a guessed weight). Needs two distinct RIs."""
    if weighting not in WEIGHTINGS:
        raise ValueError(f"unknown weighting {weighting!r}")
    x = np.asarray(ri, dtype=np.float64)
    yy = np.asarray(y, dtype=np.float64)
    sy = np.asarray(y_u, dtype=np.float64)
    sx = np.zeros_like(x) if ri_u is None else np.nan_to_num(np.asarray(ri_u, dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0)
    if not (x.shape == yy.shape == sy.shape == sx.shape):
        raise ValueError("ri, y, y_u and ri_u must have the same length")
    sx = np.abs(sx)
    use = np.isfinite(x) & np.isfinite(yy)
    if weighting == WEIGHT_INVERSE_VARIANCE:
        use &= np.isfinite(sy) & (sy > 0.0)
    x, yy, sy, sx = x[use], yy[use], sy[use], sx[use]
    n = int(x.size)
    distinct = int(np.unique(x).size)
    if distinct < 2:
        return _nan_fit(n, distinct, weighting, "needs steps of at least two solutions with different refractive indices")

    if weighting == WEIGHT_EQUAL:
        return _fit_equal(x, yy, sy, sx, n, distinct)
    return _fit_weighted(x, yy, sy, sx, n, distinct)


def _weighted_line(x: np.ndarray, y: np.ndarray, w: np.ndarray) -> tuple[float, float, float]:
    """(slope, intercept, sum w (x - x_w)^2) of the weighted least-squares line."""
    sw = float(w.sum())
    xw = float((w * x).sum()) / sw
    yw = float((w * y).sum()) / sw
    dx = x - xw
    sxx = float((w * dx * dx).sum())
    slope = float((w * dx * (y - yw)).sum()) / sxx
    return slope, yw - slope * xw, sxx


def _fit_weighted(x: np.ndarray, y: np.ndarray, sy: np.ndarray, sx: np.ndarray, n: int, distinct: int) -> BulkFit:
    var = sy * sy
    slope, intercept, sxx = _weighted_line(x, y, 1.0 / var)
    if np.any(sx > 0.0):  # effective variance: the RI's uncertainty in the y unit, at the current slope
        for _ in range(_EFFECTIVE_VARIANCE_ROUNDS):
            var = sy * sy + (slope * sx) ** 2
            new_slope, intercept, sxx = _weighted_line(x, y, 1.0 / var)
            settled = abs(new_slope - slope) <= _RELATIVE_TOLERANCE * max(abs(new_slope), 1.0)
            slope = new_slope
            if settled:
                break
        var = sy * sy + (slope * sx) ** 2
        slope, intercept, sxx = _weighted_line(x, y, 1.0 / var)
    u_data = 1.0 / math.sqrt(sxx)
    chi2_red = _chi2_red(x, y, var, slope, intercept, n)
    return BulkFit(slope, intercept, _birge(u_data, chi2_red), u_data, chi2_red, n, distinct, WEIGHT_INVERSE_VARIANCE)


def _fit_equal(x: np.ndarray, y: np.ndarray, sy: np.ndarray, sx: np.ndarray, n: int, distinct: int) -> BulkFit:
    slope, intercept, sxx = _weighted_line(x, y, np.ones_like(x))
    var = sy * sy + (slope * sx) ** 2  # NaN where a step has no uncertainty
    c = (x - x.mean()) / sxx
    u_data = math.sqrt(float((c * c * var).sum())) if np.all(np.isfinite(var)) else float("nan")
    chi2_red = _chi2_red(x, y, var, slope, intercept, n) if math.isfinite(u_data) else float("nan")
    u_scatter = float("nan")
    if n >= 3:  # the ordinary least-squares standard error: the scatter about the line
        residual = y - (slope * x + intercept)
        u_scatter = math.sqrt(float((residual * residual).sum()) / (n - 2) / sxx)
    finite = [u for u in (u_data, u_scatter) if math.isfinite(u)]
    return BulkFit(slope, intercept, max(finite) if finite else float("nan"), u_data, chi2_red, n, distinct, WEIGHT_EQUAL)


def _chi2_red(x: np.ndarray, y: np.ndarray, var: np.ndarray, slope: float, intercept: float, n: int) -> float:
    if n < 3 or not np.all(var > 0.0):
        return float("nan")
    residual = y - (slope * x + intercept)
    return float((residual * residual / var).sum()) / (n - 2)


def _birge(u_data: float, chi2_red: float) -> float:
    """``u_data`` raised by the Birge ratio when the points scatter more than their uncertainties say; never lowered."""
    if not math.isfinite(chi2_red):
        return u_data
    return u_data * max(1.0, math.sqrt(chi2_red))
