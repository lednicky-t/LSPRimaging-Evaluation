# Chromatic landmark lab: findings (2026-10-04)

Offline experiment for automatic chromatic-correction landmarks in the rewrite. Script: `tools/chromatic_landmark_lab.py` (standalone, no app imports; writes an HTML viewer). Data: `04_Bulk_sensitivity_pumpplan_0-70percent_to_LbL_with_water_v1_LED_V2`, frame 0, 26 wavelengths 470-720 nm (wl 0 excluded), reference 600 nm, 1300x900 px, 15x10 array of dark disks (~40 px diameter, ~80 px pitch).

## Physical assumptions (maintainer)
Features are always darker than the background at every wavelength (no contrast flipping). Size, shape and placement vary with the sample design, so none of them is hard-coded.

## Method
1. Contrast map `(background - image) / background` (background = grey closing on a half-resolution copy).
2. Feature size measured from the reference (scale-space blob search; found 41 px, true ~40). `--feature-diameter` overrides.
3. Candidates = blob peaks at that size; kept only if localisable in x and y (shifted-self correlation), no shape rules.
4. One candidate per node of an nx x ny grid over the search rectangle (Hungarian assignment).
5. Track outward from the reference, one wavelength at a time, searching only `--max-step` px (default 5): patch cross-correlation on contrast maps, sub-pixel peak refinement.
6. Per step, a robust similarity fit flags landmarks that disagree (nothing is replaced silently); final fit per wavelength with in-sample and leave-one-out (LOO) error.

## Results
- 15 landmarks are enough; 32 and 60 gave no better fit (LOO 0.11-0.12 px).
- Scale about the reference runs +0.95 % (470 nm) to -0.37 % (720 nm), monotonic; centre shift < 0.3 px.
- Tracking beats centroid re-detection (centroid lost landmarks at long wavelengths, 2x noisier).
- Stress tests (smaller features down to ~7 px, noise 5 %, contrast 30 %): fit error stayed 0.1-0.3 px, scale curve within 0.14 px at the image edge. Not tested: other shapes, real optical blur.
- Timing (full 26 wavelengths): load 1.2 s, feature size 1.0 s, contrast maps 2.1 s, candidate search 0.55 s, tracking 0.14 s, fit 0.05 s; pipeline ~4 s. An earlier 12 s measurement was not reproduced and is unexplained (possibly machine load). Tracking is negligible; cost is per-image preparation, and in the app, reading each wavelength image.

## Wavelength subsampling (track every Nth, interpolate the rest)
Error vs the full run, whole image, interpolated wavelengths only (PCHIP):

| Stride | Images used | rms | worst point |
|---|---|---|---|
| 2 | 14 | 0.032 px | 0.09 px |
| 3 | 10 | 0.035 px | 0.09 px |
| 5 | 7 | 0.039 px | 0.10 px |
| 7 | 5 | 0.055 px | 0.17 px |

Tracking noise (LOO) is ~0.14 px, so strides up to 5 add less error than the measurement noise. PCHIP is marginally better than linear or cubic spline. Caveat: the reference run is itself noisy and landmark sets differ slightly between runs, so the table includes both runs' noise; the aberration here is smooth and monotonic, a sample with irregular wavelength jumps may need a denser stride. Subsampling matters most in the app because it cuts image reads (7 of 26 reads at stride 5).

## Is the correction static or time dependent? (cube index = frame, 314 frames)
Each frame analysed independently (15 landmarks, reference 600 nm in the SAME frame, so a global stage shift between frames is removed by construction). Difference of each frame's transforms to frame 0, evaluated over the whole image (all 26 wavelengths):

| Frame | rms | worst point |
|---|---|---|
| noise floor (frame 0, 8x4 vs 5x3 landmarks) | 0.069 px | 0.24 px |
| 78 | 0.035 px | 0.10 px |
| 157 | 0.035 px | 0.11 px |
| 235 | 0.198 px | 0.67 px |
| 313 | 0.052 px | 0.18 px |

Follow-up on neighbours of 235: frames 200 and 270 are back at 0.04-0.07 px; 220-250 sit at 0.08-0.20 px rms (worst 0.4-0.66 px) with a higher leave-one-out error (0.23-0.34 px vs 0.12-0.14 px elsewhere). `measureing_times.csv` shows frames 200-250 are the 70 % EG to water flush (pump-plan notes). Cause is not established: refractive-index/focus change, flow-induced motion or bubbles are all plausible. Magnitude is 0.2 px rms vs ~6 px total correction at the image edge (about 3 %).
Conclusion: a static correction from one frame is accurate to ~0.05 px for 80+ % of the run and to < 0.7 px worst point during a liquid exchange; a per-frame option is cheap (~4 s per frame) if wanted. The model store is already keyed by `(cube, wavelength)`, so per-cube correction can be added later without changing the data model.
