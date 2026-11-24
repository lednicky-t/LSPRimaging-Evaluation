# Rim size, rim distance and the noise of the final value (2026-11-24)

Question (maintainer): is it scientifically OK to compare sample / reference ROIs whose rims have different sizes, and does a bigger rim give a better signal-to-noise ratio and less noise in the final value?

Short answer: the size of the rim (its area) made **no measurable difference** to the noise of the final value, so a bigger rim is not a free gain. What matters is the **gap**: how far the rim sits from the sample's edge. A rim that touches or nearly touches the disc has less noise but a **biased** peak position; a rim far from it has more noise. The default gap (about 7.9 px) is a good compromise. Nothing in the app was changed.

## What was measured

Read-only, in memory, through the app's own scan code (`analysis/scan_jobs.build_scan_plan`, `analysis/scan.scan_cubes`). Nothing was written to the datasets or sessions. For each chosen ROI, copies of it were scanned with different rims (the copy has a new id, the same centre and disc, another `Border`), at the same time as the real neighbours, so the reference ring loses the same sample pixels as in a real scan (`exclude_all_sample_rois`). The final value is the app's own: `query.fit_and_metric` on `log10(Ir / Is)` with the session's `MetricSettings`.

**Noise** = the standard deviation of the difference of neighbouring cubes divided by sqrt(2) (a slow drift cancels, white noise stays). A robust version (median absolute deviation) was computed too and agrees.

**Pipeline check** (both datasets): with the stored ROI layout the in-memory scan reproduces the stored `scan.h5` exactly where no pixel is masked: LbL 4,368 cells, Bulk 18,720 cells, identical pixel counts, zero difference of the means. (The stored `scan.h5` files are older than the sessions: the LbL one has other ROI positions and fewer masks, so the tests use the current session, as a new scan would.)

Two rim families per ROI:
- **A**: gap kept at the ROI's own, rim area = k x the sample disc area, k = 0.25, 0.5, 1, 2, 4, 8 (thickness solved for the area).
- **B**: rim area = 1 x the disc, gap = 0, 3, 15, 25, 40 px (and a few more in the LbL run).

### Dataset 1: LbL, ROI 75

`05_LbL_pumpplan_..._60cycles_v2..._u16.ome.zarr`, session `2026-11-20_174644`. ROI 75 at (601, 439), disc 34.2 px (920 px), default rim gap 7.9 px / thickness 5.3 px. Step 1 = cubes 1-144 (144 cubes; the plan's step 1 start/end times). Fit: polynomial order 3, centroid of the top 20 %. Final-value noise about 0.20 nm.

### Dataset 2: Bulk sensitivity, 12 scattered ROIs

`04_Bulk_sensitivity_pumpplan_0-70percent_to_LbL_with_water_v1_LED_V2/images/images_1275x799_314x27_c80IZ_u16.ome.zarr`, session `2026-10-22_172035` (the newest; the `images.ome.zarr` folder itself only has an old-format session). 12 circle sample ROIs on a 4 x 3 grid over the field: 34, 66, 130, 22, 86, 134, 26, 74, 138, 31, 94, 142. Step 1 (H2O) = cubes 2-31 (30 cubes; the plan's edited start time). No masked pixels in these ROIs in this step. Fit: the session's polynomial order 11, centroid of the whole curve. Final-value noise 0.03-0.05 nm per ROI.

## Results

**Noise of the final value, relative to the same ROI's A k=1 (default-size rim):**

| Rim area / sample area (default gap) | LbL ROI 75 | Bulk, 12 ROIs (geo-mean, 95 % interval over ROIs) | Pixel-count model |
|---|---|---|---|
| 0.25 | 0.87-0.90 | 1.08 (1.03-1.14) | 1.57 |
| 0.5 | 0.94 | 1.05 (0.99-1.10) | 1.22 |
| 1 | 1.00 | 1.00 | 1.00 |
| 2 | 1.10 | 0.99 (0.95-1.03) | 0.87 |
| 4 | 1.20 | 1.03 (0.98-1.07) | 0.79 |
| 8 | 1.13-1.16 | 1.01 (0.96-1.07) | 0.75 |

The model is sqrt(1/N_sample + 1/N_rim) (independent pixels). Neither dataset follows it; the two disagree in sign, so the honest reading is "no dependence on area within about +-10 %".

**Same rim area (1 x), different gap** (noise relative to the default gap; peak position offset in nm; absorbance level offset):

| Gap | LbL ROI 75 noise | Bulk noise | Bulk position offset (median [min, max]) | Absorbance level (Bulk) |
|---|---|---|---|---|
| 0 px | 0.82 | 1.04 | -0.29 [-0.77, +0.28] | -0.064 (and wavelength-dependent) |
| 3 px | 0.78 | 0.84 (9 of 12 ROIs better) | **-0.81 [-1.09, -0.41]** (LbL: -1.46) | -0.014 |
| 7.9 px (default) | 1.00 | 1.00 | 0 | 0 |
| 15 px | 1.25 | 1.12 | -0.02 [-0.18, +0.29] | 0.000 |
| 25 px | 1.25 | 1.16 (0 of 12 better) | -0.04 [-0.16, +0.06] | 0.000 |
| 40 px | 0.79 | 0.96 | -0.28 [-0.48, -0.11] | -0.003 |

**Peak position vs rim size at the default gap:** median offset within 0.03 nm, but single ROIs move by up to +-0.1-0.2 nm in the Bulk set (several times the 0.03-0.05 nm noise there). It is a constant offset of that ROI, so it cancels in a shift against the same ROI's baseline, but not when comparing absolute peak positions between ROIs with different rims.

**Why more pixels do not help:** the noise of the rim's mean is 1.7-4 x what independent pixels would give (Bulk, per ROI), and 3.9 x for LbL ROI 75 (1.33e-3 against 0.34e-3). The rest is noise shared across pixels. The correlation between the cube-to-cube changes of the sample and of the rim (median over ROIs, Bulk): 0.85 for a rim touching the disc, 0.66 at the default, 0.43 at a 25 px gap. A near rim cancels the shared fluctuations better; a far rim less. In LbL ROI 75 the far rims were even anti-correlated (about -0.5); the Bulk ROIs did not reproduce that, so it is specific to that ROI.

## Interpretation (not proven)

- A rim at 0-3 px picks up the particle's own halo (light of the particle outside its disc): less noise, but the spectrum is changed (absorbance level and shape) and the peak moves by up to about -1.5 nm. A worse error than the noise it saves.
- The 40 px rim sits just outside the neighbours' discs (lattice pitch about 80 px); its negative offset is probably their halos. Not tested.
- Why far rims (15-25 px) are noisier: the shared fluctuations are less common to the sample. Mechanism (focus or sub-pixel registration jitter moving light between disc and surroundings was one idea) not tested.

## Limits

- One ROI (LbL) and 12 ROIs (Bulk); first step only (no solution change, so signal-dependent halo effects are not probed).
- 30 cubes per Bulk ROI give about +-13 % on one ROI's noise; the intervals come from pooling ROIs.
- The two datasets use different fit settings, so compare the ratios, not the absolute noise.
- Only a ROI's **own** rim was studied (the scan's reference side). Linked references (pooled by pixel, `analysis/reference_pairing.py`) were not.

## Reproduce

`tools/rim_size_study/` (paths to the datasets are written into the scripts; they only read):
- LbL ROI 75: `python rim_scan.py OUT.npz 75 144 1` (output, ROI id, number of cubes, first cube), then `python rim_analysis.py OUT.npz`.
- `rim_scan2.py DATA SESSION OUT.npz FIRST_CUBE N_CUBES auto:12 WIDTH HEIGHT` for the scattered-ROI run, then `python rim_analysis2.py OUT.npz SESSION/session/session.json`.
- `validate_pipeline.py`: the exact-match check against the stored `scan.h5`.

The scripts build the app's modules through `tools/analysis_stage_timing.build_modules`, which keeps autosave suspended (a tool must never save into the real session).

## Consequence for the app

None built. The default rim (gap about 7.9 px, area about 1 x the disc) stays. If a rim-size or rim-gap control is ever added, the gap deserves a warning (a rim closer than about 5 px biases the peak), the area does not need one.
