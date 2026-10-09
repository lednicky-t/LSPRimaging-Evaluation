# ROI tab "Array" section: audit and proposed design (2026-10-08)

Status: proposal, nothing built. Written for the maintainer to decide the open questions at the end.
Scope: circular sample disks plus reference rings only (as requested). Image panel, ROIs tab, rewrite generation.

## 1. What already exists

| Piece | Where | State in the rewrite |
|---|---|---|
| Semi-automatic detection (`detect_rois`: smooth, local maxima, contrast score, optional grid fit with user rows / cols / spacing) | `roi/detection.py` | Ported. `RoiToolbox.detect_rois(rois, array_groups)` stores a result (replaces all ROIs, one undo step). **No UI calls it.** |
| Fully automatic geometry (`estimate_array_geometry`: `blob_log` scale-space, lattice from nearest-neighbour distance + 1-D clustering, occupancy check) and ring formula (`estimate_reference_ring_radii`) | `processing/roi_array_geometry.py` | Old-generation folder. Only the stable app (`gui/analysis_tasks.py:168`) calls it. Has 17 unit tests (`tests/unit/test_lspri_roi_array_geometry.py`). Not wired to the rewrite. |
| Array recipe `RoiArrayGroup` (rows, cols, spacing x / y, anchor, rotation, member ids) | `roi/model.py` | Stored and restored, but no UI creates, edits or uses it (feature inventory, section 6). **The Array section is the UI that finishes this.** |
| Resize / move commands, undo batches | `RoiToolbox.resize_rois`, `translate_rois` | Done. Sizing only needs to call them. |
| Chromatic landmark search (`contrast_map`, `fill_invalid`, `estimate_feature_radius`, `_find_candidates`, Hungarian node assignment, trackability) | `image_tools/chromatic/auto_landmarks.py` | Pure, progress / cancel aware, validated on the real 15 x 10 dataset (lab findings 2026-10-04: size 41 px vs true ~40). |

Two detectors for "dark round spots on a grid" therefore exist, with different front ends.

## 2. Weak points found in the existing ROI detection (read from code, not yet measured)

1. **Integer pitch.** `array_spacing_px: int` (`roi/model.py`, used at `roi/detection.py:89,122,164`). A real pitch of 80.4 px rounded to 80 drifts 0.4 px per column, about 6 px over 15 columns. Grid origin fitting and the "nearest ROI within tolerance" matching then work from slightly wrong nodes. Needs float, and separate x / y pitch (the recipe class already has both).
2. **Square-pitch assumption.** `estimate_array_geometry` rejects the result when row and column spacing differ by more than 20 % (`roi_array_geometry.py:233`). A rectangular pitch is rejected as "not an array".
3. **Axis-aligned only.** Rows and columns come from independent 1-D clustering of x and y (`_cluster_1d`), and `_filter_by_array_support` compares dx / dy to the spacing. A tilt of even 1-2 degrees smears the clusters across a 15-column array. Tilt is not measured or reported.
4. **Normalisation.** The auto path min-max normalises the whole image and inverts it (`roi_array_geometry.py:117-125`). Uneven illumination or a bright edge changes the result. Chromatic's `contrast_map` (relative to a grey-closing background) is the more robust front end.
5. **Size is not measured in the semi-auto path.** `detect_rois` takes the diameter from the settings. The auto path takes `blob_log` radius: 12 scales up to 80 px, and sigma x sqrt(2) is the blob scale, not the physical edge. Good as a starting size, not as the final ROI size.
6. **Centre refinement is a Python double loop** (`_refine_roi_center`, `detection.py:316-337`), each pixel calling a function that builds a `mgrid`. Probably slow for a few hundred ROIs. Not measured; it can be replaced by one filter pass (section 4.3).
7. **Progress shape.** Integer percent callback, no cancel. The rewrite rule is `progress(fraction, text)` plus `cancelled()` (see `progress.py`, `StageProgress`).
8. **Missing spots** are filled at the grid node with `inferred=True`. That is good behaviour and should stay.

## 3. Proposed model: one pipeline, three levels of prior knowledge

The three modes are not three algorithms. Every input is an optional *prior*; whatever the user leaves empty is estimated.

```
ArrayPrior(diameter=None, rows=None, cols=None, pitch_x=None, pitch_y=None,
           anchor=None, rotation=None)
```

| Mode | User gives | Pipeline |
|---|---|---|
| **Auto** | nothing | estimate everything, refine, size |
| **Semi-auto** | any subset of rows, cols, disk diameter, pitch | the given values constrain the search (a known diameter limits the scale range to about +-30 %; known rows x cols lets a dim or partly missing array be accepted; known pitch seeds the lattice) |
| **Manual** | everything incl. anchor and rotation | no image analysis: stamp the lattice. Optional "Snap to image" refines each node locally, as `_fit_grid_array` does today |

Stages (all pure functions, no Qt; `roi/array_detection.py` and `roi/array_layout.py`):

1. **Contrast map** on the reference-frame processed image (dark features positive; works for bright mode by sign flip). NaN from rotation excluded via a valid mask. External ignore mask honoured as today.
2. **Candidates and size prior**: LoG scale-space on the contrast map (as Chromatic's `estimate_feature_radius` and `_find_candidates`), subpixel peaks.
3. **Lattice fit**: pick two lattice vectors from the candidate nearest-neighbour vectors, assign each candidate to an integer (i, j), then a least-squares fit of origin + two vectors on the assigned points, iterate once with outlier rejection. This gives pitch x / y, tilt, rows / cols and a per-node residual in one step, replaces the 1-D clustering, and handles a small rotation and a rectangular pitch. Reject if too few nodes are occupied (as now) and say why in the report.
4. **Centre refinement**: one matched-filter score map (disk minus surrounding annulus, same idea as `_roi_circular_contrast_score`) computed once for the image with `cv2.filter2D`, then a local argmax + parabolic subpixel inside +-0.4 pitch of each node. Replaces the Python loop. Nodes with no signal keep the lattice position, `inferred=True`.
5. **Sizing** (the new part): for each found spot, azimuthally averaged radial profile of the contrast map around its refined centre (bilinear polar sampling, subpixel). Edge radius = radius where contrast falls to 50 % of the plateau (half-maximum; for a blurred step edge this is the true edge). Array disk diameter = median over spots, with the MAD kept as a quality number. Then:
   - sample diameter = edge diameter x `shrink` (open question 1),
   - ring = existing convention from `estimate_reference_ring_radii` (inner 1.4 x R, outer chosen for equal area), **plus a check that the outer ring stays clear of the neighbouring disks**; if it would not, shrink the ring and warn (the check does not exist today).
   - one uniform size for the whole array by default (per-spot sizes would make spots not comparable).
6. **Result object** (frozen dataclass): lattice (pitch x / y, tilt, anchor, rows, cols), per-node position / found / score, measured disk diameter + spread, proposed sample / ring diameters, and a short human-readable report (counts, tilt, worst residual, reason when rejected).

The result is applied, not computed in place: a new toolbox command (or `detect_rois` with an `RoiArrayGroup`) writes ROIs + one `RoiArrayGroup` recipe as one undo step. "Fit sizes" on an existing selection calls `resize_rois` only (no re-detection) and is also one undo step.

## 4. How it fits the rewrite rules

- **Module boundaries**: the pure code lives in `roi/`. `roi/` must not import from `image_tools/chromatic/` (only `affine_for` / `warp_mask` are public). So either copy the 3 small functions (`contrast_map`, `fill_invalid`, `estimate_feature_radius`) into a neutral pure module, or extract them to a shared module that both use. Extraction touches Chromatic, which needs a check-in (open question 6).
- **Threading**: detection reads a plane, which is dataset-adjacent, so it runs on a plain `threading.Thread`, never `QThreadPool`. It must take `progress` and `cancelled`, stage weights from measured times, report to the `TaskIndicator` (reference: `ChromaticAutoDetect`). The pure function receives an in-RAM array; only the owning wrapper reads the plane.
- **Spaces**: ROI coordinates are processed image space, stored in the reference frame, so detection runs on the **reference wavelength** processed image (identity chromatic affine there). Say so in the docstrings.
- **Persistence**: Array settings (mode, rows, cols, diameter, pitch x / y, rotation, shrink) are remembered controls: `ui_state.bind` per widget. `array_spacing_px` int to float needs a migration note (reading an old int is fine; writing float is the format change, so check in before doing it).
- **Numerics**: replacing the centre refinement and the grid fit changes ROI positions. Needs a before / after on the real dataset (15 x 10, ~40 px, ~80 px pitch, `04_Bulk_sensitivity...`) with the shift quantified.
- **Tests**: unit tests, no Qt, small synthetic images: clean grid, non-integer pitch (80.4), rectangular pitch, 2 degree tilt, missing spots, uneven background, bright mode, debris, random scatter rejected, partial array with known rows x cols, NaN corners. The 17 existing tests are the starting set.

## 5. GUI (to approve before coding, per gui.md)

```
Screen purpose: create or correct a regular array of disk + ring ROIs on the Image panel.
Main user actions: choose mode (Auto / Semi / Manual); enter known values; Detect or Place;
  "Fit sizes" on the selection; cancel a running detection; undo.
Layout: ROIs ribbon tab, new captioned group "Array" beside the existing groups (Select/Add,
  Sample, Reference, Labels). The ribbon row is fixed height and the inputs are too many for it,
  so: a mode selector + Detect/Place + Fit sizes buttons in the row, and the numeric inputs
  (rows, cols, disk D, pitch x, pitch y, rotation, anchor) in a small popup opened from an
  "Array settings" button. Result report (what was found, tilt, spread, warnings) in the
  status line / TaskIndicator result text.
Important states: no dataset; analysis running (refuse, as Undo does); detection running
  (progress + Cancel); result found / rejected; existing ROIs present (confirm replace).
Validation: rows, cols >= 1; D > 0; pitch > D; ring inside pitch; manual anchor inside image.
Error messages: the rejection reason from the report, never a silent empty result.
```

Preview before applying (ghost circles over the canvas) is the safer behaviour because Detect replaces all ROIs; undo exists, so a preview is a second step, not a first-version blocker.

## 6. Suggested order of work

1. **Offline lab script** `tools/array_lab.py` (as was done for Chromatic): run old `estimate_array_geometry` + `detect_rois` and the new pipeline on the real dataset, compare centres, diameters, time. Decides open questions 1 and 3 with data.
2. Pure `roi/array_detection.py` + `roi/array_layout.py` + unit tests.
3. Toolbox command for placing an array + recipe; `array_spacing_px` to float migration.
4. Worker wrapper (progress / cancel / TaskIndicator) and the ribbon group + popup; remembered state.
5. Optional: preview overlay; edit the array recipe afterwards (move / re-pitch the whole array).

## 7. Open questions (need the maintainer)

1. **Sample disk size**: should the ROI disk be the measured half-maximum diameter, or smaller (e.g. 80 %) so edge pixels are excluded? Which default?
2. **Ring**: keep the current convention (inner 1.4 x R, equal area), or something else?
3. **Detection image**: reference wavelength after rotate / crop and after background removal, or without background removal?
4. **Replace vs add**: Detect replaces every ROI and group today. Should the Array section keep non-array ROIs and replace only its own array?
5. **Tilt**: handle a small rotation inside the lattice fit (recommended, cheap), or only report the tilt and suggest the Rotate tool?
6. **Shared code**: OK to extract the Chromatic feature helpers to a shared pure module (touches Chromatic), or copy now and de-duplicate later?

---

# 8. Maintainer decisions and revised design (2026-10-08, same day)

## 8.1 Decisions

| # | Decision |
|---|---|
| Size | Not a fixed fraction. Estimate the disk edge from the blurred edge by keeping only pixels of "similar intensity as most of the ROI". Separate module, several estimation models behind a switch, binary mask first (no grayscale weighting yet: scientific validity of weights undecided). The best model is picked from a lab comparison and the others are then removed. |
| Image | Always the **reference wavelength**, in the **current cube** (not necessarily the reference cube). If the user is on another wavelength when starting, prompt to jump to the reference wavelength. Background removal follows whether it is applied. |
| Scope toggle | Persistent / Individual toggle like the Mask one, default Persistent, also governing dragging and size changes (see 8.4: this is a data-model question). |
| Replace | Replace everything within the **selection**. |
| Tilt | Handled inside the lattice fit. If the tilt is above 2 degrees, ask the user at the end whether to rotate the image (yes / no). |
| Shared code | Extract the Chromatic feature helpers (contrast map, NaN fill, feature size) to a shared pure module; Chromatic then imports from it. |
| Pitch | Float, resolution 0.1 px. Every length control is entered in px or um through a small text toggle behind the field. |
| Arrays | Always regular (rectangular lattice, pitch x and y independent). My "rectangular pitch rejected" point meant only that the old code insisted on pitch x = pitch y; the new fit takes them independently. |
| Background | Uneven illumination is left to Background removal. The detector keeps its own local contrast, but no extra normalisation work. |
| Size options | Option A: one value for the whole array (the **minimum** measured). Option B: each ROI individually from its own measurement. |
| Refine | New feature "Refine array": for a selected array of ROIs, take its parameters (lattice, ROI sizes) and re-fit positions and sizes with the same image analysis. Reference wavelength rule as above. |
| Ring and sample overlap | See 8.5. Already implemented in the engine; needs to be told to the user. |

## 8.2 Edge-size estimation: proposed models (switchable, lab-tested)

Module `roi/edge_size.py` (pure). Input: contrast / intensity image, refined centre, rough radius from the blob search. Per spot it first measures **plateau** P (median of the core pixels, r < 0.5 R), **noise** sigma (MAD x 1.4826 of the core pixels) and local **background** B (median of an annulus outside the spot, away from neighbours). Models:

1. `plateau_fraction` (the maintainer's idea): a pixel belongs to the spot if it lies within a fraction f of the way from P to B (default f = 15 %). Diameter = equivalent-area diameter of the connected region around the centre, 2 sqrt(A / pi). A blurred edge of 25 / 50 / 75 % pixels therefore ends up outside the ROI.
2. `plateau_sigma`: same, but the tolerance is k x sigma (default k = 3). Caution: depends on the noise level (very clean images give a tiny tolerance, noisy ones a loose one), so the lab should also test `max(f x (P - B), k x sigma)`.
3. `half_max`: the radius where the azimuthally averaged radial profile crosses 50 % between P and B. Reference value, true edge for a symmetric blur.
4. `max_gradient`: the radius of steepest descent of the radial profile (edge inflection). Equals half_max for a symmetric blur, noisier.

All four report the diameter, so the lab can plot "model diameter minus truth" against blur width and noise on synthetic disks (known truth), then on the real dataset (spread across the array, and agreement between models). Models 1 and 2 are strictly smaller than 3 by about the blur width; whether that conservative size is what you want is a decision the lab numbers will inform. Weights: model outputs stay binary; a fractional-weight variant can come later via the existing `rasterize_fractional`.

Caveat for plateau models: a disk with a slow intensity gradient across it has no flat plateau; if the lab shows this on real data, fit a plane inside the core before computing P.

## 8.3 Speed (weak point 6) and recommended method

Recommended: (a) one matched-filter score map for the whole image with `cv2.filter2D` (disk minus annulus kernel), replacing the per-pixel Python loop; (b) candidate spots from a single-scale LoG on a half-resolution contrast map; (c) all per-spot radial profiles in one batched `cv2.remap` / `map_coordinates` call (N spots x radii x angles), not a loop per spot. Expectation: tens of milliseconds for ~150 spots, but that is an expectation. First measure the current `detect_rois` on the real 15 x 10 dataset as the baseline, then report before / after as the numerics rule requires.

## 8.4 Persistent / Individual scope: the biggest open item

`MaskScope` means "this cube and every cube after it" (Persistent) versus "this exact frame only" (Individual) (`image_tools/mask_scope.py`). ROIs have **no timeline today**: one centre, one set of diameters for all cubes, plus a rare manual nudge per (cube, wavelength) (`roi/model.py`). So a scope toggle for ROI editing is a data-model change, not a UI toggle:

- Individual mode needs per-cube ROI geometry (centre and diameters) stored somewhere, keyed by cube, and displayed / analysed accordingly. The analysis side is already per cell (ROI x cube), so inputs per cube are possible.
- Today's behaviour (edit affects all cubes) is not exactly the mask's Persistent meaning (this cube onward). Which one should ROI Persistent be?
- Hard-coded to Persistent first (today's behaviour) is a valid first step that does not block the Array work. I have not yet read how the mask stores its timeline, so I cannot say how much of it can be reused.

Needs a decision and a separate design before building (touches saved data format, so it needs a migration plan).

## 8.5 Ring versus sample: how overlap works (already implemented, now to be documented for users)

The analysis already applies the rule you described: with the default reference mode `exclude_all_sample_rois` (`analysis/provenance.py:327`, `analysis/tasks.py:308-537`) the pixels of **all** sample disks are removed from every reference ring. So rings may overlap each other (shared pixels counted in each ring), but a ring never counts a sample disk pixel. The sample disk is masked only by the user's mask, never by a ring. The "all sample ROIs" union includes the ring's own disk.

To do: put this sentence where the user sees it, e.g. the tooltip of the Reference toggle on the ROIs tab, the Array popup, and the analysis-settings help. I have not checked whether the mode is exposed in any UI today.

## 8.6 UI notes

- Array parameters (rows, cols, disk D, pitch x / y, rotation, anchor, size mode, edge model while it is still being compared) live in a **dropdown / popup** opened from the Array group. In the row itself: mode selector, Detect / Place, Refine, Fit sizes.
- The ROIs-tab "view" groups (Sample, Reference, Labels) can shrink to **visibility toggles only**, with colour and transparency in an expandable flyout (a small arrow on each). This frees the row for Scope and Array. Remembered state per control (`ui_state.bind`).
- Scope toggle position: left of all editing icons and right of the view section (my reading; to be confirmed).
- Unit toggle: the length fields follow the app-wide px / um display unit (`UnitToggle`, `ScaleBarControls`); the text toggle behind a field is disabled without a calibration, like the existing ones.

---

# 9. Ring rules, settings menu, scope, and first lab results (2026-10-08, later)

## 9.1 Decisions (maintainer)

- **Diameters only** in code, UI and docs; never radii. (Existing helper `estimate_reference_ring_radii` and the stable app's radius fields are radius-based and will not be reused as they are.)
- **Inner ring diameter must be larger than the sample diameter.** Default way to set it: measure it from the image, the same way as the sample size: as small as possible while avoiding the (darker) disk pixels. A switch replaces this by a fixed **ratio** (inner / sample) with a ratio field.
- **Ring thickness** follows the equal-area rule by default; also a switch (other rule to be defined by the user in the menu).
- **Settings icon + menu** on the Array group holds every option; nothing needs deciding now: edge model (+ its parameters: fraction, sigma k), size mode (one value for the whole array = minimum, or each ROI individually), inner-diameter mode (measured / ratio) + ratio, thickness mode (equal area / other), a **"Reference ring excludes sample disks"** checkbox (the existing analysis rule `exclude_all_sample_rois`, section 8.5; its explanation goes into this menu and the Reference toggle tooltip).
- **Scope = timeline like the mask**: Persistent = "this cube and every later cube", Individual = this exact frame. ROI parameters (centre, diameters) therefore become **frames with different ROI parameters**, stored in the mask's format. A ROI edited on a single cube only is today's behaviour. First version is hard-wired to Persistent; the timeline model is designed afterwards (it changes the saved format, so it gets its own plan and migration).
- Toggle position confirmed (ROIs tab, between the view groups and the editing tools). Rotation above 2 degrees: prompt to rotate (yes / no) after the procedure.

## 9.2 Built today

- `image_features.py`: contrast map / NaN fill / feature size extracted from Chromatic (`image_tools/chromatic/auto_landmarks.py` imports them, `AutoLandmarkError` is now an alias of `FeatureSearchError`). Chromatic auto tests: 23 pass.
- `roi/edge_size.py`: the five spot-size models behind one function, `measure_spot_sizes()`. Tests `tests/unit/test_lspri_roi_edge_size.py` (12 pass): half-max and max-gradient recover a blurred 24 px spot to <0.8 px; plateau models are smaller than the true edge by an amount that grows with the blur; sigma-only depends on the noise level (known weakness).
- `tools/array_lab.py`: baseline timing + model comparison on real data.

## 9.3 First lab result (one dataset, frame 0, 600 nm, `04_Bulk_sensitivity...`; single run)

Baseline of the code the app has today:

| Step | Result |
|---|---|
| `estimate_array_geometry` | 1.2 s; says **10 rows x 18 cols**, pitch 79.45 px, diameter 37.3 px |
| `detect_rois` with that geometry | **47 s**, 170 ROIs |

- 18 columns at a pitch of 79.45 px do not fit into the 1300 px image (16 would), and the Chromatic lab found a 15 x 10 array. So the old column clustering over-counts, and the 170 ROIs include false ones. (Not yet checked which: needs a look at the overlay.) Supports the lattice-fit replacement.
- The 47 s confirms the slow Python refinement loop (weak point 6). The new pieces below run in well under a second.

Edge models on those centres (diameter in px, over the spots that could be measured; the centre list still contains false spots, so `min` is not meaningful):

| Model | n | median | MAD | note |
|---|---|---|---|---|
| half_max | 164 | 39.45 | 0.23 | agrees with the Chromatic estimate (41) and the nominal ~40 |
| max_gradient | 164 | 39.66 | 0.16 | same as half_max, as expected |
| plateau_fraction f = 0.15 | 145 | 35.70 | 0.67 | ~3.8 px (about 10 %) smaller than the true edge: the feathered edge is excluded |
| plateau_fraction f = 0.05 / 0.30 | 145 | 32.80 / 37.58 | 1.46 / 0.50 | tolerance moves the size by ~5 px |
| plateau_sigma (3 sigma) | 145 | 29.58 | 2.80 | noise-driven, large spread: not usable alone |
| plateau_combined | 145 | 35.70 | 0.67 | identical to the fraction model here (the fraction dominates) |
| oversample x4 | 145 | 35.55 | 0.67 | no gain over real pixels, 4x slower |

Time for 170 spots: about 0.3 s per model (0.26 - 0.30 s; x4 oversample 1.2 s).

First reading (not a decision): the plateau-fraction model behaves as intended and is stable (MAD 0.67 px); sigma-only should go; oversampling is not worth it. How tight the fraction should be (5 / 15 / 30 % changes the size by ~5 px) is the scientific choice for you. 19 more spots failed for the plateau models than for half_max (reasons: edge not found, no contrast, outside the image); to check on clean centres once the new detector gives them.

## 9.4 Next

1. Candidate detection + lattice fit (replaces the old geometry and the 47 s loop), validated against the 15 x 10 reference on this dataset.
2. Ring measurement module (inner diameter avoiding disk pixels, thickness rule), in the same style as `edge_size.py`.
3. Toolbox command to place / replace an array within the selection; "Refine array"; worker; ribbon group + settings menu; then the ROI timeline design.

---

# 10. New lattice detector built and checked (2026-10-08, later)

`roi/array_detection.py` (`find_array`, `lattice_nodes`, `ArrayPrior`, `ArrayFit`), tests `tests/unit/test_lspri_roi_array_detection.py` (16 pass), lab section "[new]" in `tools/array_lab.py`.

## 10.1 Correction to section 9.3

The array in the development dataset is **10 rows x 16 columns = 160 spots** (checked on an overlay of the found centres), not 15 x 10: that number was the Chromatic landmark grid. So the old code's answer (10 x 18, 170 ROIs) had 2 extra columns' worth of false spots, and the new one is right.

## 10.2 Result on the real image (frame 0, 600 nm)

| | Old (`estimate_array_geometry` + `detect_rois`) | New (`find_array`) |
|---|---|---|
| Time | 1.2 - 2.9 s + 47 - 57 s (two runs) | 0.87 s total |
| Rows x cols | 10 x 18 (wrong) | **10 x 16** |
| Pitch | 79.45 px (one value, integer in the settings) | x 79.68 / y 79.71 px (float, separate) |
| Tilt | not measured | 1.46 degrees (below the 2 degree prompt threshold) |
| Spots located | 170 ROIs (false ones included) | 152 of 160 |
| Residual from the lattice (found spots) | not available | rms 0.59 px (median 0.36, 90 % under 0.87 px) |

The 8 spots not located are the top row, which is cut off by the image edge (the matched-filter score is low); they stay at their lattice positions with `found = False`. An overlay shows every circle on its spot.

## 10.3 Edge models on the clean centres

| Model | n | median | MAD | min |
|---|---|---|---|---|
| half_max | 152 | 39.44 | 0.23 | 38.21 |
| max_gradient | 152 | 39.62 | 0.16 | 38.47 |
| plateau_fraction 15 % | 147 | 35.70 | 0.68 | 25.95 |
| plateau_fraction 5 % / 30 % | 147 | 32.82 / 37.59 | 1.50 / 0.50 | 21.82 / 34.80 |
| plateau_sigma 3 sigma | 147 | 29.98 | 2.51 | 24.23 |

Half-max / gradient are very uniform across the array (no outlier). The plateau models have a few outliers (min 26 vs median 35.7) and 5 spots where no connected region is found; filling holes did not change that, so the cause is something else (a scratch crossing the spot, most likely): to be looked at before choosing. Time for all 152 spots: about 0.25 - 0.4 s per model.

## 10.4 Lessons from building it (kept as rules in the code)

- A dark holder border or a thin dark strip has a spot-like core: candidates are therefore kept only if they are **isolated disks** (at most 2 of 8 ring directions dark, disk at least 85 % filled). Without this the real image grew an 11th row on the black band, and a synthetic band produced a false row.
- A hard "all directions clear" rule rejected true spots close to a border; the 2-of-8 allowance fixes that.
- Everything is float and in pixels; priors (diameter, rows, cols, pitch) only produce warnings in this version. Using them to constrain the search (semi-automatic mode) is the next step.

## 10.5 Next

1. Ring measurement module (inner diameter avoiding disk pixels, thickness rule; diameters only).
2. Toolbox command: place / replace an array inside the selection (one undo step, `RoiArrayGroup` recipe), refine array.
3. Worker + ribbon group + settings menu; then the ROI timeline design.

---

# 11. Ring module built and checked on the real image (2026-10-08, later)

`roi/ring_size.py` (`measure_rings`, `ring_from_inner`, `outer_from_inner`, `neighbour_warnings`, `summarize_rings`, `RingParams`), tests `tests/unit/test_lspri_roi_ring_size.py` (14 pass). Diameters only.

## 11.1 What it does

- **Inner diameter, measured** (default): per direction, the radius where the radial profile has fallen back to background (within max(5 % of the spot contrast, 3 x background noise) of B); the inner diameter is the 90th percentile over 72 directions (a scratch or neighbour in one direction does not decide) plus an optional margin. Always at least `sample + 1 px`.
- **Inner diameter, ratio**: `ratio x sample`, no image used (switch for the settings menu).
- **Outer diameter** by thickness rule (switch): `equal_area` (default, pi/4 (outer^2 - inner^2) = pi/4 sample^2), `thickness` (fixed ring width), `outer_ratio`.
- **Overlap**: rings may overlap each other; the analysis removes every sample disk from every ring (`exclude_all_sample_rois`). `neighbour_warnings` reports a ring that reaches a neighbouring disk (it is then thinner than drawn).
- **Array-wide size**: for the sample disk the minimum is the safe uniform value; for the ring inner diameter the maximum is. A single debris-affected spot would decide the plain maximum, so `summarize_rings` also gives `inner_robust` (maximum after dropping MAD outliers).

## 11.2 Real image (152 located spots; ~0.5 - 1 s per run for the measured mode, 1 ms for ratio)

| Sample size | Inner mode | Inner median (min - max) | Robust max (outliers) | Outer max |
|---|---|---|---|---|
| plateau 15 % (35.7 px) | measured, 5 % | 47.0 (44.5 - 76.0) | 48.5 (9) | 83.9 |
| plateau 15 % (35.7 px) | measured, 15 % | 44.5 (43.0 - 51.0) | 45.5 (2) | 62.2 |
| plateau 15 % (35.7 px) | ratio 1.4 | 50.0 | 50.0 | 61.4 |
| half-max (39.4 px) | measured, 5 % | 47.0 (44.5 - 76.0) | 48.5 (9) | 85.6 |
| half-max (39.4 px) | ratio 1.4 | 55.2 | 55.2 | 67.9 |

- The spot fades to within 5 % of background at about **47 px** (median); 15 % at 44.5 px. The visible 39.4 px half-max edge therefore has a feathered skirt about 4 px wide on each side. Pixels from there inwards are spot pixels.
- The plain maximum (76 px) comes from a few spots with debris or a scratch nearby; 9 spots exceed the robust limit at 5 %. For a uniform array ring use `inner_robust` (48.5 px), or measure each ring individually.
- No ring reaches a neighbouring disk (pitch 79.7 px).
- Because the ring inner diameter is measured from the same spots, the sample and ring numbers fit together: sample 35.7 - 39.4 px, inner about 47 - 48.5 px, i.e. a gap of 8 - 12 px of feathered edge between them. A ratio of 1.4 gives 50 px (from 35.7) or 55 px (from 39.4).

## 11.3 Next

1. Toolbox command: place / replace an array inside the selection (ROIs + `RoiArrayGroup` recipe, sample and ring diameters per ROI, one undo step); "Refine array" for an existing selection.
2. Worker (plain thread, progress / cancel, TaskIndicator) and the one pure entry point that runs: find array, size sample, size ring, report.
3. Ribbon "Array" group + settings menu (modes, edge model, ring modes, size mode, overlap note); then the ROI timeline design.

---

# 12. The Array group is built (2026-10-08, end of day)

ROIs tab -> new group **Array** (after Labels). Not looked at in the running app (static checks + tests only): please check it by eye.

## 12.1 Pieces

| Piece | File |
|---|---|
| Shared contrast helpers (extracted from Chromatic) | `image_features.py` |
| Spot detection + lattice fit, centre refinement | `roi/array_detection.py` |
| Sample disk size models | `roi/edge_size.py` |
| Reference ring (diameters only) | `roi/ring_size.py` |
| One entry point per action: `detect_array`, `refine_array`, `place_array` | `roi/array_pipeline.py` |
| Background thread, progress / cancel, TaskIndicator | `roi/array_task.py` (`ArrayAction`) |
| Store the result, one undo step | `RoiToolbox.place_array`, `RoiToolbox.refine_rois` |
| Buttons, settings menu, px / um length fields | `panels/image/array_controls.py` |
| What the buttons do (reference wavelength jump, selection replace, rotate prompt) | `panels/image/array_actions.py` |
| Remembered controls | `storage/ui_state_keys.py` `ARRAY_KEYS` |
| Lab / comparison script | `tools/array_lab.py` |

## 12.2 Behaviour

- Row: **mode** (Auto / Semi / Manual), **Run** (wand = find, grid = place, X = cancel while running), **Refine** (needs selected ROIs), **gear** (settings menu with every option).
- **Run (Auto / Semi)**: works on the **reference wavelength of the current cube**; on another wavelength it asks whether to jump there. Finds the array, measures sample disks and rings, then **replaces the selected ROIs** with it (nothing selected: added next to the others). One undo step; ROIs not replaced are renumbered like after a delete and their stored results follow. A tilt above 2 degrees asks whether to rotate the image (yes: rotate, detect again; existing ROIs follow through the geometry sync). Refused while an analysis runs.
- **Run (Manual)**: stamps rows x columns from the typed numbers (first-disk position, pitch along row / column, rotation, disk diameter, ring from the ring settings; "Snap to image" moves nodes onto spots and measures).
- **Refine**: moves each selected ROI to its spot (limit 0.35 x nearest-neighbour distance) and re-measures sample and ring. ROIs with no spot nearby stay where they are.
- **Settings menu**: Array (rows, columns, disk diameter, pitch x / y, rotation, first-disk x / y, snap, bright spots), Sample disk size (edge model, fraction, sigma, one value / each), Reference ring (inner measured / ratio, background fraction, extra gap, ratio, outer by equal area / fixed width / ratio, one value / each), the overlap rule as text. Length fields have a **px / um text toggle** (the app-wide display unit, disabled without calibration) and always store pixels, resolution 0.1 px. All remembered across restarts.
- **Overlap rule** is written in the Run tooltip, the settings menu and the Reference toggle tooltip.

## 12.3 Not built / open (decisions needed or deliberately left)

1. **Scope toggle (Persistent / Individual) and the ROI frame timeline**: not built; every edit is Persistent (as ROIs always were). Needs its own design and migration (section 8.4 / 9.1).
2. **Semi mode only checks priors** (warnings); rows / columns / pitch do not narrow the search yet. Diameter does set the search size.
3. **Ring-exclusion checkbox**: only the text. A working checkbox needs the engine's `reference_exclusion_mode` provider wired to a setting (it exists in `AnalysisEngine`, default `exclude_all_sample_rois`, no UI).
4. **The painted-icon test** `test_the_new_icons_are_painted_at_the_ribbons_size_under_the_app_theme` (`tests/integration/test_lspri_rewrite_roi_overlay_controls.py`) already fails on the committed HEAD ("sample icon is too small", 12 px): not caused by this work; to be looked at separately.
5. Detection ignores the user's mask (it uses finite pixels only). The stable app could ignore marked pixels.
6. Default edge model is `plateau_fraction` at 15 %; the model, the fraction and uniform-minimum vs individual are the choices to settle from the lab and by eye.
7. `RoiToolbox.delete_rois` restores the ROI dict in non-sorted order on undo (ids are right); `place_array` sorts. Harmless today, noted.
