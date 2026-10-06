# Background estimation: speed vs quality study — 2026-10-06

Scripts: `tools/bg_study/` (read-only on the data; `common.py` holds the data path and crop).
Data: `04_Bulk_sensitivity_pumpplan_0-70percent_to_LbL_with_water_v1_LED_V2`, 27 wavelengths x 314 cubes,
cropped to (x16, y18, 1269x742). 136 full disks found (rings fully usable); 17x10 lattice, 28.5 % of pixels excluded.
Disk shift vs wavelength / time < 0.5 px, so one fixed mask is used. Dark frame (wl 0) is 0, so I = B * T holds.

## Metrics (all robust: 1.4826*MAD / median across the 136 ROIs)
- **ring CV** — spread of the reference-ring mean across ROIs. Flat illumination -> small.
  Reported **hold-out**: half the disks at a time are removed (disk + ring, radius 1.75 r) from the estimate and the
  surface must predict them. (Without hold-out a small sigma looks better only because it chases ring pixels.)
- **S/R CV** — spread of sample/reference across ROIs. **Temporal** — per ROI, std over 53 cubes of its value relative
  to the across-ROI median (common real drift removed); wl 500/600/700.

## Results
| method | ms/call | hold-out ring CV % | S/R CV % sub / div | temporal ring % | temporal S/R % sub / div |
|---|---|---|---|---|---|
| none | 4 | 2.51 | 1.37 / 1.37 | 0.121 | 0.185 / 0.185 |
| app s48 b2 (current) | ~150 | 0.52 | 2.35 / 1.36 | 0.071 | 0.238 / 0.190 |
| app s48 b8 | ~42 | 0.50 | 2.33 / 1.36 | 0.069 | 0.235 / 0.189 |
| cv s48 f8 (cv2 downsample+blur) | ~14 | 0.54 | 2.35 / 1.37 | 0.069 | 0.237 / 0.188 |
| cv s48 f4 | ~16 | 0.53 | 2.35 / 1.36 | – | – |
| app s24 b2 | ~95 | 0.40 | 2.34 / 1.35 | 0.056 | 0.238 / 0.192 |
| app s12 b2 | – | – | – | 0.044 | 0.241 / 0.198 |
| block32 median, s1.5 | ~58 | 0.55 | 2.34 / 1.37 | 0.069 | 0.239 / 0.188 |
| poly4 / poly6 | 450 / 1000 | 0.71 / 0.61 | 2.30 / 1.35 | 0.093 | 0.223 / 0.188 |
| poly3 | 250 | 1.84 | – | – | – |
| app s96 b2 | ~260 | 0.95 | – | – | – |

## Findings
1. **Speed:** same maths with cv2 (area-downsample x8, cv2 Gaussian, linear upsample) is ~10x faster than the current
   path and indistinguishable on every metric. Even scipy with bin 8 is 3.5x faster at no measurable loss.
2. **Sigma:** the illumination has structure finer than 48 px; sigma 24 predicts held-out rings clearly better
   (0.40 vs 0.52 %). Low-order polynomials (<= poly4) are worse and slower.
3. **Subtraction vs division (science, needs a decision):** the current flatten is `I - B + baseline`. Illumination is
   multiplicative, so for dark/absorbing objects this injects the background variation into the sample value:
   S/R spread across ROIs grows 1.37 -> 2.35 % and temporal S/R scatter 0.185 -> 0.238 %. Division (`I / B * baseline`)
   keeps 1.36 % / 0.19 %. With division, background removal barely changes S/R at all (the local ring already cancels
   the illumination); it mainly reduces ring-level shape drift (0.121 -> 0.07 %).
4. Not tested: other reduction methods (plane fit etc.), other datasets, absorbance after dark/reference subtraction.

## Displaced-reference test (maintainer's case b: reference not around the sample)
`tools/bg_study/run_displaced.py`. Truth per ROI = sample / its own centred ring (case a). Test = sample / a clean
10 px-radius background patch in the gap between disks, at ~40 / 120 / 240 px from the sample (patches held out of the
estimate). Error = (S/R_patch)/(S/R_ring) - 1, RMS over 136 ROIs x 7 wl x 3 cubes (%):

| estimator | mode | 40 px | 120 px | 240 px |
|---|---|---|---|---|
| – | none | 2.02 | 3.29 | 7.87 |
| cv sigma 48 | subtract (current) | 3.41 | 3.11 | 2.97 |
| cv sigma 48 | divide | 1.52 | 1.26 | 1.58 |
| cv sigma 24 | divide | 1.36 | 1.05 | 1.35 |
| cv sigma 12 | divide | 1.02 | 0.69 | 0.99 |

Division fixes the displaced-reference error at every distance (240 px: 7.9 -> 1.6 %); subtraction is worse than no
removal at 40 px and only ~2.6x better than none at 240 px. Division carries a small bias (-0.2..-0.6 %).

## Implemented 2026-10-06 (`image_tools/background/estimate.py`, `apply.py`)
- **cv2 estimate** for every binning (area-downsample, cv2 Gaussian, linear upsample); region requests now slice the
  full estimate (the full upsample is cheap). `_background_baseline` and the scipy resize helpers were removed.
- **`apply_background(mode="divide"|"subtract")`**, new setting `flatten_background_mode` (default divide; Background
  tab "Mode"). Sessions saved before the field existed load as `subtract` (what they were computed with).
  Divide gives NaN where the background is not positive. Stored analysis cells go stale on the new setting
  (it is part of the provenance fingerprint) - intended.
- Before/after on real frames (cropped 1269x742, sigma 48, 5 wl x 3 cubes, time includes exclusion-mask build):

| binning | old ms | new ms | estimate difference (valid pixels) |
|---|---|---|---|
| 1 | 899 | 204 | max 0.1 ADU (identical) |
| 2 | 171 | 40 | rms 12 ADU = 0.022 % of level, max ~240 ADU (0.4 %, at the borders: cv2 grid is centre-aligned, kernel truncated at ~3 sigma) |
| 4 | 61 | 24 | rms 36 ADU = 0.064 % |
The study's "10x" was cv2 bin 8 vs scipy bin 2; at equal binning the gain is 3.8-4.4x. Bin 4 + cv2 is ~7x faster than the
old default (bin 2, scipy) with equal quality in the study metrics.

## Follow-up 2026-10-06 (maintainer decision): divide only
Divide is the only mode: the app measures absorbance from transmitted intensity, where illumination is a gain.
Subtraction would only suit an additive offset (the dark frame's job) - removed from `apply_background`, from
`flatten_background` and from the Background tab (no Mode dropdown). `BackgroundSettings.flatten_background_mode`
stays (always "divide") only so the analysis fingerprint distinguishes cells computed by the old subtraction; old
sessions load as divide and their cells recompute once. The tab has an "i" popup explaining the method, strengths and
the time cost (`BACKGROUND_INFO_HTML` in `panels/image/background_tab.py` - update its timing line if the speed changes).

## Binning and sigma (2026-10-06, `tools/bg_study/bins.py`, `sigma_study.py`)
**Binning.** Whole `flatten_background` on a 1269x742 frame (min of 25): bin 1 268 ms, 2 48, 4 30, 8 22, 16/32 23 ms.
The ~22 ms floor is full-resolution work (mask 3, float conversion + area-resize ~8, upsample 4, divide 5).
Quality (displaced-reference error) is unchanged up to bin 16, slightly worse at 32 (sigma/bin = 1.5).
Rule implemented: `bin <= sigma/6` (`max_binning_for_sigma`); the Background tab lowers binning with a fading hint.
Default binning 8 (sigma 48). The expensive median fallback is now computed only where a pixel has no valid neighbour.

**Sigma** (auto binning, 4 wl x 2 cubes, RMS %). "Hole error" = white level predicted at the centre of an artificial
disk-sized hole in clean substrate vs its true value - the error that goes straight into the sample intensity.
| sigma | bin | hole r=24.5 (ROI-sized) | hole r=40 | displaced-ref S/R error 40 / 120 / 240 px |
|---|---|---|---|---|
| 6 | 1 | 0.88 | **5.42** | 1.53 / 1.37 / 1.45 |
| 12 | 2 | 0.87 | 1.23 | 1.06 / 0.71 / 1.02 |
| 24 | 4 | 0.91 | 1.24 | 1.46 / 1.14 / 1.43 |
| 36 | 4 | 0.97 | 1.24 | 1.58 / 1.28 / 1.58 |
| 48 | 8 | 1.03 | 1.28 | 1.63 / 1.36 / 1.67 |
| 72 | 8 | 1.26 | 1.50 | 1.68 / 1.51 / 1.90 |
| 96 | 8 | 1.58 | 1.83 | 1.73 / 1.68 / 2.21 |
| 150 | 8 | 2.48 | 2.82 | 1.80 / 2.09 / 3.08 |
| 250 | 8 | 4.15 | 4.65 | 1.92 / 2.65 / 4.54 |
Two failure modes: sigma too **small** cannot bridge a large hole (sigma 6 vs r=40: 5.4 %); sigma too **large** cannot
follow the illumination (error grows steadily above ~50). Flat optimum 12-36 px for these ~37 px disks (hole r ~25 px).
Floor ~0.9 % = real local structure (dust, fringes) nobody can predict from outside.
