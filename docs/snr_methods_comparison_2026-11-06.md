# Signal-to-noise of the spectral readouts, S1/B1 re-check (2026-11-06)

Question (maintainer): the polynomial fit seems to give the best result: about half the noise of the template shift, with a smaller signal (~4 nm). Can the sensitivity (SNR, limit of detection) be improved by the maths alone (fitting / comparison models)? Re-check data, masks, ROI positions, S1/B1. Follows `shift_estimators_comparison_2026-10-25.md`.

**Data.** Session `2026-10-22_172035` of `04_Bulk_sensitivity_pumpplan_0-70percent_to_LbL_with_water_v1_LED_V2`, `scan.h5` as rescanned 2026-11-06 (one ROI layout, one recipe): 314 cubes, 160 ROIs, 26 wavelengths 470-720 nm, absorbance. Plan pair 1: **B1 = step 1** (H2O, cubes 2-31, 30 cubes), **S1 = step 8** (cubes 201-225, 25 cubes), windows from the session's plan edits.

**Score.** Noise = the app's step noise (SD about the least-squares line through the step, `step_stats.step_statistics`), i.e. scatter over time inside one step. SNR = |mean(S1) - mean(B1)| / noise(B1); median over the 160 ROIs ("per ROI"), plus the same on the group lines (10-ROI column averages, median over groups) and on the 160-ROI average. Existing methods were run through the app's own code (`trace_fit.traces_from_inputs`); scripts were throwaway (scratchpad). Response-shape weights were learned on other groups or other cubes than they were scored on.

## Result (B1 = step 1, S1 = step 8)

| readout | signal (nm) | noise B1 (nm) | SNR per ROI | SNR group | SNR 160 avg |
|---|---|---|---|---|---|
| Template shift (session setting, ref cubes 0-9) | 7.08 | 0.067 | 102 | 475 | 771 |
| Template, ref = B1 cubes | 7.08 | 0.067 | 102 | 472 | 771 |
| Template + free tilt | 5.61 | 0.081 | 67 | 324 | 587 |
| Template + free width | 6.92 | 0.067 | 101 | 463 | 753 |
| Template, pooled 1/sigma wavelength weights | 7.39 | 0.067 | 109 | 501 | 816 |
| Polynomial 3 (or 4), centroid, all points | 4.14 | 0.035 | **117** | 459 | 845 |
| Polynomial 3, centroid of the top 20 % | 7.39 | 0.062 | 116 | 475 | 841 |
| Polynomial 11, centroid, all points | 4.00 | 0.035 | 111 | 438 | 835 |
| Polynomial 13, centroid | 4.03 | 0.038 | 103 | 428 | 802 |
| Polynomial 3 / 11, maximum | 13.6 / 6.7 | 0.141 / 0.148 | 94 / 45 | | |
| Gaussian maximum / Voigt maximum, all points | 7.73 / 7.41 | 0.067 / 0.066 | 112 / 111 | 440 / 481 | 693 / 753 |
| Gaussian centroid | 3.56 | 0.030 | 115 | 462 | 722 |
| **Response shape** (matched filter: weights = mean S1-B1 change / noise variance, learned on the other 15 groups) | 7.23 | 0.056 | **125** | **524** | **895** |

The 160-ROI and group numbers come from one trace each over 25-30 cubes: their own uncertainty is about ±13 %, so differences below ~15 % there are not meaningful. The per-ROI median is the robust comparison.

**Other pairs** (response shape learned on 1 -> 8 only, applied unchanged): the ranking holds on 9->8, 2->3, 2->6, 3->7, 6->9. Per ROI the response shape beats the template by +10-20 % on every pair. The polynomial 3 centroid beats the template by +2-15 %, and its top-20 % variant matches or beats the all-points centroid.

## What it means

1. **"Half the noise" is half the signal.** The all-points polynomial centroid reads 58 % of the shift the template reads (4.1 vs 7.1 nm). The noise in nm shrinks by the same factor. SNR, not noise, sets the limit of detection (3 sigma / signal). On SNR the polynomial is ahead by 2-15 %, not 2x. To compare noise in nm between methods, use the top-20 % centroid, which reads the full shift at the same SNR.
2. **The template shift is at the theoretical limit for a pure shift.** The Cramér-Rao bound (the best possible unbiased shift estimator) for the measured per-wavelength absorbance noise is 0.065 nm in B1 and 0.037 nm in S1. The template reaches 0.067 / 0.039. The noise is white in time (point-to-point noise = detrended SD), independent between ROIs (correlation 0.01) and nearly independent between wavelengths (mean |corr| 0.07). No other shift estimator can do better than about +4 %.
3. **Why a centroid can beat that limit.** Water -> EG changes more than the position: amplitude and width change too. A centroid also picks up part of that, so it is not a pure shift. The best linear readout of the whole change (the response shape, item 4) sets the ceiling: about 125 per ROI, +20 % over the template, +7 % over the best polynomial. That is how much the maths alone can still gain on this data.
4. **Response-shape readout: the only candidate with a real gain, with caveats.** It projects each spectrum onto the expected change, weighted by the noise at each wavelength. Its nm scale is calibrated to the template on the training event. It is not a peak position: it counts any change shaped like the training response. It needs a calibration event (a bulk-RI step), and the shape of a binding response can differ from a bulk response (shorter decay length), so transfer from bulk to biology is untested. Not built.
5. **What does not help:** a free tilt (noise +20 %, signal -20 %), a free width (no change), the B1 cubes as template reference (no change), weighting each ROI's own wavelengths by its own variance (variance noisier than the gain), polynomial order above 4, maximum instead of centroid. Weighting ROIs by 1/noise² in the 160 average: +3 %.

## Masks and ROI positions

Clean. One ROI layout for the whole scan. In S1 no pixel is masked. In B1 six ROIs have a few masked pixels (at most 134 of 898). No ROI uses different pixels in S1 and B1. Image motion cube-to-cube is ~0.01 px in every step (phase correlation at 600 nm). Step 7 drifts 0.3 px over the step.

## The real limit: step noise depends on the pump, not on the maths

Absorbance noise per step (median per ROI and wavelength, ×1e-4 A): step 1: 5.7, step 5: 6.3, step 9: 4.8; steps 2, 3, 6, 7, 8: 3.4-3.7. The light level is the same in every step, the camera pixel noise is shot-noise-like and the same in every step, and image motion is the same. **Steps 1, 5 and 9 are exactly the steps where pump 1 runs at 0.04 ml/min** (pump plan CSV); with pump 1 off or at 0.02 the steps are quiet. The extra noise is in the sample ROIs, not the reference ROIs (step 5: sample ROI 7.2, reference ROI 4.9, sample-reference correlation 0.57 against 0.8 in quiet steps). It is random from frame to frame and between ROIs, so no spectral model removes it. In step 1 there is also a large shared light flicker (6.1 vs ~2.5), but the reference ROI cancels most of it. Cause not identified (flow-related local fluctuation at the sample ROIs?). A quiet baseline step would give ~1.7x SNR, more than any estimator.

## Limits of this test

One dataset, bulk RI events only. Response-shape transfer tested only between bulk steps of the same run. Group and 160-average SNRs have ±13 % uncertainty from the cube count. Step 4 has no edited start in the session and was skipped in the per-step noise table.

## Decisions and what was built (2026-11-06/07)

Maintainer's decisions: (1) build the response shape, calibrated on **two cube ranges** typed in the Fit menu, scaled to **pure-shift nm**, weights learned **per ROI group**; (2) new-session default
polynomial order 3, centroid of the top 20 %, all parameters still changeable; (3) build data-quality checks: 1-6 and 8-10 in the Sensorgram, 7 (image motion) in the Image panel's Analysis tab.

Built (uncommitted, LSPRi submodule + umbrella tests):
- `analysis/response_shape.py` (calibration + readout), `scan_trace.response_traces` / `trace_key(..., groups)`, Spectra panel Fit menu entry with four cube fields, Sensorgram axis label and status, step
  table and Spectra panel recompute on regroup. Method reference: `spectra_analysis_methods.md` section 8a. On this scan with B = cubes 2-31, S = 201-225 and the 16 column groups: signal 8.60 nm,
  SNR per ROI 125.4 (scratch prototype: 8.61 nm, 126.0; the difference is the "complete spectra only" rule).
- New defaults in `settings.MetricSettings`; old sessions decode with `settings.LEGACY_METRIC_DEFAULTS` (section 8b of the method reference). Tests written for the old defaults pin them explicitly.
- `analysis/step_diagnostics.py` (checks 1-6, 8-10) and `analysis/image_motion.py` (check 7), pure. On this session they report: steps 1, 5, 9 noisier (1.3-1.7x), with "pump1 rate 0.04 and pump2
  rate 0.02 ml/min in every noisy step and 0 of 5 quiet steps"; step 5's noise in the sample ROIs; step 1 with 2.5x shared light flicker; step 7 not settled (3-4 noise SDs of drift); the polynomial
  centroid reads 58 % of the template shift; ROIs 13, 24, 25, 37, 38, 50 change pixels inside the baseline; ROIs 129-133 and 113 read a ~22 % smaller signal and the signal falls ~6 % from top to bottom
  of the image; the change is 92 % a pure shift.

### The screens (built 2026-11-07, as the maintainer approved the description)

- **Sensorgram, Experimental plan tab, group "Checks"** (after "Step stats"): one check-box button (`panels/sensorgram/checks_group.py`) with a status dot: grey = not run or out of date, blue =
  running, green = nothing to warn about, amber = at least one warning, red = the run failed. A click opens the **report window** (`panels/check_report_window.py`, non-modal: findings with a
  dot each, warnings first; a folded table of the numbers per step; Copy; Save report as JSON / CSV / text, written on a thread) and runs the checks unless the last report is still current.
  The module (`selection/data_checks_module.py`) fits all ROIs off the GUI thread through the shared `TraceStore` (a second run fits nothing), keeps the report, and works out each time
  whether it is current (scan, metric, formula, steps, ROI groups, pair): a change makes it *stale* (marked, kept), the change undone makes it current again.
  `checks_presenter.py` connects the three and owns the status-bar task ("Data checks", with Cancel).
- **Evaluation pair**: the checks compare the first complete pair, or the one picked in the window (a picker appears when there are several; remembered, `sensorgram/checks_pair`).
  Picking another runs the checks for it. Without a pair only the step checks are made, and the window says so.
- **Image panel, Analysis tab, group "Motion"** (check 7): a button and a wavelength box (remembered, `analysis/motion_wavelength_nm`, default 600 nm; the nearest wavelength the dataset has is
  read and named in the report). Runs `selection/image_motion_module.py` off the GUI thread (one raw plane per cube, read one at a time, never held: a 100-cube step would otherwise need
  200 MB); the button is a red Stop meanwhile; the result opens in the same report window class (title "Image motion"; jitter and drift per step, flagged ones as warnings with the reason).
- **Deviations from the description**: the pair picker is in the report window, not on the ribbon (the ribbon stays one button wide); the Motion wavelength box defaults to 600 nm, the
  wavelength of the measurement above, instead of the wavelength shown in the Image panel.
- Tests: `tests/unit/test_lspri_analysis_check_report.py` (report, text / CSV / JSON, streaming motion), `tests/integration/test_lspri_data_checks.py` (thread helper, both modules,
  window, button, presenters, wiring; real modules on the small plan-bar dataset). The pure files are in the Qt-free list of `.importlinter`.
