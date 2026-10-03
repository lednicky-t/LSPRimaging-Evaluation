# Rotation-fill regions: how they are made and how every tool treats them (audit, 2026-10-03)

**Scope:** the LSPRi Evaluation **rewrite** (`rewrite` branch, package `lspr_imaging_app`; app commit `3255dbf`).
**Method:** static code reading plus one throw-away numerical script (synthetic images, results in §6). No GUI was driven. No code was changed.
**Audience:** an outside reviewer with no prior knowledge of the app.

---

## 1. Summary

1. Rotating an image by a non-zero angle makes the pixel grid **larger**. `scipy.ndimage.rotate(reshape=True)` builds a canvas that encloses the whole rotated picture. The empty corners of that canvas have **no measurement behind them**. This document calls them **rotation-fill regions**. They can be 10% of the canvas at 3° and about 49% at 33° (§6).
2. The fill is **not stored anywhere**. It is recomputed from `GeometrySettings` each time. A boolean "this pixel is fill" mask is available from one function, `rotation_fill_pixel_mask()`.
3. The pixels in the fill are given values in one of two ways, chosen by the "Rotation fill" checkbox (`rotation_fill_dark`):
   - **Edge-stretch** (default): copy the nearest edge pixel outward. The result looks seamless but is not data.
   - **Dark**: set them to 0.
4. **Only part of the pipeline knows about the fill mask.**

   | Consumer | Excludes fill pixels? |
   |---|---|
   | Background flattening estimate | **Yes**, unconditionally |
   | ROI detection (`roi/detection.py`) | Supports it, but **no rewrite code path passes it** (see §5.3) |
   | **Analysis: sample/reference pixel selection (`analysis/tasks.py`)** | **No**: finding F1 |
   | Histogram panel | No: finding F3 |
   | Image panel display | Not marked: fill is shown as black or as stretched pixels |
   | Chromatic landmark auto-detection | No: finding F4 |
   | OME-Zarr export | Fill is baked into the exported pixels (by design) |

5. **The main risk:** an ROI whose sample circle or reference ring overlaps the fill gets fill pixels averaged into its spectrum, with no warning (F1). In the numerical check, an ROI centred 1–3 px inside the fill boundary gave a sample mean of 478 where the correct value was 1000.
6. Other tools handle **moving existing ROIs** correctly when rotation changes. `RoiGeometrySync` remaps ROI centres and freeform masks to the new canvas (§5.6). It does not check whether an ROI ends up in the fill or off the canvas (F5).

---

## 2. How the fill regions are generated

All in `src/lspr_imaging_app/image_tools/geometry/transform.py` (pure NumPy/SciPy, no Qt).

### 2.1 Settings (`geometry/model.py`)
`GeometrySettings`: `image_tools_enabled`, `rotation_angle_deg`, `rotation_fill_dark`, `flip_horizontal`, `flip_vertical`, `crop` (`CropDefinition`). Every change to one of them goes through `GeometryModule` (`geometry/module.py`) and emits a `GeometryComputationalChange(reason=...)`. The reasons are `image_tools_enabled`, `rotation`, `rotation_fill`, `flip`, `crop`.

### 2.2 Transform order
`_apply_spatial_transform` (`transform.py:443`): **rotate → flip → crop**, always in that order.
- Rotation uses `ndimage.rotate(reshape=True, order=1)`. The canvas is enlarged to hold the whole rotated image.
- Crop coordinates are in the **rotated+flipped canvas**, not in raw pixels.
- If `image_tools_enabled` is False, the image is returned untouched, so there is no fill and no mask.

### 2.3 What value fills the new corners
`apply_spatial_preprocessing` (`transform.py:22`):
- `rotation_fill_dark=False` → `mode="nearest"` (edge-stretch).
- `rotation_fill_dark=True` → `mode="constant", cval=0.0`.

### 2.4 The fill mask
`rotation_fill_pixel_mask(raw_shape, settings, skip_crop=False)` (`transform.py:47`):
- Rotates an array of ones with `order=0, mode="constant", cval=0`. Every output pixel that comes out `< 0.5` is fill.
- It is **independent of `rotation_fill_dark`**: the same pixels are flagged whether they are shown black or stretched.
- Returns `None` when image tools are off or the angle is ~0. Flip and crop alone never create pixels.
- It is returned in the same processed space as the image. `skip_crop=True` is used while the crop tool is open so the mask matches the uncropped canvas.
- Checked numerically (§6): in dark mode the mask equals the set of zero-valued pixels exactly, so the two cannot disagree.

### 2.5 Other code paths that produce the same fill
- `apply_spatial_preprocessing_export` (`transform.py:349`) is the faster export version. It uses OpenCV only for edge-stretch and SciPy for dark fill (OpenCV disagreed on ~2.6% of boundary pixels for constant fill). The numerical check found 0 differing zero-pixels against the GUI path.
- `resample_raw_patch_to_processed_box` / `raw_bounding_box_for_processed_box` / `combined_transform_for_box` read small patches through the same rotation. They are currently called only from the stable app's `gui/analysis_tasks.py`, not from the rewrite.

---

## 3. Data flow (rewrite)

```
raw plane ──► apply_preprocessing()  [image_tools/preprocess.py:129]
               1. zero masked pixels in raw space (legacy path)
               2. apply_spatial_preprocessing  → rotated/flipped/cropped image  (FILL CREATED HERE)
               3. zero processed-space ignore-mask pixels
               4. if flatten_background_enabled:
                    rotation_fill_pixel_mask(...)  → passed as rotation_fill_mask   (FILL EXCLUDED HERE ONLY)
                    flatten_background(...)
               5. return processed image  (still contains fill values)
                         │
        ┌────────────────┼──────────────────────────────┐
        ▼                ▼                              ▼
  panels/image/render.py   analysis/tasks.py compute_cell    panels/histogram/panel.py
  (display; no fill        (rasterize sample+reference,      (histogram of displayed image;
   marking)                 average; NO fill exclusion)       no fill exclusion)
```

The fill mask is built **inside** `apply_preprocessing` and used for the background step only. It is not returned, so a caller cannot reuse it without recomputing it with `rotation_fill_pixel_mask()` itself.

---

## 4. Provenance / invalidation

- `compute_cell` stores `asdict(geometry_settings)` in each `SettingsSnapshot` (`analysis/tasks.py`, snapshot block). That includes `rotation_angle_deg`, `rotation_fill_dark`, `flip_*`, `crop`. Changing the fill mode therefore changes the fingerprint, and stored analysis cells become stale and are recomputed. Verified by reading, not by running.
- `RoiGeometrySync` deliberately does **not** move ROIs for `rotation_fill` or `image_tools_enabled` (`roi_geometry_sync.py:54`), because neither moves a coordinate. This is correct.

---

## 5. Per-tool behaviour

### 5.1 Image panel (display), `panels/image/render.py:203`
Calls `apply_preprocessing`. Fill is shown as black (dark mode) or stretched edge pixels (default). **No overlay, hatching or border marks the fill region.** In edge-stretch mode a user cannot tell fill from data by eye.

### 5.2 Background flattening, `image_tools/background/estimate.py` (`_combined_exclusion_mask`, ~line 377)
Fill pixels are OR-ed into the exclusion mask **unconditionally**, even if the "exclude mask" toggle is off, so they never pull on the local background average. An optional `flatten_background_exclusion_dilation_px` widens the exclusion by N pixels to catch boundary pixels. Correct behaviour. **Coverage gap:** the existing unit tests for the fill mask import the stable app's copy (`processing/preprocess.py`, `PreprocessingSettings`). Nothing found tests the rewrite's copy (`image_tools/...`) of the mask or of this exclusion.

### 5.3 ROI detection, `roi/detection.py`
`ignored_pixel_mask` / `detect_rois` accept `rotation_fill_mask` and always exclude it (tested, `tests/unit/test_lspri_roi_detection.py:193-232`). **However, in the rewrite nothing calls `detect_rois` with that argument.** The only caller is `image_tools/chromatic/landmark_autotrack.py:307,453`, which passes none (F4). The rewrite's own "detect ROIs" UI path was not found: `RoiToolbox.detect_rois` takes already-detected ROIs. When detection is wired in, the caller must pass the fill mask or the protection in this file is unused.

### 5.4 Analysis, `analysis/tasks.py::compute_cell` (F1)
Sequence per wavelength: `apply_preprocessing` → `rasterize_sample` / `rasterize_reference` → remove sample-aperture union from the reference ring → remove user ignore-mask pixels → average (`reduce_sample_and_reference`). `analysis/` contains **no** reference to the fill mask, and `analysis/reduction.py` does no zero/NaN filtering. So fill pixels inside an ROI aperture reach the mean/median/trimmed-mean/plane-fit.

### 5.5 Histogram panel, `panels/histogram/panel.py::_redraw`
Histograms the displayed image (`np.isfinite` is the only filter), so fill pixels add a spike at 0 (dark) or duplicate edge values (stretch). Affects the "All pixels" curve and the percent/normalised Y-scales (F3).

### 5.6 ROI repositioning on rotation, `roi_geometry_sync.py` + `geometry/transform.py:159`
On `rotation`/`flip`/`crop`, `RoiToolbox.remap_all` moves every ROI centre, per-wavelength override and freeform mask with the exact affine between old and new processed space. The author's checks: round-trip to 1e-14 and < 0.22 px against the real renderer. Mask-shaped ROIs whose warped mask is empty are reported, not replaced. Circles and annuli move by centre only. Rectangle/polygon shapes are reported as unsupported.
- **Not handled:** an ROI that lands in the fill, or off the canvas after a crop, is not flagged (F5). Example from §6: raw point (10, 10) with rotation 20° plus a crop at (100, 100) maps to (−87, 43).

### 5.7 Ignore masks, `image_tools/preprocess.py::resolve_external_mask`
Masks are authored in raw space and rotated/flipped/cropped with `apply_spatial_mask` (nearest-neighbour, constant 0). Fill pixels therefore come out as "not masked". Chromatic warp is applied afterwards, in processed space. This is the correct order. A user cannot paint a mask inside the fill, since it does not exist in raw space.

### 5.8 Chromatic correction, `image_tools/chromatic/*`
The chromatic affine is defined in processed space, so landmarks, models and ROI rasterization at other wavelengths all live in the rotated canvas. Auto landmark detection (`landmark_autotrack.py`) runs `detect_rois` on the processed image without the fill mask (F4).

### 5.9 OME-Zarr export, `dataset/io.py`
When image tools are enabled the export writes rotated/cropped pixels, fill included, to a new folder. Source TIFFs are never modified. Fill is not marked in the exported file. (Export code was read only for the spatial-transform call; not audited in depth.)

---

## 6. Numerical check (script run 2026-10-03; synthetic 300×400 image)

| Check | Result |
|---|---|
| Fill fraction of canvas at 3° / 15° / 33° | 10.4% / 34.6% / 49.0% (canvas 321×415 / 393×464 / 469×499) |
| Dark mode: pixels equal to 0 but not in fill mask | 0 at all three angles |
| Dark mode: fill-mask pixels that are not 0 | 0 at all three angles |
| Export path vs GUI path, 20° dark | same shape; 0 differing zero-pixels |
| Flat image of 1000; ROI (sample r=6, ring 12–18) centred 1–3 px inside the fill edge, 20° dark | sample 113 px, **59 in fill**; ring 572 px, **288 in fill**. Mean with fill **477.9** (sample) and **496.5** (ref), without fill **1000.0** for both |
| Same ROI, edge-stretch, image with a 1.0→1.5 gradient | with fill 1498.5, without 1496.9 (small here only because stretch copies real values) |
| Remap of raw (10,10), 20° rotation | (12.7, 142.7) on a 419×478 canvas (correct, inside canvas) |
| Remap of raw (10,10), 20° rotation + crop (100,100,150,100) | (−87.3, 42.7): off-canvas, no warning |

The ROI in row 4 is an extreme placement chosen to straddle the boundary. It shows the mechanism, not the typical size of the effect. Typical ROIs far from the fill are unaffected. The ratio sample/reference is what ends up in absorbance, so a partly-filled sample and a partly-filled ring can partly cancel or can bias, depending on geometry.

---

## 7. Findings

| ID | Severity | Finding | Where |
|---|---|---|---|
| **F1** | High (priority 1: correctness) | `compute_cell` does not remove fill pixels from the sample and reference masks. Any ROI aperture that overlaps the fill is silently averaged with synthetic values. In dark mode the bias is toward 0. | `analysis/tasks.py:342-367` |
| F2 | Medium | The fill mask is built inside `apply_preprocessing` for the background step and not returned. The fix for F1 has to recompute it (`rotation_fill_pixel_mask(raw.shape, geometry_settings)`, cheap, cacheable per geometry). | `image_tools/preprocess.py:229` |
| F3 | Low | The histogram counts fill pixels as data. | `panels/histogram/panel.py::_redraw` |
| F4 | Medium | Chromatic landmark auto-detection calls `detect_rois` without the fill mask, so a bright stretched edge or the fill boundary step could be picked as a landmark. | `image_tools/chromatic/landmark_autotrack.py:307,453` |
| F5 | Low/Medium | No warning when ROIs end up in the fill or off the canvas after a rotation or crop edit. | `roi_geometry_sync.py`, `RoiToolbox.remap_all` |
| F6 | Medium (UX) | Fill is not visually marked in the Image panel (especially edge-stretch). | `panels/image/render.py` |
| F7 | Low (test coverage) | The rewrite's `rotation_fill_pixel_mask` and its use in background exclusion have no direct test. The tests that exist target the stable app's copy. Rewrite tests only cover the checkbox and the "no remap on fill change" rule. | `tests/unit/test_lspri_preprocess.py` (old copy), `tests/integration/test_lspri_rewrite_roi_geometry_sync.py:145` |
| F8 | Info | The mask uses `order=0, mode="constant"`. In the author's own notes SciPy treats a coordinate just below 0 as outside, so the mask can flag up to about half a pixel of real data along the rotated edge. This errs on the safe side. The optional dilation setting adds a margin for background only. Not independently measured beyond the zero/mask equality in §6. | `transform.py:47` |

### Suggested fix direction (not implemented, needs maintainer approval)
- **F1:** in `compute_cell`, compute `fill = rotation_fill_pixel_mask(wl_input.raw_image.shape[:2], wl_input.geometry_settings)` and do `sample_mask &= ~fill; reference_mask &= ~fill` next to the ignore-mask lines. This changes computed values for affected ROIs, so per `.claude/rules/numerics.md` it needs a before/after comparison on real data. It also needs a decision on a minimum covered-pixel fraction (what to do when most of a ROI is fill: NaN or keep).
- The fill geometry is already part of the provenance fingerprint (§4), so stored cells would recompute correctly after the change.
- **F3, F4, F6:** the same `rotation_fill_pixel_mask` call can feed each (histogram exclusion, landmark detection, a translucent display overlay).

## 8. What this audit did not cover
- The stable (old) app's equivalent handling, other than noting its `rotation_fill_mask` plumbing exists (`gui/analysis_tasks.py`, `gui/mask_controller.py`).
- Real measurement data (only synthetic images were used).
- The rewrite's future "detect ROIs" and ROI-editor tools; they were not found wired in.
- Export-file content beyond the one spatial-transform call.

## 9. Related documents
- `docs/image_tools_coordinate_spaces.md`: why image tools are not display-only.
- `docs/roi_scoped_resample_cv2_fast_path.md`: the OpenCV fast path and its measured difference.
- `docs/rewrite_build_log_2026-09.md`: entries on ROI geometry sync, rotate tool, and background exclusion.
- `docs/whole_app_bug_audit_2026-09.md` §3.2: earlier mention of ROIs over rotation-fill pixels producing huge absorbance values (stable app).
