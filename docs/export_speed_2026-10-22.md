# OME-Zarr export speed (2026-10-22)

Maintainer question: can the export be made faster, are there bottlenecks to overcome or mitigate? Outcome: two shortcuts
built (maintainer's go-ahead, "ideas 1 and 2"), export statistics added to the status bar and to the "open the exported
dataset?" box. Nothing the export writes has changed: **all 387 files of three real exports are byte-for-byte identical to
the last commit's** (shards, valid mask and metadata).

Machine: i7-10510U, 4 cores / 8 threads, Windows. Data: `04_Bulk_sensitivity_...` (1300 x 900 uint16, LZW TIFF with 300
strips of 3 rows each, rotation 1.42 deg, crop 1297 x 806, 80 x 80 chunks, lz4). Scripts were throw-away (session scratchpad).

## Where one plane's CPU time goes (before; one thread, warm cache, quiet machine)

| Step | Share |
|---|---|
| Rotation (Lanczos4 warp + outside-source mask) | 56 % |
| TIFF decode (tifffile; libtiff does the same in 27 ms against 56) | 28 % |
| Camera-step check (`src % 16 == 0` over the plane) | 5 % |
| Round / clip to uint16 (`_to_stored`: where, divide, rint, multiply, clip, astype) | 5 % |
| Encode 187 tiles (lz4 + bitshuffle) | 5 % |
| Write the shard | 1 % |

## What was found not to matter (measured, 3 rounds, cold files)

- **Worker count** (6, 8, 12 processes): 27-29 planes/s, flat. **OpenCV threads per worker** (1, 2, 8): flat. The pool is
  CPU-bound on 4 physical cores; only less work per plane helps.
- **The adaptive worker tuning** (`ADAPTIVE_*` in `dataset/io.py`, on by default, first check after 1 GB written): it counts
  decode and rotation as "I/O" (`read_s`), so on a CPU-bound export it should grow the pool to 2 x cpu_count = 16. 16 workers
  measured the same as 8 (27.6 against 27.7 planes/s) and cost memory. **Not changed** (not part of the go-ahead); a candidate
  for switching off by default.
- **Rotating the uint16 data directly** (cv2 Lanczos on uint16): bit-identical but its warp is slower than float32's, so no gain.
- **Worker start-up** looked like 12 s in the first run; that was a stray test process of mine burning a core. It is about 4-6 s.

## What was built

1. **libtiff decode in the export workers** (`io/_zarr_export_worker.py` `_load_image`). The app already had a verified libtiff
   path for the render thread (`dataset/io.py` "fast TIFF decoding": `use_fast_tiff_decoder`, `_read_tiff_fast`); the workers
   did not use it. Same pixels (checked on 5 files spread over the set, and each worker process checks its own first 3 files
   against tifffile, switching the decoder off on any difference), plain 2-D images only, tifffile for everything else,
   `LSPR_TIFF_DECODER=tifffile` switches it off. 56 -> 27 ms a plane.
2. **Cheaper step check and rounding.**
   - `on_value_step`: for a power-of-two step the OR of all values has its low bits clear exactly when each value does (one
     pass, 4 ms instead of 12).
   - `transform.apply_spatial_preprocessing_export_stored`: divides the values by the step *before* the warp (a power of two
     scales every float32 number exactly, so the warp sees the same numbers, smaller), takes the matrix and the outside-source
     mask from a small cache (`_export_plan`, once per export, not once per plane), zeroes the pixels with no source directly
     and rounds with a plain `rint`. It returns `None` for anything it does not cover (float input, signed target dtype, a
     non-power-of-two step, no rotation, tiny images, image tools off) and the worker then takes the general route unchanged.

## Measured

| | before | after |
|---|---|---|
| Real `export_ome_zarr_dataset`, 378 planes, steady rate (median of 4, warm cache) | **30.6 planes/s** | **37.6 planes/s** (+23 %) |
| Fixed cost (start-up, value step, valid mask, metadata) | ~5.6 s | ~6.0 s |
| Full 8478 planes, extrapolated | ~4.7 min | ~3.9 min |
| Output files vs the last commit | | identical, 387 of 387 files, 3 pairs |

An earlier benchmark with a copy of the new code inside a pool gave +35 %; the real function gives +23 %. The machine's speed
drifted by up to 3x between rounds of the same configuration (heat or power limits, or Defender scanning the many new files:
not established), so the comparisons are alternating pairs and medians, and absolute numbers should not be quoted.

## Tests

`tests/unit/test_lspri_export_fast_paths.py` (17): the fused route equals the general one over 6 angles x 4 flip combinations x
crop on / off x 4 data / step cases (192 comparisons, extremes 0 and 65535 included); it declines what it should; the bit test
agrees with the modulo check (unsigned, signed, float); the worker's decode equals tifffile (and falls back when switched off
or for a multi-page file); **a whole shard written with the shortcuts equals the general route's shard byte for byte**;
`ExportStats` arithmetic and wording. Existing: `test_lspri_rewrite_ome_zarr_export_metadata.py` (rotated, rounded export) passes.

## Export statistics (same day, maintainer request)

`dataset/export_stats.py` (`ExportStats`, pure). `export_ome_zarr_dataset(..., export_stats=)` fills the images written, the
bytes on disk, one stored plane's size and the worker count; `DatasetModule.export_to_ome_zarr` times the whole run and emits
`export_stats_ready` just before `export_finished`.

- **Status bar** (task indicator): "written to NAME in 4 min 52 s, 29 images/s".
- **The box after a carried export** ("open the exported dataset now?"). For example; the export lines are the numbers of the
  378-plane run above, the session line is illustrative:

  ```
  Export took 16 s: 378 images at 23 images/s (2.9 GB/min of image data).
  Wrote 484 MB to disk, 61 % of the 790 MB uncompressed.

  Session (0.4 s): 160 ROIs, 411 masks, 8478 chromatic models, 250 mask pointers

  Open the exported dataset now?
  ```
- "GB/min of image data" is **uncompressed** pixels per minute, so it says how fast the pixels are processed whatever the
  compression does; "wrote" is what reached the disk. Decimal units (1 GB = 10^9 bytes).
- Logged as `dataset.export_done` (seconds, planes, MB written, planes/s, workers).
- An export started with the Session button off gets the status-bar line only: no box appears for it (the box exists to offer
  the carried session).

## Not done / ideas left

- Rotation is 56 % and has no cheap fix: cubic is ~2.5x faster but was measured to triple the ROI-ratio error (docs
  `rotated_zarr_export_interpolation_2026-10-20.md`); a three-shear rotation needs numba (rejected as a dependency).
- Worker start-up (~6 s) could lose ~1.5 s by not importing the dataset layer for TIFF sources; not worth it at this size.
- ~~The Scan and analysis paths still read TIFFs with tifffile.~~ **Done the same day (maintainer: "switch to the new decoder
  everywhere"), see the next section.**
- Turn the adaptive worker tuning off by default (see above).

## The fast TIFF decoder on for every thread (same day, maintainer request)

`use_fast_tiff_decoder` used to be opt-in per thread (render thread, Mask-tab Scan, `analysis/scan.py`, and since this morning the
export workers); the analysis workers, chromatic / reference auto-detect, the GUI thread and the native-dtype reader stayed on tifffile.
Now **every thread starts opted in** (`fast_tiff_decoder_active` defaults to True); `use_fast_tiff_decoder(False)` still opts one
thread out, `LSPR_TIFF_DECODER=tifffile` still switches it off for the process, and the guards are unchanged: plain 2-D images only,
tifffile for anything else or on any error, and the first 3 files read in a process are compared pixel by pixel with tifffile (any
difference switches the fast path off for the session). `_load_image_array_native` (the export's dtype-keeping reader) got the same fast path.

Re-measured, because the old comment said tifffile was kept "on the strength of an earlier measurement" (warm cache, 120 real files,
alternating, median of 5, files/s, tifffile -> libtiff):

| threads | decoder only | the app's float32 reader `_load_image_array_uncached` |
|---|---|---|
| 1 | 43 -> 81 (1.9x) | 33 -> 54 (1.7x) |
| 2 | 41 -> 102 (2.5x) | |
| 4 | 41 -> 182 (4.5x) | 32 -> 123 (3.8x) |
| 8 | 38 -> 252 (6.6x) | 29 -> 142 (5.0x) |

tifffile does not scale with threads at all (its per-strip Python work holds the GIL: 300 strips a file); libtiff does one native call per
file, so the analysis pools, which run several readers at once, gain the most. Pixels identical on all files.
Cold disk reads are unchanged (the disk is the limit there); the gain is the decode, which was CPU-bound on warm files.

Tests: `tests/unit/test_lspri_fast_tiff_decoder.py` (opt-in test turned into "every thread uses it unless it opts out", plus the native reader);
the analysis-engine, scan, chromatic, histogram, area-selection, crop-tool, mask-export, session-autosave and export test files all pass.

Left as it was: `calibrate_analysis_worker_count` (`dataset/io.py`) still times tifffile reads, but only the old app uses it.
The old app's own `io/dataset.py` reader is untouched (edit the old `gui/` generation only on request).
