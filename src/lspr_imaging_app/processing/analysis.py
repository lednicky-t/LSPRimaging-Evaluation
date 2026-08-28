from __future__ import annotations

from dataclasses import replace

import numpy as np
from scipy.optimize import curve_fit

from lspr_imaging_app.domain.models import FitResult, FormulaSpectrumResult


FORMULA_KEYS: tuple[str, ...] = ("absorbance", "ratio", "relative_change", "mod_absorbance")


def formula_value(sample_value: float, reference_value: float, formula_key: str) -> float:
    """Combine a ROI pair's reduced sample/reference values (see
    processing/roi_math.py) into the final per-wavelength value. formula_key
    selects among a fixed menu, not a user-editable expression: a typo'd
    free-form formula would silently produce wrong scientific data with no
    way to catch it in review, so only these documented, reproducible
    formulas are offered (see the "roi_math" panel help entry)."""
    sample_value = max(float(sample_value), 1e-9)
    reference_value = max(float(reference_value), 1e-9)
    key = str(formula_key).strip().lower()
    if key == "ratio":
        return float(sample_value / reference_value)
    if key == "relative_change":
        return float((reference_value - sample_value) / reference_value)
    if key == "mod_absorbance":
        return float(-1000.0 * np.log10(sample_value / reference_value))
    return float(np.log10(reference_value / sample_value))  # "absorbance" (default)


def formula_values_from_reduced_values(sample_reduced_value: np.ndarray, reference_reduced_value: np.ndarray, formula_key: str) -> np.ndarray:
    """Vectorized `formula_value`, for re-expressing an already-reduced
    spectrum (one sample/reference value per wavelength, from whichever
    Reduction method produced it - not necessarily an arithmetic mean) under
    a different formula - see `project_formula_spectrum`. Must mirror
    `formula_value` exactly; locked together by a test in
    test_lspri_analysis.py."""
    sample = np.maximum(np.asarray(sample_reduced_value, dtype=np.float64), 1e-9)
    reference = np.maximum(np.asarray(reference_reduced_value, dtype=np.float64), 1e-9)
    key = str(formula_key).strip().lower()
    if key == "ratio":
        return sample / reference
    if key == "relative_change":
        return (reference - sample) / reference
    if key == "mod_absorbance":
        return -1000.0 * np.log10(sample / reference)
    return np.log10(reference / sample)  # "absorbance" (default)


def project_formula_spectrum(result: FormulaSpectrumResult | None, formula_key: str) -> FormulaSpectrumResult | None:
    """Re-express an already-computed FormulaSpectrumResult under a
    different formula, purely from its stored sample_reduced_value/
    reference_reduced_value - no pixel access. Safe because those two values
    never depend on formula_key (see processing/roi_math.py's
    reduce_sample_and_reference); only the cheap final formula_value()
    combine step does. This is what lets switching the active ROI's-formula
    selection be instant instead of triggering a full recompute - see
    AreaRoiDetectionSettings.formula_key and gui/analysis_worker_mixin.py's
    _active_formula_key().

    Recurses into area_roi_results (each is itself a per-ROI result, so
    derivation is exact); the combined top-level curve for a multi-ROI
    result is the per-wavelength mean over whichever ROIs are finite at that
    wavelength, matching _formula_spectrum_task's own combination rule (a
    NaN sample/reference yields NaN under every formula, so the finite/NaN
    pattern is identical regardless of which formula produced the original
    result - see that function's docstring)."""
    if result is None:
        return None
    key = str(formula_key).strip().lower()
    if str(result.formula_key).strip().lower() == key:
        return result
    projected_roi_results = {
        roi_id: project_formula_spectrum(roi_result, key) for roi_id, roi_result in result.area_roi_results.items()
    }
    if projected_roi_results:
        stacked = np.stack([roi_result.formula_values for roi_result in projected_roi_results.values()])
        finite_counts = np.sum(np.isfinite(stacked), axis=0)
        sums = np.nansum(stacked, axis=0)
        formula_values = np.divide(
            sums, finite_counts, out=np.full(sums.shape, np.nan), where=finite_counts > 0
        )
    else:
        formula_values = formula_values_from_reduced_values(result.sample_reduced_value, result.reference_reduced_value, key)
    return replace(result, formula_values=formula_values, formula_key=key, area_roi_results=projected_roi_results)


def project_reduction_result(result: FormulaSpectrumResult | None, reduction_method: str, formula_key: str) -> FormulaSpectrumResult | None:
    """Re-express an already-computed FormulaSpectrumResult under a
    different Reduction method, purely from its `reduced_values_by_method`
    dict (populated at pixel-extraction time by
    processing/roi_math.py's reduce_sample_and_reference_all_methods) - no
    pixel access. Unlike `project_formula_spectrum`, this can genuinely miss:
    `reduced_values_by_method` only holds the methods actually computed
    alongside the cube's own active method at extraction time (always
    mean/median/plane_fit, plus trimmed_mean at whatever Trim % was set
    then) - a `trimmed_mean` entry at a now-different Trim % is stale and
    won't be present. Returns None on a miss, same contract as a cache miss,
    so the caller falls back to a full recompute.

    Re-derives formula_values for `formula_key` too (a reduction change
    changes the underlying sample/reference values, so the previously-active
    formula's curve is no longer valid). Recurses into area_roi_results
    FIRST, same pattern (and same reason) as project_formula_spectrum: a
    multi-ROI combined result's own top-level reduced_values_by_method is
    beside the point once real per-ROI results exist to derive from - only a
    leaf (single-ROI) result ever reads its own reduced_values_by_method
    directly. All-or-nothing across ROIs, same contract as
    _combined_formula_spectrum_results_from_ram_or_disk: one member ROI
    missing the requested method misses the whole combined result too."""
    if result is None:
        return None
    method = str(reduction_method).strip().lower()
    key = str(formula_key).strip().lower()
    if str(result.reduction_method).strip().lower() == method:
        return project_formula_spectrum(result, key)
    if result.area_roi_results:
        projected_roi_results: dict[int, FormulaSpectrumResult] = {}
        for roi_id, roi_result in result.area_roi_results.items():
            projected_roi_result = project_reduction_result(roi_result, method, key)
            if projected_roi_result is None:
                return None
            projected_roi_results[roi_id] = projected_roi_result
        roi_values = list(projected_roi_results.values())
        formula_stacked = np.stack([roi_result.formula_values for roi_result in roi_values])
        finite_counts = np.sum(np.isfinite(formula_stacked), axis=0)
        formula_values = np.divide(
            np.nansum(formula_stacked, axis=0), finite_counts, out=np.full(formula_stacked.shape[1:], np.nan), where=finite_counts > 0
        )
        # Sample/reference combine the same way, over the same finite mask as
        # formula_values (see this function's docstring / _formula_spectrum_
        # task's own combination rule: a NaN sample/reference yields NaN
        # under every formula, so the finite/NaN pattern is shared).
        sample_stacked = np.stack([roi_result.sample_reduced_value for roi_result in roi_values])
        reference_stacked = np.stack([roi_result.reference_reduced_value for roi_result in roi_values])
        sample_reduced_value = np.divide(
            np.nansum(sample_stacked, axis=0), finite_counts, out=np.full(sample_stacked.shape[1:], np.nan), where=finite_counts > 0
        )
        reference_reduced_value = np.divide(
            np.nansum(reference_stacked, axis=0), finite_counts, out=np.full(reference_stacked.shape[1:], np.nan), where=finite_counts > 0
        )
        return replace(
            result,
            formula_values=formula_values,
            formula_key=key,
            sample_reduced_value=sample_reduced_value,
            reference_reduced_value=reference_reduced_value,
            reduction_method=method,
            area_roi_results=projected_roi_results,
        )
    entry = result.reduced_values_by_method.get(method)
    if entry is None:
        return None
    sample_reduced_value, reference_reduced_value = entry
    formula_values = formula_values_from_reduced_values(sample_reduced_value, reference_reduced_value, key)
    return replace(
        result,
        formula_values=formula_values,
        formula_key=key,
        sample_reduced_value=np.asarray(sample_reduced_value, dtype=np.float64),
        reference_reduced_value=np.asarray(reference_reduced_value, dtype=np.float64),
        reduction_method=method,
    )


def absorbance_from_means(sample_mean: float, reference_mean: float) -> float:
    return formula_value(sample_mean, reference_mean, "absorbance")


def fit_polynomial_curve(
    wavelengths_nm,
    formula_values,
    poly_order: int = 3,
    wl_min: float | None = None,
    wl_max: float | None = None,
    sample_count: int = 400,
) -> FitResult:
    x = np.asarray(wavelengths_nm, dtype=np.float64)
    y = np.asarray(formula_values, dtype=np.float64)
    valid_mask = np.isfinite(x) & np.isfinite(y)
    x = x[valid_mask]
    y = y[valid_mask]

    if wl_min is not None:
        mask = x >= float(wl_min)
        x = x[mask]
        y = y[mask]
    if wl_max is not None:
        mask = x <= float(wl_max)
        x = x[mask]
        y = y[mask]

    if x.size < 2:
        return FitResult(x.copy(), y.copy(), np.array([]), None, None, None)

    order = np.argsort(x)
    x = x[order]
    y = y[order]

    # Capped at half the point count, not (point count - 1): letting order
    # approach the point count turns the fit into an (near-)exact
    # interpolation through every point, noise included, which for a
    # polynomial means wild oscillation - worst right at the ends of the
    # range. Since the peak/centroid search below always considers x_min/
    # x_max as fallback candidates, a large oscillation spike at one edge can
    # outscore the real peak and get reported as the metric, landing the
    # "peak" or "centroid" at the edge of the fitted window instead of near
    # the actual feature. Halving the cap keeps enough residual points for
    # the fit to average out noise instead of chasing it.
    effective_order = min(max(int(poly_order), 1), max(int(x.size) // 2, 1))
    polynomial = np.polynomial.Polynomial.fit(x, y, effective_order).convert()
    x_min = float(np.min(x))
    x_max = float(np.max(x))

    if x_max <= x_min:
        fitted_x = np.asarray([x_min], dtype=np.float64)
    else:
        fitted_x = np.linspace(x_min, x_max, max(int(sample_count), int(x.size)))
    fitted_y = np.asarray(polynomial(fitted_x), dtype=np.float64)

    derivative = polynomial.deriv()
    critical_points = derivative.roots()
    critical_points = critical_points.real[np.abs(critical_points.imag) < 1.0e-8]
    critical_points = critical_points[(critical_points >= x_min) & (critical_points <= x_max)]
    candidate_x = np.concatenate(
        [
            np.asarray([x_min, x_max], dtype=np.float64),
            np.asarray(critical_points, dtype=np.float64),
        ]
    )
    candidate_y = np.asarray(polynomial(candidate_x), dtype=np.float64)
    peak_wavelength = None
    peak_value = None
    finite_candidate_mask = np.isfinite(candidate_x) & np.isfinite(candidate_y)
    if np.any(finite_candidate_mask):
        candidate_x = candidate_x[finite_candidate_mask]
        candidate_y = candidate_y[finite_candidate_mask]
        peak_index = int(np.argmax(candidate_y))
        peak_wavelength = float(candidate_x[peak_index])
        peak_value = float(candidate_y[peak_index])

    centroid = None
    integral = polynomial.integ()
    weighted_integral = (polynomial * np.polynomial.Polynomial([0.0, 1.0])).integ()
    area = float(integral(x_max) - integral(x_min))
    if np.isfinite(area) and abs(area) > 1.0e-12:
        weighted_area = float(weighted_integral(x_max) - weighted_integral(x_min))
        centroid_value = weighted_area / area
        if np.isfinite(centroid_value):
            centroid = float(np.clip(centroid_value, x_min, x_max))

    return FitResult(
        fitted_wavelengths_nm=np.asarray(fitted_x, dtype=np.float64),
        fitted_values=np.asarray(fitted_y, dtype=np.float64),
        coefficients=np.asarray(polynomial.coef, dtype=np.float64),
        peak_wavelength_nm=peak_wavelength,
        centroid_nm=centroid,
        peak_value=peak_value,
    )


def _gaussian_model(x: np.ndarray, amplitude: float, center: float, sigma: float, offset: float) -> np.ndarray:
    return amplitude * np.exp(-((x - center) ** 2) / (2.0 * sigma * sigma)) + offset


def fit_gaussian_curve(
    wavelengths_nm,
    formula_values,
    wl_min: float | None = None,
    wl_max: float | None = None,
    sample_count: int = 400,
) -> FitResult:
    """Nonlinear least-squares Gaussian peak fit (scipy.optimize.curve_fit),
    the "Gauss" Fitting option in Metric trace. coefficients holds
    [amplitude, center, sigma, offset] (not polynomial coefficients, despite
    the shared field name - nothing else in the codebase interprets
    FitResult.coefficients, see fit_polynomial_curve above for the
    polynomial case). peak_wavelength_nm is the fitted center, clamped to
    the fitted window; centroid_nm is computed the same way as
    fit_polynomial_curve's (intensity-weighted mean over the sampled fitted
    curve), which for a symmetric Gaussian lands at the same place as the
    center - computing it identically keeps Metric trace's Centroid option
    meaningful regardless of which fit produced the curve.

    Falls back to an empty FitResult (same shape as fit_polynomial_curve's
    "too few points" case) if there aren't enough points to fit 4
    parameters, or if curve_fit fails to converge - a stuck/flat/noisy
    spectrum shouldn't crash the sensorgram loop, it should just skip that
    frame's metric (same as fit_polynomial_curve already does elsewhere).
    """
    x = np.asarray(wavelengths_nm, dtype=np.float64)
    y = np.asarray(formula_values, dtype=np.float64)
    valid_mask = np.isfinite(x) & np.isfinite(y)
    x = x[valid_mask]
    y = y[valid_mask]

    if wl_min is not None:
        mask = x >= float(wl_min)
        x = x[mask]
        y = y[mask]
    if wl_max is not None:
        mask = x <= float(wl_max)
        x = x[mask]
        y = y[mask]

    if x.size < 4:
        return FitResult(x.copy(), y.copy(), np.array([]), None, None, None)

    order = np.argsort(x)
    x = x[order]
    y = y[order]
    x_min = float(np.min(x))
    x_max = float(np.max(x))

    offset_guess = float(np.min(y))
    amplitude_guess = float(np.max(y) - offset_guess)
    center_guess = float(x[int(np.argmax(y))])
    sigma_guess = max((x_max - x_min) / 6.0, 1.0e-3)
    try:
        coefficients, _covariance = curve_fit(
            _gaussian_model,
            x,
            y,
            p0=[amplitude_guess, center_guess, sigma_guess, offset_guess],
            maxfev=10000,
        )
    except (RuntimeError, ValueError):
        return FitResult(x.copy(), y.copy(), np.array([]), None, None, None)

    amplitude, center, sigma, offset = coefficients
    sigma = abs(float(sigma))

    if x_max <= x_min:
        fitted_x = np.asarray([x_min], dtype=np.float64)
    else:
        fitted_x = np.linspace(x_min, x_max, max(int(sample_count), int(x.size)))
    fitted_y = np.asarray(_gaussian_model(fitted_x, amplitude, center, sigma, offset), dtype=np.float64)

    peak_wavelength = float(np.clip(center, x_min, x_max))
    peak_value = float(_gaussian_model(np.asarray([peak_wavelength]), amplitude, center, sigma, offset)[0])

    centroid = None
    area = float(np.trapezoid(fitted_y, fitted_x))
    if np.isfinite(area) and abs(area) > 1.0e-12:
        weighted_area = float(np.trapezoid(fitted_y * fitted_x, fitted_x))
        centroid_value = weighted_area / area
        if np.isfinite(centroid_value):
            centroid = float(np.clip(centroid_value, x_min, x_max))

    return FitResult(
        fitted_wavelengths_nm=np.asarray(fitted_x, dtype=np.float64),
        fitted_values=fitted_y,
        coefficients=np.asarray([amplitude, center, sigma, offset], dtype=np.float64),
        peak_wavelength_nm=peak_wavelength,
        centroid_nm=centroid,
        peak_value=peak_value,
    )


def fit_curve_for_method(
    wavelengths_nm,
    formula_values,
    fit_method_key: str,
    poly_order: int = 3,
    wl_min: float | None = None,
    wl_max: float | None = None,
) -> FitResult:
    """Single dispatch point for "which fit implementation does this Fitting
    key use" - both the live spectrum plot (gui/plot_manager.py) and the
    sensorgram loop (gui/analysis_tasks.py) call this instead of duplicating
    the poly/gaussian choice."""
    key = str(fit_method_key).strip().lower()
    if key == "gaussian":
        return fit_gaussian_curve(wavelengths_nm, formula_values, wl_min=wl_min, wl_max=wl_max)
    return fit_polynomial_curve(wavelengths_nm, formula_values, poly_order=poly_order, wl_min=wl_min, wl_max=wl_max)


def metric_value_from_fit(fit: FitResult, metric_key: str) -> tuple[float | None, float | None]:
    key = str(metric_key).strip().lower()
    if key == "maximum":
        return fit.peak_wavelength_nm, fit.peak_value
    if key == "centroid":
        if fit.centroid_nm is None:
            return None, None
        if fit.fitted_wavelengths_nm.size and fit.fitted_values.size:
            centroid_y = float(np.interp(fit.centroid_nm, fit.fitted_wavelengths_nm, fit.fitted_values))
        else:
            centroid_y = None
        return fit.centroid_nm, centroid_y
    return None, None


def metric_value_from_spectrum(
    wavelengths_nm, formula_values, metric_key: str, wl_min: float | None = None, wl_max: float | None = None
) -> tuple[float | None, float | None]:
    """Maximum/Centroid read straight off the raw formula-spectrum points - no
    curve fit involved. Mirrors metric_value_from_fit's two metrics but is
    used when Fitting is "None": Maximum is a plain argmax and Centroid is
    the intensity-weighted mean wavelength (trapezoidal), both of which are
    well-defined without a fitted curve."""
    x = np.asarray(wavelengths_nm, dtype=np.float64)
    y = np.asarray(formula_values, dtype=np.float64)
    valid_mask = np.isfinite(x) & np.isfinite(y)
    x = x[valid_mask]
    y = y[valid_mask]
    if wl_min is not None:
        mask = x >= float(wl_min)
        x = x[mask]
        y = y[mask]
    if wl_max is not None:
        mask = x <= float(wl_max)
        x = x[mask]
        y = y[mask]
    if x.size == 0:
        return None, None

    order = np.argsort(x)
    x = x[order]
    y = y[order]

    key = str(metric_key).strip().lower()
    if key == "maximum":
        peak_index = int(np.argmax(y))
        return float(x[peak_index]), float(y[peak_index])
    if key == "centroid":
        if x.size < 2:
            return float(x[0]), float(y[0])
        area = float(np.trapezoid(y, x))
        if not np.isfinite(area) or abs(area) < 1.0e-12:
            return None, None
        weighted_area = float(np.trapezoid(y * x, x))
        centroid = weighted_area / area
        if not np.isfinite(centroid):
            return None, None
        centroid = float(np.clip(centroid, float(x[0]), float(x[-1])))
        centroid_y = float(np.interp(centroid, x, y))
        return centroid, centroid_y
    return None, None
