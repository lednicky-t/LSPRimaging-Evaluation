# Spectra analysis methods: what is computed where (LSPRi Evaluation rewrite)

Reference for the maths between the stored scan and the one number per ROI and cube (the metric) that the Sensorgram plots. Written 2026-10-25.
Each step says what it does, the formula, which function does it, and which setting controls it. Everything described here is built. Records of the tests behind the choices: `fit_window_modes_test_2026-10-25.md`; the template shift (section 7): `shift_estimators_comparison_2026-10-25.md`.

**Two ways to the number.** Fit method *No fit / Polynomial / Gaussian* go through steps 3-5 below (window, fit, maximum or centroid). Fit method *Template shift* (added later the same day) replaces steps 3-5 by one: the whole spectrum is compared with the ROI's reference spectrum and the sideways shift that fits best is the metric (section 7). It has no fit window, order or maximum / centroid choice.

## 0. The pipeline in one table

| Step | What goes in | What comes out | Code | Settings |
|---|---|---|---|---|
| 1. Scan (stored) | pixels of the sample and reference ROI at every wavelength and cube | mean, STD, `n_valid` per (cube, ROI, wavelength, side) in `analysis/scan.h5` | `analysis/frame_reduce.py`, `scan_store.py` | ROI geometry, reduction method (these invalidate the file) |
| 2. Formula | mean sample `Is`, mean reference `Ir` of ONE ROI | one value per wavelength: the spectrum | `query.formula_values` | formula (Absorbance, ...) |
| 3. Fit window | the finite points of one spectrum | which points are fitted: a range (low, high) in nm | `fit_window.py` | window mode and its parameters |
| 4. Fit | the points inside the window | a smooth curve (polynomial or Gaussian) | `query.fit_polynomial`, `query.fit_gaussian`; batch: `trace_fit.py`, `gaussian_fit.py` | fit method, polynomial order |
| 5. Metric | the fitted curve | one wavelength: maximum or centroid | `query.metric_from_fit`, `query.metric_from_fit_in_range`, `metric_range.py`; batch: `trace_fit._polynomial_group`, `_gaussian_group` | metric, metric range |
| 6. Statistics | the metric trace over cubes | spike rejection, smoothing, baseline, averages of ROIs | `statistics.py`, `scan_trace.py` | Sensorgram settings |

Steps 2-5 never change anything on disk. They are recomputed from the stored values whenever a setting changes. Raw data is never touched.

**One implementation, two routes.** The Spectra panel calls `query.fit_and_metric` for one spectrum at a time. The Sensorgram needs every cube of
every ROI, so `trace_fit.py` does the same calculation for many spectra at once with numpy. They must give the same number; this is pinned by tests
(`tests/unit/test_lspri_analysis_trace_fit.py`, `test_lspri_analysis_fit_window.py`; agreement measured on the real scan: 3e-10 nm).

## 1. The spectrum (step 2)

Values are floored first: `Is, Ir >= 1e-9` (a value of zero or below is not physical and would put -inf / NaN into the fit).
Per ROI, per wavelength, never pooled across ROIs:

- Absorbance: `A = log10(Ir / Is)`
- Sample / reference: `Is / Ir`
- Relative change: `(Ir - Is) / Ir`
- Modified absorbance (calculated, not offered in the menu): `-1000 log10(Is / Ir)`

A wavelength with no valid pixel in the sample or the reference (or a non-finite value) is left out of the spectrum, not filled in.

## 2. The fit window (step 3), `analysis/fit_window.py`

The window decides which points the fit sees. Everything outside is ignored by the fit; the fitted curve is drawn only over the window.

| Mode | Window `(low, high)` | Same for every spectrum? |
|---|---|---|
| No window | all points | yes |
| Manual range | the range set by dragging the band on the plot | yes |
| Maximum ± width | `c - h`, `c + h` with `h` = the width field (nm) | no: `c` is each spectrum's own centre |
| Maximum ± FWHM | `c - k W`, `c + k W`, `k` = the multiplier, `W` = that spectrum's FWHM | no |

Points are kept when `low <= x <= high` (inclusive), the same comparison in the batch and the one-spectrum route, so both pick identical points.

### 2.1 Centre `c` of a spectrum

1. Smooth lightly: `s_i = (y_{i-1} + y_i + y_{i+1}) / 3`; an end point repeats its own value outside the data. (Purpose: one noisy point must not pull
   the window off the peak.)
2. `i = argmax s`. If `i` has a neighbour on both sides, `c` is the top of the parabola through the three points
   `(x0, y0), (x1, y1), (x2, y2)` = `(x_{i-1}, s_{i-1}), (x_i, s_i), (x_{i+1}, s_{i+1})`:

   `c = x1 - 0.5 * [ (x1-x0)^2 (y1-y2) - (x1-x2)^2 (y1-y0) ] / [ (x1-x0)(y1-y2) - (x1-x2)(y1-y0) ]`

   kept between `x0` and `x2`. This works for uneven spacing. Why: with the 10 nm wavelength step the grid maximum jumps 10 nm at a time; the
   parabola top moves continuously. At an end of the data, or where the three points do not curve downwards, `c = x_i`.

### 2.2 FWHM `W` (quick method)

On the smoothed spectrum `s`: `base = min s`, `top = max s`, `level = base + (top - base) / 2`.
Walk left and right from the maximum to the first point at or below `level`; the crossing wavelength between that point and its neighbour is found
by linear interpolation. `W = x_right - x_left`.

- A side that never drops to the level (peak wider than the data): `W = 2 x (distance from c to the other crossing)`.
- Neither side crosses, or the spectrum is flat (`top - base <= 1e-9 x |top|`): no window, all points are fitted.
- Fewer than 4 valid points: no window.

Note the baseline is the lowest smoothed point of the whole spectrum, not a fitted offset. With absorbance spectra that sit on a baseline of
about 0.1 this matters: the half height is half-way between that baseline and the top.

A Gaussian-refit FWHM (`2.3548 sigma`) was compared on 6280 real spectra: median difference +0.4 nm, the same points selected in 88-98 % of spectra,
74 times slower. The quick method was chosen.

### 2.3 "Same number of points"

Only for the two per-spectrum modes. Half-width `h` (the width field, or `k W`) becomes a count `N = round(2 h / step) + 1` where `step` is the median
wavelength spacing, limited to `2 <= N <= number of points`. The window is the `N` neighbouring points nearest to `c`:
first point index `j = floor(f - (N-1)/2 + 0.5)` with `f` the fractional index of `c`, then moved inwards if it would run past an end of the data
(so near an end the window takes points outside its nominal range). The window `(low, high)` is the wavelength of the first and the last of these.
Effect: a point never joins or leaves the fit just because `c` moved a few nm; the window moves only in whole points.

### 2.4 What the tests showed about jumping (20 ROIs x 314 cubes, 10 nm step)

The window still changes between neighbouring cubes in 1-8 % of cases in every mode. A hard window on a 10 nm grid changes whenever an edge crosses a
point. The parabola removes the 10 nm jumps of the centre but lets the edges sit close to a point more often. Table: `fit_window_modes_test_2026-10-25.md`.

### 2.5 Independence

A spectrum's window is computed from its own points only. Selecting other ROIs, or other cubes, never changes it.

## 3. The fit (step 4)

Both fits use only the points inside the window, sorted by wavelength.

**Polynomial** (`query.fit_polynomial`; batch `trace_fit._polynomial_group`). Least squares, order `min(order, n_points // 2)` (capped at half the
number of points: a higher order chases noise and oscillates at the ends). The batch route keeps the polynomial in the scaled variable
`t = (2x - x_min - x_max) / (x_max - x_min)`, which avoids rounding error; results agree with the one-spectrum route to < 1e-6 nm. The curve is
sampled at `max(400, n)` points between the first and last point of the window for drawing.

**Gaussian** (`query.fit_gaussian`; batch `gaussian_fit.solve_gaussian`): `y = a exp(-(x - mu)^2 / (2 sigma^2)) + b`. The amplitude `a` and offset `b` are
solved exactly for each `(mu, sigma)`; only `(mu, sigma)` are iterated (Newton steps, damped). Spectra the solver does not converge on, or whose
`sigma` runs away (a window of a handful of points), are fitted by scipy `curve_fit`. Fewer than 4 points: no fit (a gap in the trace). Details:
`gaussian_fit.py` header.

**No fit:** the metric is read directly off the measured points inside the window (below).

## 4. The metric (step 5), `query.metric_from_fit` / `metric_from_spectrum`

Both metrics are read off the fitted curve. The maximum always uses the whole fit window. The centroid uses the whole fit window unless a **metric range** is chosen (section 6).

**Maximum.**
- Gaussian: `mu`, clipped to the window.
- Polynomial: the highest of the two window ends and every real stationary point inside the window (roots of the derivative). Exact, not read off
  the sampled curve.
- No fit: the highest measured point.

**Centroid** (intensity-weighted mean wavelength): `x_c = integral(x y dx) / integral(y dx)` over the window, clipped to the window.
- Polynomial: exact integral of the polynomial.
- Gaussian: trapezoid over the curve sampled at `max(400, n)` points.
- No fit: trapezoid over the measured points.

Things to know about the centroid as built:
- **The baseline counts as weight.** `y` includes the fitted offset, so the centroid is pulled towards the middle of the window. A rigid shift of the
  peak by 1 nm moves it by much less than 1 nm: measured on real spectra shifted by known amounts, the centroid moved 0.2-0.5 nm per nm for a
  fixed or whole-range window.
- **The window edges cut the curve at a non-zero height**, so a moving or narrow window makes the centroid jump (noise 0.4-0.95 nm with windows of
  ± 0.5-0.75 FWHM, against 0.02-0.08 nm with a fixed or wide window that holds the whole peak).
- **For a Gaussian fit the centroid adds nothing:** the centroid of a whole symmetric Gaussian is its centre `mu`, which is the maximum. The centroid
  differs from the maximum only when the window truncates the curve asymmetrically, which is a bias, not information. (Maintainer: the centroid is
  not used with the Gaussian fit.)
- **The polynomial maximum can exaggerate a shift.** A cubic fitted over the whole spectrum is a poor model of the peak: its maximum moved 2.0 nm for a
  rigid 1 nm shift of the real spectrum (1.17 for a fixed 540-640 nm window, 1.0 when the window follows the peak). The reported shift in nm
  therefore depends on the fit window and the model. Check "gain" (below) before comparing sensitivities between settings.

## 5. How a metric was judged (the tests behind the choices)

Two numbers per metric and setting, on the real scan `04_Bulk_sensitivity...` (20 ROIs, 160 consecutive cubes, 26 wavelengths, 10 nm step):
- **Noise:** the cube-to-cube scatter of the metric trace, `std(t_i - (t_{i-1} + t_{i+1}) / 2) x sqrt(2/3)` (a slow drift cancels).
- **Gain:** spectra of 4 cubes x 20 ROIs shifted rigidly by -4, -2, 0, 2, 4 nm (interpolated, edges held) and the slope of metric against shift. An
  honest metric has gain 1.00. Gain below 1 under-reports a shift, above 1 exaggerates it.
- **Noise / gain** is the noise expressed in nm of real peak shift: the figure to compare. Caveats: the shift is synthetic (a rigid translation of a
  measured spectrum, no change of peak shape); one dataset.

## 6. The metric range (built 2026-10-25), `analysis/metric_range.py`

The fit window decides only which raw points constrain the fit. The metric range, a second menu in the Metric group, decides which part of the
**fitted curve** the **centroid** is read from. The curve is continuous, so the 10 nm spacing of the measured wavelengths plays no part. It applies to
the centroid of a fitted curve only: the maximum has no range, and with "No fit" there is no curve (the menu is disabled).

| Choice | Weights `w` over the fitted curve `y(x)` | Centroid |
|---|---|---|
| Whole fit window (default, as before) | `y` (baseline included) | `integral(x w) / integral(w)` |
| Top part of the peak, fraction `f` | `max(y - level, 0)`, `level = base + f (top - base)` | same |
| Manual range `[lo, hi]` | `max(y - base, 0)` for `lo <= x <= hi`, else 0 | same |

`top` is the highest point of the fitted curve. `base` is the baseline: the fitted offset `b` of a Gaussian, the lowest point of the fitted curve for a
polynomial. The integrals are trapezoid sums over the fit's own grid (`max(400, n_points)` points between the first and last window point); the result
is clipped to the curve's wavelength range; weights that add up to nothing give no value (a gap). A missing bound of the manual range is the end of the
curve. The "top part" field is the percentage `100 f` ("above 30 % of height" reads the top 70 % of the peak); default 30 %.

Why the top part: its weights go to zero where the part ends, so where the fit window ends cannot move the result, and the baseline has no weight.
One implementation: the Spectra panel (one spectrum, `query.fit_and_metric`) and the Sensorgram (all cubes, `trace_fit`) both call
`metric_range.range_centroid`; they agree to < 1e-6 nm (pinned by `tests/unit/test_lspri_analysis_metric_range.py`).

**Test result** (real scan, polynomial fit, noise / gain in nm, lower is better; gain in brackets when it is not 0.9-1.1; prototype figures, the
built code reproduces the noise to the third decimal):

| Polynomial fit | all points | fixed 540-640 | max ± 50 nm | max ± 0.75 FWHM |
|---|---|---|---|---|
| centroid, whole fit window | 0.120 (gain 0.47) | 0.115 (0.21) | 0.023 | 0.407 |
| maximum | 0.105 (2.00) | 0.142 (1.17) | 0.191 | 0.333 |
| centroid, top part, 30 % | 0.108 | 0.120 (1.11) | 0.118 | 0.136 |

| Gaussian fit | all points | fixed 540-640 | max ± 50 nm | max ± 0.75 FWHM |
|---|---|---|---|---|
| centroid, whole fit window | 0.114 (0.45) | 0.122 (0.23) | 0.022 | 0.409 |
| maximum | 0.116 | 0.119 (1.13) | 0.109 | 0.130 |
| centroid, top part, 30 % | 0.116 | 0.211 (0.70) | 0.054 | 0.210 |

How to read it: for the polynomial fit the top part at about 30 % is the most window-independent number (noise / gain 0.108-0.136, gain 0.99-1.11 in all
four windows). For the Gaussian fit it equals the maximum when every point is fitted (a symmetric bell) and is worse when the fit window cuts the bell,
because a Gaussian fitted in a cut window does not determine its own offset (the baseline), and the level is measured from that offset. For a Gaussian
use the maximum; the centroid adds nothing there (maintainer: not used with the Gaussian fit). Lower fractions lower the noise but the polynomial's gain
then falls below 1 (f = 0: 0.66 with all points); 30-40 % gave the gain nearest 1. Full tables, f from 0 to 0.9: `fit_window_modes_test_2026-10-25.md`.

**What the range does not fix.** It makes the *shift* reliable (gain near 1, small noise). The *absolute* position still depends a few nm on the fit
window and the fit model: on the real scan the polynomial top-part centroid had a median of 587.7 nm with the fixed 540-640 window, 587.5 nm with
max ± 50 nm, 591.8 nm with max ± 0.75 FWHM and 596.5 nm with all points. Compare absolute positions only between runs analysed with the same settings.

Cost: the ranged centroid needs the fitted curve sampled, about 0.025-0.036 ms per spectrum against 0.010-0.025 ms for the whole-window centroid (about
1.5 s more for 160 ROIs x 314 cubes).

Not drawn on the plot: neither the level nor a manual metric range. The marker (diamond) shows the centroid on the fitted curve.

## 7. The template shift (built 2026-10-25), `analysis/template_shift.py`

**What it answers.** A fit first turns the points into a curve and then reads the top of the curve. The template shift asks the question the experiment is about directly: *by how many nm must
the reference spectrum of this ROI be moved sideways to agree best with this spectrum?* Every measured point takes part (both flanks and the tail), no fit window is needed, no peak shape is
assumed (the shape is the ROI's own measured reference), and a change of amplitude or of a constant offset is not read as a shift. The decision (maintainer, 2026-10-25): use the whole
measured range, not a cut of it; deal with the baseline as a separate step later (section 7.6).

**Settings** (`MetricSettings`, saved with the session; a session saved before this loads with the defaults): `fit_method = "template"`; `template_first_cube`, `template_last_cube` = the
reference cubes (inclusive, the cube numbers the panels show; default the first ten, 0 to 9; typed in either order). Window, order, maximum / centroid and metric range are not read. In the Spectra
panel: Fit menu, fourth choice "Template shift", with the two cube fields in its row.

### 7.1 The reference (template)

Per ROI, per wavelength: the mean of the formula values (absorbance, ...) over the reference cubes that have a value at that wavelength. This averages one ROI over time; ROIs are never pooled. A
wavelength with no value in any reference cube is left out of the template (not filled in); fewer than 4 wavelengths left: no template, the trace is a gap and the Spectra panel says why (also when the
whole scan has fewer than 4 wavelengths). The template function is the cubic spline through its points, held at its end values outside its range (edge hold). The reference position `p0` is the
maximum of that spline (0.05 nm grid, parabola refinement). The reference cubes are scanned cubes only (never-scanned cubes have no data). `trace_fit.reference_template` builds it from a ROI's whole trace;
a ROI's cubes must therefore never be split between calls (`trace_pool` hands out whole ROIs).

### 7.2 The shift

For one spectrum `y(w)` on the points `w` it has (a missing point is a zero weight) and a shift `d`:

    y(w) = a T(w - d) + b + noise,        a (scale) and b (offset) free

For each `d` on a grid (step 0.25 nm, +/- 60 nm) `a` and `b` are solved exactly, which leaves the sum of squares `SSE(d)`; with the sums over the spectrum's points (`n`, `S_y`, `S_T`, `S_yy`,
`S_TT`, `S_yT`) and the centred ones `S_TTc = S_TT - S_T^2/n`, `S_yTc = S_yT - S_y S_T/n`, `S_yyc = S_yy - S_y^2/n`:

    SSE(d) = S_yyc - S_yTc^2 / S_TTc,       a = S_yTc / S_TTc,       b = (S_y - a S_T) / n

The shift is the grid minimum refined by the parabola through its two neighbours. The metric (what the Sensorgram plots) is `p0 + d`, in nm, so it reads like a peak position and the Sensorgram's
baseline-offset statistic works as for the fits. Gaps (NaN, never a made-up number): fewer than 4 points; a template that is flat over the spectrum's points; a minimum on the edge of the grid. This is
the same least-squares problem as maximising the normalised cross-correlation; the known-shift test and the comparison with the Fourier phase slope are in the record.

**Cost.** 160 ROIs x 314 cubes: 1.9 s for the positions (about 12 ms per ROI, one ROI per call, in the helper process like the other fits); 0.7 s to draw all 160 ROIs on one cube.

### 7.3 One implementation, two routes

The Spectra panel (`query.fit_and_metric(..., template=...)`, one spectrum; the curve drawn is `a T(w - d) + b` over the whole range, the diamond at `p0 + d` on that curve) and the Sensorgram
(`trace_fit.template_trace`, every cube) call the same `template_shift.fit_template_shift`; on the real scan they agree to 3e-11 nm (pinned by `tests/unit/test_lspri_analysis_template_shift.py`).
`fit_and_metric` without a template raises `ValueError`, and `fit_spectrum("template")` too: the dispatch must never fall through to the polynomial. The settings are in the Sensorgram's cache key
(`MetricSettings.template_key()`).

### 7.4 What was measured (real scan `04_Bulk_sensitivity...`, 160 ROIs, 26 wavelengths of 10 nm; the built code)

Event: cubes 201-227 against the baseline cubes 231-end, per ROI, median over ROIs. Reference cubes 0-9 and 5-30 give the same numbers.

| | built template shift | for comparison (same data) |
|---|---|---|
| shift on the event | 7.27 nm | model-free peak top of the ROI average 7.0 nm; polynomial top-30 % centroid 8.4; Gaussian maximum 7.95 |
| noise per ROI (std over the baseline cubes) | 0.057 nm | |
| SNR (shift / noise) | 126 | polynomial top-30 % centroid 129, Gaussian maximum 126, any windowed fit 38-89, parabola 13-37 |
| response to a known rigid shift (real spectra moved by interpolation) | 1.000 | |
| false shift for +1 % amplitude / +0.002 A offset | 0.000 / 0.000 nm | |
| false shift for a tilt of +0.002 A across the range | 0.20 nm | peak-top methods 0.07-0.11 |

The whole-spectrum fits sit at the noise floor (Cramer-Rao bound for the measured, nearly white noise of 0.0005 A: 0.05 nm per spectrum); no estimator tried does better (record).

### 7.5 Diagnostics: scale, rms and the tilt

`TemplateFit` carries, per spectrum: `scale` `a`, `offset` `b`, `rms` (residual), and the **tilt** `c`: the change of the baseline across the whole range (in the units of the spectrum), from a *second,
joint* fit `y = a T(w - d) + b + c u`, `u = (w - w_first)/(w_last - w_first)`, whose own shift `tilt_shift_nm` is searched within +/- 10 nm of the metric's shift. The metric never uses it (freeing the tilt
costs 16 % more noise per spectrum and credits part of the real response to the baseline: on the real scan 5.84 nm instead of 7.27). An added tilt is measured in full (a synthetic 0.004 A tilt is returned as
0.0040000; a pure offset as 0).

`response_law(shift, tilt)` splits the tilt into the part that follows the shift (`kappa x tilt_shift`; the red tail of the peak changes with its position) and `beyond` = the rest, i.e. baseline drift.
Measured on the real scan: `kappa` about 0.0026 A per nm (ROI median 0.00254, p10-p90 0.00235-0.00274), R^2 of tilt against shift 0.99 over the whole run; tilt noise per spectrum 0.00033 A, which is mostly
measurement noise; scatter of `beyond` on the ROI average 0.00006 A in the baseline cubes and 0.00034 A over the ramp (per ROI 0.0004 / 0.0006 A). Ignored, a drift of that size would be 0.006 nm on the average.
Limits: `kappa` is fitted to the same trace, so a drift that arrives together with the shift is partly absorbed (a synthetic drift of 0.002 A shows as a step of about 0.001 A in `beyond`): the diagnostic detects
drift, it does not measure it exactly. Cost: 14 s for 160 ROIs x 314 cubes, so it is computed on request only (`trace_fit.template_diagnostics`), never in the position trace.
**Not wired into a panel yet:** the Spectra panel keeps shift, scale and tilt in `RoiSpectrum` (not displayed); a per-cube tilt channel in the Sensorgram is the planned next step (needs a decision on its layout).

### 7.6 What the shift is, and what it is not

- It is the translation that best fits the *whole* spectrum. If the peak also changes shape (here: the red tail changes with the position), this differs from the position of the top of the peak by a
  model-dependent amount: 7.27 nm here, the peak top moved 7.0 nm, a fit with a free tilt gives 5.84 nm. **Compare values between runs only when analysed with the same settings, reference cubes included.**
- The absolute number `p0 + d` is the reference's peak position plus the shift. If the reference has no maximum inside the range (a peak cut by the end of the scan), `p0` is an end of the range: read
  the number as a shift only.
- The scatter of the reference cubes themselves is smaller than that of other cubes (their noise is in the template): never take it as the noise of the method.
- Large shifts: the reference is held at its end values outside its range, so shifts of a third of the peak width or more are read slightly wrong (synthetic: 0.003 nm at 15 nm, 0.03 at 25, 0.13 at 40).
- **No baseline removal.** A baseline cut from the spectrum ends removes part of the response when the peak is wider than a third of the range (measured: SNR 124 -> 77-86, read-out 0.79-0.94 of the
  shift), because no part of the range is peak-free. A separate baseline step needs a baseline from something independent of the peak (a measured baseline, a model of the optics). Offset and amplitude
  drift are already handled by the free `a`, `b`. Tilt drift is not removed (0.2 nm per 0.002 A); the tilt diagnostic is there to show it, and a soft penalty on the tilt can be added later if a run needs
  it (cost at 0.001 A: SNR 126 -> 91-95, false shift for 0.002 A: 0.2 -> 0.03-0.04 nm; table in the record).

## 8. Is the polynomial order a good one? (built 2026-10-25), `analysis/order_quality.py`

The Spectra panel's order menu (1st to 13th) marks every order with a coloured dot. The verdict is **leave-one-out cross-validation**: leave one measured point out, fit the rest, compare with the
point, RMS over the points. For a least-squares fit no refit is needed: with the hat matrix `H` of the polynomial the prediction error of point `i` is `e_i / (1 - h_ii)`. One QR decomposition of the
Vandermonde matrix of the points gives the hat matrices of all orders at once (nested: the first `k + 1` columns of `Q` span the polynomials up to order `k`), so the 13 orders cost a few numpy calls
per set of points. The in-sample error cannot be used: it falls with every order (at the 12th it equals the noise: the curve follows the noise).

**Verdict per order and cube, relative to the best order of the same spectra** (`TOLERANCE` = 1.25): *good* if the error is at most 1.25 x the smallest; *over-fitted* if the order is above the best and worse;
*under-fitted* if below the best and worse. The good orders are not always one block (real scan, all points: the 7th and 9th good, the 8th, 10th and 13th not). The error of a cube is the median over the selected
ROIs (at most 20, evenly spread) of their errors, computed on the points the fit would use (the fit window per spectrum, the wavelengths that have a value). The fit never uses more than half as many terms as
points, so an order above that cap has no verdict for those points (grey).

**Colours** (for the present cube): green = good here and at 95 % of the cubes judged or more (`GOOD_SHARE`; "every cube" was too strict: with one or two ROIs the best order of a cube jumps between neighbours with the noise); yellow = good here, but over- or under-fitted at more than 5 % of the cubes; amber = over-fitted here; purple = under-fitted here; grey = not available or not
judged. A cube change only looks the colours up; a new selection, formula, fit window or scan recomputes (worker thread, 30-190 ms).

**Reading it.** It says how well an order predicts a point it did not see; it does not make the peak position quiet or unbiased: the noise of the polynomial maximum rises with every order (real scan, all points:
0.05 nm at the 4th, 0.18 nm at the 12th) and its bias shrinks. Over the whole range no order predicts to the noise (the best is 11 times the noise), a sign to use a window or the template shift (section 7).
Measured tables and the real-data behaviour: `poly_order_quality_study_2026-10-25.md`. Tests: `tests/unit/test_lspri_analysis_order_quality.py`.

## 9. Where to look when a number looks wrong

- A spectrum looks wrong: formula (section 1), then `n_valid` / masks in the scan.
- The fitted curve starts or ends where you did not expect: the window (section 2); the curve is drawn only between the first and last window point.
- A metric disagrees between the Spectra panel and the Sensorgram: that must not happen; run `test_lspri_analysis_trace_fit.py` and
  `test_lspri_analysis_fit_window.py`.
- A shift in nm looks too big or too small compared with another setting: gain (sections 4-6); a centroid that barely moves with the peak is the
  whole-window centroid (baseline weight): choose a metric range.
- Cached traces: the Sensorgram caches a trace per settings tuple (`MetricSettings.window_key()`, `metric_range_key()`, `template_key()` plus fit, order, metric); a setting that changes the
  chosen points but is missing from that key would show a stale trace (pinned by `test_the_cache_key_changes_with_every_setting_that_changes_the_points`; for the reference cubes by
  `test_the_cache_key_follows_the_reference_only_when_it_matters`).
- The order menu shows a colour you do not expect: it is relative to the best order of the same points (section 8), judged on the fit window and the selected ROIs; with one ROI or few cubes the best order is noisy, so two
  neighbouring orders can alternate. Grey after the 5th or so means the window holds too few points (at most half as many terms as points).
- A template-shift trace is empty (all gaps): the reference cubes have no usable data for that ROI (change them), or the scan has fewer than 4 wavelengths; the Spectra panel prints which.
- A template-shift trace reads a different shift from a fit of the same data: expected (section 7.6: the shift of the whole spectrum is not the position of its top). Compare within one setting.
- A template-shift trace drifts slowly while the peak does not: look at the tilt (section 7.5, `trace_fit.template_diagnostics`); a baseline that tilts by 0.001 A moves it by 0.1 nm.
