# Finding small features on ROIs, from the image alone (2026-10-29)

Maintainer request: "a semi-automatic or filter-based feature to apply a mask. The goal is to find small features overlapping sample or
reference ROIs [that] hinder the ROI performance by increasing noise or lowering signal ... purely image-based." Examples named:
reference ring, dark features: ROIs 139, 105, 17; sample, dark: 4, 1, 117, 151; sample, bright: 69, 9, 76.

**State**: the pure detector, its tests and a lab tool are built and verified on real data, and (section 7) the **Mask tab's "ROI
features" tool** runs it in the app. Uncommitted. The performance test did **not** show the hoped-for gain (section 4): the tool
finds and masks the features, whether that helps the measurement is for the user to judge on their own data.

## 1. Data used

`Data_PyTest/04_Bulk_sensitivity_pumpplan_0-70percent_to_LbL_with_water_v1_LED_V2/images/images_1275x799_314x27_c80IZ_u16.ome.zarr`,
session `2026-10-22_172035` (the session open last; 160 ROIs, array of 16 x 10, sample disk 33.7 px, reference ring 49.5-59.9 px; the
zarr is already rotated, so image and ROI pixels coincide). Detection frame: cube 0, 610 nm. The session's baseline step 1 (30 cubes)
and signal step 8 (25 cubes) were used **only to judge** the result, never by the detector.

What the named features are (crops of one frame and of a 12-cube mean): a 6 px dark spot and a 5 x 8 px dark stub on the ring (139,
105), a soft 15 px dark blob and a few dots across the ring (17), a dark spot inside the disk (4, 151), a small dark dot (1), a notch
at the rim (117), bright notches at the rim (69) and thin bright streaks ~5 px wide, 25-35 px long across the disk (9, 76). They are
static (the same in the 12-cube mean).

## 2. Method (`image_tools/mask/roi_features.py`, pure numpy/scipy, no Qt)

1. **`fit_patch`** per ROI: fit the disk's centre (moved up to ~2.5 px from the ROI's) and its radial profile, outliers left out;
   the residual / profile contrast on a common grid. 85 ms per ROI.
2. **`score_rois`** over all ROIs: the expected residual is a median image plus 3 principal components over the ROIs, fitted robustly to
   each; `z` = what is left / the per-pixel spread among ROIs. Robust rms of `z` on the real frame: 0.86 (so `z` is slightly conservative).
3. **`find_features`**: connected same-sign runs of `|z| >= 3` holding at least 2 pixels `>= 5`, area 4-500 px, width <= 20 px, overlapping the
   sample disk or ring; mask = feature grown 1 px. A component on the edge of the data is dropped (the rotated frame darkens smoothly
   over ~20 px at its border; ROIs 1, 2, 4, 13 touch it).
4. **Second pass** (`detect`): what pass one found is left out of the radial profiles and the array is scored again.

Why not something simpler (each was tried on the real frame and failed):

| Tried | What went wrong |
|---|---|
| median-filter residual | the soft disk edge gives a ring of false positives; near the disk even the sign of a ring feature was wrong |
| radial profile alone | clean on all ten named ROIs, but flagged ~every ROI: each disk has a small bright **lobe at the lower-right rim**, of different strength each time (0 to 33 % of the contrast, no pattern across the field) |
| median template of neighbours | the lobe's strength varies, so a single template cannot take it |
| template + per-pixel spread | helped, still 98 of 160 ROIs |
| median + 3 principal components (kept) | absorbs the lobe's strength variation; a lobe in a different place or shape is still reported (ROIs 100, 85, 5 ...: real notches, like the bright notches of your ROI 69) |
| (one pass) | a long streak through the centre pulls the profile and makes a dark twin next to it; fixed by the second pass (pinned by a test) |

## 3. Detection on the real frame

- All **10 named ROIs are found**, each at the place you described (montage: `roi_feature_lab` output `montage_c0_610nm.png`).
- 94 of 160 ROIs have at least one feature, 161 features (47 darker, 114 brighter than expected); median area 61 px (90th percentile
  164), median width 6.3 px (90th percentile 10). The ten named ROIs are a sample of a larger population: ~35 other ROIs have features
  of similar size, mostly the same kind of bright notch at the rim.
- Estimated effect of the features on the sample/reference ratio (`ratio_effect`): median 0.56 % over ROIs with features, 90th
  percentile 1.7 %, maximum 6.1 % (ROI 9); 52 ROIs above 0.5 %, 26 above 1 %.
- Share of the sample disk the masks would cover: above 10 % in 29 ROIs, above 25 % in 5, above 40 % in 3 (ROIs 76, 109, 114;
  ROI 76 is the named ROI with two bright streaks plus the dark fringes beside them: 73 %). Those are **left out of the lab's mask
  file** (`--max-share`, default 0.4): no mask can fix them, they leave too few pixels.
- Time for a whole frame: 16 s on one core (160 ROIs). Not tuned further.
- Checks: 16 unit tests (synthetic array with a lobe of varying strength, injected dark spot, bright streak, invalid border with a
  smooth darkening; each rule has a test that fails without it).

## 4. Does masking them help? (the independent test, `tests` not in the repo: scratch scripts)

Spectra of every ROI at all 26 wavelengths for the 30 baseline and 25 signal cubes, sample / reference means with and without the
detected features (mask found at 610 nm, warped to each wavelength like a persistent mask), and 3 **controls** (the same masks moved to
random places that still overlap the ROI). Metric: the session's own (absorbance, polynomial order 11, centroid), through
`trace_fit.traces_from_inputs`. Noise = detrended standard deviation of the trace in each step.

| Group (n) | noise masked / unmasked | SNR masked / unmasked | (controls) |
|---|---|---|---|
| the 10 named ROIs | x1.03 | x0.99 | x1.00, x0.995 |
| other ROIs, ratio effect > 0.5 % (43) | x1.00 | x1.01 (SNR up in 60 % of ROIs) | x1.00, x1.00 (50 %) |
| other flagged, effect <= 0.5 % (41) | x0.995 | x1.01 (63 %) | x1.00, x1.00 (44 %) |

(this test masked every detected feature, including ROI 76's 73 %: with the share rule that ROI is left alone.)

Also, across the 160 ROIs:

- Baseline noise of the metric is ~30 pm (median), 90th percentile 36 pm, and it is **not** related to the size of the feature effect
  (Spearman rho +0.08, p 0.32). Neither is the deviation of a ROI's signal (rho +0.11, p 0.16). The noisiest ROIs (25, 13, 12, 10, 14, 11)
  are in the first row of the array, at the edge of the valid image.
- The features do move each ROI's **absolute peak position**: median 100 pm, 90th percentile 350 pm, maximum 1.1 nm (random-position
  masks: median 14 pm). But the spread of peak position between ROIs is ~1.3 nm and does not shrink when the features are masked (1332
  -> 1376 pm; controls 1353).

Reading: in this bulk-sensitivity dataset the baseline noise is at the shot-noise floor, so removing pixels costs a little averaging
and removes no noise source; the features are static, so they offset the signal without adding noise. **No consistent gain in noise or
SNR was found**; per ROI it goes both ways (ROI 9: SNR 77 -> 92; ROI 1: 115 -> 114; ROI 76 would lose 15 %). What the features do change
measurably is the absolute peak position of the ROI (hundreds of pm). If the problem seen in the sensorgrams is something else (drift,
steps, a moving feature, a different experiment, low light), this test does not cover it: it needs the sensorgram you looked at.

## 5. Limits

- Needs at least 8 ROIs of one kind in the frame; says what deviates from the other ROIs, not whether it matters.
- Round ROIs only (radial profile). Free-form masks are not handled.
- A feature that most ROIs share is "normal" and not reported; a feature wider than 20 px is not small (a very wide blob is partly taken
  into the profile and shows up as fragments).
- One frame (cube 0, 610 nm), one dataset. Features are static here; moving ones (particles, bubbles) are the Mask tab's Scan / Auto mask.
- Thresholds (`z_seed` 5, `z_grow` 3, width 20, 3 modes) were set looking at the named ROIs and checked by eye on ~15 others; there is no
  ground truth beyond your list.

## 6. Run it

    python tools/roi_feature_lab.py --dataset <zarr or TIFF folder> --session <session folder> --cube 0 --wavelength 610 ^
        --rois 139 105 17 4 1 117 151 69 9 76 --out <folder>

Writes `features_*.csv`, `mask_*.png` (255 = masked, raw pixels; Mask -> Load **replaces** the mask at that frame and scope, not
undoable) and `montage_*.png` (needs matplotlib). Read-only for the dataset and the session.

## 7. In the app: the Mask tab's "ROI features" tool (built 2026-10-29, maintainer's go-ahead)

Maintainer's request: a feature finder that works on the current mask, adds pixels to it when accepted, follows the Persistent /
Individual setting, with a Process icon, an Apply icon and, if needed, a parameters icon.

**Screen purpose**: find the small features on the ROIs of the image on screen, look at them, add them to the mask.
**Layout** (the sixth entry of the Manual-edit picker, one row like the other tools): `[eye] [settings v] [process] [apply +]`.
**Main actions**:

- **Process** looks at the image on screen (3-7 s, progress and Cancel in the status bar). It changes no mask.
- **Eye** shows the found pixels on the image (the same switch as the other tools; remembered).
- **Settings** (a popup; the tooltip always shows the numbers; all remembered): *Strength* (the score a feature must reach, default 5),
  *Extent* (where a feature ends, 3), *Largest feature* (20 px), *Margin* (1 px), *Skip ROIs above* (40 % of the disk or ring).
  A "Defaults" button puts them back. A change of the numbers does not touch the result on the image; the Process tooltip says
  "settings changed since the last run" until it is pressed again.
- **Apply** adds the pixels to the mask in effect at this frame (the persistent mask warped in, or the frame's own individual mask),
  at the scope of the State group, cut to the area selection. One mask change; like the other tools' Add it is **not on the undo
  stack** (mask edits are not, `MaskModule` docstring). Enabled only while the image on screen is the one the result was made for; the
  tooltip says which image that is when it is not.

**Which frame** (the open question of the first draft): the one on screen, as for every tool. A persistent mask is authored at that
frame and warped to the others by the chromatic model, like any persistent mask; features are static, so any frame will do, the one
where they are best seen is the best.

**"Works on the current mask"**: Apply merges *with* the mask in effect (nothing is taken away). The finder itself looks at the picture
only: the mask is *not* hidden from it, because pixels hidden as "no data" would make a feature next to an existing mask look like
the edge of the data and the edge rule would drop it. A consequence worth knowing: what is found does not depend on what is masked, so
a mask edit never makes a result stale.

**Pieces**: `image_tools/mask/roi_feature_run.py` (pure job: `RoiCrop`, `FeatureParams`, `fit_crops`, `find_in_crops`, `RoiFinding`),
`panels/roi_feature_task.py` (moved from `image_tools/` on 2026-11-02; `RoiFeatureTask(ThreadedTask)`: crops, preprocessing without the mask, raw-pixel result),
`RoiFeaturesEditPanel` in `panels/image/mask_edit_panels.py`, the preview kind `KIND_ROI_FEATURES` in `panels/mask_edit_preview.py`,
Apply through `MaskCandidateEditor`, the icon `roi_features_icon` in `panels/image/mask_edit_tool_icons.py`, task-indicator and
Cancel wiring in `app_rewrite.py`.

**Where the time goes** (cube 0, 610 nm, 160 ROIs, the development laptop): rasterizing and cutting out the ROIs 0.5 s, **fitting
11 s on one core** (69 ms per ROI), scoring and extracting 1.3 s. The fit runs on **worker processes**, not a thread: it is a few dozen
small numpy calls per ROI, each needing the interpreter lock, and a thread doing that for seconds starved the window of it (the
Sensorgram lesson of 2026-10-24, `analysis/trace_pool.py`). The worker count comes from `lspr_core.perf` (task key
`mask.roi_features`, unit = a task of 4 ROIs, `perf.run` is logged); the workers import only numpy / scipy and the finder (0.7 s, no
Qt). Measured through the real task on the real session: **first run 6.9 s** (Standard mode: every core but one), a **re-run with other
numbers 1.4 s** because the crops and the fit are kept (none of the five numbers touches the fit; the keep is tied to the frame,
geometry, background, ROI geometry and chromatic matrix and is recomputed when any differs). The 1.3 s scoring step runs in the task's
own thread (a short burst; moving it to a process would add a start-up cost larger than it saves).

**Checks**: the mask the task gives on the real frame is **pixel-identical** to the one `tools/roi_feature_lab.py` wrote (16,575 px, 0
differ; 94 of 160 ROIs, 161 features, ROIs 76, 109, 114 left out by the 40 % rule). `tests/unit/test_lspri_roi_feature_run.py` (14):
patches and mask identical at 1, 2 and 3 processes, the numbers do what they say, Cancel with and without processes, too few ROIs.
`tests/integration/test_lspri_roi_features_tool.py` (17, a real panel, 16 synthetic ROIs, real worker processes): Process finds
ROIs 5 and 9 and previews exactly those pixels; Apply merges at the persistent and the individual scope, with an existing mask and
inside an area selection; the result is dropped on a ROI change or a new dataset, and refused if the ROIs change while it runs; a
re-run reuses the fit; the numbers are remembered.

## 8. Spectral split: grey dirt vs missing nanostructure (experiment 2026-11-03, not built)

Maintainer: the tool is not precise and its numbers are hard to tune. Idea tested: the detector sees one frame (cube 0, 610 nm), but
features are static and every pixel has 26 wavelengths. In log intensity, dirt is roughly **grey** (a flat offset over wavelength);
more or less nanostructure follows the **disk's contrast spectrum** (measured here: -0.19 at the ends to -0.61 at the resonance, log
units, so the two shapes are well separable). Per pixel, the deviation from the median of all ROIs (log I, each ROI/wavelength
normalised by its own background, chromatic affine applied, centre from `fit_patch` at 610 nm) was split into `a` (grey) + `b`
(plasmon-shaped) + rest, over a 12-cube mean. Scratch script, not in the repo; no family model (no radial profile, no components).

- Dark dirt (ROIs 4, 151, 105, 139, 117) is strong in the **grey** map and nearly absent in the plasmon map.
- Bright streaks and notches (9, 76, 69) are in **both**: brighter (grey +) *and* less plasmon (plasmon -). That is missing
  nanostructure (scratches), not dirt on top.
- Clean ROIs (2, 3, 7, 11): both maps quiet; the lower-rim lobe is weak and mostly plasmon-shaped. The grey map keeps a soft
  field gradient (the simple background normalisation did not take it out).
- The "rest" map lights up every disk edge: sub-pixel misregistration between wavelengths on the steep edge. Not usable as is.
- Averaging 12 cubes lowered the background pixel scatter only 1.6x (0.016 -> 0.010 log units), so most of the scatter is static
  pixel structure, not shot noise: more cubes help little; the gain is the physical split, not noise.

Proposed (awaiting the maintainer): run the existing family model on the grey and plasmon maps instead of one frame, and give
the settings physical units (e.g. "dirt dims the light by >= 2 %", "nanostructure missing >= 10 %") with a choice of what to mask
(dirt / damaged structure / both); plus tuning by example (click one feature, thresholds set from it) and a per-ROI keep/skip list.

## 9. The spectral finder on ROIs (2026-11-03) - SUPERSEDED by section 10, its code removed the same day

Kept as the record of what was learned (each finding carried into section 10). The modules `roi_features_spectral.py`,
`field_dirt.py`, `tools/roi_feature_spectral_lab.py` and their tests were removed uncommitted when the maintainer chose the ROI-free
map ("map all dirt, so changing ROIs never needs the mask again; the ROI search can be ditched"); the opt-in
`FeatureSettings.min_mode_share` was taken back out of `roi_features.py` (it served only the removed module).

Maintainer: mask both kinds, but **tell dirt from nanostructure, dark from bright, each switchable**.

**Pieces.** `image_tools/mask/roi_features_spectral.py` (Qt-free, listed in `.importlinter`): `decompose` (log intensity, per ROI and
wavelength minus a robust background plane, minus the median ROI, fitted per pixel as `g + s * c(wl)`; `c` = the median ROI's own
contrast spectrum at that pixel; `s` only where `c` has a shape; a robust 2nd-order surface taken out of `g`), `find_spectral` (both
maps through `roi_features.score_rois`, then hysteresis on **score and physical size together**), four kinds `dirt_dark`,
`dirt_bright`, `structure_missing`, `structure_extra`, each its own mask, `kinds` chooses which go into the combined mask. Lab:
`tools/roi_feature_spectral_lab.py` (mean of N cubes, all wavelengths, chromatic affine per wavelength, centre from `fit_patch`; border
ROIs padded with NaN, centre fit falls back to the ROI's own centre). Writes a CSV, the mask, a montage and **`field_spectral.png`**
(the whole field, the kinds in colour). `--dump` saves the grid stacks for fast experiments. Tests: `tests/unit/test_lspri_roi_features_spectral.py` (12).

**Settings in physical units:** `dirt_min` 2 % of the light, `structure_min` 10 % of the normal plasmon contrast, `certainty` 5 (the
old *Strength*), size limits as before. Reported size per feature in the same units (ROI 69's notches: 40 and 60 % nanostructure
missing; ROI 139's dirt: 7 % less light).

**Fixes found on the way** (each from the real data or a failing test):
- the grid must reach ~14 px beyond the ring (was 6): with less, broad shading looked small and passed the width rule;
- per-ROI smooth surface out of the dirt map: saddle-shaped shading across a window (ROIs 17, 65, 119) gave large yellow/blue wedges;
- `FeatureSettings.min_mode_share` (new, default 0 = the old tool unchanged; 0.1 here): a family component must be carried by
  about 10 % of the ROIs (participation ratio). Without it one ROI's strong defect can become a component and explain a faint defect
  away (pinned by a test). On the real 160 ROIs it changes nothing.

**Real data** (12 cubes, 160 ROIs; 7 s reading, 37 s with centre fits on one core, maps 8-13 s, a change of limits ~2 s): **all 10
named ROIs found as the expected kind** (dirt: 139, 105, 17, 4, 1, 117, 151; missing nanostructure: 69, 9, 76), at every limit tried
(dirt 1-5 %, structure 10-30 %). ROIs with dirt 99 (2 %) -> 81 (5 %); with structure 74 (10 %) -> 65 (30 %): the limits change the
count gently, most features are clearly above them. Magenta marks the dents / notches visible on many disk rims and the long scratches
(9, 26, 76, 93, 109, 114); blue the dark specks and smudges, mostly where the image shows dirt (left edge, upper right).
Note: a dirt blob touching the ring is masked whole, also where it reaches into empty background (harmless for the measurement).

**Round-disk reference (same day, maintainer: "only one bright defect detected in ROIs 37, 38, 54; 53's single one not at all").**
Those disks have rim notches, often two; the lower-right one was missed because many disks have a notch there, so the typical-ROI
family (median + components) took it as normal: the "rim lobe" of section 2 **is a defect** for the maintainer. Now
`SpectralSettings.reference = "round"` (default): the template is the median over every ROI *and every direction* at each distance
from the centre (an ideal round disk), and the nanostructure map is scored against its median and robust spread at that distance
(`_score_round`); dirt keeps the family model. `"typical"` keeps the earlier behaviour. And a nanostructure feature is limited by
`structure_max_share` (60 % of the disk) instead of the dirt pixel/width limits: ROI 76's three joined streaks are 529 px (47 % of
the disk) and were dropped by the 500 px rule.
Result: every notch of 37, 38, 53, 54 found (58-68 % missing), 9 and 76 still found; missing nanostructure now on 131 of 160 ROIs
(74 before), mostly the lower-right notch; dirt unchanged (96). Tests: 15 (both references pinned with a synthetic notch of 0-35 %).

**Dirt outside the ROIs (same day, maintainer: "the dirt around ROI 105 is not marked").** The per-ROI finder only reports what
touches a disk or ring, and the 105 particle sits in a soft shadow wider than the 20 px dirt limit. New pure module
`image_tools/mask/field_dirt.py` (in `.importlinter`), run by the lab after the per-ROI finder (`--no-field` switches it off):
input = mean over wavelengths of log intensity, each wavelength resampled onto the reference wavelength's pixels by the chromatic
model; disks + rings (up to the ring's outer radius - 3 px, `--field-exclude`) are the per-ROI finder's. `prepare_field`: shading
from pixels > 44 px from every disk -> each disk's halo (median over directions per 0.5 px of radius, linear between, measured
only where that disk is the nearest, so a neighbour's halo cannot set its zero) -> shading again with the halos out -> halos again.
`find_field_dirt`: shading = local **plane** through 8 px block medians over 5 x 5 blocks (a median is biased next to a cut-out disk on
a slope; blocks within 0.5 % of the plane are never rejected), score against the image's robust pixel noise, the same `dirt_min`
/ `certainty` hysteresis, kinds `dirt_dark` / `dirt_bright` (same switches). 20 px edge margin. Tests:
`tests/unit/test_lspri_field_dirt.py` (6; synthetic field with the real proportions: halo wider at the left, fall-off as measured,
~13 % over 140 px; a clean field gives nothing at 6 noise seeds).
Bugs found on the way, each by a failing synthetic test: overlapping halo windows overwrote each other's correction (marks right of /
below every disk); the plane's outlier pass threw out the slope's own blocks on smooth data (now a 0.5 % floor); one shading pass
left a light/dark pair between the leftmost disks (two passes).
Real data: 130 field features (119 dark), 105's particle + shadow found (469 px, -2 %), the speck trail near 103, the lines near 57
and at the left, the shadow below 119, the smudges near 31 and 79; ~11 s. **Known:** yellow ("bright dirt") rims next to large dark
smudges (top left, near 65 / 81 / 79) are mostly the shading pulled down by the smudge, not bright dirt: leave "Dirt, bright" off
unless wanted.

## 10. The feature map: the whole picture, no ROIs (2026-11-03, maintainer's go-ahead; not in the app yet)

Maintainer, after section 9: the stub of dirt attached to ROI 105's disk (left, level with the label) was still missed; "I would like
all dirt to be mapped even though it does not influence measurements, but when the user changes ROIs he doesn't need to recalculate
the mask again. So this can be the default behaviour, only the ROI search can be ditched."

**Pieces.** `image_tools/mask/feature_map.py` (pure, Qt-free, in `.importlinter`; progress + Cancel per wavelength), lab
`tools/feature_map_lab.py` (mean of N cubes per wavelength; geometry applied, the app's background flattening **off** because it may
use the ROIs; every wavelength resampled onto 610 nm's pixels by the chromatic model; the session's ROIs only label the table and
choose the close-ups). Writes `features.csv`, `mask.png` (enabled kinds, raw pixels), `field.png`, `closeups.png`; `--dump` saves the
aligned log stack. Tests `tests/unit/test_lspri_feature_map.py` (8).

**Method** (each choice from a failure on real or synthetic data, in brackets):
1. `find_disks`: dark round blobs of the common size in the wavelength-mean; centre by `roi_features.fit_patch`. On an array a
   lattice is fitted to the whole disks: a dark lattice point without a disk is added (ROIs 1, 2 merged with the dark top-left border),
   a found "disk" off the lattice is dropped and so reported as dirt (2 smudges were taken as disks), a disk cut by the border takes
   its lattice point if it is dark there (a circle fit to a cut soft edge was 7-12 px off; a sliver at the left edge snapped to a point
   outside the image). Step vectors are oriented along their group's mean direction (a sign rule on x split vertical steps into up
   and down: median ~0, 133,704 "disks"); degenerate or parallel steps, or a span far beyond the found disks, mean "no lattice".
   `refine_centres`: the radial fit again on the picture without its shading (the fall-off across a left-edge disk pulled the centre
   by a fraction of a pixel: a light / dark pair at the steep edge), accepted only if it moves < 1.5 px.
2. Per wavelength: shading (local plane through 8 px block medians, from pixels > 2 disk radii from every disk) -> each disk's round
   model -> shading again with the disks out -> model again. The disk model: at the edge and in the halo the disk's own profile
   (median per 0.5 px of radius over its nearest pixels: its own focus); inside, the median profile of its 9 nearest disks (a disk
   with a band of missing nanostructure pulled its own model and its intact half looked "extra": ROIs 76, 109, 126), shifted to the
   disk's level = the half-sample mode of its inner pixels (the most common value, not a median: a band through the centre), unless
   that differs from a robust plane through the neighbours' levels (itself left out; obvious outliers out before the first fit) by
   more than 0.05 or 3 spreads, then the plane (a 0.02 tolerance replaced the correct own level of left-edge disks, where the plane is
   extrapolated over a curved fall-off).
   A first look at the wavelength-mean (same steps) marks outliers (3 spreads and half the dirt limit, + 2 px); they are left out of
   every shading and disk model (a big dark smudge pulled the shading down and got a bright "dirt" rim: real bright dirt 39 -> 8
   features). A wider margin (4, 8 px) made it worse.
3. Per pixel over the wavelengths: deviation = g + s c(wl), c = the disk model there. g = dirt (fraction of light), s = nanostructure
   change (fraction of its contrast, where c has a spectral shape). Scored by the robust spread at that distance from the nearest disk,
   pooled over all disks. Dirt anywhere (20 px edge margin); nanostructure on the disks, at most 60 % of a disk. A bright "dirt" patch
   mostly on a disk's rim that touches missing nanostructure is that notch's rim (moved; its size is NaN where s is undefined).

**Real data** (12 cubes, 160 disks, ~70 s on one core; not tuned): all 160 disks found (1 and 2 from the lattice); 650 features:
dirt dark 356 (49k px), bright 7, nanostructure missing 273 (31k px), extra 14. ROI 105: the stub (up to 40 % darker) and its shadow
are one dark-dirt feature (1238 px, -3 %), also where the shadow reaches over the disk; notches of 37, 53, 54, 69 (56-65 % missing);
streaks of 76, 109, 126 clean magenta; 139's dirt (-7 %) and notch. **Uncertain**: the leftmost column (49, 81) where the image is
least sharp: 49 reads as broadly "missing" (its structure score is negative over most of the disk).

**Synthetic tests** (array 7 x 5 with the real proportions; cut disk; smudge; speck; stub on an edge; notch; extra spot; 40 % band;
bright and faint dirt): every one found as its own kind, a clean picture gives nothing at 5 noise seeds, centres within 0.5 px.
Known residue (not in the real data): 3.6 % of the band disk's area as "extra" at its rim; small marks where a big smudge sits
next to the steep fall-off of the field's left edge.

**Extent of a feature (same day, maintainer: "ROI 105 is a bit too much masked").** The particle at the 105 stub takes 39 % of the
light, but sits in a faint shadow of 2-4 % spreading ~1700 px, also over the disk; growing down to 60 % of the dirt limit (1.2 %)
covered all of it. Now `FeatureMapSettings.core_share` (0.3): a feature extends only to where it reaches 30 % of its own peak
(95th percentile over its seed pixels), the pieces holding a seed; for faint dirt the 1.2 % floor still decides. ROI 105: 1738 ->
133 px (-14 %); a standalone smudge without a stronger core (below ROI 119, ~5 %) stays whole; notches are trimmed to their cores
(-65 to -72 %). Pinned by `test_dense_particle_in_a_faint_shadow_is_the_particle` (and that `core_share=0` masks the shadow).
Real data after it: dirt dark 42k px (49k before), nanostructure missing 23k px (31k).

**Up to the border of the image (same day, maintainer: "the affected area seems to have a border offset, can't it be the whole
image?").** The 20 px strip left out along the border of the data (from the first field method, whose shading could not follow the
rotated frame's dark edge) is now 3 px (`edge_margin_px`: the rotation's interpolation blur). What the strip used to hide:
- `_unlit_border`: areas connected to the border below 30 % of the field's bright level (`unlit_level`; a disk is at ~60 %) that are
  long (>= 45 px) or touch two sides (a corner) are **no data**, grown 8 px (the aperture's soft edge): the field's black rounded
  corners. A particle cut by the border is short and on one side, so it stays (the black particle at the bottom border below ROI 151's
  column is found). Bug on the way: the side count added numpy booleans (True + True = True), so "two sides" never fired.
- A feature with half its pixels within `border_zone_px` (15) of the border **and** 45 px long runs along the border: the field's dark
  edge (the top-right strip), dropped; a scratch that runs into the border (left, row of ROI 81) stays.
- Nanostructure is judged from 15 px inside the border only, and dirt not at the rim of a disk within that zone: a disk cut by the top
  border showed false arcs right at the cut (its round model sees part of it).
- A disk touching the border starts from its lattice point and is then refined by the radial fit where it can be (< 1.5 px): disk 145
  touches the widened corner and its blob merged with it (centroid pulled; the plain lattice point 0.5 px off gave an arc pair).
- `find_disks`' first radial fit is guarded like the second (a black corner taken as a candidate crashed it).
Real data: masked share 0.6 % in the outer 3 px, 2-3 % at 3-10 px (inside the field ~10-14 %). Test
`test_border_unlit_corner_is_no_dirt_but_a_cut_particle_is` (synthetic black rounded corner + cut particle; without the rule 620 px of
the corner are marked).

**Faint dirt, not masked by default (same day, maintainer: "big blue blobs under ROI 3, between 31/32, 48/64, next to 119 and 101,
inside 49, on top of 65 seem over-sensitive; does masking them make sense?").** Measured: the blobs are real, round, soft grey
patches in the picture, 30-40 px across, 2-5 % of the light (119: up to 9 %): dust out of focus (on the flow cell's window, not the
chip). Static and grey: a constant offset in absorbance, no peak shift and no noise (section 4 found no gain from masking features);
masking one costs a disk's worth of pixels. Their steepest step (98th percentile of the slope of the smoothed dirt map over the
feature + 2 px) is 0.6-1.6 % of light per pixel; particles 3.5-15 %/px; grey inside damaged disks 2-3.3 %/px. Because the optics blur
every edge over ~5 px, this mostly measures depth: faint small specks (~1 %/px) fall with the shadows. So: new kind `DIRT_FAINT`
("Dirt, faint (gentle)", `faint_slope` 1.7 %/px), found and shown, **not in the default mask** (`kinds` defaults to the other four).
Real data: 153 features / 18.7k px moved from dark dirt (now 221 / 25.7k px). All the named blobs are faint except ROI 49's: that one
is inside the disk with steep steps (2.4 %/px, 10.5 % deep) on a disk the map also calls damaged; it is one of the leftmost-column disks
where the image is least sharp, still uncertain. Test `test_soft_shadow_is_faint_dirt_and_not_masked_by_default`; the synthetic 3 %
speck is now faint dirt too.

**Next (not built):** the Mask tab: run the map (worker process: ~70 s on one core, the 26 wavelengths are independent), four kind
switches, the limits in % in the settings popup, which cubes to average (e.g. the plan's baseline step), Apply into the mask (raw
pixels, as now). The existing "ROI features" tool (sections 1-8) is still the one in the app.

**Not done / open**: no per-ROI list in the UI yet (to keep or skip single ROIs; the status line names the ROIs left out, the
findings are in `RoiFeatureTask.result().findings`); the memory limit per worker (`_WORKER_BYTES`, 300 MB) is three times what one
worker measured here (93 MB), on this machine only; a re-run is not started by itself when a number changes (press Process: 1.4 s); not looked at by eye in the running
app (the icon, the popup, the +/- widths of the Manual-edit group).
