# Auto mode for the Mask tab: finding flowing particles ("transients")

2026-10-16. Exploration only. Nothing in `src/` was changed; the prototype lives in a scratch folder and is not in the repo.
Dataset used: `04_Bulk_sensitivity_pumpplan_0-70percent_to_LbL_with_water_v1_LED_V2` (314 cubes x 26 wavelengths + a dark frame, 900x1300 TIFF, one frame every ~365 ms, a dark-disk nanoparticle array in bright field).

## What the data looks like

- A particle crossing the field is a **dark, sharp object that moves ~170-260 px per frame** (one frame = one wavelength step), so it is in a different place at every wavelength and never overlaps its own previous position (the hair is ~70 px long). It is visible for 3-7 frames in a row; a passage can straddle two cubes (cube N 710/720 nm, then cube N+1 470 nm...).
- Examples found: hair at cube 61, 620-680 nm (7 frames; the 7th is the exit at the right image edge, partly cut); small particle at cube 128, 490-550 nm (7 frames, ~78 px core); cube 160, 630-680 nm (the same event the 2026-09-11 spectral-outlier investigation found through ROI data, `spectral_outlier_detection_investigation.md`).
- Direction is **not fixed**: +x in cubes 61, 125-199, -x in cubes 33, 89, 146-160, 267 (pump plan changes the flow). Estimate from the data, never hard-code.

## The method that worked (prototype, full-resolution, no tuning beyond one threshold)

1. Reference `R` for frame (cube c, wavelength w) = trimmed mean (median of four) of the **same wavelength** in cubes c-2, c-1, c+1, c+2 (nearest four at the dataset ends). Same wavelength cancels the disk pattern, illumination shape and chromatic shift. Median of four = (sum - min - max) / 2, one cheap numpy line.
2. `r = I / R`, divided by its median over the valid area (cancels lamp/exposure drift; gain stayed 0.995-1.006). `r` is the particle's transmittance.
3. Valid area: `R` blurred (sigma ~32 px) above 0.45 x its 90th percentile, eroded a little. Blurring matters: per-pixel `R` would call the dark disks "outside the sample" at strongly absorbing wavelengths (first attempt lost 66% of the field at 600 nm).
4. Smooth `r` with sigma 1 px, threshold `r_s < T`, keep connected components of >= 3 px.
5. (Not yet built) feather = dilate the mask; merge onto the mask in effect; commit as an *individual* mask at that frame.

Noise on clean frames: sigma of `r` 1.1% raw, 0.4-0.7% after smoothing; the worst pixel of a clean frame is >= 0.95. So T = 0.75 ("25% darker") has a huge margin and T = 0.90 is still usable.

## Numbers

- Cube 61: 620-680 nm found, 610 and 690 clean. Cube 128: 490-550 found, 480 and 560 clean. Cube 160: 630-680 found. x positions of the hair: 100, 322, 512, 688, 881, 1062, 1293 px.
- Whole dataset, 8164 frames: **T = 0.75**: 770 flagged frames, 53 runs, 35 fast-moving passages. **T = 0.90**: 1418 flagged frames, 200 runs, 51 fast-moving passages, 97 single-frame runs (mostly noise specks and edge artefacts). Run counts depend on how adjacent runs merge; read them as "dozens".
- Cubes 175-200: a particle is present in almost every frame (one 333-frame run). Cubes 229-231: a large soft shadow (up to 31,500 px), a liquid front or bubble edge, **not a particle**. Local normalisation by a blurred `r` (sigma 24-48 px) shrinks it a lot but does not remove it, so a maximum-size cap is still needed (largest real particle seen: ~900 px).
- Edge artefacts: stationary 5-100 px detections at y = 0-7, x ~1100-1270 (cubes 0, 26, 28, 176, 178, 181).
- Feathering: the dark halo falls off smoothly. Hair, cube 61, 640 nm: area 336 px at r < 0.5, 810 at < 0.75, 1142 at < 0.9; dilating the < 0.75 mask by 2/4/6 px gives 1248/1712/2168 px. Small particle: 48 / 78 / 107 px, dilated 154 / 254 / 372 px.
- **Cost of the full scan**: 8164 frames in 432 s (53 ms/frame) and in 638 s (78 ms/frame, other jobs running). Reading was hidden by 4 reader threads (5 s of waiting); the compute is single-threaded. Obvious speed-ups: compute threads (cv2/numpy release the GIL), stride along the wavelength axis (every 3rd-5th wavelength, expand around a hit; shortest passage seen was 3 frames), 2x2 binning for the screening pass.

## Follow-up the same day: which reference, and how to make it faster (measured on the full dataset)

Reference choice (all on 4x4-binned frames, T = 0.75, 35 flowing passages found by the full-resolution scan):

| Reference | flagged frames (full-res scan: 770) | passages found | frames lost in cubes 175-199 |
|---|---|---|---|
| median of cubes c-2, c-1, c+1, c+2 | 713 | 35 (26 complete) | 25 |
| previous cube only (c-1) | 655 | 35 (26 complete) | 108 |
| brighter of c-1 and c+1, per pixel | 872 | 35 (26 complete) | 3 |
| brighter of c-1 and c-2 (causal) | 866 | 35 (26 complete) | 42 |

- The previous cube alone finds the same passages and all three known events. Its failure: a particle in the *reference* at the same place hides the current one (a dark reference makes the ratio bright), which is why it loses 108 frames in the cube 175-199 particle stream. A contaminated reference can only cause misses, never false dark detections.
- "Brighter of two neighbours" fixes that (a particle in one reference loses to the clean other one) at the price of reading one cube ahead and more 1-bin specks (136 extra frames at T = 0.75, median 16 px, mostly isolated; a min-area of 2 bins removes them). The single-reference noise is higher: at T = 0.90 the previous-only reference gives 422 extra flags against the median.
- A reference must be the **same wavelength** in another cube. The previous *checked wavelength* (stride) is no reference: global intensity alone moves 2-7% per 10 nm step and the disk contrast changes much more.
- Binning 4x4 before everything reproduces the scan: 713 of the 770 flagged frames, 0 extra at T = 0.75; the lost ones are the 3-20 px specks. Compute drops from 53-78 ms to 3.4 ms per frame.
- What is left is decoding: TIFF 18 ms/frame (13 ms with 4 threads; the disk itself does 700-2400 MB/s), 150 px OME-Zarr 13 ms (8.8 ms with 4 threads), 50 px OME-Zarr 42 ms. Measured: full scan 164 s read + 28 s compute for 8164 frames.
- Stride along the wavelength axis, passages caught of 35 (all offsets): stride 2-3 100%, stride 4-5 97% (one 3-frame passage missed at some offsets), stride 6 86%. Passage lengths 3-15 frames, mostly 5-8. A full width crossing at the highest speed (260 px/frame) is >= 5 frames.
- The pump plan cannot be used to skip frames: only 31 of 8164 frames say "Pump is not running".

## Open decisions (need the maintainer)

1. Feather: binary dilation by N px on top of a darkness threshold (masks are boolean). Suggested defaults: sensitivity 10-25% darker, feather 3 px.
2. Large soft events (cube 229-231): flag in the result list but do not auto-mask?
3. Review list ("Apply selected") versus auto-apply everything: a scan can create 250+ individual masks.
4. **Mask storage**: `MaskModule` keeps one full-plane bool array per individual mask (1.2 MB). 300 masks ~ 350 MB; bbox crop or packbits would cut it ~8x or more. This touches storage / the session format, so it needs a check-in.
5. An individual mask *replaces* the persistent mask for that frame, so the auto result must be OR-merged onto the mask in effect (as `MaskModule.apply_candidate` does), not written alone.
6. Undo: only the Mask rows' commands are undoable today (CLAUDE.md). "Apply all found" as one batch needs an explicit OK.

## Not reused

`image_tools/chromatic/landmark_autotrack.py` tracks near-static landmarks by patch matching within a few px; it cannot follow a 190 px/frame jump. The existing `raster_tools.create_relative_contrast_mask` (I / blurred I) and `apply_morphology_to_mask` (dilation = feather) are directly reusable, applied to the ratio image instead of the raw image.
