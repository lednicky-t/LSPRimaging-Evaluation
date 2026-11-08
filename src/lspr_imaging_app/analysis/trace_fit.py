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

**The Gaussian fit** is batched the same way (``gaussian_fit.py``: centre and width iterated, amplitude and offset solved), and so is **the Voigt
fit** (``voigt_fit.py``, 2026-11-02: centre and the two widths iterated, in photon energy). **The "no fit" metric** (read off the measured points)
is batched too since 2026-11-02 (``_points_group``: the same numpy calls per row; it was one cube at a time, 2 s for 160 ROIs x 314 cubes).
**Speed-ups of 2026-11-02** (same numbers): the cubes are grouped by their usable wavelengths with ``row_patterns`` (``np.unique(axis=0)`` was
~300 ms of a 160-ROI Sensorgram), and a sampled curve's centroid is one matrix product with the trapezoid weights (``trapezoid_weights``;
rounding differences <= 1e-12 nm).

**What stays per cube.** The "no fit" metric and any cube where the batch could not be trusted go through ``query.fit_and_metric`` one cube at a
time, exactly as before: for the polynomial fit a non-finite coefficient, a vanishing leading coefficient or a failed eigenvalue solve; for the
Gaussian fit a cube the solver did not converge on (or whose width ran away: a fit window of a handful of points around the top, where a bell
cannot be told from a parabola), which goes to scipy's ``curve_fit`` as it did before the solver existed. A fit that fails there is a gap (NaN),
never a stop.

**The template shift** (``fit_method == "template"``, 2026-10-25, ``template_shift.py``) is not batched across ROIs: each ROI's whole trace is one call (the reference cubes of the ROI are
part of that trace, so a ROI's cubes must never be split between calls), and that call is already vectorised over cubes and shifts (~5 ms per ROI of 314 cubes).
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
from .template_shift import ReferenceTemplate, TemplateFit, build_template, fit_template_shift, response_law

_FIT_ERRORS = (RuntimeError, ValueError, FloatingPointError, np.linalg.LinAlgError)
_IMAG_TOLERANCE_NM = 1.0e-8  # a root counts as real when its imaginary part is below this, in nm (as in query.fit_polynomial)
_MIN_AREA = 1.0e-12  # a polynomial whose area over the window is smaller than this has no centroid (as in query.fit_polynomial)


@dataclass(frozen=True)
class TraceInput:
    """Everything the metric of one ROI needs, as plain arrays (cheap to hand to a worker process).

    ``sample`` and ``reference`` are ``[cube, wavelength]``; ``usable`` marks the entries that are finite and counted; ``scanned`` is False for a cube
    that was never scanned (no result there: NaN). ``cube_indices`` (the dataset's cube number of each row; None = 0, 1, 2, ...) lets the template shift find
    its reference cubes."""

    wavelengths: np.ndarray
    sample: np.ndarray
    reference: np.ndarray
    usable: np.ndarray
    scanned: np.ndarray
    cube_indices: np.ndarray | None = None


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
    if metric.uses_response:  # needs every ROI of the group (scan_trace.response_traces); one ROI's inputs cannot give it
        raise ValueError("The response shape is computed for every ROI at once (scan_trace.response_traces), not per ROI.")
    group_fit = {"gaussian": _gaussian_group, "voigt": _voigt_group, "none": _points_group}.get(method, _polynomial_group)
    min_points = 1 if method == "none" else 2  # read off the points, one point still has a maximum (and a "centroid": itself)
    per_cube = False  # every method is batched (the "no fit" metric too since 2026-11-02: it was one cube at a time, 2 s for 160 ROIs)
    template_here = 0.0  # seconds of the template shift in this call (not part of the preparation)
    pending: dict[bytes, list[tuple[np.ndarray, np.ndarray, np.ndarray]]] = {}  # wavelengths -> (cube addresses, x, rows of values)
    for i, data in enumerate(inputs):
        wanted = data.scanned & data.usable.any(axis=1)
        if not wanted.any():
            continue
        if metric.uses_template:
            t0 = clock()
            outs[i][:] = template_trace(data, formula_key, metric)
            template_here += clock() - t0
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
        patterns, inverse = row_patterns(valid[wanted_cubes])  # cubes with the same usable wavelengths fit together
        for g, pattern in enumerate(patterns):
            cubes = wanted_cubes[inverse == g]
            x = wl[pattern]
            if x.size < min_points:
                continue  # too few points: no fit, no metric (a gap)
            pending.setdefault(x.tobytes(), []).append((offsets[i] + cubes, x, values[np.ix_(cubes, np.flatnonzero(pattern))]))
    t_fit = clock()
    t_ms["prep_ms"] += (t_fit - t_start - per_cube_here - template_here) * 1e3
    t_ms["per_cube_ms"] += per_cube_here * 1e3
    t_ms["fit_ms"] += template_here * 1e3
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


def row_patterns(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """``np.unique(mask, axis=0, return_inverse=True)`` for a 2-D bool array, with the inverse flattened: the distinct rows in the same
    (lexicographic) order and, per row, the index of its pattern.

    ``np.unique(..., axis=0)`` sorts the rows as raw byte strings, which took ~300 of the ~460 ms of preparing a Sensorgram of 160 ROIs x 314 cubes
    (2026-11-02). Each row's bits packed into one big-endian integer (first wavelength = highest bit) sort in exactly the same order, as plain
    integers. Up to 64 wavelengths; more go the original way."""
    mask = np.asarray(mask, dtype=bool)
    if mask.ndim != 2 or mask.shape[1] > 64 or mask.shape[0] == 0:
        patterns, inverse = np.unique(mask, axis=0, return_inverse=True)
        return patterns, np.ravel(inverse)
    packed = np.packbits(mask, axis=1)  # big-endian bit order: the first column is the highest bit of the first byte
    padded = np.zeros((mask.shape[0], 8), dtype=np.uint8)
    padded[:, : packed.shape[1]] = packed
    keys = padded.view(">u8")[:, 0]
    _keys, first, inverse = np.unique(keys, return_index=True, return_inverse=True)
    return mask[first], np.ravel(inverse)


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
    template = reference_template(data, formula_key, metric) if metric.uses_template else None
    for cube in cubes:
        usable = data.usable[cube]
        if not usable.any():
            continue
        spectrum = formula_spectrum(wavelengths[usable], data.sample[cube][usable], data.reference[cube][usable], formula_key)
        try:
            _fit, x, _y = fit_and_metric(spectrum, metric, gaussian_fast_first=gaussian_fast_first, template=template)
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
        curve = sampled_curve(fitted_x, amplitude, centre, sigma, offset)
        if metric.ranged_centroid:  # part of the fitted curve only; the baseline is the fitted offset (metric_range.py)
            found[converged] = range_centroid(fitted_x, curve, offset[:, 0], metric)
            return good, found
        # The two trapezoid integrals as one matrix product with the trapezoid weights (2026-11-02): the same sums in another order, so the
        # centroid differs from np.trapezoid's by rounding only (<= 1e-12 nm measured), at a third of the cost (no curve * x array).
        sums = curve @ trapezoid_weights(fitted_x)
        area = sums[:, 0]
        with np.errstate(all="ignore"):
            centroid = sums[:, 1] / area
        has = np.isfinite(area) & (np.abs(area) > _MIN_AREA) & np.isfinite(centroid)
        values = np.full(area.shape, np.nan)
        values[has] = np.clip(centroid[has], x_min, x_max)
        found[converged] = values
    return good, found


def _voigt_group(x: np.ndarray, y: np.ndarray, metric: MetricSettings) -> tuple[np.ndarray, np.ndarray] | None:
    """The Voigt fit of every row (``voigt_fit.solve_voigt``) and its metric, as ``query.fit_voigt`` + ``metric_from_fit`` read it: the centre's
    wavelength clipped to the window, or the centroid of the curve sampled at ``max(400, n)`` wavelengths. A row that did not converge is not
    ``good`` and goes to scipy afterwards (the per-cube path with ``gaussian_fast_first=False``)."""
    from .voigt_fit import model as voigt_model
    from .voigt_fit import peak_wavelength_nm, solve_voigt

    n_spectra = y.shape[0]
    if x.size < 5:
        return np.ones(n_spectra, dtype=bool), np.full(n_spectra, np.nan)  # fewer points than parameters: a gap, as in fit_voigt
    x_min, x_max = float(x[0]), float(x[-1])
    if not x_max > x_min:
        return None
    params, converged = solve_voigt(x, y)
    found = np.full(n_spectra, np.nan)
    key = str(metric.metric_key).strip().lower()
    if not converged.any():
        return converged, found
    if key == "maximum":
        found[converged] = np.clip(peak_wavelength_nm(params[converged]), x_min, x_max)
    elif key == "centroid":
        fitted_x = np.linspace(x_min, x_max, max(400, x.size))
        curve = voigt_model(fitted_x, params[converged])
        if metric.ranged_centroid:  # the baseline is the fitted offset (metric_range.curve_base)
            found[converged] = range_centroid(fitted_x, curve, params[converged, 4], metric)
            return converged, found
        sums = curve @ trapezoid_weights(fitted_x)
        with np.errstate(all="ignore"):
            centroid = sums[:, 1] / sums[:, 0]
        has = np.isfinite(sums[:, 0]) & (np.abs(sums[:, 0]) > _MIN_AREA) & np.isfinite(centroid)
        values = np.full(sums.shape[0], np.nan)
        values[has] = np.clip(centroid[has], x_min, x_max)
        found[converged] = values
    return converged, found


def _points_group(x: np.ndarray, y: np.ndarray, metric: MetricSettings) -> tuple[np.ndarray, np.ndarray] | None:
    """The "no fit" metric of every row, read off the measured points as ``query.metric_from_spectrum`` reads it: the maximum is the wavelength of
    the highest point (the first of equal ones), the centroid the intensity-weighted mean wavelength by the trapezoid rule, clipped to the points'
    range (none when the area is below 1e-12). The same numpy functions on the same numbers, a row at a time in one call."""
    n_spectra = y.shape[0]
    good = np.ones(n_spectra, dtype=bool)
    found = np.full(n_spectra, np.nan)
    key = str(metric.metric_key).strip().lower()
    if key == "maximum":
        found[:] = x[np.argmax(y, axis=1)]
    elif key == "centroid":
        if x.size < 2:
            found[:] = x[0]
            return good, found
        area = np.trapezoid(y, x, axis=1)
        with np.errstate(all="ignore"):
            centroid = np.trapezoid(y * x[None, :], x, axis=1) / area
        has = np.isfinite(area) & (np.abs(area) >= _MIN_AREA) & np.isfinite(centroid)
        found[has] = np.clip(centroid[has], x[0], x[-1])
    return good, found


def sampled_curve(grid: np.ndarray, amplitude: np.ndarray, centre: np.ndarray, sigma: np.ndarray, offset: np.ndarray) -> np.ndarray:
    """``amplitude * exp(-(grid - centre)^2 / (2 sigma^2)) + offset`` for column vectors of parameters: ``[spectrum, grid]``. Built in place
    (one array instead of six temporaries; the same operations in the same order, so the same numbers as the expression above)."""
    curve = grid[None, :] - centre
    np.square(curve, out=curve)
    curve /= -(2.0 * sigma * sigma)  # -(d^2) / (2 s^2): negating the divisor is exact
    np.exp(curve, out=curve)
    curve *= amplitude
    curve += offset
    return curve


def trapezoid_weights(grid: np.ndarray) -> np.ndarray:
    """``[len(grid), 2]``: ``curve @ w`` gives ``(trapezoid(curve, grid), trapezoid(curve * grid, grid))`` for every row of ``curve``."""
    weights = np.zeros_like(grid, dtype=np.float64)
    step = np.diff(grid)
    weights[:-1] += step / 2.0
    weights[1:] += step / 2.0
    return np.stack([weights, weights * grid], axis=1)


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


# -- the template shift (template_shift.py) ----------------------------------------------------------------------------------------------


def formula_arrays(data: TraceInput, formula_key: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """``(wavelengths, values, valid, wanted)`` of one ROI for the whole-trace methods (the template shift, the polynomial order check): wavelengths ascending (non-finite ones dropped), the formula values ``[cube, wavelength]``, which entries
    may be used, and which cubes have a result at all (scanned and at least one usable wavelength)."""
    wl = np.asarray(data.wavelengths, dtype=np.float64)
    keep = np.flatnonzero(np.isfinite(wl))
    keep = keep[np.argsort(wl[keep], kind="stable")]
    with np.errstate(all="ignore"):  # unusable entries may be NaN; they are never used
        values = formula_values(data.sample[:, keep], data.reference[:, keep], formula_key)
    wanted = data.scanned & data.usable[:, keep].any(axis=1)
    valid = data.usable[:, keep] & np.isfinite(values) & wanted[:, None]
    return wl[keep], values, valid, wanted


def _reference_cubes(data: TraceInput, metric: MetricSettings, wanted: np.ndarray) -> np.ndarray:
    """Boolean per cube: in the reference range of the settings and with a result."""
    index = np.arange(data.sample.shape[0]) if data.cube_indices is None else np.asarray(data.cube_indices)
    first, last = metric.reference_cube_range()
    return wanted & (index >= first) & (index <= last)


def reference_template(data: TraceInput, formula_key: str, metric: MetricSettings) -> ReferenceTemplate | None:
    """The reference spectrum of one ROI for these settings (mean of the reference cubes), or None when no reference cube has enough data."""
    wl, values, valid, wanted = formula_arrays(data, formula_key)
    if wl.size == 0:
        return None
    return build_template(wl, values, valid, _reference_cubes(data, metric, wanted))


def template_fit_of_input(
    data: TraceInput, formula_key: str, metric: MetricSettings, *, diagnostics: bool = True
) -> tuple[TemplateFit, ReferenceTemplate] | None:
    """The template shift of every cube of one ROI, with the reference it used; None when the reference cubes hold no usable data (the trace is then a gap)."""
    wl, values, valid, wanted = formula_arrays(data, formula_key)
    if wl.size == 0:
        return None
    template = build_template(wl, values, valid, _reference_cubes(data, metric, wanted))
    if template is None:
        return None
    return fit_template_shift(template, wl, values, valid, diagnostics=diagnostics), template


def template_trace(data: TraceInput, formula_key: str, metric: MetricSettings) -> np.ndarray:
    """The metric of one ROI at every cube for the template shift: the template's peak + the shift, in nm; NaN where there is none. The tilt diagnostics are not computed here."""
    found = template_fit_of_input(data, formula_key, metric, diagnostics=False)
    if found is None:
        return np.full(data.sample.shape[0], np.nan)
    return found[0].position_nm


@dataclass(frozen=True)
class TemplateDiagnostics:
    """What the template shift measured besides the position, per cube of one ROI (see ``template_shift.py``): ``shift_nm``, ``scale`` (amplitude relative to the reference), ``rms`` of the
    residual, ``tilt`` (change of the baseline across the range, from the joint shift-and-tilt fit whose shift is ``tilt_shift_nm``), ``kappa`` (tilt per nm of that shift over this trace) and
    ``tilt_beyond`` (``tilt - kappa x tilt_shift``: baseline drift that the shift does not explain)."""

    position_nm: np.ndarray
    shift_nm: np.ndarray
    scale: np.ndarray
    rms: np.ndarray
    tilt: np.ndarray
    tilt_shift_nm: np.ndarray
    kappa: float
    tilt_beyond: np.ndarray
    reference_cubes: int


def template_diagnostics(data: TraceInput, formula_key: str, metric: MetricSettings) -> TemplateDiagnostics | None:
    """The diagnostics of the template shift of one ROI; None when there is no reference."""
    found = template_fit_of_input(data, formula_key, metric, diagnostics=True)
    if found is None:
        return None
    fit, template = found
    kappa, beyond = response_law(fit.tilt_shift_nm, fit.tilt)
    return TemplateDiagnostics(fit.position_nm, fit.shift_nm, fit.scale, fit.rms, fit.tilt, fit.tilt_shift_nm, kappa, beyond, template.n_cubes)
