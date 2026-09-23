# Does a background estimated once stay valid? — measurement, 2026-09-23

**Short answer: no.** The illumination reshapes measurably over a run, so
`BackgroundModule` should keep re-estimating the background per frame. The
"estimate once, reuse forward" (persistent/individual) timeline that
`docs/analysis_provenance_store_design_2026-09.md` asks for should **not**
be built, and that doc's background row needs correcting.

## Why this was measured rather than reasoned about

The provenance design doc specifies background provenance as a 16-bit PNG
versioned "per (cube, wavelength, persi/indiv)" — the same
persistent/individual scoping the ignore mask has. But `flatten_background()`
re-estimates from each frame's own pixels every time; there is no
carry-a-background-forward concept anywhere in the code for that tag to
describe. Implementing the tag therefore meant first deciding a scientific
question: *is the background stable enough across a run to estimate once
and reuse?*

The maintainer asked for this to be settled by measurement on real data
rather than argued from first principles.

## Setup

- Dataset: `04_Bulk_sensitivity_pumpplan_0-70percent_to_LbL_with_water_v1_LED_V2`
  (TIFF-stack candidate, `images/`), 40 spectral cubes.
- Wavelength: 600 nm, fixed.
- `estimate_background_profile(image, sigma_px=48.0, binning=1)` —
  `BackgroundSettings`' own default sigma.
- Each cube's profile compared against **cube 0's** profile.
- Scripts: `bg_drift_study.py`, `bg_shape_study.py` (scratch, not committed —
  reproducible from this document).

## Result 1: the background drifts, monotonically

Mean background level at cube 0 is ~45,800 ADU (image range 560–61,104).

| vs. cube 0 | RMS drift (ADU) | % of background level | × shot noise | % of raw frame-to-frame change |
|---|---|---|---|---|
| cube 1 | 206 | 0.45% | 0.99 | 26% |
| cube 5 | 546 | 1.19% | 2.62 | 53% |
| cube 10 | 701 | 1.53% | 3.36 | 58% |
| cube 20 | 870 | 1.90% | 4.16 | 61% |
| cube 39 | 980 | 2.14% | 4.69 | 57% |

"× shot noise" is against `mean(sqrt(signal))`, the single-frame photon
noise floor — the level below which a difference is not measurable on one
frame. The drift crosses that floor by cube 2 and reaches ~4.7× by the end.

It is also **monotonic**, which is the important part: this is a systematic
trend, not frame-to-frame scatter that would average out.

## Result 2: most of it is a level shift, but the *shape* changes too

A drift number alone does not settle the question, because a spatially
**uniform** change (lamp dimming, or a flat absorbance change) largely
cancels in a sample/reference ratio, while a change in the illumination
**shape** does not — sample and reference ROIs sit at different positions
in the field, so a reshaping background biases their ratio directly.

Splitting each difference into its mean offset and the residual after that
offset is removed:

| vs. cube 0 | total drift | uniform part | **shape part** | shape as % of bg level | shape ÷ shot noise |
|---|---|---|---|---|---|
| cube 5 | 546 | 473 | **272** | 0.60% | 1.31 |
| cube 10 | 701 | 620 | **328** | 0.72% | 1.57 |
| cube 20 | 870 | 747 | **445** | 0.97% | 2.13 |
| cube 30 | 948 | 785 | **531** | 1.16% | 2.54 |
| cube 39 | 980 | 776 | **599** | 1.31% | 2.87 |

About 79% of the drift is a uniform level change. The remaining ~600 ADU at
cube 39 is genuine reshaping: **1.3% of the background level and 2.9× the
shot-noise floor**, and it too grows monotonically — already 1.3× shot
noise by cube 5.

## Conclusion

Freezing the background at cube 0 and reusing it would leave a
spatially-structured residual that (a) does not cancel in the
sample/reference ratio, (b) exceeds the single-frame noise floor from about
cube 5 onward, and (c) grows steadily for the rest of the run. That is a
systematic error injected into every later measurement, not a rounding
concern.

**Therefore:**

1. **Keep per-frame estimation.** No persistent/individual timeline for
   `BackgroundModule`.
2. **Correct the design doc's background row**: drop the `persi/indiv` tag;
   background gets the same treatment as the chromatic model — versioned
   per `(cube, wavelength)`, no scope tag, because like a chromatic model it
   is frame-specific by nature.
3. Storing the *computed profile* (rather than the sigma/binning settings
   that produced it) is still worth doing and is unaffected by this: it
   records what was actually subtracted and survives a later change to the
   estimation algorithm.

## Caveats, stated plainly

- **One dataset, one wavelength.** The trend is strong and monotonic, but
  it has not been checked across other runs, other wavelengths, or other
  illumination hardware. Worth re-running before treating the numbers as
  general.
- **The "shape" component is not proven to be illumination alone.** A
  spatially non-uniform change in the sample itself would also appear in a
  48 px-sigma estimate. This does not change the conclusion — either way,
  a background frozen at cube 0 is subtracting a stale pattern — but it
  does mean these numbers are an upper bound on illumination drift
  specifically, not a measurement of it.
- The comparison is of background *estimates*, not of final measured ROI
  values. Translating the residual into an absorbance error would need ROIs
  placed and the full pipeline run; that was not done here because the
  conclusion does not depend on it.
