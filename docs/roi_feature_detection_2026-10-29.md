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
`image_tools/roi_feature_task.py` (`RoiFeatureTask(ThreadedTask)`: crops, preprocessing without the mask, raw-pixel result),
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

**Not done / open**: no per-ROI list in the UI yet (to keep or skip single ROIs; the status line names the ROIs left out, the
findings are in `RoiFeatureTask.result().findings`); the memory limit per worker (`_WORKER_BYTES`, 300 MB) is three times what one
worker measured here (93 MB), on this machine only; a re-run is not started by itself when a number changes (press Process: 1.4 s); not looked at by eye in the running
app (the icon, the popup, the +/- widths of the Manual-edit group).
