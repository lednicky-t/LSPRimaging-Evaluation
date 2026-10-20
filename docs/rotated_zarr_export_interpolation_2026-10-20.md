# Rotated OME-Zarr export: interpolation measurement (2026-10-20)

Maintainer decision (2026-10-20): the OME-Zarr export becomes a *corrected* dataset, rotated and cropped. This
reverses the same day's earlier "export stays raw pixels" decision (`zarr_chunk_planner_2026-10-19.md`). The chunk
grid then lies in processed space and is drawn straight. Before building it, we measured how much rotation changes
the data. Script: `tools/rotation_interpolation_lab.py`.

## Setup

- Dataset: `04_Bulk_sensitivity_pumpplan_0-70percent_to_LbL_with_water_v1_LED_V2`, 1300 x 900 uint16 TIFF.
- Geometry: the session's own, 1.4 deg rotation with crop (13, 10, 1297 x 806).
- ROIs: 144 of the 160 session ROIs, those fully inside both grids.
- Frames: 9, at wavelengths 500/600/700 nm and cubes 0/156/312.
- Every method uses the app's own transform (`_combined_export_transform`).
- Pixel noise sigma: 486 counts at a level of about 5e4. Measured from a gain-normalised, high-passed difference of
  adjacent cubes.
- Single-frame ROI noise: about 1060 ppm for the sample mean and 890 ppm for the sample/reference ratio.

## Metrics

- **1-pass err/sigma:** RMS pixel difference against a band-limited (FFT sinc, three-shear) rotation at the same
  geometry.
- **roundtrip/sigma:** rotate by +theta, then by -theta. This uses two interpolations, so it is an upper bound.
- **noise kept:** std of rotated pure noise divided by the std before rotation. Values below 1 mean neighbouring
  pixels were averaged. The range is over 32 px blocks.
- **ROI mean / ROI ratio:** RMS relative error per ROI against the same disc (and annulus 1.4r-2r) measured on the
  raw pixels. Area-weighted masks, 8 x 8 supersampling.
- **varying:** the part of the per-ROI ratio error that changes across the 9 frames (wavelength and time).

| method | ms/frame | 1-pass err/sigma | roundtrip/sigma | noise kept (blocks) | ROI mean | ROI ratio | varying |
|---|---|---|---|---|---|---|---|
| cv2 nearest | 7 | 1.02 | 0.28 | 1.02 (0.88-1.10) | 680 ppm | 677 ppm | 472 ppm |
| **cv2 linear (app today)** | 7 | 0.52 | 0.68 | **0.72 (0.60-0.80)** | 716 ppm | 722 ppm | 507 ppm |
| cv2 cubic | 11 | 0.38 | 0.41 | 0.89 (0.77-0.98) | 470 ppm | 474 ppm | 313 ppm |
| **cv2 lanczos4** | **19** | 0.29 | 0.30 | 0.94 (0.81-1.02) | **137 ppm** | **140 ppm** | **84 ppm** |
| scipy linear | 67 | 0.52 | 0.68 | 0.72 | 716 ppm | 722 ppm | 507 ppm |
| scipy cubic spline | 155 | 0.33 | 0.35 | 0.91 | 147 ppm | 147 ppm | 93 ppm |
| scipy quintic spline | 289 | 0.27 | 0.28 | 0.94 | 150 ppm | 150 ppm | 96 ppm |
| FFT sinc (reference) | 703 | 0 | 0.05 | 1.02 | 152 ppm | 152 ppm | 98 ppm |

## Reading

- **Linear (today's display and analysis path) is the weak one.**
  - It removes about 28 % of the pixel noise, and how much varies across the image (60-80 %), with the sub-pixel
    phase. That is a moiré in the noise.
  - Its ROI ratio error (about 720 ppm) is close to the single-frame ROI noise (about 890 ppm).
- **Lanczos4, the splines and FFT all land at about 140-150 ppm.** That is probably the floor of this measurement
  (disc-edge supersampling, and camera images that are not strictly band-limited), so they cannot be ranked against
  each other here. OpenCV's 1/32 px position step does not limit Lanczos4.
- **Lanczos4 is the practical choice:**
  - 19 ms/frame, about 2.7 min single-threaded for the 8478 planes.
  - About 6x below the single-frame ROI noise, and keeps 94 % of the noise.
  - It rings slightly past the source range (about +-40 counts at the extremes), so clip to [0, 65535] when storing
    uint16.
- **uint16 storage is fine for the values.** Rounding adds 0.29 counts RMS against sigma 486. But NaN needs float:
  - The session's crop has 1.23 % NaN corner pixels per frame (outside the rotated raw image).
  - Lanczos4 spreads NaN about 3-4 px further in (its kernel reaches the border).
  - A uint16 export therefore needs the crop inside the valid area, with a margin of about 4 px.

## Side finding (not changed)

The app's own rotated display and analysis use linear interpolation:

- `apply_spatial_preprocessing`: cv2 INTER_LINEAR.
- `resample_raw_patch_to_processed_box`: scipy order 1.

So today's analysis of rotated data carries the linear-interpolation errors above. This needs its own decision.

## Outcome (2026-10-20)

**Lanczos4 is now the app's rotation (maintainer decision).** It is done in one place, `geometry/transform.py`
`_cv2_affine`, so it covers:

- display;
- analysis and scan (`apply_spatial_preprocessing`);
- the old app's export and ROI-patch paths, which call the same helper.

How the warp works:

- It runs with a replicated border.
- Then exactly the pixels whose source lies outside the raw image are set to NaN (`_outside_source_mask`).

Checked on a real frame at 1.4 / -7.3 / 33 deg:

- The NaN sets are identical to the bilinear version's and to scipy's.
- RMS from the quintic spline is 29 counts, against 188 for bilinear.
- It takes 17-25 ms per frame, against about 7 ms.
- An interior NaN hole keeps its bilinear footprint.

Tests re-pinned:

- `test_lspri_rewrite_image_refresh_speedups.py`: compared against a quintic spline, plus the noise kept.
- `test_lspri_rewrite_rotation_fill_phase0.py`: "no zero fill", instead of `min > 0`. Lanczos rings below 0 on white
  noise.

Not changed:

- **The chromatic-correction warp** (`chromatic/warp.py`) is still bilinear. Its per-wavelength sub-pixel shifts have
  the same smoothing issue, so it needs its own decision.
- **The scipy fallbacks** (no OpenCV, or images 4 px or smaller) are still bilinear.

## Cost audit (2026-10-20, maintainer request)

Method:

- One real frame, 1300 x 900 uint16, rotated by 1.4 deg with the session crop.
- Times are the minimum of 60 runs. Medians on the busy machine were unreliable (up to 2x).
- "1 thread" stands for a busy machine: during Play the reader threads decode TIFFs on the same cores.

| step | 8 OpenCV threads | 1 thread |
|---|---|---|
| uint16 -> float32 | 2 ms | 2 ms |
| warp bilinear, NaN border (before) | 1.7 ms | 5-6 ms |
| warp cubic | 6 ms | 18-20 ms |
| **warp Lanczos4** | **16-18 ms** | **37-44 ms** |
| Lanczos4 via `cv2.remap` with cached maps | 17 ms | 53 ms (no gain) |
| outside-source NaN mask | 2-3 ms | 2 ms |
| **whole rotation, before (bilinear)** | **~4 ms** | **~7 ms** |
| **whole rotation, now (Lanczos4)** | **~24 ms** | **~47-52 ms** |

The Lanczos4 kernel itself (64 taps per pixel) is the cost. Caching the NaN mask gave no measurable gain over noise,
so it was not kept. Skipping the NaN scan for integer input was kept: it is free and trivial.

Where rotation runs:

- **Image panel**: once per rendered frame (`render.py`, `apply_preprocessing`). The result also feeds the
  Histogram, the Mask edit tools (threshold, histogram selection), the highlight tint and the cursor readout.
- **Analysis / scan** (`apply_preprocessing_at`), chromatic auto-detect, array detection: once per plane they read.

Impact:

- **Play.** testing9.jsonl shows a cycle of ~46 ms (~21 fps), of which `prep_ms` was 1.3-5 ms. +20-45 ms per frame
  would bring it to roughly 13-15 fps.
- **Full analysis run** (8478 planes). Estimated +20-40 ms per plane: about +3-6 CPU-minutes, spread over the
  worker processes.

## Decision: fast while moving, exact at rest (2026-10-20)

The maintainer chose option A:

- While the slideshow plays, render requests carry `fast_rotation`, a bilinear warp of about 4 ms. These are the
  same NaN pixels as before 2026-10-20 (`apply_spatial_preprocessing(fast=True)`).
- When Play stops, `ImagePanel._on_playback_changed` redraws the current frame with Lanczos4, so the Histogram, the
  Mask tools and the cursor readout work on the exact image at rest.
- Analysis, scan, auto-detect and export always use Lanczos4.

Tests:

- `test_lspri_rewrite_slider_ribbon.py::test_play_rotates_fast_and_the_stopped_frame_is_redrawn_exactly`.
- `test_lspri_rewrite_image_refresh_speedups.py::test_fast_rotation_is_the_bilinear_one_with_the_same_nan_pixels`.

Faster exact rotation, checked and rejected:

- **OpenCL (Intel UHD).** It has no Lanczos kernel, so it falls back to the CPU with identical output. Cubic is no
  faster once upload and download are counted.
- **`cv2.remap` with cached maps.** No gain.
- **Separable three-shear (about 24 taps instead of 64).** It needs a compiled helper (numba, not installed, a large
  dependency). In numpy it would be slower.
- **Machine load.** On an idle machine Lanczos4 measured 9.4 ms per frame (8 threads), against 16-18 ms with a
  browser and video player running.
- **The real saving is the rotated export.** A corrected dataset needs no rotation at all afterwards.

## Rotated export built (2026-10-20)

The format is the one the maintainer approved:

- The stored dtype is the source's (`probe_ome_zarr_export_shape`). Values are Lanczos4 output, rounded and
  clipped (`_zarr_export_worker._to_stored`).
- The pixels the rotation creates are stored as 0.
- One `valid_mask` array (uint8, stored H x W, 1 = measured) sits next to `0`. It is announced in
  `lspr.valid_mask`, and `lspr.image_tools.interpolation` is "lanczos4".
- Readers turn mask-0 pixels into NaN in both read paths: full planes (`_load_image_array_uncached`) and ROI
  patches (`dataset_load_plane_roi`), via `_apply_valid_mask`.
- Re-exporting a masked export without rotation carries the mask over. Rotating it again is refused with a clear
  error, because its stored 0 corners would be interpolated into their neighbours.
- Export tab: the "Rotate/crop" toggle (default on, `export/apply_image_tools`). In processed space the grid is drawn
  straight (`ChunkGridPreview.processed`) and the ROI layout needs no mapping.
- Also fixed in the tab: it took the stored dtype from `load_plane`, which is always float32. As a result the folder
  name said f32, Find best counted 4 bytes per pixel, and it sampled float32 compression.

Real check: 81 planes (3 cubes) of the 04_Bulk dataset, 80 px chunks, lz4.

| export | time | size |
|---|---|---|
| raw | 11.0 s | 118 MB |
| rotated | 10.9 s | 147 MB |

- Read back: the same NaN pixels as the app's rotation, worst difference 0.5 counts, 1.23 % NaN.
- Speed is the same: the rotation runs in the worker processes.

**Why the rotated file is larger:**

- The camera data is 12-bit, scaled by 16: every raw value is a multiple of 16, so the low 4 bit-planes are zero
  and bit-shuffle compresses them away.
- Lanczos values fill those bits.
- Compressed share: raw 50.2 %, rotated 74.0 %, rotated and rounded to multiples of 16 49.7 %.
- Rounding to the camera step adds 4.5 counts RMS of error, against ~490 counts of pixel noise. Open question to
  the maintainer.

**Decision (maintainer, 2026-10-20): round onto the camera step.** Built:

- `_zarr_export_worker.value_step` is the gcd of the first plane's non-zero values (16 here, at most 4096).
- `_to_stored(..., step)` rounds onto it.
- Every plane is checked in the worker. A plane whose source is off the step is rounded to whole counts and counted.
- Metadata `lspr.image_tools.value_step` and `planes_not_on_value_step`, plus a log warning when the count is not 0.
- Find best samples with the same rounding.
- Flip/crop-only exports are not rounded: they copy the values.

Same 81 planes: rotated 104 MB in 9.0 s, against raw 118 MB in 9.2 s (smaller because of the crop). Read back:
the same NaN pixels, worst difference 8 counts (half a step).

Test: `test_lspri_rewrite_ome_zarr_export_metadata.py::test_rotated_12_bit_data_is_rounded_onto_the_camera_step`.
