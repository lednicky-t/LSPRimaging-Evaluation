from __future__ import annotations

import numpy as np
from scipy.optimize import curve_fit

from lspr_imaging_app.domain.models import FitResult


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
