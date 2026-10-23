"""Error bars for a formula spectrum, from the stored pixel spread (2026-10-23). Pure, no Qt.

``scan.h5`` keeps, per ROI, cube, wavelength and side, the mean, the STD of the valid pixels and their count. The standard error of the mean
is ``SEM = STD / sqrt(n_valid)``. A formula combines the sample and the reference value, so the error of the result follows from first-order
propagation (each side independent, derivatives of the same formulas as ``query.formula_values``, including its floor on the values):

    absorbance      A = log10(R / S)          sigma_A   = 1/ln(10) * sqrt((sigma_R / R)^2 + (sigma_S / S)^2)
    mod_absorbance  M = -1000 * log10(S / R)  sigma_M   = 1000 * sigma_A
    ratio           S / R                     sigma     = (S / R) * sqrt((sigma_S / S)^2 + (sigma_R / R)^2)
    relative_change (R - S) / R = 1 - S / R   sigma     = same as the ratio

**What the bars mean.** They are the *pixel-to-pixel spread inside the aperture*, divided by the square root of the pixel count: how well the
ROI's mean value is determined by its pixels. They are not the reproducibility between repeated measurements (that is the scatter of a metric
over cubes, in the Sensorgram), and they treat the sample and reference pixels as independent although both see the same illumination, so
they are an honest upper-ish bound on the noise of the mean, not a full uncertainty. The spectrum panel's tooltip says so.
"""

from __future__ import annotations

import numpy as np

from .query import _VALUE_FLOOR

_LN10 = float(np.log(10.0))


def standard_error(std, n_valid) -> np.ndarray:
    """``std / sqrt(n_valid)``; NaN where there is no pixel (``n_valid`` < 1) or no spread."""
    std = np.asarray(std, dtype=np.float64)
    n = np.asarray(n_valid, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(n >= 1.0, std / np.sqrt(np.maximum(n, 1.0)), np.nan)


def formula_errors(sample, reference, sample_error, reference_error, formula_key: str) -> np.ndarray:
    """One-sigma error of ``query.formula_values(sample, reference, formula_key)``, same floor on the values; NaN where an input is NaN."""
    s = np.maximum(np.asarray(sample, dtype=np.float64), _VALUE_FLOOR)
    r = np.maximum(np.asarray(reference, dtype=np.float64), _VALUE_FLOOR)
    ds = np.asarray(sample_error, dtype=np.float64)
    dr = np.asarray(reference_error, dtype=np.float64)
    relative = np.sqrt((ds / s) ** 2 + (dr / r) ** 2)
    key = str(formula_key).strip().lower()
    if key in ("ratio", "relative_change"):
        return (s / r) * relative
    if key == "mod_absorbance":
        return 1000.0 * relative / _LN10
    return relative / _LN10  # "absorbance" (default)
