"""The metric of one ROI at every cube, in one go (2026-10-24). Pure numpy, no Qt, no h5py: a worker process starts from this file.

**What it replaces.** ``scan_trace.metric_trace`` used to call ``query.fit_and_metric`` once per cube: a ``Polynomial.fit``, a ``convert``, a
companion-matrix root search and a few evaluations, a dozen tiny numpy calls and ~2 ms per cube. For 10 ROIs x 314 cubes that is 4-20 s of Python
holding the interpreter lock, which froze the window for seconds (``docs/sensorgram_fit_freeze_2026-10-24.md``). The cubes of one ROI nearly always
share the same wavelengths, so the polynomial fits of all of them are **one least-squares problem with many right-hand sides**, and their peaks come
from **one batched eigenvalue call**.

**Same calculation, same choices.** For the polynomial fit this is ``query.fit_polynomial`` + ``query.metric_from_fit``, step for step: the finite
points inside the fit window, sorted; the order capped at half the point count; a least-squares fit; the maximum of the polynomial on the closed
interval (the two ends and every real stationary point inside it are the candidates, the highest wins) or its analytic centroid, clipped to the
interval. Two things differ, both on purpose: the fit is *kept in the scaled variable* t = (2x - x_min - x_max) / (x_max - x_min), in [-1, 1], instead of
being converted to powers of the wavelength in nm (the conversion subtracts large, nearly equal numbers, so the old coefficients carried a rounding
error the scaled ones do not); and so the roots and integrals are found in t. The result agrees with the per-cube path to rounding error
(measured: ``docs/sensorgram_fit_freeze_2026-10-24.md``; pinned by ``tests/unit/test_lspri_analysis_trace_fit.py``).

**The Gaussian fit** is batched the same way (``gaussian_fit.py``: centre and width iterated, amplitude and offset solved).

**What stays per cube.** The "no fit" metric and any cube where the batch could not be trusted go through ``query.fit_and_metric`` one cube at a
time, exactly as before: for the polynomial fit a non-finite coefficient, a vanishing leading coefficient or a failed eigenvalue solve; for the
Gaussian fit a cube the solver did not converge on (or whose width ran away: a fit window of a handful of points around the top, where a bell
cannot be told from a parabola), which goes to scipy's ``curve_fit`` as it did before the solver existed. A fit that fails there is a gap (NaN),
never a stop.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from .fit_window import restrict_to_window
from .gaussian_fit import solve_gaussian
from .metric_range import range_centroid
from .query import fit_and_metric, formula_spectrum, formula_values
from .settings import MetricSettings

_FIT_ERRORS = (RuntimeError, ValueError, FloatingPointError, np.linalg.LinAlgError)
_IMAG_TOLERANCE_NM = 1.0e-8  # a root counts as real when its imaginary part is below this, in nm (as in query.fit_polynomial)
_MIN_AREA = 1.0e-12  # a polynomial whose area over the window is smaller than this has no centroid (as in query.fit_polynomial)


@dataclass(frozen=True)
class TraceInput:
    """Everything the metric of one ROI needs, as plain arrays (cheap to hand to a worker process).

    ``sample`` and ``reference`` are ``[cube, wavelength]``; ``usable`` marks the entries that are finite and counted; ``scanned`` is False for a cube
    that was never scanned (no result there: NaN)."""

    wavelengths: np.ndarray
    sample: np.ndarray
    reference: np.ndarray
    usable: np.ndarray
    scanned: np.ndarray


BATCH_ROWS = 2000
"""Cubes fitted in one batch at most. A pass of the solver costs mostly numpy call overhead, so the cost per cube falls with the batch size, up to
~2000 (measured 2026-10-24: 0.080 ms per cube at 314, 0.055 at 1000-3000, 0.100 at 10 000, when the arrays outgrow the cache). The ROIs given to
``traces_from_inputs`` are merged into batches of this size, and ``trace_pool`` hands the helper process this many cubes per task."""


def trace_from_input(data: TraceInput, formula_key: str, metric: MetricSettings) -> np.ndarray:
    """The metric wavelength (nm) at every cube: ``float64 [cube]``, NaN where there is none."""
    return traces_from_inputs([data], formula_key, metric)[0]


def traces_from_inputs(
    inputs: Sequence[TraceInput], formula_key: str, metric: MetricSettings, timings: dict[str, float] | None = None
) -> list[np.ndarray]:
    """``trace_from_input`` for several ROIs: the same numbers, one trace per input, but the cubes of different ROIs that share their wavelengths are
    fitted in common batches of up to ``BATCH_ROWS`` (each cube is still fitted alone: a row of the batch never sees another).

    ``timings`` (optional, filled in; adds to what it holds, so one dict can total several calls) says where the time went, for the log: ``prep_ms``
    (formula values and grouping of the cubes), ``fit_ms`` (the batched fits, ``batches`` of them), ``per_cube_ms`` / ``per_cube_cubes`` (cubes fitted
    one at a time: the "no fit" metric and the cubes the batch could not fit, ``redo_cubes`` of them). Reading the clock changes no result."""
    clock = time.perf_counter
    t_start = clock()
    per_cube_here = 0.0  # seconds of the per-cube path in this call (not part of the preparation)
    t_ms = timings if timings is not None else {}
    for key in ("prep_ms", "fit_ms", "per_cube_ms", "per_cube_cubes", "redo_cubes", "batches"):
        t_ms.setdefault(key, 0.0)
    sizes = [d.sample.shape[0] for d in inputs]
    offsets = np.concatenate([[0], np.cumsum(sizes)]).astype(np.int64)
    flat = np.full(int(offsets[-1]), np.nan)  # the traces of all ROIs one after the other; a cube is addressed by offsets[roi] + cube
    outs = [flat[offsets[i] : offsets[i + 1]] for i in range(len(inputs))]
    method = str(metric.fit_method).strip().lower()
    group_fit = _gaussian_group if method == "gaussian" else _polynomial_group
    per_cube = method == "none"  # no fit to batch: the metric is read off the measured points, one cube at a time
    pending: dict[bytes, list[tuple[np.ndarray, np.ndarray, np.ndarray]]] = {}  # wavelengths -> (cube addresses, x, rows of values)
    for i, data in enumerate(inputs):
        wanted = data.scanned & data.usable.any(axis=1)
        if not wanted.any():
            continue
        if per_cube:
            t0 = clock()
            _fill_per_cube(outs[i], np.flatnonzero(wanted), data, formula_key, metric)
            per_cube_here += clock() - t0
            t_ms["per_cube_cubes"] += int(wanted.sum())
            continue
        with np.errstate(all="ignore"):  # unusable entries may be NaN; they are never used
            values = formula_values(data.sample, data.reference, formula_key)
        valid = data.usable & np.isfinite(values)
        wl = np.asarray(data.wavelengths, dtype=np.float64)
        valid &= np.isfinite(wl)[None, :]
        order = np.argsort(wl, kind="stable")  # the points of every cube are fitted in wavelength order, as _windowed sorts them
        wl, values, valid = wl[order], values[:, order], valid[:, order]
        valid = restrict_to_window(wl, values, valid, metric)  # the fit window: a fixed range, or each cube's own (fit_window.py)
        wanted_cubes = np.flatnonzero(wanted)
        patterns, inverse = np.unique(valid[wanted_cubes], axis=0, return_inverse=True)  # cubes with the same usable wavelengths fit together
        inverse = np.ravel(inverse)
        for g, pattern in enumerate(patterns):
            cubes = wanted_cubes[inverse == g]
            x = wl[pattern]
            if x.size < 2:
                continue  # fewer than two points: no fit, no metric (a gap)
            pending.setdefault(x.tobytes(), []).append((offsets[i] + cubes, x, values[np.ix_(cubes, np.flatnonzero(pattern))]))
    t_fit = clock()
    t_ms["prep_ms"] += (t_fit - t_start - per_cube_here) * 1e3
    t_ms["per_cube_ms"] += per_cube_here * 1e3
    redo: list[np.ndarray] = []
    for items in pending.values():
        x = items[0][1]
        address = np.concatenate([item[0] for item in items])
        rows = np.concatenate([item[2] for item in items])
        for start in range(0, rows.shape[0], BATCH_ROWS):
            here = address[start : start + BATCH_ROWS]
            result = group_fit(x, rows[start : start + BATCH_ROWS], metric)
            t_ms["batches"] += 1
            if result is None:
                redo.append(here)
                continue
            good, found = result
            flat[here[good]] = found[good]
            redo.append(here[~good])
    t_redo = clock()
    t_ms["fit_ms"] += (t_redo - t_fit) * 1e3
    if redo:
        failed = np.sort(np.concatenate(redo))
        owner = np.searchsorted(offsets, failed, side="right") - 1
        for i in np.unique(owner):
            _fill_per_cube(outs[i], failed[owner == i] - offsets[i], inputs[i], formula_key, metric, gaussian_fast_first=False)
        t_ms["redo_cubes"] += failed.size
        t_ms["per_cube_ms"] += (clock() - t_redo) * 1e3
        t_ms["per_cube_cubes"] += failed.size
    return outs


def trace_from_input_per_cube(data: TraceInput, formula_key: str, metric: MetricSettings) -> np.ndarray:
    """The same trace, one cube at a time through ``query.fit_and_metric``: the reference the batch is checked against (tests, benchmarks)."""
    out = np.full(data.sample.shape[0], np.nan)
    _fill_per_cube(out, np.flatnonzero(data.scanned), data, formula_key, metric)
    return out


def _fill_per_cube(
    out: np.ndarray, cubes: np.ndarray, data: TraceInput, formula_key: str, metric: MetricSettings, *, gaussian_fast_first: bool = True
) -> None:
    """The reference path: one spectrum, one fit, one metric per cube (``query.fit_and_metric``). A failed fit leaves NaN.
    ``gaussian_fast_first=False`` for cubes the batch solver has just failed on: straight to scipy, not a second try of the solver."""
    wavelengths = np.asarray(data.wavelengths, dtype=np.float64)
    for cube in cubes:
        usable = data.usable[cube]
        if not usable.any():
            continue
        spectrum = formula_spectrum(wavelengths[usable], data.sample[cube][usable], data.reference[cube][usable], formula_key)
        try:
            _fit, x, _y = fit_and_metric(spectrum, metric, gaussian_fast_first=gaussian_fast_first)
        except _FIT_ERRORS:
            continue
        if x is not None and np.isfinite(x):
            out[cube] = x


def _gaussian_group(x: np.ndarray, y: np.ndarray, metric: MetricSettings) -> tuple[np.ndarray, np.ndarray] | None:
    """The Gaussian fit of every row of ``y`` against the shared ``x`` (``gaussian_fit.solve_gaussian``) and its metric, as ``query.fit_gaussian`` +
    ``metric_from_fit`` read it: the centre clipped to the window, or the centroid of the curve sampled at ``max(400, n)`` points. Same return as
    ``_polynomial_group``; a row that did not converge is not ``good`` and is fitted by scipy afterwards."""
    n_spectra = y.shape[0]
    if x.size < 4:
        return np.ones(n_spectra, dtype=bool), np.full(n_spectra, np.nan)  # fewer points than parameters: no fit (a gap), as in fit_gaussian
    x_min, x_max = float(x[0]), float(x[-1])
    if not x_max > x_min:
        return None
    params, converged = solve_gaussian(x, y)
    good = converged
    found = np.full(n_spectra, np.nan)
    key = str(metric.metric_key).strip().lower()
    if key == "maximum":
        found[converged] = np.clip(params[converged, 1], x_min, x_max)
    elif key == "centroid":
        fitted_x = np.linspace(x_min, x_max, max(400, x.size))
        amplitude, centre, sigma, offset = (params[converged, i : i + 1] for i in range(4))
        curve = amplitude * np.exp(-((fitted_x[None, :] - centre) ** 2) / (2.0 * sigma * sigma)) + offset
        if metric.ranged_centroid:  # part of the fitted curve only; the baseline is the fitted offset (metric_range.py)
            found[converged] = range_centroid(fitted_x, curve, offset[:, 0], metric)
            return good, found
        area = np.trapezoid(curve, fitted_x, axis=1)
        with np.errstate(all="ignore"):
            centroid = np.trapezoid(curve * fitted_x[None, :], fitted_x, axis=1) / area
        has = np.isfinite(area) & (np.abs(area) > _MIN_AREA) & np.isfinite(centroid)
        values = np.full(area.shape, np.nan)
        values[has] = np.clip(centroid[has], x_min, x_max)
        found[converged] = values
    return good, found


def _polynomial_group(x: np.ndarray, y: np.ndarray, metric: MetricSettings) -> tuple[np.ndarray, np.ndarray] | None:
    """Fit ``y[c]`` (one row per cube) against the shared ``x`` (ascending, >= 2 points) and read off the metric.

    Returns ``(good, found)``: per cube, whether the batch result can be trusted and the metric wavelength (NaN: this cube has no metric).
    None: the whole group must be done cube by cube."""
    n = x.size
    x_min, x_max = float(x[0]), float(x[-1])
    if not x_max > x_min:
        return None
    degree = min(max(int(metric.poly_order), 1), max(n // 2, 1))  # capped at half the point count (see query.fit_polynomial)
    half, middle = (x_max - x_min) / 2.0, (x_max + x_min) / 2.0
    t = (x - middle) / half
    vander = np.polynomial.polynomial.polyvander(t, degree)
    column_scale = np.sqrt(np.sum(vander * vander, axis=0))
    try:
        solution = np.linalg.lstsq(vander / column_scale, y.T, rcond=n * np.finfo(np.float64).eps)[0]
    except np.linalg.LinAlgError:
        return None
    coef = (solution.T / column_scale).T  # [degree + 1, cube]: p(t) = sum coef[k] t^k
    good = np.all(np.isfinite(coef), axis=0)
    key = str(metric.metric_key).strip().lower()
    found = np.full(y.shape[0], np.nan)
    if key == "centroid" and metric.ranged_centroid:
        grid = np.linspace(x_min, x_max, max(400, n))  # the grid the per-spectrum fit samples its curve on
        curves = (np.polynomial.polynomial.polyvander((grid - middle) / half, degree) @ coef).T  # [cube, grid]
        has = good & np.all(np.isfinite(curves), axis=1)
        if has.any():
            found[has] = range_centroid(grid, curves[has], curves[has].min(axis=1), metric)
        return good, found
    if key == "centroid":
        i0 = sum(coef[k] * (2.0 / (k + 1)) for k in range(0, degree + 1, 2))  # integral of p over [-1, 1]
        i1 = sum(coef[k] * (2.0 / (k + 2)) for k in range(1, degree + 1, 2))  # integral of t p over [-1, 1] (degree >= 1 here)
        area = half * i0  # the area in nm, as the per-cube path measures it
        with np.errstate(all="ignore"):
            centroid = middle + half * i1 / i0
        has = good & np.isfinite(area) & (np.abs(area) > _MIN_AREA) & np.isfinite(centroid)
        found[has] = np.clip(centroid[has], x_min, x_max)
        return good, found
    if key != "maximum":
        return good, found  # an unknown metric has no value (query.metric_from_fit returns None)
    candidates_t, candidates_ok = _candidates(coef, degree, half)
    if candidates_t is None:
        return None
    with np.errstate(all="ignore"):
        heights = _horner(coef, candidates_t)
    heights = np.where(candidates_ok & np.isfinite(heights), heights, -np.inf)
    best = np.argmax(heights, axis=1)  # the first of equal heights wins, ends before roots, as np.argmax does in the per-cube path
    rows = np.arange(y.shape[0])
    peak_t = candidates_t[rows, best]
    has = good & np.isfinite(heights[rows, best])
    found[has] = np.where(best[has] == 0, x_min, np.where(best[has] == 1, x_max, middle + half * peak_t[has]))
    return good, found


def _candidates(coef: np.ndarray, degree: int, half: float) -> tuple[np.ndarray | None, np.ndarray | None]:
    """``[cube, 2 + roots]`` positions t where the maximum can sit (the two ends, then the real stationary points) and which of them count.
    ``(None, None)`` when the roots cannot be found for the batch."""
    n_cube = coef.shape[1]
    ends = np.array([-1.0, 1.0])
    t = np.tile(ends, (n_cube, 1))
    ok = np.ones((n_cube, 2), dtype=bool)
    if degree < 2:
        return t, ok  # a line has no stationary point: the maximum is at an end
    derivative = coef[1:] * np.arange(1, degree + 1)[:, None]  # [degree, cube]: p'(t) = sum derivative[k] t^k
    leading = derivative[-1]
    if np.any(leading == 0.0) or not np.all(np.isfinite(derivative)):
        return None, None  # a vanishing leading coefficient changes the degree: leave it to the per-cube path
    m = degree - 1
    if m == 1:
        roots = (-derivative[0] / derivative[1])[:, None].astype(np.complex128)
    else:
        companion = np.zeros((n_cube, m, m))
        companion[:, np.arange(1, m), np.arange(m - 1)] = 1.0
        companion[:, :, -1] = (-derivative[:-1] / leading).T
        try:
            roots = np.linalg.eigvals(companion)
        except np.linalg.LinAlgError:
            return None, None
    real = (np.abs(roots.imag) * half < _IMAG_TOLERANCE_NM) & (roots.real >= -1.0) & (roots.real <= 1.0)
    return np.concatenate([t, roots.real], axis=1), np.concatenate([ok, real], axis=1)


def _horner(coef: np.ndarray, t: np.ndarray) -> np.ndarray:
    """``p_c(t[c, j])`` for coefficients ``coef[k, c]`` and positions ``t[c, j]``."""
    result = np.zeros_like(t) + coef[-1][:, None]
    for k in range(coef.shape[0] - 2, -1, -1):
        result = result * t + coef[k][:, None]
    return result
