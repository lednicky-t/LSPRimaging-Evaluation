"""Pillar I's layers 2 and 3 - formula spectrum, curve fit, metric value
(`docs/analysis_pipeline_layers.md`, sketch §6).

Ports `processing/analysis.py`'s formula/fit/metric functions verbatim.
This genuinely is a "mostly as-is" port, unlike `analysis/tasks.py`'s was:
that file's stable-build counterpart was entangled with batch dispatch and
caching, while these are already pure functions over two arrays. The
clamps, fallbacks and candidate-point choices below are all carried over
with their original reasoning intact - see the comments, several of which
record a real bug that was fixed once already.

**What makes this layer separate at all**: `compute_cell` stores raw
reduced (sample, reference) pairs and stops there, so everything in this
file is a re-derivation of already-stored numbers, never a recompute.
Changing formula, fit or metric costs one pass over arrays already in
memory; it never re-reads a pixel and never invalidates a stored cell
(sketch §6's last bullet).

**Never call this from the GUI thread for a whole dataset.** One gaussian
fit is ~1 ms, so a 160-ROI x 300-cube trace is ~48 s of pure fitting.
`engine.py` runs it on the analysis worker and caches the result; AGENTS.md
forbids a "live preview" fit on the GUI thread outright.

No Qt import allowed in this file (AGENTS.md testing rule).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import curve_fit

FORMULA_KEYS: tuple[str, ...] = ("absorbance", "ratio", "relative_change", "mod_absorbance")
"""A fixed menu, not a user-editable expression - carried over from the old
app along with the reason: a typo'd free-form formula would silently
produce wrong scientific data with no way to catch it in review."""

_VALUE_FLOOR = 1e-9
"""Reduced values are floored before any division or log. A reduced value
of 0 or below is not physically meaningful (it means the ROI saw no light,
or a background subtraction over-subtracted), and the alternative to a
floor is `-inf`/`nan` propagating silently into a fitted peak position."""


@dataclass(slots=True)
class FormulaSpectrum:
    """One ROI's spectrum at one spectral cube, under one formula.

    Carries the reduced pairs it was derived from, not just the result:
    that is what lets a formula change be re-projected from this object
    with no access to the store at all (the old app's
    `project_formula_spectrum`), and it keeps a spectrum self-describing
    about which formula produced it."""

    wavelengths_nm: np.ndarray
    values: np.ndarray
    sample_values: np.ndarray
    reference_values: np.ndarray
    formula_key: str = "absorbance"


@dataclass(slots=True)
class FitResult:
    """A fitted curve plus the two positions a metric can be read from.

    `coefficients` means polynomial coefficients for a polynomial fit and
    `[amplitude, center, sigma, offset]` for a gaussian one - a shared
    field with two meanings, carried over from the old app, where nothing
    outside the fit functions themselves ever interprets it."""

    fitted_wavelengths_nm: np.ndarray
    fitted_values: np.ndarray
    coefficients: np.ndarray = field(default_factory=lambda: np.array([]))
    peak_wavelength_nm: float | None = None
    centroid_nm: float | None = None
    peak_value: float | None = None


def formula_values(
    sample_values,
    reference_values,
    formula_key: str,
) -> np.ndarray:
    """Combine a ROI's reduced sample/reference values into one value per
    wavelength.

    Vectorized only - the old app kept a scalar `formula_value` alongside
    this and had to pin the two together with a test to stop them drifting.
    Nothing in the rewrite needs the scalar form (a single wavelength is a
    length-1 array), so there is one implementation and nothing to drift.
    """
    sample = np.maximum(np.asarray(sample_values, dtype=np.float64), _VALUE_FLOOR)
    reference = np.maximum(np.asarray(reference_values, dtype=np.float64), _VALUE_FLOOR)
    key = str(formula_key).strip().lower()
    if key == "ratio":
        return sample / reference
    if key == "relative_change":
        return (reference - sample) / reference
    if key == "mod_absorbance":
        return -1000.0 * np.log10(sample / reference)
    return np.log10(reference / sample)  # "absorbance" (default)


def formula_spectrum(
    wavelengths_nm,
    sample_values,
    reference_values,
    formula_key: str,
) -> FormulaSpectrum:
    """Layer 2: one ROI's stored pairs -> that ROI's own spectrum.

    **Per ROI, never pooled.** `docs/analysis_pipeline_layers.md`'s
    non-negotiable rule, and a real bug that was fixed once: pixels from
    different physical apertures must never be combined before the ratio,
    because pooling-then-dividing is not the same calculation as dividing
    each ROI then averaging. This function only ever sees one ROI's
    numbers, which is the structural version of that guarantee."""
    wavelengths = np.asarray(wavelengths_nm, dtype=np.float64)
    sample = np.asarray(sample_values, dtype=np.float64)
    reference = np.asarray(reference_values, dtype=np.float64)
    return FormulaSpectrum(
        wavelengths_nm=wavelengths,
        values=formula_values(sample, reference, formula_key),
        sample_values=sample,
        reference_values=reference,
        formula_key=str(formula_key).strip().lower(),
    )


def _windowed(x: np.ndarray, y: np.ndarray, wl_min: float | None, wl_max: float | None):
    """Finite points within [wl_min, wl_max], sorted by wavelength.

    Shared by every function below so the fit and the metric can never
    disagree about which points are in the window - in the old app each
    one re-implemented this same block."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    valid = np.isfinite(x) & np.isfinite(y)
    x, y = x[valid], y[valid]
    if wl_min is not None:
        keep = x >= float(wl_min)
        x, y = x[keep], y[keep]
    if wl_max is not None:
        keep = x <= float(wl_max)
        x, y = x[keep], y[keep]
    order = np.argsort(x)
    return x[order], y[order]


def fit_polynomial(
    wavelengths_nm,
    values,
    poly_order: int = 3,
    wl_min: float | None = None,
    wl_max: float | None = None,
    sample_count: int = 400,
) -> FitResult:
    x, y = _windowed(wavelengths_nm, values, wl_min, wl_max)
    if x.size < 2:
        return FitResult(x.copy(), y.copy())

    # Capped at half the point count, not (point count - 1): letting order
    # approach the point count turns the fit into a near-exact interpolation
    # through every point, noise included, which for a polynomial means wild
    # oscillation - worst right at the ends of the range. Since the peak/
    # centroid search below always considers x_min/x_max as fallback
    # candidates, a large oscillation spike at one edge can outscore the real
    # peak and get reported as the metric, landing the "peak" at the edge of
    # the fitted window instead of near the actual feature. Halving the cap
    # keeps enough residual points for the fit to average out noise instead
    # of chasing it.
    effective_order = min(max(int(poly_order), 1), max(int(x.size) // 2, 1))
    polynomial = np.polynomial.Polynomial.fit(x, y, effective_order).convert()
    x_min, x_max = float(np.min(x)), float(np.max(x))

    if x_max <= x_min:
        fitted_x = np.asarray([x_min], dtype=np.float64)
    else:
        fitted_x = np.linspace(x_min, x_max, max(int(sample_count), int(x.size)))
    fitted_y = np.asarray(polynomial(fitted_x), dtype=np.float64)

    # The true maximum of a polynomial on a closed interval is at a
    # stationary point or at an endpoint - so both are candidates, rather
    # than taking an argmax over the sampled curve.
    derivative = polynomial.deriv()
    critical = derivative.roots()
    critical = critical.real[np.abs(critical.imag) < 1.0e-8]
    critical = critical[(critical >= x_min) & (critical <= x_max)]
    candidate_x = np.concatenate([np.asarray([x_min, x_max], dtype=np.float64), np.asarray(critical, dtype=np.float64)])
    candidate_y = np.asarray(polynomial(candidate_x), dtype=np.float64)
    peak_wavelength = peak_value = None
    finite = np.isfinite(candidate_x) & np.isfinite(candidate_y)
    if np.any(finite):
        candidate_x, candidate_y = candidate_x[finite], candidate_y[finite]
        peak_index = int(np.argmax(candidate_y))
        peak_wavelength = float(candidate_x[peak_index])
        peak_value = float(candidate_y[peak_index])

    # Analytic, not trapezoidal: the polynomial's own integral is exact,
    # where the gaussian case below has to integrate its sampled curve.
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
        fitted_wavelengths_nm=fitted_x,
        fitted_values=fitted_y,
        coefficients=np.asarray(polynomial.coef, dtype=np.float64),
        peak_wavelength_nm=peak_wavelength,
        centroid_nm=centroid,
        peak_value=peak_value,
    )


def _gaussian_model(x: np.ndarray, amplitude: float, center: float, sigma: float, offset: float) -> np.ndarray:
    return amplitude * np.exp(-((x - center) ** 2) / (2.0 * sigma * sigma)) + offset


def fit_gaussian(
    wavelengths_nm,
    values,
    wl_min: float | None = None,
    wl_max: float | None = None,
    sample_count: int = 400,
) -> FitResult:
    """`coefficients` holds `[amplitude, center, sigma, offset]`.

    Falls back to an empty `FitResult` - the same shape as the too-few-
    points case - when there aren't enough points for 4 parameters or when
    `curve_fit` fails to converge. A stuck, flat or very noisy spectrum
    should cost that one cube its metric, not kill the run."""
    x, y = _windowed(wavelengths_nm, values, wl_min, wl_max)
    if x.size < 4:
        return FitResult(x.copy(), y.copy())

    x_min, x_max = float(np.min(x)), float(np.max(x))
    offset_guess = float(np.min(y))
    amplitude_guess = float(np.max(y) - offset_guess)
    center_guess = float(x[int(np.argmax(y))])
    sigma_guess = max((x_max - x_min) / 6.0, 1.0e-3)
    try:
        coefficients, _covariance = curve_fit(
            _gaussian_model, x, y,
            p0=[amplitude_guess, center_guess, sigma_guess, offset_guess],
            maxfev=10000,
        )
    except (RuntimeError, ValueError):
        return FitResult(x.copy(), y.copy())

    amplitude, center, sigma, offset = coefficients
    sigma = abs(float(sigma))

    if x_max <= x_min:
        fitted_x = np.asarray([x_min], dtype=np.float64)
    else:
        fitted_x = np.linspace(x_min, x_max, max(int(sample_count), int(x.size)))
    fitted_y = np.asarray(_gaussian_model(fitted_x, amplitude, center, sigma, offset), dtype=np.float64)

    peak_wavelength = float(np.clip(center, x_min, x_max))
    peak_value = float(_gaussian_model(np.asarray([peak_wavelength]), amplitude, center, sigma, offset)[0])

    # Computed the same way as the polynomial's, so "Centroid" stays a
    # comparable number regardless of which fit produced the curve - for a
    # symmetric gaussian it lands on the centre anyway.
    centroid = None
    area = float(np.trapezoid(fitted_y, fitted_x))
    if np.isfinite(area) and abs(area) > 1.0e-12:
        weighted_area = float(np.trapezoid(fitted_y * fitted_x, fitted_x))
        centroid_value = weighted_area / area
        if np.isfinite(centroid_value):
            centroid = float(np.clip(centroid_value, x_min, x_max))

    return FitResult(
        fitted_wavelengths_nm=fitted_x,
        fitted_values=fitted_y,
        coefficients=np.asarray([amplitude, center, sigma, offset], dtype=np.float64),
        peak_wavelength_nm=peak_wavelength,
        centroid_nm=centroid,
        peak_value=peak_value,
    )


def fit_spectrum(
    spectrum: FormulaSpectrum,
    fit_method: str,
    *,
    poly_order: int = 3,
    wl_min: float | None = None,
    wl_max: float | None = None,
) -> FitResult | None:
    """Single dispatch point for "which fit does this method key use" -
    both the spectrum plot and the sensorgram loop go through here rather
    than duplicating the choice, the same role the old app's
    `fit_curve_for_method` played.

    Returns `None` for `"none"`, which is a real setting, not a failure:
    the metric is then read straight off the measured points (see
    `metric_from_spectrum`). `None` and an empty `FitResult` mean different
    things - "no fit was asked for" versus "a fit was asked for and did not
    converge"."""
    key = str(fit_method).strip().lower()
    if key == "none":
        return None
    if key == "gaussian":
        return fit_gaussian(spectrum.wavelengths_nm, spectrum.values, wl_min=wl_min, wl_max=wl_max)
    return fit_polynomial(
        spectrum.wavelengths_nm, spectrum.values,
        poly_order=poly_order, wl_min=wl_min, wl_max=wl_max,
    )


def metric_from_fit(fit: FitResult, metric_key: str) -> tuple[float | None, float | None]:
    """The metric as (x, y) - a wavelength and the curve's value there."""
    key = str(metric_key).strip().lower()
    if key == "maximum":
        return fit.peak_wavelength_nm, fit.peak_value
    if key == "centroid":
        if fit.centroid_nm is None:
            return None, None
        if fit.fitted_wavelengths_nm.size and fit.fitted_values.size:
            return fit.centroid_nm, float(np.interp(fit.centroid_nm, fit.fitted_wavelengths_nm, fit.fitted_values))
        return fit.centroid_nm, None
    return None, None


def metric_from_spectrum(
    spectrum: FormulaSpectrum,
    metric_key: str,
    *,
    wl_min: float | None = None,
    wl_max: float | None = None,
) -> tuple[float | None, float | None]:
    """The same two metrics read straight off the measured points, for when
    the fit method is `"none"`. Maximum is a plain argmax; centroid is the
    intensity-weighted mean wavelength by trapezoidal integration - both
    well-defined without a fitted curve."""
    x, y = _windowed(spectrum.wavelengths_nm, spectrum.values, wl_min, wl_max)
    if x.size == 0:
        return None, None

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
        centroid = float(np.trapezoid(y * x, x)) / area
        if not np.isfinite(centroid):
            return None, None
        centroid = float(np.clip(centroid, float(x[0]), float(x[-1])))
        return centroid, float(np.interp(centroid, x, y))
    return None, None


def metric_value(
    spectrum: FormulaSpectrum,
    fit_method: str,
    metric_key: str,
    *,
    poly_order: int = 3,
    wl_min: float | None = None,
    wl_max: float | None = None,
) -> tuple[float | None, float | None]:
    """Layer 3 in one call: spectrum -> (metric wavelength, metric value).

    The wavelength is what a sensorgram plots - a peak *shifting* is the
    measurement. The second value is the curve height there, used to place
    a marker on the spectrum plot."""
    fit = fit_spectrum(spectrum, fit_method, poly_order=poly_order, wl_min=wl_min, wl_max=wl_max)
    if fit is None:
        return metric_from_spectrum(spectrum, metric_key, wl_min=wl_min, wl_max=wl_max)
    return metric_from_fit(fit, metric_key)
