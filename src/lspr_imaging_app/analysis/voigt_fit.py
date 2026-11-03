"""A Voigt fit of many spectra at once (2026-11-02). Pure numpy + ``scipy.special.wofz``, no Qt.

**The model.** ``y = amplitude * Re w(z) + offset`` with ``z = (E - centre + i gamma) / (sigma sqrt 2)``, where ``E`` is the photon energy in eV
(``E = 1239.84198 / wavelength in nm``) and ``w`` the Faddeeva function. ``Re w(z) / (sigma sqrt(2 pi))`` is the Voigt profile: a Gaussian of
width ``sigma`` (inhomogeneous broadening: the spread of sizes and shapes) convolved with a Lorentzian of half width ``gamma`` (homogeneous
broadening: the plasmon's own damping). Both widths are in eV; ``gamma = 0`` is a Gaussian, ``sigma -> 0`` a Lorentzian. The offset is the same
flat background the Gaussian fit has, fitted together with the peak (not subtracted first).

**Why in energy, not in wavelength.** A plasmon's damping is a width in energy; a resonance that is symmetric in energy is skewed in wavelength
(d lambda / d E grows as lambda^2). A symmetric shape fitted in nm forces that skew into the fit. Absorbance is dimensionless, so no Jacobian factor
is needed when the axis changes. The curve is still drawn and read in nm: the value at a wavelength is the model at that wavelength's energy, so
the maximum sits at ``1239.84198 / centre`` exactly, and the centroid is taken over the curve in nm, as for the other fits. (On the maintainer's
scan, 2026-11-02, this maximum sits ~5.5 nm bluer than the Gaussian-in-nm one: the axis skew, not a different peak.)

**How it is solved.** As in ``gaussian_fit.py``: for given (centre, sigma, gamma) the best amplitude and offset have a closed form (a straight-line
fit of the data against the profile), so only those three are iterated ("variable projection"). Damped Gauss-Newton (Levenberg-Marquardt) steps
with the exact derivatives of the Faddeeva function (``w'(z) = -2 z w(z) + 2i / sqrt(pi)``), a step accepted only when the cost goes down; many
spectra are one array. The widths are iterated as they are (not as logarithms), with ``gamma >= 0`` and ``sigma >=`` a small floor as bounds: a
width at its bound that the cost would push further is held there for that step. (Logarithms were tried first: the slope in ln gamma vanishes as
gamma goes to 0, which is where real ROI spectra are - gamma went to ~0 in 99.6 % of the cubes of the maintainer's scan - and every cube needed
~45 passes.)

**First guess**: the Gaussian solver's fit of the same spectra in energy, with gamma = 0: the best pure Gaussian, which is where most spectra end;
where a Lorentzian part lowers the cost the slope in gamma says so and the fit moves away from it. (Widths of a Voigt are nearly interchangeable
on a few broad points: the centre is well determined, the split between sigma and gamma much less so. The centre is what the Sensorgram uses.)

**When it gives up** (a step limit, non-finite numbers, a width over 100 times the energy span): that spectrum is not ``ok``; the caller fits it
with scipy's ``least_squares`` from the same first guess, and if that fails too it is a gap (NaN), never a stop.
"""

from __future__ import annotations

import numpy as np
from scipy.special import wofz

from .gaussian_fit import solve_gaussian

HC_EV_NM = 1239.84198  # h c in eV nm: E (eV) = HC_EV_NM / wavelength (nm)
VOIGT_PARAMS = ("amplitude", "centre_ev", "sigma_ev", "gamma_ev", "offset")
_SQRT2 = np.sqrt(2.0)
_TWO_OVER_SQRT_PI = 2.0 / np.sqrt(np.pi)
_MAX_ITERATIONS = 200
_CONVERGED_REDUCTION = 1.0e-12  # predicted drop of the cost, relative to the cost
_CONVERGED_STEP = 1.0e-9  # step in each of centre, sigma, gamma, relative to the energy span
_DAMPING_LIMIT = 1.0e12
_MAX_WIDTH_SPANS = 100.0  # a width this many times the energy span is not determined by the data
_MIN_SIGMA_SPANS = 1.0e-4  # sigma is kept above this fraction of the span (~1e-4 eV: the profile needs sigma > 0; a narrower Gaussian part changes nothing)
_TINY = 1.0e-300


def energy_ev(wavelength_nm: np.ndarray) -> np.ndarray:
    return HC_EV_NM / np.asarray(wavelength_nm, dtype=np.float64)


def profile(energy: np.ndarray, centre: np.ndarray, sigma: np.ndarray, gamma: np.ndarray) -> np.ndarray:
    """``Re w(z)`` (the Voigt shape up to a constant) for column vectors of parameters: ``[spectrum, point]``."""
    z = (energy[None, :] - centre[:, None] + 1j * gamma[:, None]) / (_SQRT2 * sigma[:, None])
    return wofz(z).real


def model(wavelength_nm: np.ndarray, params: np.ndarray) -> np.ndarray:
    """The fitted curve at these wavelengths for ``params`` ``[spectrum, 5]`` (``VOIGT_PARAMS``): ``[spectrum, point]``."""
    params = np.atleast_2d(np.asarray(params, dtype=np.float64))
    shape = profile(energy_ev(wavelength_nm), params[:, 1], params[:, 2], params[:, 3])
    return params[:, 0:1] * shape + params[:, 4:5]


def peak_wavelength_nm(params: np.ndarray) -> np.ndarray:
    """Where the curve is highest, in nm: the centre's energy (the profile is symmetric in energy and the offset is flat)."""
    return HC_EV_NM / np.atleast_2d(params)[:, 1]


def _linear_part(y: np.ndarray, shape: np.ndarray):
    """Best amplitude and offset of ``y ~ amplitude * shape + offset`` per row, with the residual, the centred shape and its sum of squares."""
    shape_centred = shape - shape.mean(axis=1, keepdims=True)
    y_mean = y.mean(axis=1, keepdims=True)
    y_centred = y - y_mean
    sum_ss = np.einsum("ij,ij->i", shape_centred, shape_centred)
    with np.errstate(all="ignore"):
        amplitude = np.einsum("ij,ij->i", shape_centred, y_centred) / sum_ss
    offset = y_mean[:, 0] - amplitude * shape.mean(axis=1)
    residual = y_centred - amplitude[:, None] * shape_centred
    return amplitude, offset, residual, shape_centred, sum_ss


def _terms(energy: np.ndarray, y: np.ndarray, centre: np.ndarray, sigma: np.ndarray, gamma: np.ndarray):
    """Cost, gradient ``[n, 3]`` and Gauss-Newton matrix ``[n, 3, 3]`` of the reduced problem in (centre, sigma, gamma).

    With r the residual at the best amplitude a and offset, g_j = d shape / d theta_j, its centred version g^_j, the centred shape s^ (sum of squares
    S) and beta_j = s^ . g_j / S: gradient_j = -a r . g_j, M_jk = a^2 (g^_j . g^_k - S beta_j beta_k) (the derivative projected off the
    straight lines of the shape, as in ``gaussian_fit._reduced_terms``)."""
    scale = 1.0 / (_SQRT2 * sigma[:, None])
    z = (energy[None, :] - centre[:, None] + 1j * gamma[:, None]) * scale
    w = wofz(z)
    shape = w.real
    amplitude, offset, residual, shape_centred, sum_ss = _linear_part(y, shape)
    dw = -2.0 * z * w + 1j * _TWO_OVER_SQRT_PI  # dw / dz
    derivs = np.stack(
        [
            (dw * -scale).real,  # dz / d centre = -1 / (sigma sqrt 2)
            (dw * (-z / sigma[:, None])).real,  # dz / d sigma = -z / sigma
            (dw * (1j * scale)).real,  # dz / d gamma = i / (sigma sqrt 2)
        ],
        axis=1,
    )  # [n, 3, point]
    centred = derivs - derivs.mean(axis=2, keepdims=True)
    beta = np.einsum("ip,ijp->ij", shape_centred, derivs) / sum_ss[:, None]
    gram = np.einsum("ijp,ikp->ijk", centred, centred) - sum_ss[:, None, None] * beta[:, :, None] * beta[:, None, :]
    matrix = (amplitude * amplitude)[:, None, None] * gram
    gradient = -amplitude[:, None] * np.einsum("ip,ijp->ij", residual, derivs)
    cost = 0.5 * np.einsum("ij,ij->i", residual, residual)
    return cost, gradient, matrix, amplitude, offset, sum_ss


def first_guess(energy: np.ndarray, y: np.ndarray, sigma_floor: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(centre, sigma, gamma) in eV: the Gaussian solver's fit in energy with gamma = 0 (where it converged), else the highest point and a sixth
    of the span."""
    order = np.argsort(energy)
    e_sorted = energy[order]
    span = max(float(e_sorted[-1] - e_sorted[0]), 1.0e-12)
    centre = e_sorted[np.argmax(y[:, order], axis=1)].astype(np.float64)
    sigma = np.full(y.shape[0], span / 6.0)
    if energy.size >= 4:
        params, ok = solve_gaussian(e_sorted, y[:, order])
        use = ok & np.isfinite(params[:, 1]) & np.isfinite(params[:, 2]) & (params[:, 2] > 0) & (params[:, 2] < 10.0 * span)
        centre[use] = params[use, 1]
        sigma[use] = params[use, 2]
    return centre, np.maximum(sigma, sigma_floor), np.zeros(y.shape[0])


def solve_voigt(wavelength_nm: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Fit ``y[i]`` (one spectrum per row) against the shared wavelengths (nm, at least 5 points).

    Returns ``(params, ok)``: ``params[i]`` = ``VOIGT_PARAMS`` (centre, sigma, gamma in eV; sigma > 0, gamma >= 0) and whether spectrum ``i``
    converged (the other rows are NaN)."""
    energy = energy_ev(wavelength_nm)
    y = np.asarray(y, dtype=np.float64)
    n_spectra = y.shape[0]
    params = np.full((n_spectra, 5), np.nan)
    ok = np.zeros(n_spectra, dtype=bool)
    span = max(float(np.max(energy) - np.min(energy)), 1.0e-12)
    low = np.array([_MIN_SIGMA_SPANS * span, 0.0])  # sigma, gamma
    high = _MAX_WIDTH_SPANS * span
    with np.errstate(all="ignore"):
        centre, sigma, gamma = first_guess(energy, y, float(low[0]))
    theta = np.stack([centre, sigma, gamma], axis=1)
    damping = np.full(n_spectra, 1.0e-3)
    # The cost, gradient and matrix at each spectrum's current point. A trial point is evaluated in full (one Faddeeva call, the expensive
    # part); when it is accepted its terms are the next iteration's, so each iteration costs one evaluation, not two.
    with np.errstate(all="ignore"):
        cost, gradient_all, matrix_all, _a, _o, sum_ss_all = _terms(energy, y, theta[:, 0], theta[:, 1], theta[:, 2])
    active = np.flatnonzero(np.isfinite(cost))
    eye = np.eye(3)
    for _iteration in range(_MAX_ITERATIONS):
        if active.size == 0:
            break
        th, ya, lam = theta[active], y[active], damping[active]
        current, gradient, matrix, sum_ss = cost[active], gradient_all[active].copy(), matrix_all[active].copy(), sum_ss_all[active]
        with np.errstate(all="ignore"):
            # A width at its lower bound that the cost would push lower (gamma = 0: no Lorentzian part; sigma at its floor) is held there for
            # this step: its row and column leave the system, so the other parameters still move.
            held = (th[:, 1:] <= low[None, :] * (1.0 + 1.0e-12) + 1.0e-15) & (gradient[:, 1:] > 0.0)
            for k in (1, 2):
                rows = held[:, k - 1]
                gradient[rows, k] = 0.0
                matrix[rows, k, :] = 0.0
                matrix[rows, :, k] = 0.0
                matrix[rows, k, k] = 1.0
            diag = np.einsum("ijj->ij", matrix)
            damped = matrix + lam[:, None, None] * (diag[:, :, None] * eye[None, :, :] + 1.0e-12 * eye[None, :, :])
            usable = np.all(np.isfinite(damped), axis=(1, 2)) & np.all(np.isfinite(gradient), axis=1)
            step = np.zeros_like(th)
            if usable.any():
                try:
                    step[usable] = -np.linalg.solve(damped[usable], gradient[usable][:, :, None])[:, :, 0]
                except np.linalg.LinAlgError:
                    usable[:] = False
            new = th + step
            new[:, 1:] = np.clip(new[:, 1:], low[None, :], high)
            step = new - th  # the step actually taken (after the bounds)
            predicted = -np.einsum("ij,ij->i", step, gradient)
            new_cost, new_gradient, new_matrix, _na, _no, new_sum_ss = _terms(energy, ya, new[:, 0], new[:, 1], new[:, 2])
            better = usable & np.isfinite(new_cost) & (new_cost < current)
            nothing_left = (np.abs(predicted) <= _CONVERGED_REDUCTION * current) | (current <= _TINY)
            small = np.all(np.abs(step) <= _CONVERGED_STEP * span, axis=1)
        accept = active[better]
        theta[accept], cost[accept] = new[better], new_cost[better]
        gradient_all[accept], matrix_all[accept], sum_ss_all[accept] = new_gradient[better], new_matrix[better], new_sum_ss[better]
        damping[active] = np.where(better, np.maximum(lam / 10.0, 1.0e-15), lam * 4.0)
        runaway = better & np.any(new[:, 1:] >= high * (1.0 - 1.0e-9), axis=1)
        done = usable & (nothing_left | small) & ~runaway & (sum_ss > _TINY)
        ok[active[done]] = True
        gave_up = runaway | ~usable | (~done & ~better & (damping[active] > _DAMPING_LIMIT))
        active = active[~(done | gave_up)]
    finished = np.flatnonzero(ok)
    if finished.size:
        c, s, g = theta[finished, 0], theta[finished, 1], theta[finished, 2]
        with np.errstate(all="ignore"):
            amplitude, offset, _r, _sc, sum_ss = _linear_part(y[finished], profile(energy, c, s, g))
        good = np.isfinite(amplitude) & np.isfinite(offset) & (sum_ss > _TINY)
        params[finished[good]] = np.stack([amplitude, c, s, g, offset], axis=1)[good]
        ok[finished[~good]] = False
    return params, ok


def solve_voigt_scipy(wavelength_nm: np.ndarray, y: np.ndarray) -> np.ndarray | None:
    """One spectrum with scipy's ``least_squares`` from the same first guess (all five parameters, the same bounds on the widths): the fallback for
    a spectrum ``solve_voigt`` did not converge on. None when it fails too."""
    from scipy.optimize import least_squares

    energy = energy_ev(wavelength_nm)
    y = np.asarray(y, dtype=np.float64)
    span = max(float(np.max(energy) - np.min(energy)), 1.0e-12)
    sigma_floor, high = _MIN_SIGMA_SPANS * span, _MAX_WIDTH_SPANS * span
    with np.errstate(all="ignore"):
        centre, sigma, gamma = first_guess(energy, y[None, :], sigma_floor)
        amplitude, offset, *_ = _linear_part(y[None, :], profile(energy, centre, sigma, gamma))
    start = np.array([amplitude[0], centre[0], sigma[0], gamma[0], offset[0]])
    if not np.all(np.isfinite(start)):
        return None
    start[2] = np.clip(start[2], sigma_floor, high * 0.5)
    start[3] = np.clip(start[3], 0.0, high * 0.5)

    def residual(p: np.ndarray) -> np.ndarray:
        return p[0] * profile(energy, p[1:2], p[2:3], p[3:4])[0] + p[4] - y

    try:
        result = least_squares(residual, start, bounds=([-np.inf, -np.inf, sigma_floor, 0.0, -np.inf], [np.inf, np.inf, high, high, np.inf]))
    except (ValueError, RuntimeError, FloatingPointError):
        return None
    p = result.x
    if not result.success or not np.all(np.isfinite(p)) or np.any(p[2:4] >= high * (1.0 - 1.0e-6)):
        return None
    return p
