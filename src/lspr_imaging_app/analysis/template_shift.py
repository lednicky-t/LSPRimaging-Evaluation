"""Template shift: the position of a spectrum read from ALL its points against a reference spectrum of the same ROI (2026-10-25). Pure numpy/scipy, no Qt.

**The idea.** A fit (polynomial, Gaussian) first turns the measured points into a curve and then asks where its top is. The template shift asks the
question the experiment is about directly: *by how many nm must the reference spectrum of this ROI be moved sideways to agree best with this
spectrum?* Every measured point takes part, both flanks and the tail, no fit window is needed and no peak shape is assumed: the shape is the ROI's own
measured reference. Measured on the real bulk-sensitivity scan it ties the best fits at the noise floor of the data (``docs/shift_estimators_comparison_2026-10-25.md``,
which also records why a window, a baseline cut from the spectrum ends, or a derivative method are worse).

**Reference (template).** Per ROI, the mean of the formula values (absorbance, ...) of the *reference cubes* (a cube range chosen in the Spectra panel), wavelength by
wavelength, over the reference cubes that have a value there. This averages one ROI over time; ROIs are never pooled. A wavelength with no value in any reference
cube is left out of the template (not filled in). The template is the cubic spline through its points; outside its wavelength range the spline is held at its end
value (edge hold). ``position_nm``, the template's own peak (maximum of that spline), turns a shift into a position: ``position = peak of the template + shift``.

**Model.** For one spectrum ``y(w)`` (points ``w`` with a value) and a shift ``d`` (nm):

    y(w) = a T(w - d) + b + noise,        a (scale) and b (offset) free, d the unknown.

For each ``d`` on a grid (step ``SHIFT_STEP_NM`` = 0.25 nm, +/- ``MAX_SHIFT_NM`` = 60 nm) ``a`` and ``b`` are solved exactly (linear least squares), which leaves one
number, the sum of squares ``SSE(d)``. The shift is the minimum of ``SSE`` on the grid refined by the parabola through the three grid points round it. A minimum on
the edge of the grid (a shift of 60 nm or more) or a degenerate spectrum (fewer than ``MIN_POINTS`` points, a template that is flat over those points) gives NaN, a gap,
never a made-up number. With sums taken over the points the spectrum has (``n``, ``S_y``, ``S_T``, ``S_yy``, ``S_TT``, ``S_yT``):

    S_TTc = S_TT - S_T^2 / n,   S_yTc = S_yT - S_y S_T / n,   S_yyc = S_yy - S_y^2 / n
    SSE(d) = S_yyc - S_yTc^2 / S_TTc,   a = S_yTc / S_TTc,   b = (S_y - a S_T) / n

Because ``a`` and ``b`` are free, a change of the amplitude or of a constant offset is NOT read as a shift (measured: 0.000 nm for +1 % and +0.002 A). Maximising the
normalised cross-correlation gives the same shift (it is the same least-squares problem); this is that method without a boundary correction and without an integer-lag grid.

**Diagnostics** (``TemplateFit``), per spectrum: ``scale`` ``a``, ``offset`` ``b`` and ``rms`` of the residual at the shift found, and the **tilt**. The tilt comes from a second, joint fit
that is NOT the metric: ``y = a T(w - d) + b + c u`` with ``u = (w - w_first) / (w_last - w_first)``, searched for ``d`` within +/- ``TILT_SEARCH_NM`` of the shift found (same grid step, parabola
for ``d``, linear interpolation for ``c``: shift and tilt are strongly coupled, so ``c`` moves fast along the grid). ``c`` (``tilt``) is the change of the baseline across the whole range
in the units of the spectrum and ``tilt_shift_nm`` the shift of that joint fit (it differs from the metric's shift by an amount that grows with the tilt: on the real scan 5.84 against 7.27 nm
at the plateau). The metric never uses this fit: freeing the tilt costs 16 % more noise per spectrum and reads part of the real response as baseline (see the record). The measured
behaviour on the real scan: independent drift is tiny, and the tilt that exists follows the shift (``c = kappa x tilt_shift``): the red tail of the peak changes with its position.
``response_law`` separates the two: ``kappa`` from the trace, and ``beyond`` = ``c - kappa x tilt_shift``, the part that is NOT locked to the shift (baseline drift). Computed only
when asked (``diagnostics=True``); the Sensorgram's position trace skips it.

**Size of the shift.** The reference is held at its end values outside its range, so a shift of a third of the peak width or more (tens of nm for a 110 nm wide band) is read
slightly low or high: in a synthetic test 0.003 nm at 15 nm, 0.03 nm at 25 nm, 0.13 nm at 40 nm. Typical LSPR sensing shifts (a few nm) are far from this. The grid ends at +/- 60 nm.

**What the shift is not.** It is the translation that best fits the whole spectrum. If the peak also changes shape (a tail that grows), the result differs from the
position of the top of the peak by a model-dependent amount (on the real scan 7.27 nm, the top moved 7.0 nm, and with a free tilt in the fit 5.84 nm). Compare shifts between
runs analysed with the same settings, and the reference cubes are part of those settings.

**Noise of the reference cubes.** The reference is the mean of its own cubes, so the metric of those cubes carries less noise than that of other cubes (their own noise is in
the template). Do not take the scatter inside the reference range as the noise of the method.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.interpolate import CubicSpline

MAX_SHIFT_NM = 60.0
SHIFT_STEP_NM = 0.25
TILT_SEARCH_NM = 10.0  # the joint shift-and-tilt fit looks this far either side of the shift of the metric
MIN_POINTS = 4  # fewer points than this in a spectrum: no shift (the model has three unknowns)
MIN_TEMPLATE_POINTS = 4
_FLAT = 1.0e-12  # a template whose centred sum of squares over the spectrum's points is below this has no shift
_PEAK_GRID_STEP_NM = 0.05

_DELTAS = np.arange(-MAX_SHIFT_NM, MAX_SHIFT_NM + 0.5 * SHIFT_STEP_NM, SHIFT_STEP_NM)


@dataclass(frozen=True)
class ReferenceTemplate:
    """The reference spectrum of one ROI. ``wavelengths_nm`` ascending, ``values`` finite; ``position_nm`` the wavelength of the template's top; ``n_cubes`` the number of reference
    cubes that contributed at least one wavelength."""

    wavelengths_nm: np.ndarray
    values: np.ndarray
    position_nm: float
    n_cubes: int

    def spline(self) -> CubicSpline:
        return CubicSpline(self.wavelengths_nm, self.values)

    def evaluate(self, spline: CubicSpline, wavelengths_nm: np.ndarray) -> np.ndarray:
        """The template at the given wavelengths, held at its end values outside its range."""
        return spline(np.clip(wavelengths_nm, self.wavelengths_nm[0], self.wavelengths_nm[-1]))


@dataclass(frozen=True)
class TemplateFit:
    """The shift of each spectrum (one entry per cube) and what it was measured with. NaN where no shift could be found."""

    shift_nm: np.ndarray
    position_nm: np.ndarray  # template peak + shift: the number the Sensorgram plots
    scale: np.ndarray
    offset: np.ndarray
    rms: np.ndarray  # root of the residual sum of squares / (n - 2)
    tilt: np.ndarray  # baseline change across the range from the joint shift-and-tilt fit (module docstring); NaN when diagnostics were not asked for
    tilt_shift_nm: np.ndarray  # the shift of that joint fit: the one ``response_law`` compares the tilt with
    n_points: np.ndarray


def _check_ascending(wavelengths_nm: np.ndarray) -> None:
    if wavelengths_nm.ndim != 1 or (wavelengths_nm.size > 1 and not np.all(np.diff(wavelengths_nm) > 0)):
        raise ValueError("The wavelengths must be strictly ascending.")


def template_peak_nm(wavelengths_nm: np.ndarray, values: np.ndarray) -> float:
    """The wavelength of the highest point of the cubic spline through the template, on a 0.05 nm grid refined by a parabola. At an end of the range the end itself."""
    spline = CubicSpline(wavelengths_nm, values)
    grid = np.arange(wavelengths_nm[0], wavelengths_nm[-1] + 0.5 * _PEAK_GRID_STEP_NM, _PEAK_GRID_STEP_NM)
    curve = spline(grid)
    i = int(np.argmax(curve))
    if 0 < i < grid.size - 1:
        y0, y1, y2 = curve[i - 1], curve[i], curve[i + 1]
        curvature = y0 - 2.0 * y1 + y2
        if curvature < 0.0:
            return float(grid[i] + 0.5 * (y0 - y2) / curvature * _PEAK_GRID_STEP_NM)
    return float(grid[i])


def build_template(
    wavelengths_nm: np.ndarray, values: np.ndarray, valid: np.ndarray, reference_cubes: np.ndarray
) -> ReferenceTemplate | None:
    """The reference spectrum of one ROI. ``values`` and ``valid`` are ``[cube, wavelength]`` (``valid``: the value may be used); ``reference_cubes`` is a boolean per cube.
    None when fewer than ``MIN_TEMPLATE_POINTS`` wavelengths have a value in any reference cube."""
    wavelengths_nm = np.asarray(wavelengths_nm, dtype=np.float64)
    _check_ascending(wavelengths_nm)
    use = np.asarray(valid, dtype=bool) & np.isfinite(values) & np.asarray(reference_cubes, dtype=bool)[:, None]
    count = use.sum(axis=0)
    have = count > 0
    if int(have.sum()) < MIN_TEMPLATE_POINTS:
        return None
    total = np.where(use, values, 0.0).sum(axis=0)
    mean = total[have] / count[have]
    x = wavelengths_nm[have]
    return ReferenceTemplate(x, mean, template_peak_nm(x, mean), int(use.any(axis=1).sum()))


def fit_template_shift(
    template: ReferenceTemplate, wavelengths_nm: np.ndarray, values: np.ndarray, valid: np.ndarray, *, diagnostics: bool = True
) -> TemplateFit:
    """The shift of every row of ``values`` (``[cube, wavelength]``, ``valid`` marks the usable entries) against ``template``. Rows are fitted independently: a row never sees another.
    ``diagnostics=False`` skips the joint shift-and-tilt fit (``tilt`` and ``tilt_shift_nm`` are then NaN): the position does not depend on it."""
    wavelengths_nm = np.asarray(wavelengths_nm, dtype=np.float64)
    _check_ascending(wavelengths_nm)
    y = np.asarray(values, dtype=np.float64)
    n_cube = y.shape[0]
    weight = (np.asarray(valid, dtype=bool) & np.isfinite(y)).astype(np.float64)  # 1 where the point takes part
    y = np.where(weight > 0.0, y, 0.0)
    nan = np.full(n_cube, np.nan)
    n = weight.sum(axis=1)
    enough = n >= MIN_POINTS

    spline = template.spline()
    grid_t = template.evaluate(spline, wavelengths_nm[None, :] - _DELTAS[:, None])  # [shift, wavelength]
    wy = weight * y
    s_y = wy.sum(axis=1)
    s_yy = (wy * y).sum(axis=1)
    s_t = weight @ grid_t.T  # [cube, shift]
    s_tt = weight @ (grid_t * grid_t).T
    s_yt = wy @ grid_t.T
    with np.errstate(all="ignore"):
        n_col = n[:, None]
        s_ttc = s_tt - s_t * s_t / n_col
        s_ytc = s_yt - s_y[:, None] * s_t / n_col
        s_yyc = (s_yy - s_y * s_y / n)[:, None]
        sse = s_yyc - s_ytc * s_ytc / s_ttc
    sse = np.where(np.isfinite(sse) & (s_ttc > _FLAT) & enough[:, None], sse, np.inf)
    best = np.argmin(sse, axis=1)
    rows = np.arange(n_cube)
    interior = (best > 0) & (best < _DELTAS.size - 1) & np.isfinite(sse[rows, best])
    k = np.clip(best, 1, _DELTAS.size - 2)
    y0, y1, y2 = sse[rows, k - 1], sse[rows, k], sse[rows, k + 1]
    with np.errstate(all="ignore"):
        curvature = y0 - 2.0 * y1 + y2
        offset_steps = np.where(curvature > 0.0, 0.5 * (y0 - y2) / curvature, 0.0)
    offset_steps = np.where(np.isfinite(offset_steps), np.clip(offset_steps, -1.0, 1.0), 0.0)
    shift = np.where(interior, _DELTAS[k] + offset_steps * SHIFT_STEP_NM, np.nan)

    scale, offset, rms, tilt, tilt_shift = nan.copy(), nan.copy(), nan.copy(), nan.copy(), nan.copy()
    ok = np.isfinite(shift)
    if ok.any():
        t_at = template.evaluate(spline, wavelengths_nm[None, :] - shift[ok][:, None])  # [cube, wavelength] at each cube's own shift
        w, yy, nn = weight[ok], y[ok], n[ok]
        s_t1, s_tt1 = (w * t_at).sum(axis=1), (w * t_at * t_at).sum(axis=1)
        s_y1, s_yt1 = (w * yy).sum(axis=1), (w * yy * t_at).sum(axis=1)
        s_yy1 = (w * yy * yy).sum(axis=1)
        with np.errstate(all="ignore"):
            ttc = s_tt1 - s_t1 * s_t1 / nn
            a = (s_yt1 - s_y1 * s_t1 / nn) / ttc
            b = (s_y1 - a * s_t1) / nn
            residual = s_yy1 - 2.0 * a * s_yt1 - 2.0 * b * s_y1 + a * a * s_tt1 + 2.0 * a * b * s_t1 + nn * b * b
            dof = np.maximum(nn - 2.0, 1.0)
            r = np.sqrt(np.maximum(residual, 0.0) / dof)
        scale[ok], offset[ok], rms[ok] = a, b, r
        if diagnostics:
            tilt_shift[ok], tilt[ok] = _joint_tilt_fit(template, spline, wavelengths_nm, w, yy, shift[ok])
    position = np.where(ok, template.position_nm + shift, np.nan)
    return TemplateFit(shift, position, scale, offset, rms, tilt, tilt_shift, n.astype(np.int64))


def _joint_tilt_fit(
    template: ReferenceTemplate, spline: CubicSpline, wavelengths_nm: np.ndarray, weight: np.ndarray, y: np.ndarray, centre_nm: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """``(shift, tilt)`` of the joint fit ``y = a T(w - d) + b + c u``: for each row ``d`` is searched on the grid of ``centre_nm +/- TILT_SEARCH_NM`` (``a``, ``b`` and ``c`` solved exactly by one
    3 x 3 system per grid point), the minimum is refined by the parabola through the sum of squares, and ``c`` is interpolated linearly between the grid points either side. NaN where the
    minimum sits on the edge of the search or the system is singular."""
    span = float(wavelengths_nm[-1] - wavelengths_nm[0])
    u = (wavelengths_nm - wavelengths_nm[0]) / span if span > 0.0 else np.zeros_like(wavelengths_nm)
    offsets = np.arange(-TILT_SEARCH_NM, TILT_SEARCH_NM + 0.5 * SHIFT_STEP_NM, SHIFT_STEP_NM)
    d = centre_nm[:, None] + offsets[None, :]  # [cube, trial shift]
    t = template.evaluate(spline, wavelengths_nm[None, None, :] - d[:, :, None])  # [cube, trial shift, wavelength]
    w = weight[:, None, :]
    s_tt, s_t, s_tu = (w * t * t).sum(2), (w * t).sum(2), (w * t * u).sum(2)
    n_c, s_u, s_uu = weight.sum(1)[:, None], (weight * u).sum(1)[:, None], (weight * u * u).sum(1)[:, None]
    r_t, r_1, r_u = (w * t * y[:, None, :]).sum(2), (weight * y).sum(1)[:, None], (weight * y * u).sum(1)[:, None]
    ones = np.ones_like(s_tt)
    gram = np.stack([np.stack([s_tt, s_t, s_tu], -1), np.stack([s_t, n_c * ones, s_u * ones], -1), np.stack([s_tu, s_u * ones, s_uu * ones], -1)], -2)  # [cube, trial, 3, 3]
    rhs = np.stack([r_t, r_1 * ones, r_u * ones], -1)  # [cube, trial, 3]
    with np.errstate(all="ignore"):
        scale_ref = np.maximum(np.abs(s_tt * n_c * s_uu), 1.0e-300)
        singular = ~(np.abs(np.linalg.det(gram)) > 1.0e-12 * scale_ref)
    gram = np.where(singular[..., None, None], np.eye(3), gram)
    coef = np.linalg.solve(gram, rhs[..., None])[..., 0]  # [cube, trial, 3]
    syy = (weight * y * y).sum(1)[:, None]
    sse = np.where(singular, np.inf, syy - (coef * rhs).sum(2))
    best = np.argmin(sse, axis=1)
    rows = np.arange(y.shape[0])
    interior = (best > 0) & (best < offsets.size - 1) & np.isfinite(sse[rows, best])
    k = np.clip(best, 1, offsets.size - 2)
    y0, y1, y2 = sse[rows, k - 1], sse[rows, k], sse[rows, k + 1]
    with np.errstate(all="ignore"):
        curvature = y0 - 2.0 * y1 + y2
        step = np.where(curvature > 0.0, 0.5 * (y0 - y2) / curvature, 0.0)
    step = np.where(np.isfinite(step), np.clip(step, -1.0, 1.0), 0.0)
    neighbour = np.where(step >= 0.0, k + 1, k - 1)
    c_here, c_next = coef[rows, k, 2], coef[rows, neighbour, 2]
    tilt = c_here + np.abs(step) * (c_next - c_here)
    shift = d[rows, k] + step * SHIFT_STEP_NM
    return np.where(interior, shift, np.nan), np.where(interior, tilt, np.nan)


def response_law(shift_nm: np.ndarray, tilt: np.ndarray) -> tuple[float, np.ndarray]:
    """``(kappa, beyond)``: ``kappa`` = the tilt per nm of shift, the least-squares slope through the origin over the cubes that have both; ``beyond = tilt - kappa x shift``
    for every cube (NaN where either is missing): the part of the tilt that does not follow the shift, i.e. baseline drift. A trace with no shift at all has ``kappa`` 0."""
    shift = np.asarray(shift_nm, dtype=np.float64)
    tilt = np.asarray(tilt, dtype=np.float64)
    both = np.isfinite(shift) & np.isfinite(tilt)
    denominator = float((shift[both] ** 2).sum())
    kappa = float((tilt[both] * shift[both]).sum() / denominator) if denominator > 0.0 else 0.0
    with np.errstate(invalid="ignore"):
        beyond = np.where(both, tilt - kappa * shift, np.nan)
    return kappa, beyond


def fitted_curve(
    template: ReferenceTemplate, wavelengths_nm: np.ndarray, shift_nm: float, scale: float, offset: float, count: int = 400
) -> tuple[np.ndarray, np.ndarray]:
    """The curve ``a T(w - d) + b`` over the range of ``wavelengths_nm``, at ``max(count, n)`` points: what the Spectra panel draws through the measured points."""
    wavelengths_nm = np.asarray(wavelengths_nm, dtype=np.float64)
    x = np.linspace(float(wavelengths_nm.min()), float(wavelengths_nm.max()), max(int(count), wavelengths_nm.size))
    spline = template.spline()
    return x, scale * template.evaluate(spline, x - shift_nm) + offset
