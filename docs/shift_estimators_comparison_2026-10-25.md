# Ways to read a peak shift other than fit-then-compare (test, 2026-10-25)

Question (maintainer): is there a better way to compare spectra / peak positions than fitting each spectrum and comparing the fitted metric? Goal: best signal-to-noise for the shift between the highest plateau (cubes 201-227) and the baseline (cubes 231-end). This records what was measured (the research, with throwaway prototypes). The outcome, the **template shift**, was then built: see "Decision and what was built" at the end and `spectra_analysis_methods.md` section 7 (the method reference).

**Data.** `scan.h5` of `04_Bulk_sensitivity_pumpplan_0-70percent_to_LbL_with_water_v1_LED_V2` (session 2026-10-22_172035): 314 cubes, 160 ROIs, 26 wavelengths (470-720 nm, 10 nm step), absorbance per ROI. Cube numbers are the file's 0-based `cube_index` (a one-cube slip changes nothing). Peak 581.8 nm in the baseline, 588.8 nm on the plateau, about 7.0-7.3 nm shift.

**Score.** Per ROI: SNR = |mean(plateau) - mean(baseline)| / std(baseline cubes). d' = the same with the mean of the baseline and plateau standard deviations (penalises a method that is only quiet in the baseline). Median over 160 ROIs. Methods that learn something from the data (template, covariance, projection) were **cross-fitted**: the cubes of one parity are scored with a model trained only on the other parity. Scripts were throwaway (scratchpad).

## Result (per ROI, median over 160 ROIs)

| method | shift (nm) | SNR | d' |
|---|---|---|---|
| Template shift: least-squares shift of each spectrum against a reference spectrum (cubic-spline template, scale / offset free or not) | 7.27-7.33 | 124-126 | 144-146 |
| Same, template = mean of cubes 5-30 only (a reference picked before the event) | 7.27-7.32 | 126-128 | 146-148 |
| Same, template = Gaussian shape fitted to cubes 5-30 | 7.63 | 129 | 148 |
| First-order (linearised) shift, closed form, no search | 7.62 | 129 | 145 |
| Existing: polynomial order 3, centroid of the top 30 %, all points | 8.44 | 129 | 150 |
| Existing: Gaussian fit, maximum, all points | 7.95 | 126 | 145 |
| Existing: polynomial order 3, maximum, all points | 13.9 | 117 | 130 |
| Existing: Gaussian, maximum, window 540-640 / max ± 50 nm | 7.6 / 6.8 | 74 / 89 | 93 / 91 |
| Existing: polynomial maximum, max ± 50 nm | 7.0 | 38 | 42 |
| Parabola top of the 3-point-smoothed raw spectrum (no fit) | 7.2 | 37 | 42 |
| Two flank wavelengths subtracted (one on each side of the peak) | (absorbance) | 61 | 66 |
| One wavelength on the steepest flank | (absorbance) | 22 | 25 |
| Fisher projection trained on the event (not a peak position, see below) | (arbitrary) | 193 | 206 |

Weighting by the baseline noise (inverse-variance weights, or the full noise covariance, "GLS") raises SNR to 140-166 but d' falls to 25-126: the plateau scatter becomes 1.4-4 times the baseline scatter, because the weights favour wavelengths that are quiet in the baseline but not in the changed spectrum. Not recommended.

## What it means

1. **The estimators that use every point sit at the noise floor.** The Cramér-Rao bound for a pure shift with independent noise (measured per-wavelength noise 0.0003-0.0006 absorbance, nearly white) is 0.05 nm per spectrum, i.e. an SNR of about 140 for a 7.3 nm shift. Template shift, linearised shift, the polynomial top-30 % centroid and the all-points Gaussian maximum reach 145-150. They differ by less than the ROI-to-ROI spread (p10-p90 about 108-152). **No estimator will do materially better on this data; the cut to the all-points fits is the 3-5x over windowed fits and the unsmoothed parabola.**
2. **Template shift ties with the best and is the most robust.** It needs no fit window, no model (the template is a measured spectrum), is independent of the 10 nm grid, its nm scale is honest (7.27 nm, the same as the model-free parabola on the ROI average), and with free scale + offset it does not read an amplitude change as a shift. It costs about 1 s for 160 ROIs x 314 cubes. It gives a shift relative to the template (state of the reference cubes), not an absolute position; add the template's own fitted peak for an absolute number. Over the 0-200 ramp (never used for training) it is linear against the other methods (slope 1.02-1.03).
3. **The Fisher projection looks 40 % better but is a different thing.** It learns the direction in spectrum space that separates plateau from baseline, so it counts any spectral change (shift, amplitude, width, background), not only a shift, and it needs a known event to train on. On the unseen ramp its advantage disappears (point-to-point noise 0.098 nm against 0.100 for the others) because that noise is real and shared (next point). Do not use it as a peak position.
4. **Where the limit really is.** In the quiet baseline the per-ROI noise is nearly independent (ROI-to-ROI correlation 0.03); the 160-ROI average reaches an SNR of about 420, i.e. 0.26 of the value independent noise would give, so a shared part (noise PC1 27 %) remains. During the ramp the point-to-point noise doubles (0.054 -> 0.10 nm per ROI) for every method and is **shared by the ROIs** (correlation 0.67; the 160-ROI average is 0.081 nm, ten times above the independent expectation 0.008). That is a real fluctuation of what all ROIs see (most likely flow / mixing of the medium), not measurement noise, and no estimator can remove it.
5. **What would help beyond the estimator is the acquisition.** Information about the shift (derivative squared over variance) sits at 520-560 nm (about 37 %, the blue flank) and spread over 600-720 nm (the red tail, about 3-5 % per wavelength). Finer steps or repeated frames at those wavelengths would give more than any change of estimator. Caveat: the red tail may carry background, not only shift information; a template with free offset / slope handles it better than a fit that assumes a peak.

## Limits of this test

One dataset, one event (a bulk refractive-index step), 26 wavelengths at 10 nm. The shift of the real peak is not known independently; "shift in nm" is relative to the methods themselves. The cross-fitting interleaves cubes of the same event, so it guards against overfitting noise but not against a difference between events. Not tested: a different dataset or particle type, a template taken from another run, peak shape that changes strongly.

## Second round: the five textbook methods (maintainer's table, same day)

Same data, same score, same cross-fitting. Extra checks: **known-shift gain** (real baseline spectra shifted by -8..+8 nm, local gain at 0 and secant 0 -> 7.27 nm), **non-shift changes** (amplitude +1 %, offset +0.002 A, tilt +0.002 A across 470-720 nm, broadening +2 %, reported as false shift in true nm), and linearity on the unseen ramp (cubes 0-200).

| method (variant) | shift on the event (nm) | SNR | d' | gain at baseline / secant | false shift, tilt 0.002 A (nm) | verdict |
|---|---|---|---|---|---|---|
| Normalised cross-correlation, continuous (spline template, overlap only, 0.25 nm grid + parabola) | 7.26 | 128 | 148 | 1.000 / 1.000 | 0.20 | ties the best. Maximising NCC is the same as the template shift with free scale + offset; it stays honest because the overlap loses only one point at 7 nm |
| NCC, textbook (integer lags of 10 nm + parabola) | 7.10 | 123 | 144 | 0.989 / 0.973 | 0.19 | slightly worse: the parabola on a coarse lag grid bends (0.055 nm non-linearity) |
| Fourier phase slope, K = 2-6 lowest frequencies, no window | 7.30-7.35 | 123-126 | 143-145 | 1.000 / 1.002 | 0.19 | ties the best; K = 10 adds noise (d' 142) |
| Fourier phase slope with a Hann window | 5.9-6.0 | 87-94 | 103-110 | 0.85 / 0.86 | 0.09 | worse and under-reads: the window throws away the tails that carry the shift |
| Spectral centroid of the raw spectrum, all points | 4.29 | 123 | 141 | 0.44 / 0.45 | 0.51 | looks quiet but reads only 44 % of the shift (baseline weight); true-nm noise 0.078 nm |
| Centroid, 540-640 nm | 1.77 | 97 | 111 | 0.25 / 0.26 | 0.13 | reads 25 % of the shift |
| Centroid of the raw points above 30 % of height | 7.63 | 113 | 124 | 1.00 / 0.96 | 0.11 | usable; 12 % noisier than the fitted top-30 % centroid (129 / 150) and 0.16 nm non-linear |
| Derivative zero crossing, Savitzky-Golay 9/2 | 7.05 | 86 | 99 | 0.98 / 1.00 | 0.10 | ~1.5x the noise of NCC; very dependent on the filter: 7/3 gives SNR 28, 5/2 gives 42 |
| Cross-correlation + Hilbert zero crossing | 5.12 | 388 | 224 | 0.2-0.4 / 0.73 | 0.09 | **not trustworthy**: apparent SNR comes from low sensitivity near the baseline (gain 0.2-0.4, rising to 0.74 at 7 nm: ~1 nm non-linear), plateau scatter 2.3x baseline, 0.25 nm residual on the ramp. After correcting by the local gain it is about as noisy as NCC (0.03-0.06 nm) and can not be calibrated to nm |
| Unnormalised cross-correlation maximum + parabola | -0.02 | 18 | 21 | 0.00 | 2.2 | does not track a shift at all on these spectra (the correlation peak is anchored by the baseline and the 470-720 nm edges) |

What it adds to the first round:
- **NCC, Fourier phase slope and the template shift are one family.** Same noise (0.054 nm per ROI per cube) and same gain; choose by convenience. NCC and the template shift are exact at any shift size; the phase slope is a closed form (no search), but a window ruins it.
- **Raw centroids are quiet because they are insensitive**, not because they are good: judge a centroid by noise / gain (true-nm noise 0.078 nm, 1.4x NCC).
- **Derivatives amplify noise as the table said:** best filter 0.076 nm, worst 0.23 nm.
- **Boundary effects are real but small here**: the overlap-only NCC is unbiased at 7 nm shifts, because the edges of the data (470 and 720 nm) are flat.
- **A weakness of all methods that use the whole spectrum is baseline tilt.** A tilt of 0.002 A across the range (under 1 % of the 0.3 A peak) gives a false shift of ~0.2 nm (4x the noise) in template / NCC / Fourier; the top-of-peak methods give 0.07-0.11 nm. Freeing a linear baseline in the template fit removes it exactly (false shift 0.000) but costs a lot: SNR 84 instead of 124 (d' 96 against 145), and the shift on the event falls from 7.27 to 5.84 nm (the real event is not a pure translation of the baseline shape; part of it looks like tilt or tail change). Whether the tilt in real runs is large enough to matter is not measured.

## Third round: whole-spectrum fit and the baseline (maintainer's decision: keep the whole measured range, handle the baseline as its own step)

**How big is a tilt of the baseline in this run?** Free scale / offset / tilt template fit on every cube (template = mean of the baseline cubes; tilt c = change of absorbance across 470-720 nm, peak height of the template 0.214 A).
- Independent drift is small: scatter of c in the baseline cubes 0.00035 A per spectrum (mostly measurement noise), 0.00006 A for the 160-ROI average. Ignored, that is about 0.035 nm per ROI and 0.006 nm on the average (the 0.2 nm quoted above was for an invented 0.002 A tilt).
- The tilt that exists is part of the response: over the whole run (baseline, ramp, plateau) the ROI-averaged tilt follows the shift almost exactly (R^2 = 0.996, 0.0026 A per nm; plateau - baseline = 0.015 A; amplitude +5.3 %). So the real spectrum does not just translate: its red tail changes with the shift.

**A baseline step cut from the spectrum ends does not work well here.** Line through the first and last k wavelengths, removed from the spectrum and from the template, then the whole-spectrum template shift:

| baseline step | event shift (nm) | SNR | d' | gain for a real shift | false shift, tilt 0.002 A |
|---|---|---|---|---|---|
| none | 7.27 | 124 | 145 | 1.00 | 0.20 nm |
| line through 2 / 3 / 4 / 6 end points per side | 5.35 / 5.21 / 5.04 / 4.59 | 77 / 81 / 83 / 86 | 89 / 93 / 94 / 97 | 0.94 / 0.91 / 0.88 / 0.79 | 0.000 |
| tilt free inside the fit | 5.84 | 84 | 96 | 1.00 | 0.000 |

Reason: the peak is about 109 nm wide in a 250 nm window, so no part of the range is peak-free; the ends hold tail of the peak, the step removes part of the response, and the read-out shrinks by an amount that depends on k. A separate baseline step needs a baseline that comes from something independent of the peak (a measured baseline or reference, a physical model of the optics); the scan has none.

**What the tilt costs.** In true nm (noise / gain) the noise per spectrum goes from 0.054 nm (tilt forbidden) to 0.063 nm (tilt free), 16 % more. Most of the SNR loss in the table is that the event reads smaller (5.84 against 7.27 nm) because the response-locked tilt is no longer counted as shift. Which of 7.27 and 5.84 is "the shift" is a matter of definition; the model-free peak top of the ROI-averaged spectrum moved 7.0 nm.

**Soft tilt (Gaussian prior on c, sigma_c; sigma_n = 0.0005 A).** Event SNR / false shift for +0.002 A tilt: forbidden 124 / 0.196 nm; sigma_c = 0.0005: 102 / 0.079; 0.001: 91 / 0.028; 0.002: 86 / 0.008; free 84 / 0.000. A prior centred on the response law (c ~ N(kappa delta, sigma_c^2), kappa = 0.00266 A per nm learned per ROI from the other cube parity) is better at equal immunity: sigma_c = 0.0005: SNR 111, false shift 0.10 nm; 0.001: 95 / 0.043; 0.002: 87 / 0.013 (the same shift of 5.85 nm). The false shifts are the same on baseline-state and plateau-state spectra. No setting gives both the 124 and the immunity: the tilt direction overlaps the response direction, so protection costs signal.

Not tested: a curved baseline (only linear tilt and offset), a baseline that drifts in a real run, other datasets.

## Decision and what was built (maintainer: "go with the better choice but document it properly", 2026-10-25)

**Decision.** Whole-spectrum fitting (no cut of the measured range) and the baseline as a separate step *later*, not now. Chosen metric: the **template shift** (least-squares shift of the whole spectrum against the ROI's reference spectrum, scale and offset free), with a tilt diagnostic and no tilt removal. Reasons, from the rounds above: it ties the best fits at the noise floor (SNR 126 against 129 for the polynomial top-30 % centroid and 126 for the Gaussian maximum, 3-5x better than any windowed fit), it needs no window and no assumed peak shape, its nm scale is honest (response 1.000 to a known shift), it ignores amplitude and offset changes, and it costs about 12 ms per ROI. A baseline cut from the spectrum ends does not work for peaks this wide (round 3).

**Built (all in the LSPRi submodule unless noted; uncommitted at the time of writing).**

| What | Where |
|---|---|
| The maths: reference, shift, diagnostics, response law | `analysis/template_shift.py` (pure, no Qt) |
| Settings: fit method `template`, `template_first_cube`, `template_last_cube`, `template_key()`, `reference_cube_range()` | `analysis/settings.py` (saved with the session; old sessions load with 0-9) |
| One spectrum (Spectra panel): `fit_and_metric(..., template=...)`, `template_fit_and_metric`; `fit_spectrum("template")` and a template-less call raise | `analysis/query.py` |
| Every cube (Sensorgram): `template_trace`, `reference_template`, `template_diagnostics`; `TraceInput.cube_indices` | `analysis/trace_fit.py`, `analysis/scan_trace.py` (`trace_input`, `trace_key`) |
| The Spectra panel's data (builds the ROI's reference from the whole scan; shift, scale, tilt kept) | `analysis/scan_view.py` |
| Cache key | `analysis/scan_trace.trace_key`, `analysis/engine.py` fingerprint |
| Controls: "Template shift" in the Fit menu (own icon), two cube fields in its row, the controls that mean nothing for it are disabled, no window band drawn; Sensorgram axis label and status line | `panels/spectra/panel.py`, `panels/spectra/pick_menu.py`, `panels/sensorgram/panel.py` |
| Tests (umbrella repo) | `tests/unit/test_lspri_analysis_template_shift.py` (29), plus one in `tests/unit/test_lspri_analysis_trace_pool.py` and two in `tests/integration/test_lspri_spectra_panel.py` |

**Checked on the real scan with the built code** (reference cubes 0-9 and 5-30 alike): event shift 7.27 nm, noise 0.057 nm per ROI, SNR 126, Spectra route and Sensorgram route agree to 3e-11 nm; 160 ROIs x 314 cubes in 1.9 s; the tilt diagnostic costs 14 s for the same, so it is computed on request only. The tilt figures in round 3 came from the prototype (the tilt taken from the nearest grid point); the built code interpolates and gives: tilt noise per spectrum 0.00033 A, `kappa` 0.00254 A/nm (ROI median), R^2 0.99, scatter of the drift beyond the law 0.00006 A (ROI average, baseline cubes) and 0.00034 A (ramp). Same conclusions.

**One thing found while building.** The first version measured the tilt at the shift of the metric. A drift also bends that shift (0.2 nm per 0.002 A), so only about 40 % of an added drift showed. The tilt now comes from a joint shift-and-tilt fit (the metric is not affected) and an added tilt is returned in full (a synthetic 0.004 A as 0.0040000). What remains: `kappa` is fitted to the same trace, so a drift that arrives together with the shift is partly absorbed (a synthetic 0.002 A drift shows as a step of about 0.001 A beyond the law).

**Not built, and why.**
- A per-cube tilt channel in the Sensorgram and a readout in the Spectra panel: they are new screens, which need a layout decision first (proposal: a small plot under the Sensorgram sharing its time axis, the tilt beyond the law of the selected ROIs or their average).
- A soft penalty on the tilt (a setting for runs with visible baseline drift): measured trade-off above (round 3); build only when a run needs it.
- A separate baseline step: needs a baseline independent of the peak (a measured baseline or a model of the optics); none exists in the scan.
- The icon, the cube fields in the menu row and the axis label were not looked at by eye (GUI is checked by the maintainer).
