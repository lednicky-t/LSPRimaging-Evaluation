# Image panel refresh performance analysis (2026-10-11)

Analysis only, no code changed. Trigger: slider changes in the Image panel feel slow.
Measured with `scratchpad/bench_render.py`, `bench_rot.py`, `bench_lm.py` (not committed) on the real
dataset `04_Bulk_sensitivity_...` (TIFF, 900x1300 float32, 314 cubes x 27 wavelengths), this machine.

## Pipeline per slider move
`_schedule_redraw` (100 ms coalesce) -> `ImagePanel._redraw` (GUI thread: overlays, mask tint, snapshot)
-> `ImageRenderer` thread (load plane, `apply_preprocessing`) -> `_on_rendered` (GUI thread: setImage,
no-data, tint, `image_rendered` -> Histogram `_redraw`).

## Measurements
| Step | Thread | Cost | Notes |
|------|--------|------|-------|
| Plane load, cold (TIFF) | worker | 62-92 ms | warm lru hit 0.1 ms; cache = 96 planes (~3.5 cubes of 27) |
| Rotate 3 deg (scipy `ndimage.rotate`) | worker | 180-235 ms | no rotation / crop only: ~0 ms |
| Background flatten (binned cv2) | worker | ~51 ms | already optimised |
| `resolve_external_mask` with rotation | **GUI** (`_update_mask_overlay`), worker, **GUI** (Histogram `_resolve_ignore_mask`) | ~120 ms each, **3x per frame** | 6 ms without rotation; only when a mask exists |
| Landmarks "all wavelengths" overlay | **GUI**, every redraw | 45 ms (10 landmarks) - 160 ms (40) | result does not depend on current wavelength |
| Highlight tint (build RGBA + setImage) | GUI | ~55 ms | only when highlight overlay visible |
| Histogram population counts | GUI | ~35 ms+ per population | 1-3 populations |
| `_on_rendered` finite copy, min/max, no-data | GUI | ~10 ms | |

## Findings, ranked by payoff
1. Mask resolved up to 3x per frame, twice on the GUI thread (~120 ms each with rotation). Same inputs every
   time, so a small cache keyed on (mask identity, geometry settings, warp affine) removes all but the first.
   Numerically identical. Also breaks the "never compute on the GUI thread" intent.
2. Landmark all-wavelength overlay is redrawn on every slider move though it does not change with
   wavelength. Draw only when the landmarks / chromatic model / scope / correction toggle change.
   Per-point `QBrush` lists are the cost; grouping points per wavelength color would also cut setData.
3. Rotation: `apply_spatial_preprocessing_export` (cv2 `warpAffine`, already used for export) takes
   5 ms vs 205 ms; same shape, identical NaN set, max |diff| 0.31 counts on white noise of ~3000 (worst
   case; real images are smooth). NUMERIC CHANGE on a path analysis shares: needs maintainer approval and
   a before/after on real data. Same idea for mask (order 0) rotation: 101 ms scipy.
4. Cache the geometry stage (rotated/flipped/cropped plane) per (cube, wl, geometry), so changing
   background sigma / ROI / mask does not re-rotate. Smaller win if (3) is adopted.
5. Neighbour prefetch: cold load 62-92 ms dominates when no rotation is set. Prefetch the viewed cube's
   other wavelengths (plain `threading.Thread`, never QThreadPool; zarr rule) after the visible frame
   renders, and bump the plane lru to cover at least 2 cubes plus the reference cube. RAM: 3.5 MB/plane.
6. Processed-frame cache (final output per cube/wl/all settings): biggest sweep win, but invalidation must
   cover ROI exclusion + mask + background + geometry + chromatic; do only after 1-5, with a settings
   fingerprint, and a size cap.
7. Smaller: Histogram recomputes all populations although `_on_image_rendered` already has finite pixels;
   `_on_rendered` copies finite pixels twice; `RenderRequest.rois` rebuilt each redraw.

## Not measured (offscreen Qt cannot show it)
pyqtgraph `ImageItem` float32 -> LUT conversion at paint time, and real ROI-overlay cost with many ROIs.
Suggest DEBUG stage timing in `_redraw` / `_on_rendered` (one line per frame) before tuning these.

## Implemented the same day (maintainer: "go with 1-5, and OpenCV everywhere")

| # | Change | Where |
|---|--------|-------|
| 1 | Resolved ignore mask cached by content (blake2b of the mask + geometry fingerprint + warp matrix), returned read-only; mask tint is not rebuilt when the resolved mask object and colour are unchanged | `image_tools/preprocess.py` `resolve_external_mask`, `panels/image/panel.py` `_update_mask_overlay` |
| 2 | Landmark overlay redrawn only when its key changes (visible, scope, frame for the current-wavelength view, chromatic/geometry/dataset version) | `panel.py` `_draw_landmarks`, `_invalidate_landmark_overlay` |
| 3 | OpenCV rotation for display/analysis/mask/coordinate maps (below) | `image_tools/geometry/transform.py`, `image_tools/chromatic/warp.py` |
| 4 | **Not done, on purpose**: with OpenCV the rotate/flip/crop stage costs ~5 ms (0 ms without rotation), so a per-frame geometry cache would spend ~4.7 MB per frame to save less than a frame's paint time. Revisit only if profiling shows otherwise. | |
| 5 | Read-ahead of the viewed cube's other wavelengths (nearest first) and the same wavelength in cubes +-1, +-2, done by the render thread while idle (0.25 s quiet period, a render request pre-empts after at most one plane); plane cache 96 -> 160 | `panels/image/prefetch.py`, `panels/image/render.py` `prefetch`, `dataset/io.py` |
| - | Highlight/mask tint RGBA built with one 32-bit write per pixel | `panels/image/overlay_tint.py` |

### OpenCV numeric impact (real frames, 3 cubes/wavelengths, 900x1300, shot noise ~216 counts)
- Image rotate/flip/crop (all cases tried, incl. skip_crop): same shape, same NaN pixels, max |diff| 1.95 counts (0.9 % of shot noise); 5 ms vs 205 ms.
- Ignore mask (nearest neighbour): differs from scipy only at the image border / rotated edge (scipy treats a source position within half a pixel of the edge as outside, OpenCV as inside): 1101 of 53130 px (2 %) in an artificial mask that had content on the image border; 0-1 px for masks away from the border.
- Coordinate maps (mask edit mapping): now evaluated analytically from the transform, max |diff| 0.0000 px; 76 ms.
- `spatial_output_shape`: no longer rotates a probe image; identical to the old result at 438 angle/flip/shape combinations (73 angles x 2 flips x 3 shapes).
- Chromatic mask warp (`warp_image_affine`, order 0 only): 12.7 ms; differs from scipy at 1669 of 52642 mask pixels, of which only 2 on the outermost rows/columns, i.e. the rest are rounding of the half-pixel tie at the mask edge (OpenCV rounds the source position, scipy treats ties differently). Order 1 stays on scipy (OpenCV differs at the image edge, measured; nothing in the rewrite uses it).

### Where scipy is still used (and why)
- `roi/rasterize.py` `expand_mask_to_patch_warped`: small ROI-box warp; its docstring records scipy's exact border behaviour and a 4-px pad built around it. Left alone (analysis path, tiny arrays).
- `image_tools/chromatic/landmark_autotrack.py` `ndimage.zoom` (two calls): landmark tracking; changing the resampler would move tracked landmark positions. Candidate for `cv2.resize(INTER_AREA/LINEAR)` after a tracking before/after.
- `roi/array_detection.py`, `roi/edge_size.py` `ndimage.map_coordinates`: sample at scattered points, no image-sized resampling; OpenCV has no equivalent. Fine.
- Tiny images (min side <= 5 px) and non-2-D arrays keep the scipy rotate in `_apply_spatial_transform`.
- Old app packages `gui/`, `processing/`, `io/`: untouched (not part of the rewrite).
