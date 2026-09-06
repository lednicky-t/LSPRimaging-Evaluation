# ROI-scoped resample: cv2 fast path, and exactly what it changes about the data

2026-09-06. One of three fixes found during a "Start analysis is way too slow"
investigation (the others: bulk sweeps were computing all four Reduction
methods per pixel instead of just the active one, and the OME-Zarr dataset's
50px chunk size made zarr 3.2.1's local-store partial reads pay per-chunk
dispatch overhead that dwarfed the actual decompression work - fixed by
upgrading to zarr>=3.3, see `pyproject.toml`'s comment on that pin). This
doc covers the third one in detail because, unlike the other two, it changes
*computed pixel values* - the maintainer asked for this to be documented
transparently rather than folded in silently.

## What changed

`resample_raw_patch_to_processed_box` (`processing/preprocess.py`) is the
per-wavelength geometric resample (rotation/flip, and the sub-pixel shift
from chromatic correction) applied to a scoped ROI-sized patch during
"Start analysis" and live spectrum preview. It used `scipy.ndimage.
affine_transform` unconditionally. It now routes through `cv2.warpAffine`
(via the existing `_cv2_affine` helper, already used for years by
`apply_spatial_preprocessing_export`'s own fast path) whenever the fill
mode is "nearest" (i.e. `rotation_fill_dark` is off - the default), falling
back to the original scipy path for "constant"/`cval` fill.

This is not a new technique in this codebase - `apply_spatial_preprocessing_export`
made the identical trade for identical reasons back when OME-Zarr export
performance was tuned. This change applies that same, already-accepted
optimization to the analysis read path, which had been missed.

## Why: the performance case

Both scipy and cv2 compute bilinear interpolation, but cv2's `warpAffine`
implementation is SIMD-optimized C++ against a fixed sub-pixel lookup table,
while scipy's `affine_transform` is a general-purpose interpolation routine
with no such shortcut. Measured directly on a real 400x600px patch pulled
from an actual dataset (`Bulk_sensitivity_pumpplan`), with a realistic
small chromatic-correction-style affine (~0.3-1px shift, ~0.15° rotation):

| | scipy (`ndimage.affine_transform`) | cv2 (`warpAffine`) |
|---|---|---|
| Time per call | 18.12ms | 0.26ms |
| **Speedup** | | **70x** |

Resample is one of five per-wavelength stages in the analysis hot path
(read, resample, ROI-mask lookup, reference-pixel-coordinate lookup,
reduction). At the point this was measured, resample was ~15-20% of total
per-cube time after the zarr upgrade above already cut the read stage
down - so this fix alone is a modest ~15% win on top of that. Its bigger
payoff comes *after* the dataset is also re-exported with larger OME-Zarr
chunks (a separate, not-yet-applied fix, since it requires re-exporting the
maintainer's real data and wasn't done as part of this investigation): once
the read stage shrinks further, resample becomes proportionally the largest
remaining cost, and this fix stops being optional if the goal is sub-1s/cube
bulk analysis.

## Why it's safe: the correctness case

cv2's `INTER_LINEAR` rounds the sub-pixel interpolation offset to one of 32
discrete steps (a 5-bit lookup table) rather than computing it continuously
the way scipy does. This produces a small, *deterministic* (not random)
difference from scipy's result - confirmed directly: resampling a pure
linear-gradient synthetic image (no curvature, where true bilinear
interpolation is mathematically exact regardless of algorithm) through both
paths gave a max difference of 0.0001 and mean of 0.000009 - i.e. this is
purely a quantization artifact of interpolating real image texture/noise,
not a wrong transform or a bug.

Measured on the same real 400x600px sensor patch as above (14,233-50,603
count range, realistic chromatic-correction-sized shift):

| Quantity | Value |
|---|---|
| Max discrepancy (single worst pixel) | 0.43 counts |
| Mean discrepancy (whole patch) | 0.007 counts |
| Poisson shot noise at this patch's mean intensity (~42,700 counts) | ~207 counts |
| Discrepancy vs. shot noise | **29,469x smaller (mean), 477x smaller (worst pixel)** |

Shot noise is the physical noise floor of the measurement itself - it exists
no matter how the software is written, from photon-counting statistics
alone. A difference 2-4 orders of magnitude below that floor cannot move any
real absorbance value, peak position, or sensorgram trend by an amount that
is distinguishable from the measurement's own inherent noise.

## Where this shows up in tests

`tests/unit/test_lspri_preprocess.py::TestRoiScopedFastPathMatchesFullImage`
specifically exists to lock the ROI-scoped path and the full-image reference
path together (so a future bug can't silently make the fast path wrong
while the reference still looks fine). Its "whole image passed as the
patch" sub-check previously asserted exact (`atol=1e-6`) agreement, which
this change breaks by design - the reference path
(`apply_spatial_preprocessing`, used for anything *not* going through this
scoped fast path) still always uses `scipy.ndimage.rotate`, so the two are
now genuinely different interpolation implementations. The tolerance was
loosened to `atol=0.05` (`FULL_PATH_ATOL` on that test class), set from the
actual worst-case discrepancies measured on that test's own gradient image
(0.0078 and 0.0156 across two rotation test cases) with roughly 3x headroom
- loose enough not to fail on this expected, understood quantization noise,
tight enough that a real geometry bug (which would be orders of magnitude
larger, not a sub-percent rounding difference) still fails it. See that
test class's own docstring for the same explanation in-repo.

## What this does *not* affect

- The main image viewer, wavelength-switch display, and OME-Zarr export
  (`apply_spatial_preprocessing`, `apply_spatial_preprocessing_export`) are
  untouched - only the ROI-scoped per-wavelength analysis resample changed.
- The `rotation_fill_dark=True` ("constant" fill) path is untouched and
  still uses scipy exactly as before - cv2's boundary handling disagrees
  with scipy's near a rotated canvas's synthetic-fill edge (the same reason
  `apply_spatial_preprocessing_export` only uses cv2 for "nearest" mode),
  which matters for a feature whose entire purpose is precisely marking
  "this pixel is not real data."
- Nothing about which pixels are read, which ROI masks are built, or how
  sample/reference values are reduced changed - only the interpolation
  arithmetic for the geometric resample step.
