# Spectral outlier detection: debris, splices, and real transitions

2026-09-11. Started from a maintainer observation while reviewing a bulk-sensitivity
dataset: a small artifact appears at cube 160 of a 314-cube run, at ROI 66 (660nm)
and ROI 36 (670nm), with a third ROI (106, 650nm) suspected of showing the same
thing. The underlying question was broader than "what is this one artifact" -
whether spectral outliers in LSPRi eva's per-ROI, per-wavelength, per-cube data
can be recognized automatically, tabulated, and used to improve fitting (mostly
post-processing, since a finished run has both past and future cubes to compare
against). This doc is the result of that investigation: a validated failure-mode
taxonomy, a working (but not yet integrated) detector script, and the two real
bugs the validation process caught. Written up because reconstructing the
reasoning - three iterations of "here's my idea" / "here's how the data actually
behaves" between the maintainer and the assistant, cross-checked against the
dataset's own pump-plan log - would be expensive to redo from scratch.

**Status: exploratory. Not wired into the app.** The script
(`apps/LSPRi/eva/tools/detect_spectral_outliers.py`) is a standalone, read-only
CLI tool, uncommitted as of this writing. Nothing in `gui/`, `processing/`, or
`storage/` was touched.

Dataset used throughout: `04_Bulk_sensitivity_pumpplan_0-70percent_to_LbL_with_
water_v1_LED_V2` (a 160-ROI x 26-wavelength x 314-cube run on the maintainer's
Desktop, not part of the repo). All concrete numbers below are from that file.

## The three (four) failure modes

Data shape: for each ROI, a `(n_cubes, n_wavelengths)` matrix of formula values
(`log10(reference/sample)` for absorbance, etc. - see `processing/analysis.py`'s
`formula_value`/`formula_values_from_reduced_values`), built from the schema-7
backup's `/rois/<roi_id>/spectra/reduced/<method>/{sample,reference}` (see
`imaging_measurement_export_format.md`).

1. **`point_contamination`** - one wavelength, one cube, one ROI is off while
   everything around it (nearby cubes at that wavelength, nearby wavelengths in
   that cube, and the same cube/wavelength in spatially neighboring ROIs) is
   normal. Mechanism confirmed directly: at ROI 36, cube 160, 670nm,
   `reference_mean` dropped ~5% in that single frame (49,800 -> 47,304) while
   `sample_mean` was untouched and every other wavelength/cube/neighbor-ROI was
   clean - textbook signature of something briefly occluding/scattering light
   over that one ROI's reference-annulus footprint during that one exposure.
   Fix: drop or downweight that one wavelength when fitting that cube's
   spectrum; don't touch the cube's other wavelengths or other cubes.

2. **`point_trail_event`** - the same single-wavelength-per-ROI signature as
   above, but several ROIs in the same cube are hit at once, with the
   wavelength shifting systematically along a row or column. Wavelength order
   *is* acquisition-time order within one cube's sweep, so this is one small
   object crossing several ROIs' fields of view while the sweep runs - a bigger
   version of the maintainer's original observation. Cube 160 turned out to be
   a **5-ROI trail**, not 2: row 5, columns 3/6/9/12/15, wavelengths 670/660/
   650/640/630nm - a perfectly linear relationship (correlation of wavelength
   vs. column = -1.00). The originally-suspected third point (ROI 106, 650nm)
   is *not* part of it - checked exhaustively (every wavelength, cubes 154-166,
   and the ROI's entire 314-cube history at 650nm) and found clean; whatever
   was seen live for that ROI didn't reproduce in the finished computation.

3. **`chimeric_splice`** - a single cube's spectrum is a splice of two
   different physical states: the first part of the wavelength sweep matches
   the previous cube, the rest matches the next cube, because the sample
   changed mid-sweep. Wavelengths are captured one at a time (see
   `metaData.txt`'s per-wavelength exposure times, ~70-220ms each, plus a
   100ms filter-set time between them - a full 26-wavelength sweep takes
   several seconds), so a fast real-world event can straddle one nominal "time
   point." Confirmed directly at ROI 36, cube 228: wavelengths 470-640nm match
   cube 227 almost exactly (delta < 0.002), wavelengths 690-720nm match cube
   229 almost exactly (delta ~0.0001), with a transition in between. Only a
   handful of ROIs affected. Fix: that cube's derived metric for the affected
   ROI(s) isn't a valid single reading - exclude it from the sensorgram trend,
   don't try to repair a single "shift" value for it.

4. **`transition_event`** - the same splice signature, but hitting many ROIs
   in the same cube at once (see "Validation against the pump-plan" below for
   why this is real and not noise). Cube 61 alone affected ~80-92 of 160 ROIs.
   Fix: don't silently correct it - flag it prominently. The per-ROI split
   wavelength doubles as a rough front-arrival time (see next section), so
   this can be read as a *measurement* (solution-front speed/direction across
   the array), not only a data-quality problem.

## Validation against the pump-plan log

`measureing_times.csv` (dataset root, not `apps/`) has one row per captured
frame with a `Note pump plan` comment column that changes at each solution-plan
step. 13 distinct notes confirm the expected 8 concentration levels (H2O + 10
through 70% EG in steps of 10), realized as a continuous ramp via a 4-channel
valve handoff scheme (each note like `"20perEG_-_ch1_to_40perEG_-_ch3F_ch4P"`
describes a channel handoff advancing the ramp).

Matching the 6 automatically-detected transition cubes against the nearest
pump-plan note-change boundary (using the midpoint between the last row of the
old note and the first row of the new note as the boundary estimate):

| Cube | Nearest boundary | Offset (camera minus pump-plan) |
|---|---|---|
| 61  | 10%->20/40%EG handoff @ 555.8s  | +2.65s |
| 89  | 20/40%->30/50%EG handoff @ 806.1s | +4.28s |
| 117 | 30/50%->40/60%EG handoff @ 1056.6s | +4.88s |
| 172 | 50/70%->60%/H2O handoff @ 1557.1s | +2.58s |
| 200 | 60%/H2O->70%/H2O handoff @ 1807.5s | +5.23s |
| 228 | 70%/H2O->H2O/H2O handoff @ 2057.9s | +3.55s |

Six independent detections, six real boundaries, correct chronological order,
all offsets the same sign within a ~2.6s band - a small, physically sensible
lag (valve switches, then fluid has to travel from the valve to the imaging
chamber). Cube 228's weaker signal (18 ROIs vs. 61's ~80) matches physically
too: that boundary is a handoff between two channels that are both already
~H2O, so there's little concentration difference left to create a front.
**Negative check**: the tail-end H2O-to-H2O-only handoffs (t=2258s, 2508s,
2759s - no real concentration change) show up in *none* of the detected
cubes, consistent with the detector responding to real refractive-index
fronts rather than to valve-switching noise in general.

**Bonus finding, not originally sought**: for cube 61's ~80-92 affected ROIs,
the per-ROI split wavelength correlates strongly with array column
(r = 0.87-0.91, direction consistent), rising from ~642nm at column 0 to
>=700nm at column 11+ (the >=700 plateau is likely a ceiling artifact of the
wavelength-index search range used, not a genuine physical plateau - not
resolved, see Open items). All 5 transition cubes checked show a clean
column (or row) correlation (|r| = 0.58-0.97), and the sweep **direction
alternates** cube to cube (61 and 117 left-to-right, 89 and 200
right-to-left) - matching the expectation that a 4-channel valve system feeds
from alternating sides depending on which channel pair is active for that
step. In other words, this analysis incidentally measures solution-front
propagation direction/speed across the imaged array, at a time resolution
finer than the cube-to-cube sampling rate - a possible secondary use of this
same detector, independent of data cleaning.

## The detection methods, and two real bugs the validation caught

Three independent local-consistency checks feed `point_contamination`/
`point_trail_event` (`detect_spectral_outliers.py`'s `_raw_point_candidates`):
temporal (residual vs. a local median over ~6 neighboring cubes, same
wavelength), spectral (residual vs. ~4 neighboring wavelengths, same cube),
and spatial (residual vs. the median of up to 4 orthogonal array neighbors,
same cube+wavelength) - a point must clear a robust z-score threshold
(default 8) on **all three simultaneously**, since real signal is usually
smooth in at least one of the three axes.

**Bug 1 - z-scores must be normalized per wavelength, not pooled globally.**
The first version of the spectral/spatial checks computed one robust z-score
scale across the entire `(cube, wavelength)` residual matrix. This drowned
out the real signal: ROI 36/66 at cube 160 scored only ~-4 (invisible against
an 8-sigma threshold) because other wavelengths - especially near the
resonance peak, where a flat local-median residual is naturally larger even
in clean data due to curvature, and because different wavelengths have
genuinely different noise floors (different exposure times per
`metaData.txt`) - inflated the pooled scale. Fixed by normalizing separately
per wavelength column (`_robust_z_per_wavelength`); ROI 36/66 then scored
-50 to -67. **Any future per-point statistical check in this codebase that
spans multiple wavelengths should normalize per wavelength, not globally, for
the same reason.**

**Bug 2 - a real fast transition also trips the per-point checks, en masse.**
Even with Bug 1 fixed, cube 229 (the cube right after transition cube 228
settles) alone produced 180 "point" flags across 48 distinct ROIs - a
genuine, sharp, real change still looks locally sharp when checked one
wavelength at a time. The distinguishing signal is **how many ROIs are hit in
the same cube**: real single-point debris essentially never coincides with
many independent debris events in the same cube by chance. Fixed two ways in
`classify_point_candidates`: (a) once a cube is confirmed as a
`transition_event` by the splice detector, its point-level echoes are
dropped entirely rather than reported a second, noisier way; (b) known
transition cubes are excluded from *neighboring* cubes' own temporal
baselines (`_local_median_residual`'s `exclude_rows`), so a huge real jump
doesn't leave ghost point-flags on the cubes immediately next to it.

The splice/transition detector (`_splice_candidates`,
`find_splices_and_transitions`) is a different mechanism, needed because a
chimeric spectrum is *not* a single-point spike - every individual wavelength
in the transition still looks reasonably close to its own immediate
wavelength-neighbors, so the point-level spectral check barely reacts; only a
whole-spectrum, two-segment fit (does this cube's spectrum match "before" for
wavelengths `[0:k)` and "after" for `[k:n)`, for the best-fitting split point
`k`, better than it matches either state as a single coherent spectrum)
reveals it. `chimeric_splice` vs. `transition_event` is purely a threshold on
how many ROIs share the same cube's splice pattern (default 5).

## Open items / known rough edges

- **Cubes 229/230 still produce some noisy `point_trail_event` labels**
  (correlation r=0.75-0.96, not the ~1.00 seen for genuine debris trails) -
  an artifact of sitting immediately next to the confirmed 228 transition
  without being a clean splice themselves. Treat trail labels on cubes
  adjacent to a known transition with extra skepticism.
- **Cubes 175-198 show an unexpectedly dense run of clean, high-confidence
  point-trail events** (many at r=0.99-1.00), right in the middle of the
  60%EG->H2O ramp window. Not investigated further - worth a look at what's
  physically different about that stretch of the pump plan (more bubble
  shedding from a particular valve state? something else?).
- **Cube 61's split-wavelength-vs-column plateau at the search ceiling**
  (columns 11+ all reporting ~700nm) is likely bounded by
  `SPLICE_MIN_SEG`/the wavelength search range rather than reflecting where
  the front genuinely finished crossing those columns - not resolved.
- **The threshold constants at the top of `detect_spectral_outliers.py`**
  (`POINT_Z_THRESHOLD=8`, `SPLICE_MIN_IMPROVEMENT=4`, `TRANSITION_MIN_ROIS=5`,
  `TRAIL_MIN_ROIS=3`, `TRAIL_MIN_CORR=0.6`) were picked by checking they
  correctly separated the known confirmed cases in this one dataset, not
  tuned/validated against a second dataset. Revisit before trusting them
  elsewhere.
- **A cross-correlation/template-matching alternative to the local-median
  checks was discussed but not implemented.** Fitting each cube's spectrum
  against a locally-derived reference shape (built from a handful of
  temporally-nearby clean cubes) and using the match quality as an outlier
  score was judged likely more robust than flat local medians, and would
  naturally produce the peak-shift metric and the outlier score in one pass -
  flagged as a better foundation if this work is picked up again, not just a
  refinement of the current script.

## If this gets picked up again

The natural integration points, per the maintainer's original two questions
(feasibility of auto-detection; using it in fitting/correction):

- **Per-cube spectral fit**: pass a weight/mask derived from this script's
  output into `fit_polynomial_curve`/`fit_gaussian_curve`
  (`processing/analysis.py`) so one bad wavelength doesn't distort a 26-point
  fit.
- **Sensorgram trend**: exclude (not interpolate) `chimeric_splice`/
  `transition_event` cubes' derived metric from downstream kinetics fits -
  exclusion is more honest than inventing a value for a spliced spectrum.
- Any real integration should keep the same principle used throughout this
  investigation: the detector is a **derived, additive QC layer**, and must
  never modify the stored raw `sample`/`reference`/formula arrays - matches
  this repo's "raw data is sacred" rule elsewhere.

Script: `apps/LSPRi/eva/tools/detect_spectral_outliers.py`. Run with
`python detect_spectral_outliers.py <measurement_backup.h5> --out report.csv`
using the Suite's venv (needs `lspr_imaging_app` importable, so its formula
math can't drift from the app's own).
