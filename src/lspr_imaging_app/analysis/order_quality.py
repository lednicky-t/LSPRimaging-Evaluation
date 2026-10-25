"""Is this polynomial order a good one? A verdict per order and per cube from leave-one-out cross-validation (2026-10-25). Pure numpy, no Qt.

**Why this test.** The in-sample error of a polynomial falls with every order, so it cannot show over-fitting (on the real scan the 12th order reaches the noise level because it
follows the noise). The fit is judged by how well it predicts a point it did not see: leave one measured point out, fit the others, and compare with the point; do it for every point
and take the RMS. This is leave-one-out cross-validation. For a least-squares fit it needs no refits: with the hat matrix `H` of the polynomial (the matrix that turns the data into
the fitted values) the prediction error of point `i` is `e_i / (1 - h_ii)`, `e` the ordinary residual. A single QR decomposition of the Vandermonde matrix gives the hat matrices of
all orders at once (they are nested: the first `k + 1` columns of `Q` span the polynomials up to order `k`), so all 13 orders cost a few numpy calls per set of points.

**The verdict is relative.** The leave-one-out error is judged against the best order for the same spectra (the order with the smallest error), within `TOLERANCE` = 1.25:
- **good**: the error is at most 1.25 x the smallest;
- **over-fitted**: a higher order than the best, with a larger error: the extra terms only add variance (measured: the error rises again above the minimum);
- **under-fitted**: a lower order than the best, with a larger error: smooth structure is left in the residuals.
The good orders are therefore not always one contiguous block (on the real scan the odd 7th and 9th are good and the 8th, 10th and 13th are not). Measured numbers: `docs/poly_order_quality_study_2026-10-25.md`.

**Which spectra.** The points actually fitted (the fit window of the settings, per spectrum, and the wavelengths that have a value). The error of a cube is the median over the selected ROIs
(at most `MAX_ROIS`, evenly spread, so the cost stays small) of their leave-one-out errors; the best order is then that of the cube. A cube that was never scanned has no verdict.
**Capped orders.** The fit never uses more than half as many terms as points (`query.fit_polynomial`); an order above that cap for the points of a spectrum has no verdict
for that spectrum (``UNAVAILABLE`` when no ROI can fit it).

**What the menu shows** (`OrderQuality.state`, for one order and the present cube): green = good at the present cube and at least `GOOD_SHARE` (95 %) of all the cubes judged; yellow = good at the
present cube but out (over- or under-fitted) at more than 5 % of the cubes; amber = over-fitted at the present cube; purple = under-fitted at the present cube; grey = not available (above the cap) or
nothing to judge. (Every single cube was too strict: with one or two ROIs the best order of a cube jumps between neighbours with the noise, so a few cubes are always out.)

Independent of the noise (no noise estimate is needed) and of the scale and offset of the spectrum.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

from ..progress import Cancelled
from .fit_window import restrict_to_window
from .settings import MetricSettings
from .trace_fit import TraceInput, formula_arrays

MAX_ORDER = 13
TOLERANCE = 1.25
GOOD_SHARE = 0.95  # green when the order is good at this share of the cubes judged or more
MAX_ROIS = 20

UNKNOWN, GOOD, OVER, UNDER, UNAVAILABLE = 0, 1, 2, 3, 4
STATES = ("good", "some", "over", "under", "na", "unknown")

_LEVERAGE_FLOOR = 1.0e-12  # 1 - h_ii below this is treated as this (the point decides its own fit: an enormous prediction error, not a division by zero)
_TINY = 1.0e-300


@dataclass(frozen=True)
class OrderQuality:
    """The verdicts of orders 1..``MAX_ORDER`` (row ``order - 1``) at every cube (column, in the order of ``cube_indices``)."""

    cube_indices: np.ndarray  # the dataset's cube number of each column
    loo_rms: np.ndarray  # [order, cube] leave-one-out RMS error, median over the ROIs; NaN where there is none
    verdict: np.ndarray  # int8 [order, cube]: UNKNOWN, GOOD, OVER, UNDER, UNAVAILABLE
    best_order: np.ndarray  # int [cube]: the order with the smallest error (0: no verdict)
    n_rois: int  # ROIs the numbers come from

    def column(self, cube: int | None) -> int | None:
        if cube is None:
            return None
        hits = np.flatnonzero(self.cube_indices == int(cube))
        return int(hits[0]) if hits.size else None

    def state(self, order: int, cube: int | None) -> str:
        """``"good"`` (green), ``"some"`` (yellow), ``"over"`` (amber), ``"under"`` (purple), ``"na"`` or ``"unknown"`` (grey), for one order and the present cube."""
        if not 1 <= order <= MAX_ORDER:
            return "na"
        row = self.verdict[order - 1]
        column = self.column(cube)
        present = int(row[column]) if column is not None else UNKNOWN
        if present == OVER:
            return "over"
        if present == UNDER:
            return "under"
        if present == UNAVAILABLE:
            return "na"
        judged = (row == GOOD) | (row == OVER) | (row == UNDER)
        if not judged.any():
            return "na" if (row == UNAVAILABLE).any() else "unknown"
        out = int(((row == OVER) | (row == UNDER)).sum())
        return "good" if out <= int(judged.sum()) * (1.0 - GOOD_SHARE) + 1e-9 else "some"

    def counts(self, order: int) -> tuple[int, int, int]:
        """``(judged, over-fitted, under-fitted)`` cubes for an order."""
        row = self.verdict[order - 1]
        over, under = int((row == OVER).sum()), int((row == UNDER).sum())
        return int((row == GOOD).sum()) + over + under, over, under


def loo_rms_all_orders(x: np.ndarray, y: np.ndarray, max_order: int = MAX_ORDER) -> np.ndarray:
    """Leave-one-out RMS error of the polynomial fits of orders 1..``max_order`` (capped at ``len(x) // 2``) to each column of ``y`` (``[points, spectra]``) at the shared wavelengths ``x``.
    Returns ``[max_order, spectra]``, NaN for an order above the cap. The closed form from the hat matrix (module docstring)."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    n, count = y.shape
    top = min(int(max_order), n // 2)
    out = np.full((int(max_order), count), np.nan)
    if top < 1 or not x[-1] > x[0]:
        return out
    t = (2.0 * x - x[0] - x[-1]) / (x[-1] - x[0])
    q, _ = np.linalg.qr(np.polynomial.polynomial.polyvander(t, top))  # [n, top + 1]: column j and the ones before it span the polynomials up to order j
    coef = q.T @ y  # [top + 1, spectra]
    fitted = np.cumsum(q[:, :, None] * coef[None, :, :], axis=1)  # [n, top + 1, spectra]: the fit of each order
    leverage = np.cumsum(q * q, axis=1)  # [n, top + 1]: h_ii of each order
    residual = y[:, None, :] - fitted
    with np.errstate(all="ignore"):
        prediction_error = residual / np.maximum(1.0 - leverage, _LEVERAGE_FLOOR)[:, :, None]
        rms = np.sqrt((prediction_error * prediction_error).mean(axis=0))  # [top + 1, spectra]
    out[:top] = rms[1 : top + 1]
    return out


def _roi_errors(data: TraceInput, formula_key: str, metric: MetricSettings) -> tuple[np.ndarray, np.ndarray]:
    """``(loo [order, cube], evaluated [cube])`` of one ROI: the errors at every cube that has a result, NaN elsewhere."""
    n_cube = data.sample.shape[0]
    loo = np.full((MAX_ORDER, n_cube), np.nan)
    wl, values, valid, wanted = formula_arrays(data, formula_key)
    if wl.size == 0 or not wanted.any():
        return loo, wanted
    valid = restrict_to_window(wl, values, valid, metric)  # the points the fit would see
    cubes = np.flatnonzero(wanted)
    patterns, inverse = np.unique(valid[cubes], axis=0, return_inverse=True)  # cubes with the same points are handled together
    inverse = np.ravel(inverse)
    for g, pattern in enumerate(patterns):
        here = cubes[inverse == g]
        index = np.flatnonzero(pattern)
        if index.size < 2:
            continue
        loo[:, here] = loo_rms_all_orders(wl[index], values[np.ix_(here, index)].T)
    return loo, wanted


def evaluate_order_quality(
    inputs: Sequence[TraceInput], formula_key: str, metric: MetricSettings, *, cancelled: Callable[[], bool] | None = None
) -> OrderQuality | None:
    """The verdicts for the polynomial orders on the spectra of these ROIs (``trace_fit.TraceInput`` of each, all of the same scan). More than ``MAX_ROIS`` ROIs: an even sample of them.
    None when there is nothing to judge (no ROI). Raises ``Cancelled`` when ``cancelled()`` becomes true (checked between ROIs)."""
    if not inputs:
        return None
    if len(inputs) > MAX_ROIS:
        inputs = [inputs[i] for i in np.unique(np.linspace(0, len(inputs) - 1, MAX_ROIS).round().astype(int))]
    n_cube = inputs[0].sample.shape[0]
    cube_indices = np.arange(n_cube) if inputs[0].cube_indices is None else np.asarray(inputs[0].cube_indices)
    per_roi = np.full((len(inputs), MAX_ORDER, n_cube), np.nan)
    evaluated = np.zeros(n_cube, dtype=bool)
    for i, data in enumerate(inputs):
        if cancelled is not None and cancelled():
            raise Cancelled
        per_roi[i], wanted = _roi_errors(data, formula_key, metric)
        evaluated |= wanted
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # a cube or an order with no ROI that can fit it is all-NaN: the median is NaN, not an error
        loo = np.nanmedian(per_roi, axis=0)
    verdict = np.full((MAX_ORDER, n_cube), UNKNOWN, dtype=np.int8)
    best = np.zeros(n_cube, dtype=np.int64)
    for c in np.flatnonzero(evaluated):
        column = loo[:, c]
        finite = np.isfinite(column)
        verdict[~finite, c] = UNAVAILABLE
        if not finite.any():
            continue
        top = int(np.argmin(np.where(finite, column, np.inf)))
        limit = TOLERANCE * max(float(column[top]), _TINY)
        good = finite & (column <= limit)
        orders = np.arange(MAX_ORDER)
        verdict[good, c] = GOOD
        verdict[finite & ~good & (orders > top), c] = OVER
        verdict[finite & ~good & (orders < top), c] = UNDER
        best[c] = top + 1
    return OrderQuality(cube_indices, loo, verdict, best, len(inputs))
