# Rotation fill → NaN: Phase 1 report (removal inventory + NaN audit), 2026-10-03

Task: `TASK_rotation_fill_handling.md` (umbrella repo root). Audit: `rotation_fill_regions_audit_2026-10-03.md` (F1–F8).
**Status: STOP 1. No source code changed.** Phase 0 tests added: `tests/unit/test_lspri_rewrite_rotation_fill_phase0.py` (2 pass, 1 expected-fail for F1 until Phase 4; the xfail fails for the right reason: sample = 583 instead of 1000).
Paths below are relative to `apps/LSPRi/eva/src/lspr_imaging_app/` unless noted.

## 1. Decisions I need from you (short list)

| # | Question | My recommendation |
|---|---|---|
| D1 | Shared copies of `rotation_fill_dark` outside the rewrite (§2.2): `domain/models.py`, `dataset/io.py`, `io/dataset.py`, `storage/workspace.py`, `processing/`, `gui/`. The rewrite's **OME-Zarr export still runs through the stable app's `processing.preprocess`** and writes `rotation_fill_dark` into the export metadata. | Phase 2 removes the option from the rewrite only (`image_tools/`, `panels/`, `roi/`, `roi_geometry_sync.py`, tests). Stable-app files are untouched (out of scope per §6). The export keeps its old behaviour until Phase 7, where we decide its design. |
| D2 | Ignore-mask `np.where(mask, 0, processed)` (`image_tools/preprocess.py:215`) can overwrite a NaN with 0 (see §3, row "Ignore-mask"). | Allow one minimal fix in Phase 2: keep NaN where the pixel is already NaN (`mask & isfinite`). It does not change the ignore-mask 0 sentinel itself (still out of scope). |
| D3 | `cv2.warpAffine` vs SciPy for the export path (§3.1). | Moot while D1 stands (the rewrite does not export through `image_tools/geometry`). If you want the rewrite export moved onto `image_tools/geometry` in Phase 7, cv2 is usable. |

## 2. Removal inventory (§3 of the task)

### 2.1 Rewrite code, to remove in Phase 2/3

| Item | Where | Phase |
|---|---|---|
| `GeometrySettings.rotation_fill_dark` | `image_tools/geometry/model.py:60` (docstring of reasons: `:29`) | 2 |
| `GeometryModule.set_rotation_fill_dark` + undo closures | `image_tools/geometry/module.py:171-188` | 2 |
| `rotation_fill` change reason | `model.py:29`, `module.py:184,188` | 2 |
| Edge-stretch branch (`mode="nearest"` image fill) | `image_tools/geometry/transform.py:33` (`apply_spatial_preprocessing`), `:332` (`resample_raw_patch_to_processed_box`), `:364,:389-392` (`apply_spatial_preprocessing_export`), `_cv2_affine` `BORDER_REPLICATE` `:438` | 2 |
| Fill checkbox, handler, tooltips | `panels/workflow/transforms_settings.py:248-249` (`_fill_checkbox`), `:282` (cluster), `:319-320` (`_on_fill_clicked`), `:367-379` (refresh + tooltip), docstring `:7,25-36,220`, import `QCheckBox :108` | 2 |
| `rotation_fill` special case | `roi_geometry_sync.py:49-54` (comment + reason set) | 2 |
| Tests only for the checkbox / "no remap on fill change" | `tests/integration/test_lspri_rewrite_rotate_tool.py:499-506`, `tests/integration/test_lspri_rewrite_roi_geometry_sync.py:145-` (rename to `…enabled_does_not_remap`, drop the fill half) | 2 |
| `rotation_fill_pixel_mask` + `rotation_fill_mask` parameters | `transform.py:47`; `image_tools/preprocess.py:36,229,237`; `image_tools/background/estimate.py:42,84,89,107,118,133,151,191,211,223,364,377-378`; `roi/detection.py:16,20-27,52,63,187,201` | 3 (after every consumer uses `isfinite`) |
| Tests of those parameters | `tests/unit/test_lspri_roi_detection.py:193-232` | 3 (rewritten to NaN input) |
| Docs mentions | `docs/image_tools_coordinate_spaces.md`, `docs/rewrite_build_log_2026-09.md` (3 hits), `docs/roi_scoped_resample_cv2_fast_path.md` (2), `docs/rotation_fill_regions_audit_2026-10-03.md` (history, keep), `transforms_settings.py` docstring | 2/3 |

**Must stay:** the `mode="nearest"` calls in `spatial_coordinate_maps` (`transform.py:77-78`). They interpolate *coordinate lookup tables*, not image data, and every pixel needs a coordinate. Also all `mode="nearest"` in Gaussian filters (`background/estimate.py`, `landmark_autotrack.py`, `mask/raster_tools.py`) are filter-edge handling at the image border, not rotation fill; the task text lists `mode="nearest"` in "geometry code" only.

**Icons/assets:** none. The fill control is a plain `QCheckBox` with no icon; nothing in `packages/lspr_ui/icon_assets/` or `ICONS.md` is used only by it. (Nothing to delete there.)

**Does anything need the geometric mask instead of `isfinite`?** No. Its only rewrite use is `apply_preprocessing` → background estimate (`preprocess.py:229`). Nothing calls `detect_rois` with it (audit §5.3). `rotation_fill_pixel_mask` can be deleted in Phase 3 as the task plans.

### 2.2 Out-of-rewrite copies (see D1)
`rotation_fill_dark` also lives in the stable/shared code: `domain/models.py:227` (`PreprocessingSettings`), `dataset/io.py:881,922,928,947,990,1677` (OME-Zarr export summary + written metadata), `io/dataset.py` (6), `storage/workspace.py:485,537,616,766` (legacy sidecar JSON), `processing/preprocess.py` (23), `processing/roi_detection.py`, `gui/*`. `dataset/io.py:61` imports `apply_spatial_preprocessing` from `processing.preprocess`, i.e. **the stable copy**. The rewrite's `apply_spatial_preprocessing_export` and `resample_raw_patch_to_processed_box` have **no caller in the rewrite** (only the stable `gui/analysis_tasks.py`, `io/_zarr_export_worker.py` and a stable test use their stable twins).

### 2.3 Migration
- Session files: `storage/session.py:505` decodes via `_decode_settings`, which already drops unknown keys silently. Old `geometry.rotation_fill_dark` loads fine; I will add the one-line log message the task asks for.
- Fingerprint: `compute_cell` stores `asdict(geometry_settings)` in `SettingsSnapshot`, so removing the field changes every fingerprint and stored cells recompute once. To be noted in the build log. (Verified by reading, not by running.)

## 3. NaN audit per consumer

Verified numerically (script run 2026-10-03, synthetic 300×400 float32, 3°/15°/33°):

- **SciPy**: `ndimage.rotate(..., mode="constant", cval=np.nan, order=1)` gives a NaN set **identical** to `rotation_fill_pixel_mask` (diffs: 0/0/0 pixels; counts 13 914 / 63 050 / 114 732). Export-style `affine_transform` gives the same NaN set and identical values to the GUI path (max diff 0.0).
- **OpenCV**: `warpAffine(..., BORDER_CONSTANT, borderValue=nan)` on float32 produces NaN correctly; its NaN set equals SciPy's exactly at all three angles; finite pixels differ from SciPy by ≤ 0.036 counts (interpolation rounding, same as the existing fast-path doc). So cv2 is usable for NaN, contrary to the old constant-fill concern (that was about 0 vs real value ambiguity, which NaN removes). Not needed unless D3.
- **dtype/memory**: the rewrite's loaders already return **float32** (`dataset/io.py:504,510,1804,1825-1834`). Converting before rotation costs nothing; the pipeline is already float32 end to end. Raw uint16 → float32 is exact (checked).
- `nan * 0 = nan` (checked) – this drives the biggest hazard below.

| Consumer | Current behaviour with NaN input | Change needed |
|---|---|---|
| **Image preview** `panels/image/panel.py:1371` (`setImage(image, autoLevels=True)`) | Image is cast to float32 and shown; fill is black/stretched, no marking (F6). With NaN: pyqtgraph autoscale and the colormap's bad-value handling must be verified against the installed version (not yet checked). | Set a bad-value colour/overlay (checker) and compute levels from finite pixels explicitly (do not rely on pyqtgraph). |
| **Cursor readout** `panel.py:2009-2010` | `f"{float(image[row,col]):.1f}"` → would print `nan`. | Print "no data". |
| **Highlight overlay** `panel.py:1548` | `(image >= lo) & (image <= hi)`: NaN compares False, so NaN is never highlighted. Already correct. | None (add a test). |
| **Histogram** `panels/histogram/panel.py:177-178,233`, `compute.py` | **Already NaN-safe**: `total_pixels = count_nonzero(isfinite)`, `population_counts` drops non-finite, percent uses the finite count, highlight seed uses finite min/max. Today fill pixels are counted as data (F3); with NaN they drop out automatically. | Only add the excluded-pixel count display. Bin range is fixed 0–65535 by design (not data-dependent), so the "automatic bin range" wording does not apply. |
| **Background flattening** `image_tools/background/estimate.py` | **Breaks**: `image_f32 * weights` (lines 158, 171, 232) with weights 0 on NaN pixels still gives NaN (`nan*0=nan`), and `gaussian_filter` then spreads NaN over the whole image. `_bin_array_mean` (`:243`) averages NaN into binned blocks. `np.median(image_f32)` fallback (`:162,:175,:233`) would be NaN. Output stage `apply_background` (`apply.py:46-47`) preserves NaN (`np.clip` keeps NaN), so NaN would stay NaN in the invalid region: that part is right. | Validity from `isfinite` merged into the exclusion mask; numerator uses `np.where(valid, image, 0)`; binning on that masked array and on weights; fallbacks over valid pixels only. Add the "NaN stays NaN" and "no spread" tests. Needs the numerics-rule before/after (should be identical to the current mask path on valid pixels; I will quantify). |
| **Ignore-mask steps** `image_tools/preprocess.py:189,193,215` | Write literal **0** into the image. `:215` `np.where(mask, 0, processed)` also **overwrites a NaN with 0** wherever the (chromatically warped) mask reaches into fill (the warp moves it by a few px; rotation itself never puts mask pixels in fill). `:189,193` (raw-space zeroing) are not used by the rewrite (`mask_state=None`, `external_mask_processed=True` in `render.py`/`tasks.py`). | D2: keep NaN under `:215`. Nothing else in this task. |
| **Ignore-mask 0 → statistics** | `compute_cell` removes ignore-mask pixels from sample/reference *when the mask matches the shape* (`tasks.py:365-367`), so 0s do not reach reductions. They **do** reach: (a) the histogram "All pixels" curve (spike at 0), (b) the background estimate unless `flatten_background_exclude_mask` is on, (c) the displayed image. Out of scope per task; flagged only. | None now. |
| **ROI detection** `roi/detection.py` | `ignored_pixel_mask` has no `isfinite` term; `_masked_gaussian_filter` (`:224`) has the same `image * weights` NaN spread; `np.mean(image[valid_mask])` (`:226`) fine once valid is right. Only caller is landmark autotrack, which passes no fill mask (F4). | `valid = isfinite & ~ignored`; use `np.where` in the filter; reject detections that touch invalid pixels; edge-never-detected test. |
| **Landmark auto-detection** `image_tools/chromatic/landmark_autotrack.py:74-82,307,453` | `prepare_registration_image` runs Gaussians/Sobel on the whole image, so one NaN poisons every pixel (and `np.median(band)` → NaN); `detect_rois` is called on it. **Not wired**: nothing in the rewrite calls these functions outside the file itself. | Make `prepare_registration_image` NaN-aware (normalized convolution, mask-aware Sobel) *when it gets wired*; for Phase 3 propose: do the `detect_rois` part now, and document the registration-image part as a known gap with a failing-fast guard (raise on non-finite input). Please confirm. |
| **Chromatic warp** | In the rewrite the chromatic affine is applied to **ROI coordinates and masks** (`rasterize_sample(roi, shape, affine)`, `warp_mask`), not to the image. There is no image resampling at other wavelengths, so there is no NaN-spread-by-warp to handle; each wavelength has its own rotation NaN set, ROIs are rasterized onto it. (Spec row §4 describes an image warp that does not exist in the rewrite.) | None. Wording in the task is moot. |
| **Mask creation tools** `mask/raster_tools.py` (histogram / relative / local-contrast masks) | Gaussians on the image: NaN would spread. Histogram-selection helper `image_tools/preprocess.py:109` already uses `isfinite`. Not called on processed images in the rewrite's wired paths (not found by grep). | Note only. If wired to processed images later, needs the same NaN-aware filter. |
| **Analysis** `analysis/tasks.py:342-367`, `analysis/reduction.py` | No fill exclusion (F1). `np.mean` (`reduction.py:66,98`) etc. would return NaN silently. Plane fit (`:134,:356`) already checks `isfinite(plane_value)` on the *result* only. | Phase 4 as specified. Provisional thresholds + STOP 2. |
| **Result tables / plots / CSV export** | Not yet inspected in detail (Phase 4 item 7). | Will inventory when Phase 4 starts. |
| **ROI geometry sync** `roi_geometry_sync.py` | Unaffected apart from removing the `rotation_fill` reason. | Phase 2. Phase 6 adds the off-canvas/coverage warning. |

### Other places that call `nan_to_num`, fill 0, or `np.mean` over a whole image (rewrite only)
- `nan_to_num`: none in the rewrite outside `analysis/statistics.py` (time-series `filled` arrays for median filters, unrelated to images).
- Image-wide statistics: `background/estimate.py` medians/means (above), `roi/detection.py:226`, `landmark_autotrack.py` (above). No other whole-image `np.mean` on processed images found.
- `apply_background` clips to `[0, 65535]`: a legitimate value ≤ 0 after subtraction becomes exactly 0 (a clip, not a NaN issue; pre-existing; flagged only).

## 4. What I am NOT changing without approval
Ignore-mask 0 sentinel (beyond D2), the stable app, the export design, `mode="nearest"` for coordinate maps and filter borders.

## 5. Next step if approved
Phase 2: float conversion + `cval=np.nan` in `apply_spatial_preprocessing`, `resample_raw_patch_to_processed_box`, `apply_spatial_preprocessing_export`; remove §2.1 "Phase 2" items; session-load log message; Phase 0 pin test switched to NaN set; update/remove the two obsolete tests. Note: between Phase 2 and Phase 3 the background estimate must already handle NaN (otherwise flatten-on would break), so the minimal NaN-safe background change lands in Phase 2 together with the NaN fill, not in Phase 3. Phase 3 then covers display, readout, histogram count, detection and deleting `rotation_fill_pixel_mask`.


## Addendum 2026-10-03 (after maintainer's answers to STOP 1)

1. The fill option is removed from the rewrite and from the rewrite's OME-Zarr summary/metadata. The stable app (`gui/`, `processing/`, `io/dataset.py`, `domain/models.py`, `storage/workspace.py`) is left alone on purpose: it still runs through `run.py` and its consumers are not NaN-safe. **Export format check:** the rewrite's export panel passes no preprocessing, so it writes raw pixels and no NaN can appear. If a rotated export is ever added: its integer target dtype (uint16) cannot hold NaN (a cast would silently give 0), so it must be float32 (exact for uint16) or carry a separate validity mask - decision for Phase 7.
2. The ignore mask no longer writes into the image at all (D2 resolved more strongly than proposed).
3. Every computation in the table above was made NaN-safe, not only ROI detection; see the build log entry of the same date.
