# OME-Zarr chunk planner (2026-10-19)

Phase 1 of "help the user choose the export chunk size from the ROI layout". Code: `src/lspr_imaging_app/dataset/chunk_plan.py` (pure, no Qt). Tests: `tests/unit/test_lspri_rewrite_chunk_plan.py` (umbrella repo).

## Agreed with the maintainer

- Two buttons later: **Find best** (magnifier next to Chunk size, Export section) and **Auto adjust** (Image tools ribbon: detect array, rotate if needed, crop, carry ROIs along after a prompt).
- Three access patterns, shown side by side: **single ROI** (sweeps/tests), **group** (an array column/row or ROI-table group: column-shaped chunks), **all ROIs**.
- Rectangular chunks allowed (manual square/rectangle toggle; automatic mode picks rectangular only when >= 5 % faster).
- Grid position via **lossless** (pad left/top with fill, < 1 chunk) or **lossy** (keep only the chunk-aligned rectangle around the ROIs), both with a size estimate. The offset becomes the OME-Zarr `translation`.
- Moving a TIFF session onto the exported Zarr: parked, separate exporter, needs its own brainstorm.

## Method (why it is fast and exact)

A chunk is read for a unit when a ROI of the unit touches its column and its row, so for a lattice x and y are searched separately. Per axis, the chunk count only changes where a grid line crosses a box edge, so only those offsets (<= 2 per ROI) are evaluated; among equally good offsets the middle of the widest run is taken (`tolerance` = px of drift the layout absorbs). Axes are combined with the cost model, the winner is re-scored exactly in 2-D. Checked against brute force over every offset in the tests.

## Measured read cost (this PC, zarr v3, one shard per plane, float32, lz4 bit-shuffle)

`0.70 ms per read + chunks x (0.03 ms + 4.15 ms per Mpix)` (fit within 22 %). A chunk's pixels cost as much as its overhead at ~85 x 85 px. Reads crossing a shard cost several ms more (the export keeps a plane or a cube per shard, so ROI reads never cross). Warm OS cache: cold-disk reads weigh bytes more (see the `amplification` column).

## Real data: 04_Bulk_sensitivity..., session 2026-09-27_143549

160 ROIs, 10 x 16, pitch ~79.5 px, read box ~61 px (ring outer 59.6 px), 900 x 1300, 8478 planes.

- **The array is tilted 1.42 deg** (column x drifts ~18 px, rows ~29 px) although the stored `RoiArrayGroup.rotation_deg` is 0.009. As is, no grid gives 1 chunk per ROI (best 1.42). With the ROI centres rotated by -1.42 deg: 79 x 79 gives exactly 1 chunk per ROI (tolerance +-5 px), and the group pattern picks 79 x 776 column chunks, 1 chunk per column. This is what Auto adjust is for.
- Times (rotated): single ROI 0.76 ms (64 px today: 0.87), group 0.98 ms (64 px: 1.9), all ROIs 5-10 ms (64 px: 13). Warm-cache single-ROI reads are dominated by the 0.7 ms per-read cost, so chunk choice matters most for groups and whole-layout reads.
- Chromatic shift over all 26 distinct wavelength affines widens boxes from 61 to up to 69 px: include the affines (`roi_read_boxes(..., affines=)`).
- (Corrected 2026-10-20, see Benchmark: true only for some chunk sizes.) The existing 150 px export is 87 % of raw (most wavelengths 99 %, the darkest 63 %). Size estimate sampled over all wavelengths, ratio applied to real pixels only: within 2 % of the real files. Worth deciding separately whether lz4 is worth its decode time here.

## Benchmark 2026-10-20 (real pixels, real ROI layout; script kept outside the repo)

Real export, one 70 px region from 314 cubes: TIFF 23-32 ms per cube, Zarr whole plane 18-22, **Zarr region 2.1 (x11)**, one slice over all cubes 1.4 (x16).

Layout sweep (30 cubes x 3 wavelengths re-written, warm cache, ms per plane):

| Variant | MB/plane | 1 ROI | 1 column | all 160 ROIs as region reads | whole plane |
|---|---|---|---|---|---|
| TIFF | 2.34 | 26.5 | 26.5 | 26.5 | 26.5 |
| square 32 | 1.54 | 3.9 | 25.6 | 563 | 318 |
| square 64 | 1.49 | 2.8 | 10.3 | 524 | 106 |
| square 128 | 1.46 | 2.8 | 5.0 | 367 | 28 |
| square 256 | 1.47 | 2.2 | 3.2 | 319 | 10 |
| one chunk per plane | 1.45 | 6.2 | 6.4 | 996 | 7.8 |
| planner 'single' 79 aligned | 2.36 | 2.2 | 5.7 | 302 | 66 |
| column 79 x 900 aligned | 1.62 | 2.6 | 3.1 | 365 | 15 |
| square 79, no compression | 2.55 | 1.75 | 3.0 | 259 | 20 |

Findings:
- The big win is region reads at all (x10-15 vs TIFF). Chunk size between 64 and 256 and grid alignment change a 1-ROI read by only ~20-30 %: a ~2 ms fixed cost per read dominates.
- **All ROIs: read the whole plane** (8-30 ms), never 160 region reads (260-1000 ms).
- **Small chunks are expensive for whole-plane reads** through the zarr library (~0.27 ms per chunk: 32 px = 318 ms per plane). The `ReadCost` fit (0.03 ms/chunk, 0.7 ms/read) came from reads of <= 9 chunks and underestimates both terms; recalibrate on the real array shape before trusting predicted ms. Predicted ranking between 64-256 px layouts is within the model error.
- **Compression depends on chunk size, unexplained**: 32/64/128/256 px and 53x791 / 79x900 compress to ~62-70 %; 79 x 79 and 150 x 150 do not compress at all (2.35 MB = raw). The earlier "lz4 barely compresses this data" came from the 150 px export and is wrong in general. `estimate_compression_ratio` (numcodecs, bytes in) does not reproduce zarr's codec path: fix before using it for sizes.
- Uncompressed reads are ~30-40 % faster per ROI than lz4 at ~1.7x the disk size of a compressing layout.

## Option (b) chosen 2026-10-20: suggestions, not optimisation

The maintainer chose to simplify after the benchmark. `chunk_plan.py` was rewritten: the full size search and the
`ReadCost` time model were removed. `suggest_layouts` now returns a short list with exact chunk counts, warnings and
sizes. The list is Columns (recommended), Square of about 2 pitches, Square 256, Rows, and One ROI per chunk; without a
lattice it offers Squares 256 and 128.

- Every grid is aligned to the ROIs (`align_grid`). Sizes are snapped so the pixel count is a multiple of 8.
- `chunk_warnings` also covers sizes the user types.

**Root cause of "79/150 px don't compress":** Blosc bit-shuffle needs a pixel count that is a multiple of 8.

**Export worker bug, fixed 2026-10-20:** `_zarr_export_worker.write_shard` encoded `tile.tobytes()`. Blosc then took
the element size as 1 byte, and *no* export ever compressed (100 % of raw at every chunk size). It now encodes the
array. The format is unchanged and old exports still read. Regression test:
`tests/integration/test_lspri_ome_zarr_export_compression.py`.

Real session, as suggested:
- Columns 80 x 900 give 1.14 chunks per ROI as is, and 1.00 after removing the 1.42 deg tilt (tolerance +-5 px).
- Size with lz4: about 12.2 GB lossless, against 22 GB uncompressed and 13.2 GB for the TIFF stack.

## Export frame and rectangular chunks (backend, 2026-10-20)

The maintainer said backwards compatibility of the export format is not required. `dataset/io.export_ome_zarr_dataset`
gained two inputs: `chunk_shape=(h, w)` and `frame=(x0, y0, width, height)`. The frame is in the exported image's
space, after the image tools. `chunk_plan.ExportFrame` gives it as `(origin_x, origin_y, width, height)`.

How the worker applies the frame: `_zarr_export_worker._place_in_frame` cuts or pads each plane after the image
tools. The new `ShardWriteSpec.frame_x0/y0` fields default to 0, so the stable app is unchanged.

What gets written:
- An NGFF `translation` after `scale` (in um when calibrated), only when the origin is not (0, 0).
- `lspr.chunk_shape_px` = [h, w].
- `lspr.chunk_size_px` = the size for square chunks, 0 for rectangular ones. With 0, the stable app's fast reader
  falls back to plain zarr reads, which handle any chunk shape.
- `lspr.export_frame` = origin, size, source size, `valid_region` (the stored pixels that hold source data), `lossless`.

Readers and helpers:
- The new app's fast reader understands rectangular chunks.
- The summary, compare and folder name know about chunk shape and frame (folder tag `c<w>x<h>`).

Tests: `tests/integration/test_lspri_rewrite_ome_zarr_export_frame.py`.

Padding uses the fill value: 0 for uint16, NaN for float32. For uint16, 0 cannot be told apart from a real 0, so use
`valid_region`. Not yet wired to the UI.

## Export section UI (2026-10-20)

Maintainer decisions:
- **The export stays raw pixels.** Auto adjust (rotation) moves to the parked TIFF->Zarr session work. A rotated
  export needs float32 or a validity mask, and interpolated pixels (`rotation_nan_phase1_inventory_2026-10-03.md`).
- **Chunks are always W x H.** There is no square/rectangle switch.

Built:
- `panels/workflow/dataset_export.py`:
  - Chunk W x H fields with live `chunk_warnings`.
  - Grid position: Image corner / Aligned to ROIs, lossless / Aligned to ROIs, lossy.
  - Chunk counts, plus the pad/trim line.
  - The Find best magnifier button.
  - Remembered state: `export/chunk_size` (width, old key; height defaults to it), `export/chunk_h`,
    `export/grid_position`.
- `panels/workflow/export_roi_layout.py`: ROI read boxes in raw pixels. Chromatic affines are included when the
  correction is on. Boxes are mapped through the inverse image tools (`image_tools.geometry.transform.
  raw_to_processed_affine_xy`, new).
- `panels/workflow/chunk_layout_dialog.py`: the Find best popup. `suggest_layouts` and the compression sampling (3
  cubes x up to 5 wavelengths) run on an `AnalysisWorker`.
- Grid preview: `DatasetModule.ChunkGridPreview` (W, H, offsets) is in raw pixels. The Image panel maps the lines
  onto the displayed image, so they appear tilted or shifted under rotation or crop.

Tests: `tests/integration/test_lspri_rewrite_export_chunk_tools.py`.

**Fixed the same day, after the maintainer's first look:** the table showed Columns at 2.00 chunks per ROI. The
cause: with the chromatic shift, a column of ROIs spans up to 87 px, wider than an 80 px chunk, so every grid offset
tied at 2 chunks per column. The tie-break then took the widest run of offsets, which put a grid line through every
column. Ties now go to the fewest chunks per single ROI (`_best_offset(tie_units=...)`). Columns went to 1.29 and
Rows to 1.46. The headers now say "Chunks / ROI" (they are counts; they were read as ms).

**Not checked visually yet:** the width of the "Grid position" + "Shard" row (the panel has a width budget).

## Open for Phase 2

- `suggest_layouts` takes 40-210 ms on 160 ROIs (plus compression sampling): a worker thread is still advisable.
- Time-dependent geometry (`RoiTimeline`) is not in the boxes.
- Check the export dtype (uint16 when no image tools; float32 with NaN fill after rotation) before passing `bytes_per_pixel`.
- UI: Find best popup, square/rectangle toggle, pad/trim plus `translation` in the export. Then Auto adjust.
