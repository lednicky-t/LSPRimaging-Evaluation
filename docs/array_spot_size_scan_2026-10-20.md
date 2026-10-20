# Array detection: spot-size scan (2026-10-20)

Code: `src/lspr_imaging_app/roi/array_detection.py`. Functions: `_scan_spot_radius`, `SIZE_SCAN_RADII_PX`,
`SIZE_SCAN_GOOD_FRACTION`, and the `LinAlgError` guard in `_fit_lattice`. Tests:
`tests/unit/test_lspri_roi_array_detection.py`, class `SpotSizeScanTest`.

## Symptom

The maintainer ran ROIs > Array > Detect (everything on Auto, no ROIs) on dataset
`04_Bulk_sensitivity...`. It reported "Only 1 candidate spots (need at least 6)".

## Root cause (not a code regression)

The code from before 2026-10-19 failed in the same way.

Background removal was a red herring. The array finder does its own local background subtraction, so for detection
the app's background removal only nudges the result.

The real cause was the automatic spot-size guess (`image_features.estimate_feature_radius`):
- It uses a scale-space LoG with 14 coarse scales, about 28 % apart, and takes the median of the 30 strongest blobs.
- The guess jumped between 14, 20, 27 and 34 px radius. The true sample disk is about 17 px.
- At the reference wavelength 610 nm and longer, it said 27-34 px. At cube 150 it said about 6 px.

The isolated-filled-disk test accepts a search radius within only about +-25 % of the truth:

| Diameter given | 20 | 26 | 34 | 44 | 54 px |
|---|---|---|---|---|---|
| Images where the whole array is found | 0 | 0 | 156 | 156 | 0 of 156 |

So every spot was rejected.

## Fix

When no diameter is given, the finder scans radii from 3 to 60 px (diameter 6-120 px). It uses 27 steps about
12 % apart, which is finer than the +-25 % window, on a half-size copy of the image.

- **Score:** the number of candidates that fit one common lattice. Debris and specks do not form a lattice, so they
  cannot win.
- **Choice:** the geometric middle of the longest run of radii that score at least 90 % of the best.
- A diameter given in Array settings (Semi) skips the scan.

The maintainer asked whether limits should be added to Preferences: minimum size, ROI count, share of the array.
Decision: no.
- The scan range is the size limit.
- At least 6 spots and at least 50 % lattice occupancy stay internal safety rules.
- Sample-specific knowledge (diameter, rows, columns, pitch) belongs in Array settings > Semi.

## Before / after

Dataset `04_Bulk_sensitivity...`: 26 wavelengths x cubes 0 / 150 / 313 x background off / sigma 39 = 156 images.
Pass = a 10 x 16 array with at least 150 of 160 spots found.

| Method | Images that pass |
|---|---|
| Old single guess | 59 / 156 |
| 5 sizes around the old guess (rejected prototype) | 96 / 156 |
| Diameter 34 px given | 156 / 156 |
| **Size scan (now)** | **156 / 156** |

With the size scan:
- Chosen search radius 17.9-19.0 px.
- Tilt 1.45-1.49 deg.
- Measured sample diameter 30.8-34.8 px.
- A Detect (`detect_array`, including spot and ring sizing) takes 2.5 s median and 5.6 s at most, against about
  1.3 s for the old single guess, which often failed.

## Side fix

`_fit_lattice` could raise `LinAlgError` (singular matrix) when a few scattered candidates made the lattice vectors
parallel. That surfaced as "Unexpected error". It now reports "do not form a two-dimensional grid".

## Not covered

- Only one real dataset was scanned. Other magnifications or spot types have only been checked synthetically: spot
  diameters 10 / 24 / 50 px, a dark halo, and debris.
- Spots larger than 120 px or smaller than 6 px in diameter need the diameter set in Array settings > Semi.
