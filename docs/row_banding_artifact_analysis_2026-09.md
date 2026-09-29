# Row-banding artifact (rolling shutter × PWM illumination): analysis and proposed fix

Status: **analysis + proposed design only, nothing implemented.** Written 2026-09-29
after the maintainer asked for an investigation into faint periodic horizontal lines
visible in some wavelength images, using real data from
`Data_PyTest/04_Bulk_sensitivity_pumpplan_0-70percent_to_LbL_with_water_v1_LED_V2`.

## 1. What the artifact is

The camera has a rolling shutter (rows are exposed/read out at slightly different
times), and the LED illumination is PWM-driven (switched on/off rapidly rather than
held at a constant analog current). If a row's exposure window is short relative to
the LED's PWM period, different rows can catch different fractions of an LED on/off
cycle, so the image ends up with a faint horizontal brightness ripple as a function of
row — the same family of artifact as banding in phone-camera photos of PWM-backlit
screens.

## 2. Empirical characterization (real data, not just theory)

Scripts used are throwaway (session scratchpad, not committed), but the method is
reproducible: load raw TIFFs (`imLCTFatWL{wl}Frame{n}.tiff`, 900×1300 px, uint16),
compute `row_mean(y) = mean over all 1300 columns`, vertically high-pass it
(subtract a `sigma=4`-row Gaussian blur along y) to suppress the sample's own slow
vertical structure, then FFT the residual.

Findings, WL580/590/600/610/620, frame 0 of `.../images/`:

- **A real, distinct periodic component exists** at ≈0.115–0.117 cycles/row
  (period ≈ 8.6 rows), well separated in frequency from the dot-array's own
  vertical pitch (≈0.0122 cycles/row, period ≈82 rows — visible as the dominant,
  much larger low-frequency peak, and not the artifact). The banding fundamental
  has clean 2nd/3rd/4th harmonics (≈0.235, ≈0.35, ≈0.47 cycles/row) with decaying
  magnitude — the signature of a non-sinusoidal (duty-cycle-like) repeating
  waveform, consistent with a PWM square wave sampled through a rolling shutter,
  not with sensor fixed-pattern noise (which would be a single stable comb, not
  concentrated at one fundamental + harmonics with no lower-frequency components).
- **Amplitude**: ≈0.26–0.34% RMS of the frame mean, ≈1.3–2.3% peak-to-peak,
  present at similar strength across all 5 tested wavelengths (580–620 nm), with a
  mild downward trend toward 620 nm (2.29% p2p at 580 → 1.28% p2p at 620) —
  plausibly because per-wavelength auto-exposure gives longer exposures at dimmer
  wavelengths, letting a row's exposure window average over more PWM cycles.
- **Phase is not fixed — it drifts frame to frame.** Cross-correlating the
  isolated ~8.6-row waveform between WL600 frame 0 and frames 50/150/300 of the
  *same* wavelength gives correlations of −0.46, −0.61, −0.62 (i.e. partially
  inverted, not aligned). This rules out simple sensor fixed-pattern noise (which
  would stay ~1.0-correlated across frames) and confirms the LED PWM clock and the
  camera's frame trigger are not phase-locked — consistent with the PWM-vs-rolling-
  shutter theory, and it means **this artifact cannot be removed by one fixed
  calibration frame; it must be estimated fresh per acquired image.**
- Dark frame (`WL0`, LED off, mean ≈9 counts) is too close to the read-noise floor
  to confirm or rule out the same frequency being present at low level —
  inconclusive, not a contradiction.

## 3. Does it actually move the computed absorbance? (the maintainer's real question)

Used the app's own real ROI code (not a re-implementation) to test this
rigorously: `roi.model.AreaRoi`, `roi.rasterize.rasterize_sample` /
`rasterize_reference` (`apps/LSPRi/eva/src/lspr_imaging_app/roi/rasterize.py:539,579`),
loaded against the dataset's real `analysis/roi_table.json` (160 real ROIs, disk
radius 18.6 px), and the app's own default absorbance formula
(`log10(reference_mean/sample_mean)`, `analysis/query.py:102`).

For each of 10 real ROIs spread across the image, 5 wavelengths (580–620 nm) and 16
frames spread across the whole 314-frame pump-plan run: computed raw disk/ring means
and means after subtracting a per-row correction (§4), then compared the resulting
absorbance.

| ROI sample radius (px) | ring (px) | \|ΔA\| median | \|ΔA\| 95th pct | \|ΔA\| max |
|---|---|---|---|---|
| 18.6 (this dataset's real array) | 21.6–28.6 | 0.000009 | 0.000026 | 0.000037 |
| 10 (app's own default) | 14–18 | 0.000058 | 0.000117 | 0.000181 |
| 6 (stress case) | 8–11 | 0.000602 | 0.001053 | 0.001258 |

For comparison, the *real*, already-computed absorbance for this dataset
(`analysis/measurement_backup.h5`, `processed/absorbance_spectra/*`) has a
frame-to-frame residual noise floor (light 5-frame detrend) of ≈0.0016–0.0019
absorbance units.

**Conclusion: for this dataset's actual ROI geometry (18.6 px), the banding's
effect on absorbance is ~100× below the real measurement noise floor — not a
practical problem today.** The reason: a disk/ring that spans several full ~8.6-row
band periods already self-averages the artifact away almost completely. The effect
only becomes potentially relevant (approaching, not exceeding, the noise floor) for
much smaller ROIs than this dataset uses — e.g. close to the app's own 6 px
stress-test case. This is a genuine, quantified negative result, not a "fix
everything" case — reported per `CLAUDE.md`'s rule to flag numeric impact even when
negligible.

## 4. Proposed correction (draft, not implemented)

A cheap, self-estimating per-row correction — deliberately not a repeat of the
existing `flatten_background` 2D-Gaussian approach, since the artifact is 1D
(row-only) and reuses no ROI/mask exclusion:

**Estimate** (once per raw frame):
1. `row_profile(y) = mean over all columns` — one pass, O(H×W).
2. Vertically high-pass (small-sigma 1D Gaussian along y) to remove the sample's
   own slow structure.
3. 1D FFT of the residual (length = image height only, i.e. ~900 samples — orders
   of magnitude cheaper than the existing background flatten's full 2D blur over
   H×W ≈ 1.17M pixels).
4. Auto-detect the dominant peak within a configurable period range (e.g. 3–40
   rows) with a minimum SNR/prominence gate — no gate pass ⇒ no correction applied,
   so a dataset without this artifact pays effectively zero cost and is never
   miscorrected.
5. Reconstruct `band(y)` by inverse-FFT of just the fundamental + first few
   harmonics. This is naturally zero-mean, so no re-centering/baseline step is
   needed (unlike `flatten_background`'s baseline re-add).

**Apply**: `corrected(y, x) = raw(y, x) - band(y)` — one broadcast subtract, no 2D
convolution, no ROI/mask exclusion mask needed at all (verified: the dot-array's own
vertical pitch, ≈82 rows, and the banding period, ≈8.6 rows, are ~10× apart in
frequency, so a real sample structure and this artifact don't get confused by the
detector as tested). Verified on real data: 71× reduction in FFT magnitude at the
band's fundamental frequency after correction, with the dot edges visually
unchanged (see analysis script `fig7`).

**Where it must sit in the pipeline — important, not the same rule as the other
Image Tools steps**: crop/mask/CC/background are treated (per the rewrite's own
architecture vision) as composable coordinate-space "math tools" that can apply
after a rotate/warp. This correction *cannot* — it depends on the sensor's physical
row order, so it must run on the raw, pre-rotation pixel grid, as the very first
photometric step after loading a frame, before any rotate/warp resampling mixes
rows together. (Not an issue for this specific dataset, which has rotation
disabled, but a real correctness constraint for the general case.)

**Suggested module shape** (mirrors `image_tools/background/`'s existing
estimate/apply split — `estimate.py` / `apply.py` / `model.py` / `module.py`,
`BackgroundModule`'s "one combined Apply-button command, not per-field live
setters" pattern):

```
image_tools/row_banding/
    model.py     # RowBandingSettings + RowBandingComputationalChange
    estimate.py  # estimate_row_banding_profile() -> band(y), pure numpy/scipy
    apply.py     # apply_row_banding_correction(image, band) -> corrected
    module.py    # RowBandingModule(QObject), owns settings, one set_* command
```

No persisted "model" object, same reasoning as `background/apply.py`'s own
docstring: cheap enough (O(H log H)) to recompute live, every frame, inline in
`apply_preprocessing`, rather than caching.

## 5. Recommendation

Given §3's finding, this is **not an urgent correctness fix for current data** —
it's a data-integrity/robustness improvement (visible artifact removed, safer for
future smaller-ROI assay designs, cleaner input to anything that looks at
pixel-level structure — chromatic-correction landmark tracking, mask thresholds,
QC overlays) rather than a fix for a currently-wrong number. Suggested to implement
opportunistically, not urgently, and to re-run §3's quantification once real
implemented against a dataset that uses smaller ROIs, to confirm the benefit crosses
the noise floor there.

## Reproducing this analysis

No script was committed (throwaway session exploration). To redo it: load two or
three raw TIFFs from any `WL580`–`WL620` set in this dataset, compute
`row_mean = image.mean(axis=1)`, high-pass with
`scipy.ndimage.gaussian_filter1d(row_mean, sigma=4)` subtracted off, and FFT the
residual (`np.fft.rfft`) — the ~0.117 cycles/row peak and its harmonics should
reproduce directly.
