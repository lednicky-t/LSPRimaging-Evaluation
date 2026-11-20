"""A Gaussian fit of many spectra at once, iterating only the centre and the width (2026-10-24). Pure numpy, no Qt, no scipy.

**The model** is the one of ``query.fit_gaussian``: ``y = amplitude * exp(-(x - centre)^2 / (2 sigma^2)) + offset``.

**Why only two parameters are iterated.** For a given centre and width the best amplitude and offset have a closed form (a straight-line fit of
the data against the bell shape: slope = amplitude, intercept = offset). So the four-parameter problem is a two-parameter one, "variable
projection": the search runs over (centre, sigma) only and the amplitude and offset are *solved* at every step, not searched. The result is the
same least-squares optimum, with fewer and better-behaved iterations (the amplitude and offset are the parameters that make a four-parameter
search slow and sensitive to its first guess). All four parameters come out of every fit, so the curve can be drawn exactly as before.
The gradient used here is exact (for the optimal amplitude and offset it equals ``-(amplitude * d bell / d theta)^T residual``), so the iteration
stops at a true stationary point of the least-squares cost.

**Newton steps (second order).** The cost of the two remaining parameters has an exact 2 x 2 curvature matrix. The usual Gauss-Newton matrix
leaves out the part that comes from the residual, which makes it converge only linearly (about 10x less error per pass) when noise is left in the
residual, as it always is. Here the full matrix is used (the Schur complement of the four-parameter Hessian over amplitude and offset; formula
in ``_reduced_terms``, checked against finite differences in ``tests/unit/test_lspri_analysis_gaussian_fit.py``): quadratic convergence, about
half the passes. Where that matrix is not positive definite (far from the optimum) the Gauss-Newton matrix is used for that spectrum and step, and
every step is damped Levenberg-Marquardt style and accepted only if the cost goes down, so the safety net is the same as before.

**Why many spectra at once.** The spectra of one ROI over all cubes share their wavelengths, so every step is a handful of numpy operations on a
``[spectra, wavelengths]`` array instead of ~40 model evaluations inside scipy per spectrum, and a spectrum that has converged leaves the batch.
Measured 2026-10-24 on the real scan: 30-40x faster than ``scipy.optimize.curve_fit`` per spectrum, and *closer* to the exact optimum
(``curve_fit`` stops at MINPACK's loose default tolerance, up to 5e-4 nm from it; this stops within ~1e-6 nm). It is the first choice for
every Gaussian fit in the rewrite (2026-10-24; no switch back to scipy as a setting, scipy only for the spectra this cannot fit). A pass costs mostly numpy call
overhead, so the cost per spectrum falls with the batch size up to ~2000 spectra (``trace_fit`` merges the ROIs it is given into such batches).
``docs/sensorgram_fit_freeze_2026-10-24.md``.

**Bands (2026-11-20, spectral correction).** With ``band_sigma`` (the standard deviation of the measured filter band of each point, nm) the model is the Gaussian peak *averaged over each point's
band*, the band taken as a Gaussian: ``g_k = (sigma / tau_k) exp(-d_k^2 / 2 tau_k^2)``, ``tau_k^2 = sigma^2 + s_k^2``, ``d_k = x_k - centre``. Everything else (variable projection, exact
Newton steps) is unchanged; only the bell and its five derivatives change (``_reduced_terms``). With ``L_s = 1/sigma - sigma/u + sigma d^2/u^2`` (``u = tau^2``):
``g_c = g d/u``, ``g_s = g L_s``, ``g_cc = g (d^2/u^2 - 1/u)``, ``g_cs = g d (L_s/u - 2 sigma/u^2)``, ``g_ss = g (L_s^2 + dL_s/d sigma)``; for ``s = 0`` they are the formulas above (tested to
finite differences). No band (or all widths zero) takes the original code path, so existing numbers are bit-identical. The coefficients returned are those of the *underlying* peak.

**First guess** as in ``query.fit_gaussian``: the centre at the highest point, sigma a sixth of the wavelength span. (A data-based guess and a
warm start from neighbouring cubes were tried and did not pay: the passes needed are set by the convergence rate, not by the start.)

**When it gives up.** A spectrum that does not converge (a step limit, non-finite numbers) or whose width runs away to more than 100 times the
wavelength span is reported as not ``ok``; the caller then fits that one spectrum with scipy's ``curve_fit`` (the method used before this
solver existed), so no cube is left without a value and a failed fit stays a gap and never a stop. The runaway happens in a very narrow fit
window (a handful of points around the top, where a bell is indistinguishable from a parabola and the four parameters are not determined by the
data); there scipy's answer (a width of hundreds of nm) is as arbitrary as any, but it is the answer that was given before.
"""

from __future__ import annotations

import numpy as np

_MAX_ITERATIONS = 100
_CONVERGED_REDUCTION = 1.0e-12  # predicted drop of the cost, relative to the cost: nothing left to gain
_CONVERGED_STEP = 1.0e-9  # step in centre / span and in sigma / sigma
_DAMPING_LIMIT = 1.0e12  # damping this large and still no better point: not converging
_MAX_SIGMA_SPANS = 100.0  # a width this many times the wavelength span is a parabola, not a bell: the Gaussian is not identifiable there
_TINY = 1.0e-300
_DAMPING_START = 1.0  # first damping of every spectrum (a full Newton step from the first guess usually overshoots; measured 2026-10-24)
_SHRINK = 10.0  # damping is divided by this after an accepted step (and multiplied by 4 after a rejected one)
_USE_NEWTON = True  # False: the Gauss-Newton matrix only (the earlier behaviour; tests and comparisons)


def _linear_part(y: np.ndarray, bell: np.ndarray):
    """Best amplitude and offset of ``y ~ amplitude * bell + offset`` for every row, with the residual and the centred bell."""
    bell_centred = bell - bell.mean(axis=1, keepdims=True)
    y_mean = y.mean(axis=1, keepdims=True)
    y_centred = y - y_mean
    sum_bb = np.einsum("ij,ij->i", bell_centred, bell_centred)
    with np.errstate(all="ignore"):
        amplitude = np.einsum("ij,ij->i", bell_centred, y_centred) / sum_bb
    offset = y_mean[:, 0] - amplitude * bell.mean(axis=1)
    residual = y_centred - amplitude[:, None] * bell_centred
    return amplitude, offset, residual, bell_centred, sum_bb


def _band_variance(band_sigma) -> np.ndarray | None:
    """The squared band widths ``[point]``, or None when there is no band to average over (None, or every width zero: the point-sample model, original code)."""
    if band_sigma is None:
        return None
    var = np.asarray(band_sigma, dtype=np.float64) ** 2
    return var if np.any(var > 0.0) else None


def _bell_at(x: np.ndarray, centre: np.ndarray, sigma: np.ndarray, band_var: np.ndarray | None) -> np.ndarray:
    """``[row, point]``: the bell of each row, or (with ``band_var``) its average over each point's band."""
    if band_var is None:
        return np.exp(-((x[None, :] - centre[:, None]) ** 2) / (2.0 * sigma[:, None] ** 2))
    u = sigma[:, None] ** 2 + band_var[None, :]
    return (sigma[:, None] / np.sqrt(u)) * np.exp(-((x[None, :] - centre[:, None]) ** 2) / (2.0 * u))


def _reduced_terms(x: np.ndarray, y: np.ndarray, centre: np.ndarray, sigma: np.ndarray, *, curvature: bool = True, band_var: np.ndarray | None = None):
    """The cost ``0.5 * sum(residual^2)`` of the two remaining parameters (centre, sigma) at the best amplitude and offset: its gradient and its
    curvature, per row of ``y``.

    Returns ``(amplitude, residual, sum_bb, gradient (2 columns), gauss_newton (cc, cs, ss), newton (cc, cs, ss) or None)``.

    With d = x - centre, bell g = exp(-d^2 / 2 sigma^2), centred bell g^ (sum of squares S), residual r (centred) and amplitude a:
    ``g_c = g d / sigma^2``, ``g_s = g d^2 / sigma^3``, ``g_cc = g (d^2 / sigma^4 - 1 / sigma^2)``, ``g_cs = g d (d^2 / sigma^5 - 2 / sigma^3)``,
    ``g_ss = g (d^4 / sigma^6 - 3 d^2 / sigma^4)``. For j, k in (c, s), with beta_j = g^ . g_j / S, R_j = r . g_j and P the projection off the
    straight lines of g: gradient_j = -a R_j; Gauss-Newton matrix a^2 g_j . P g_k = a^2 (g_j^ . g_k^ - S beta_j beta_k); the exact curvature adds the
    residual terms: ``H_jk = a^2 g_j . P g_k - a r . g_jk + a (R_k beta_j + R_j beta_k) - R_j R_k / S``
    (Schur complement of the four-parameter Hessian over amplitude and offset)."""
    n = x.size
    dx = x[None, :] - centre[:, None]
    s1 = sigma[:, None]
    s2 = s1 * s1
    if band_var is None:
        bell = np.exp(-(dx * dx) / (2.0 * s2))
    else:  # the bell averaged over each point's band (module docstring)
        u = s2 + band_var[None, :]
        bell = (s1 / np.sqrt(u)) * np.exp(-(dx * dx) / (2.0 * u))
    amplitude, _offset, residual, bell_centred, sum_bb = _linear_part(y, bell)
    d2 = dx * dx
    if band_var is None:
        g_c = bell * dx / s2
        g_s = bell * d2 / (s2 * s1)
    else:
        l_s = 1.0 / s1 - s1 / u + s1 * d2 / (u * u)
        g_c = bell * dx / u
        g_s = bell * l_s
    e = np.einsum
    mean_c, mean_s = g_c.mean(axis=1), g_s.mean(axis=1)
    beta_c, beta_s = e("ij,ij->i", bell_centred, g_c) / sum_bb, e("ij,ij->i", bell_centred, g_s) / sum_bb
    cc = e("ij,ij->i", g_c, g_c) - n * mean_c * mean_c - sum_bb * beta_c * beta_c
    cs = e("ij,ij->i", g_c, g_s) - n * mean_c * mean_s - sum_bb * beta_c * beta_s
    ss = e("ij,ij->i", g_s, g_s) - n * mean_s * mean_s - sum_bb * beta_s * beta_s
    a2 = amplitude * amplitude
    gauss_newton = (a2 * cc, a2 * cs, a2 * ss)
    r_c, r_s = e("ij,ij->i", residual, g_c), e("ij,ij->i", residual, g_s)
    gradient = np.stack([-amplitude * r_c, -amplitude * r_s], axis=1)
    if not curvature:
        return amplitude, residual, sum_bb, gradient, gauss_newton, None
    rb = residual * bell
    if band_var is None:
        s4 = s2 * s2
        rg_cc = e("ij,ij->i", rb, d2 / s4 - 1.0 / s2)
        rg_cs = e("ij,ij->i", rb, dx * (d2 / (s4 * s1) - 2.0 / (s2 * s1)))
        rg_ss = e("ij,ij->i", rb, d2 * (d2 / (s4 * s2) - 3.0 / s4))
    else:
        u2 = u * u
        d_ls = -1.0 / s2 - 1.0 / u + 2.0 * s2 / u2 + d2 / u2 - 4.0 * s2 * d2 / (u2 * u)  # d L_s / d sigma
        rg_cc = e("ij,ij->i", rb, d2 / u2 - 1.0 / u)
        rg_cs = e("ij,ij->i", rb, dx * (l_s / u - 2.0 * s1 / u2))
        rg_ss = e("ij,ij->i", rb, l_s * l_s + d_ls)
    h_cc = gauss_newton[0] - amplitude * rg_cc + 2.0 * amplitude * r_c * beta_c - r_c * r_c / sum_bb
    h_cs = gauss_newton[1] - amplitude * rg_cs + amplitude * (r_s * beta_c + r_c * beta_s) - r_c * r_s / sum_bb
    h_ss = gauss_newton[2] - amplitude * rg_ss + 2.0 * amplitude * r_s * beta_s - r_s * r_s / sum_bb
    return amplitude, residual, sum_bb, gradient, gauss_newton, (h_cc, h_cs, h_ss)


def solve_gaussian(x: np.ndarray, y: np.ndarray, band_sigma: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Fit ``y[i]`` (one spectrum per row) against the shared ``x`` (ascending, at least 4 points). ``band_sigma`` (``[point]``, nm): the
    standard deviation of each point's filter band, for the full-profile spectral correction (module docstring); None = point samples.

    Returns ``(params, ok)``: ``params[i] = [amplitude, centre, sigma, offset]`` (sigma > 0) and ``ok[i]``, whether spectrum ``i`` converged
    (the other rows are NaN)."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    n_spectra = y.shape[0]
    band_var = _band_variance(band_sigma)
    span = max(float(x[-1] - x[0]), 1.0e-12)
    params = np.full((n_spectra, 4), np.nan)
    ok = np.zeros(n_spectra, dtype=bool)
    centre = x[np.argmax(y, axis=1)].astype(np.float64)
    sigma = np.full(n_spectra, max(span / 6.0, 1.0e-3))
    damping = np.full(n_spectra, _DAMPING_START)
    with np.errstate(all="ignore"):
        bell = _bell_at(x, centre, sigma, band_var)
        _a, _o, residual, _bc, sum_bb = _linear_part(y, bell)
    cost = 0.5 * np.einsum("ij,ij->i", residual, residual)
    active = np.flatnonzero(np.isfinite(cost) & (sum_bb > _TINY))
    for _iteration in range(_MAX_ITERATIONS):
        if active.size == 0:
            break
        c, s, ya, lam = centre[active], sigma[active], y[active], damping[active]
        with np.errstate(all="ignore"):
            _amplitude, _residual, _sum_bb, gradient, gauss_newton, newton = _reduced_terms(x, ya, c, s, curvature=_USE_NEWTON, band_var=band_var)
            g_c, g_s = gradient[:, 0], gradient[:, 1]
            m_cc, m_cs, m_ss = gauss_newton
            if newton is not None:  # the exact curvature where it is positive definite, the Gauss-Newton matrix elsewhere
                h_cc, h_cs, h_ss = newton
                use = np.isfinite(h_cc) & np.isfinite(h_cs) & np.isfinite(h_ss) & (h_cc > 0.0) & (h_cc * h_ss - h_cs * h_cs > 0.0)
                m_cc, m_cs, m_ss = np.where(use, h_cc, m_cc), np.where(use, h_cs, m_cs), np.where(use, h_ss, m_ss)
            # (curvature + damping * diagonal) step = -gradient, a 2 x 2 system solved in closed form
            d_cc, d_ss = m_cc * (1.0 + lam), m_ss * (1.0 + lam)
            det = d_cc * d_ss - m_cs * m_cs
            step_c = -(d_ss * g_c - m_cs * g_s) / det
            step_s = -(d_cc * g_s - m_cs * g_c) / det
            predicted = -(step_c * g_c + step_s * g_s)  # about the drop of the cost the model promises (>= 0)
            new_c, new_s = c + step_c, s + step_s
            valid = np.isfinite(new_c) & np.isfinite(new_s) & (new_s > 0.0) & np.isfinite(det)
            new_s_safe = np.where(valid, new_s, 1.0)
            new_bell = _bell_at(x, np.where(valid, new_c, 0.0), new_s_safe, band_var)
            _na, _no, new_residual, _nb, new_sum_bb = _linear_part(ya, new_bell)
            new_cost = 0.5 * np.einsum("ij,ij->i", new_residual, new_residual)
            current = cost[active]
            better = valid & np.isfinite(new_cost) & (new_sum_bb > _TINY) & (new_cost < current)
            nothing_left = (predicted <= _CONVERGED_REDUCTION * current) | (current <= _TINY)
            small_step = (np.abs(step_c) <= _CONVERGED_STEP * span) & (np.abs(step_s) <= _CONVERGED_STEP * s)
        accept = active[better]
        centre[accept], sigma[accept], cost[accept] = new_c[better], new_s[better], new_cost[better]
        damping[active] = np.where(better, np.maximum(lam / _SHRINK, 1.0e-12), lam * 4.0)
        blown = better & (new_s > _MAX_SIGMA_SPANS * span)
        done = (nothing_left | (better & small_step)) & ~blown
        ok[active[done]] = True
        gave_up = blown | (~done & ~better & (damping[active] > _DAMPING_LIMIT))
        active = active[~(done | gave_up)]
    finished = np.flatnonzero(ok)
    if finished.size:
        with np.errstate(all="ignore"):
            bell = _bell_at(x, centre[finished], sigma[finished], band_var)
            amplitude, offset, _r, _bc, sum_bb = _linear_part(y[finished], bell)
        good = np.isfinite(amplitude) & np.isfinite(offset) & (sum_bb > _TINY)
        params[finished[good]] = np.stack([amplitude, centre[finished], sigma[finished], offset], axis=1)[good]
        ok[finished[~good]] = False
    return params, ok
