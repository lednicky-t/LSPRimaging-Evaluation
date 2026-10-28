# Scanning one or a few ROIs of an OME-Zarr export: where the time goes now (2026-10-28)

Follow-up to `roi_nudge_partial_scan_proposal_2026-10-26.md` (sections 7-11). That work made a one-ROI refresh of all 314 cubes take 7.8 s in
Performance mode. Sections 1-6 say where those 7.8 s were, measured, and what could be made faster (written before any change; prototypes in
`tools/scan_subset_lab/` agreed bit for bit with the code of that morning, section 4). **Section 7 is what was built the same day (items 1 and 2
of section 5) and what the real `ScanTask` measures afterwards, including two new findings (the writer is the critical path, and the scan file grows
with every refresh).**

## 1. What was measured

* Data: `images_1275x799_314x27_c80IZ_u16.ome.zarr` (9.9 GB, uint16, **one shard file per plane**, 80 px inner chunks, blosc, 160 chunks per plane),
  carried session `2026-10-22_172035` (160 ROIs, identity geometry, background off, 411 ignore masks), 314 cubes x 26 spectral wavelengths = 8,164 frames.
  The 17 GB `images.ome.zarr` (50 px chunks, one shard per cube) has no usable rewrite session any more, so it was not used.
* Laptop: 4 cores / 8 threads, 34 GB RAM (4.7 GB free), no VM running. Timings vary about 25 % run to run; ratios are what count.
* Single process, steady state = layouts cached, files in the OS cache. Pools: `scan_pool.WarmPool` with the second pass timed (workers already running).
* The real `scan.h5` was never written (the writer test used a copy, deleted afterwards).

## 2. Budget of a one-ROI Fast scan over all 314 cubes (Performance mode)

The 7.8 s measured on 2026-10-27 is the sum of three parts that were timed separately today (the sum matches; the whole task was not re-run):

| part | time | what it is |
|---|---|---|
| **serial prefix** (task thread; the warm workers wait) | **~2.5 s** | 0.2 s plan without masks, 0.25 s cell hashes, 0.3 s `store.prepare`, **1.7 s second plan that resolves the 411 ignore masks** (399 MB) |
| scan proper | ~5.1 s | read + reduce on 4-7 workers; includes **~1-1.3 s at the start of every worker spent building pixel layouts** |
| tail | ~0.2 s | the writer finishing (the writer itself is overlapped with the scan) |

So reading the Zarr is no longer the main cost. The setup around it is about half of the run.

## 3. Findings

**F1. Layouts for a subset are built the expensive way** (`frame_reduce.LayoutCache.get(subset=)`). It builds the *full* 160-ROI layout
(`locate_pixels` for all ROIs, exclusion union, `_assemble_layout`) and then cuts the wanted ROIs out with `restrict`. 25-39 ms per layout, 26
layouts (one per wavelength) per worker, **redone at the start of every scan** (a new run resets the worker's caches on purpose). The time is counted
as "read" in `run_cube` (the lookup sits between the two timers), which is why "read" looked slow. Single process, one ROI: first pass 3.03 ms per
frame, with layouts cached 0.89 ms (0.65 s of the first pass is layouts). With 7 workers the first cube comes back after 1.9 s.
A direct build (the subset's own ROIs plus the neighbours whose sample disk can reach their ring, found with the same reach test as
`local_roi_keys`) takes **1.6 ms** per layout for 1 ROI (24x faster), 3.3 ms for 4 ROIs (11x), 6.7 ms for 16 ROIs (5x). Side effect: a worker no
longer holds 26 full layouts (~9 MB each) when it scans a subset.

**F2. The ignore masks are resolved in the parent, before any worker starts, and for this export the work is an identity operation.** 411 masks, all
with identity geometry and no chromatic warp (the rotation is baked into the export). `resolve_external_mask` still hashes, converts bool -> uint8,
runs `cv2.warpAffine` and converts back: 1.3-1.5 s of the 1.7 s. Replayed on threads (results identical): 2 threads 0.8 s, 4 threads 0.6 s, 8 threads 0.5 s.
Skipping the warp when `spatial_transform_is_identity(geometry)` and there is no `warp_affine` would remove most of it (not prototyped: it is in the
shared `image_tools/preprocess.py`, which the Image panel also uses).

**F3. Scaling stops at the physical cores.** One ROI, all 314 cubes, warm pool, no writing, new run each pass:

| workers | wall | busy ms per frame | first cube after |
|---|---|---|---|
| 1 | 9.9 s | 1.20 | 0.7 s |
| 2 | 8.2 s | 1.61 | 1.1 s |
| 4 | 5.3 s | 1.94 | 1.4 s |
| 7 (Standard here) | 5.1 s | 3.20 | 1.9 s |

Four workers are as fast as seven; the extra ones only inflate every frame's time (hyper-threads share a core). Not a code fix: it is what the
Tune-automatically option measures, and the maintainer chose Standard as the default (every core but one).

**F4. Per frame, the floor is about 0.9 ms for one ROI** (steady state, one process): file-system calls and read ~0.6 ms, reduce ~0.3 ms.
More ROIs cost the chunk decoding: 4 ROIs 1.42 ms, 16 ROIs 3.04 ms, 160 ROIs 20.5 ms per frame (read 13.4, reduce 12.6). With one shard file per plane
each frame pays `stat` + `open` (index) + `open` (chunks). One `open` with `fstat` on the handle gives identical pixels and saves 0.13-0.16 ms per plane
for one ROI (about 20 %); for 16 ROIs the difference is inside the noise. Small.
Chunk decoding is also a *data* decision: a ROI window of ~60 px on an 80 px grid touches ~3 chunks, so about 7x more pixels are decoded than used
(geometric estimate, not measured). A smaller inner chunk size at export time could be tried; not measured here.

**F5. The scan-file writer** costs 9.4-10.2 ms per cube standalone in batches of 16 (3.0 s for 314 cubes; 17.7 ms in batches of 1), whatever the
subset size: each cube is five gzip chunks that are read, patched and written back. About 45 % of it is HDF5 compressing at flush/close, ~15 %
is h5py reopening each dataset by name for every write. It runs on its own thread, so it overlaps with the scan; in the app it showed 6.4 s busy because it
competes with the workers for the same 8 hardware threads. Keeping the dataset handles open would save ~15 %.

**Not bottlenecks:** reading `scan.h5` back for the panels 0.35 s; shard-index CRC (~0.01 ms per plane with the `crc32c` package; the whole index step, stat + open + read, is 0.14 ms).

## 4. Prototypes and how well they agree with today's code

* `proto_subset_layout.py` (F1): `subset_layout(...)` against `build_layout(...).restrict(...)` on the real 160 ROIs with the 26 real affines
  (every ROI alone, random 4- and 16-subsets in sorted and unsorted order, all 160 as a subset, exclusion mode `none`) and on synthetic layouts with the
  ROIs pulled together so that rings run into neighbours' sample discs and past the image edge: **311 of 311 layouts identical** (every array of
  `PixelLayout`, `array_equal`).
* `proto_single_open.py` (F4): `read_plane_pixels` with one `open`: identical output on 200 planes, 1 ROI and 16 ROIs.
* `bench_masks_threads.py` (F2): thread results `array_equal` and same dtype as the serial ones for all 411 masks.

## 5. Suggested order (each is independent; all keep the numbers identical, so no `SCAN_ALGORITHM_VERSION` bump)

1. **Direct subset layout** in `frame_reduce.py` (used by `LayoutCache.get(subset=)`), with the test from section 4 kept as a unit test.
   Estimated: ~1 s off every scan's start (not yet measured end to end).
2. **Ignore masks: skip the identity warp and resolve on threads** in `build_scan_plan`. Estimated: the 1.7 s prefix to ~0.5 s.
3. Writer: keep dataset handles open, write slices directly (~15 % of the writer).
4. One `open` per plane in `read_plane_pixels` (~0.15 ms per frame, only for per-image shards).

Estimated total for the 314-cube one-ROI Fast scan: 7.8 s -> about 5 s (sum of the estimates; to be measured through `ScanTask` after building).
Floor with this design: roughly 0.7 s prefix + 8,164 frames x 1.2 ms over 4 effective workers = ~3.2 s.

Open: a "scan only the selected ROIs" scope (the `roi_subset` and `write_cells` machinery exists; what is missing is the button and the rule for
forcing a recompute of cells that are not stale).

## 6. Reproduce

`tools/scan_subset_lab/`: `bench_roi_subset.py` (stage times per ROI count, `--profile`), `bench_layout_split.py` (layout cost vs per-frame cost),
`bench_scaling.py` (workers 1-7), `bench_prefix.py` (the serial prefix), `bench_writer.py` (on a copy of `scan.h5`), `bench_masks_threads.py`,
`proto_single_open.py`, `proto_subset_layout.py` (now checks the app's `build_subset_layout` against `build_layout().restrict()` on the real ROIs),
`bench_mask_edit.py` (a mask edit through the real `ScanTask`: stale cells under both hash formulas, the Fast scan, equality with a full scan), `prof_prefix_parts.py`, `proto_tile_cache.py` and `prof_window_read.py` (the Tune idea, not built), `check_convert_real_file.py` (gzip -> plain on a copy), `bench_end_to_end.py` (the real `ScanTask`, Performance mode, a ROI moved by 1 px per trial, on a scratch copy of `scan.h5`; ends with the two
correctness checks of section 7; `--uncompressed` rebuilds the scratch file without compression), `bench_writer_variants.py` (compression settings and
file growth). Each takes the dataset and session paths as arguments (see the file header); run with the repo venv. None writes to the dataset or session.

## 7. Built 2026-10-28 (maintainer: "do 1., and the ignore mask point if it improves performance") and measured through the real `ScanTask`

**Item 1, built.** `frame_reduce.build_subset_layout(rs, affine, shape, mode, subset)` builds the layout of a subset directly (the subset's ROIs plus the
ROIs `ring_reach_matrix` calls neighbours of one of its rings); `LayoutCache.get(subset=)` uses it and no longer builds the full layout.
`ring_reach_matrix` is the neighbour test that used to live inside `local_roi_keys`, now shared by it and the builder, so the cell hash and the layout
can never disagree about who is a neighbour. Tests: `tests/unit/test_lspri_scan_local_staleness.py` `DirectSubsetLayoutTests` (9): equal to
`build_layout().restrict()` array for array and dtype for dtype, for every ROI of the crowded fixture, 3 affines, both exclusion modes, images smaller than the
ROIs, an empty and a one-ROI set, 24 seeded random dense layouts; the cache builds no full layout for a subset. A deliberate break (neighbours ignored) fails 4 of
them. Real data: 311 of 311 layouts identical (160 ROIs, 26 affines). Numbers: unchanged by construction, so no `SCAN_ALGORITHM_VERSION` bump.

**Item 2, built (the shortcut; threads not added).** `image_tools/geometry/transform.apply_spatial_mask`: a **bool** mask under an identity transform
(`spatial_transform_is_identity`) is returned as a copy instead of going through two float conversions. Other dtypes keep the general path (a
``>= 0.5`` threshold is not ``bool()``). Tests: `tests/unit/test_lspri_rewrite_mask_resolve_cache.py` `IdentityGeometryMaskTests` (4), against the old general path
for every way a geometry can be an identity; breaking the dtype condition fails one. Real data: all 411 masks identical. The mask part of the plan build went
from 1.7 s to 0.8 s and the whole serial prefix from ~2.5 s to ~1.7 s. What is left in it is decoding (0.4 s) and hashing (0.35 s) of the masks; threads would
save perhaps 0.4 s more and need the loop in `build_scan_plan` restructured: not done.
It also helps the Image panel on identity-geometry datasets (every `resolve_external_mask` of a new mask).

**Through the real `ScanTask`** (Performance mode, one ROI moved by 1 px per trial, Fast scan of all 314 cubes, scratch copy of `scan.h5`, laptop noisy):

| | 2026-10-27 (before) | today, file as it is (gzip) | today, file without compression |
|---|---|---|---|
| whole task, steady trial | 7.8 s | 7.2-7.9 s | 6.6-7.3 s |
| serial prefix (`analysis.scan_prepared`) | ~2.5 s | 1.4-1.6 s | 1.6 s (2.5 s in the first, noisier run) |
| first cube back | 0.41 s | 0.22-0.26 s | 0.26 s |
| writer: busy / what the collector waited at the end | 6.4 s / 0.17 s | 5.6-6.7 s / **2.1 s** | 4.5-4.7 s / 0.2 s |
| file after 3 trials | - | 96 -> 114 MB | 74.1 -> 74.2 MB |

The parts that were fixed did get shorter (prefix -1 s, first cube, layouts), but the end-to-end gain is only 0.5-1 s: the laptop is saturated (5-6 workers, the
writer and the collector thread on 4 cores), and with gzip the writer now arrives 2 s after the last cube. Correctness through the whole chain (direct
layouts, mask shortcut, chunk reads, writer): the moved ROI's stored cells equal an independent scan of the same cubes done the old way (all ROIs, full layout,
whole planes, one process), mean / std / counts, identical.

**New finding A: the scan file grows with every refresh.** A refresh rewrites each cube's five gzip chunks; the new chunk is usually a different size, HDF5 puts it
at the end and the old space is lost when the file is closed (the writer closes it per batch). About 3-7 MB per one-ROI refresh of 314 cubes (96 -> 114 MB in three
trials today; the real `scan.h5` is 96 MB where a freshly written one with the same numbers is 38.5 MB). A user who nudges a ROI 100 times adds hundreds of MB.
`bench_writer_variants.py` (new file per variant, real numbers, 4 one-ROI refreshes of 314 cubes):

| setting | first full write | one-ROI refresh | file after 4 refreshes |
|---|---|---|---|
| gzip 4 + shuffle (today) | 4.1 s | 3.3-3.8 s | 38.5 -> 55.0 MB |
| gzip 1 + shuffle | 2.9 s | 3.2-3.7 s | 38.8 -> 56.1 MB |
| lzf + shuffle | 2.2 s | 2.1-2.2 s | 42.1 -> 52.2 MB |
| no compression | 1.3 s | 1.3-1.4 s | 74.1 -> 74.1 MB |

No compression is 2.5-3x faster, constant in size (chunks never change size, so they are rewritten in place), and faster to read back; the price is a file about
twice the compact size (74 MB for 314 cubes x 160 ROIs x 26 wavelengths x 2 sides = 2.6 M slots, i.e. 28 bytes per slot for the five arrays, so it grows
with cubes x ROIs x wavelengths). A storage-format choice, so **not changed: waiting for the maintainer** (existing files keep working whatever the choice; only
newly written files would differ). Alternative that keeps gzip: persistent free-space tracking when the file is created (stops the growth, not the CPU cost).
**Decided and built later the same day (maintainer: "do uncompressed, big performance benefits"):** new files are plain, and `ScanStore.prepare` converts an
existing gzip file once (verified copy, then swapped in); real file 96.1 -> 74.3 MB in 2.7 s, identical content. Next steps (single-ROI tuning, tile cache,
Fast-scan preparation): `single_roi_tuning_design_2026-10-28.md`.

**New finding B: for one ROI the fixed costs per refresh are as large as the work.** Prefix 1.4-1.6 s (cell hashes of all 160 ROIs x 314 cubes 0.25 s,
`store.prepare` 0.3 s, plan 0.2 s, masks 0.8 s) and the writer's per-cube cost (rewriting the whole cube chunk for one ROI's cells) do not shrink with the
number of ROIs. Compute for the one ROI is ~1 ms per frame in one process, ~2.6 s on this machine for 8,164 frames if the cores were not shared.

**Direction for the single-ROI search (maintainer, 2026-10-28: "mostly focus on single ROI analysis, for a user looking for the best position / parameters
of one ROI").** Not built; options with the numbers above are in the conversation record / memory `lspri-roi-subset-scan-bottlenecks-2026-10-28`:
(A) a trial on a chosen subset of cubes, with a prefix that scales with the cubes (today it hashes and plans all 314); (B) a sweep: read the chunks
around the ROI once per frame and reduce many candidate positions / diameters from the same pixels (the read is shared, only the reduce repeats).

## 8. Built 2026-10-28 (later): a faster Fast scan (design doc steps P4 and P5), without the Tune / preview method

Maintainer, after reading `single_roi_tuning_design_2026-10-28.md`: "I am not a big fan of the proposed method ... maybe skip it and think in future how to make this
automatic ... focus now on P4-5 if they make sense on the current method." So P1-P3 (Tune toggle, pixel cache in a helper process, live preview) are **not built**;
what follows is the part that stands on its own.

**Preparation of a Fast scan (the serial part before any worker gets work), all bit-identical, no change tracking needed.** The profile found redundant work, not
anything deep, so the dirty ledger of the design doc was not built (the whole hash pass is 0.11 s, and a ledger could miss an event; the hashes stay the only truth):

| step | before | after | what |
|---|---|---|---|
| `ScanStore.prepare` | 0.30-0.43 s | 0.01 s | the layout JSON of the (one) shared ROI set was written 314 times |
| `build_scan_plan` without masks | 0.18 s | 0.05-0.08 s | `MaskChange.content_digest` memoised (it decompressed and hashed 411 masks, twice per Fast scan) |
| `cell_hashes` | 0.25 s | 0.11 s | per-ROI byte strings built once instead of 50,000 times; output pinned to the original, cell by cell, by a test |
| plan with the masks resolved | 0.8 s | 0.29 s | mask decode `unpackbits(...).view(bool)` instead of `astype(bool)` (0.85 ms per mask), the caller's content digest used as cache key, identity-geometry shortcut (section 7) |
| **total** | ~2.5 s (morning) / 1.7 s | **0.67 s** (in the app: `scan_prepared` 1.4-1.6 s -> 0.5 s) | |

**Writer: one read-patch-write per dataset and run of neighbouring cubes** instead of ~20 small h5py calls per cube (`ScanStore.write_batch`): a one-ROI refresh of 314
cubes writes in 0.4-0.6 s (was 1.3 s on the plain file, 3.3-3.8 s on gzip); in the app the writer's busy time fell 4.5 -> 3.1 s and the collector no longer waits
for it at the end (0.03 s). Only rows that are in the batch are written, never the gaps between runs (a test records the write calls); the crash-safe order is unchanged.

**Through the real `ScanTask`** (Performance mode, 7 workers, one ROI moved by 1 px, all 314 cubes, scratch copy of the file, `tools/scan_subset_lab/bench_end_to_end.py`):
**4.6-4.95 s steady** (7.8 s on 2026-10-27, 6.6-7.3 s earlier today); the first trial after the workers start is 6-6.8 s. The moved ROI's cells equal an independent
old-style scan (all ROIs, whole planes, one process). What is left: ~3.5 s of reading and reducing on 4 physical cores, ~0.5 s preparation, 0.3 s to the first cube.

**Mask edits are local (P5): the mask part of a cell's hash is the mask in the ROI's window, not the whole mask.** A cell's numbers depend on an ignore mask only through
the pixels the ROI reads, and those lie inside `frame_reduce.roi_window_boxes` (a box that is proved too big by a random test against `locate_pixels`). So `cell_hashes`
hashes, for each ROI, the mask pixels in its window; a mask edit makes stale only the ROIs it touches. Mechanics (`scan_fingerprint.py`): a process-wide memo
(`_MaskFootprints`, keyed by the mask's content key and the geometry, so it cannot go out of date) holds a coarse 16 px occupancy grid per mask as a summed-area table
(four look-ups for all 160 ROIs at once) and an exact crop digest only for the few (mask, window) pairs that touch; a mask that meets no window hashes like no mask.
`ScanPlan.mask_resolver` makes a frame's processed mask on demand (the plan still holds no mask arrays). **Whole-mask hashing stays** where it must: background removal
that cuts the masked pixels out of its estimate (`masks_are_global`), a plan without a resolver, a mask that cannot be made (that cube is stale rather than wrong), and a
free-form ROI (no bound). The previous formula is `cell_hashes(plan, local_masks=False)`.

* **Real data** (`bench_mask_edit.py`, a 12 x 12 px blob added to the masks of 20 cubes, all wavelengths, next to ROI 80): **20 stale cells instead of 3,200** (160x fewer);
  the Fast scan takes 2.3 s (the whole-mask hash would have rescanned 20 cubes x 160 ROIs, about 10 s of worker time); the file then equals an independent full scan on all
  160 ROIs of 3 sampled edited cubes.
* **Safety, tested on the numbers:** `tests/unit/test_lspri_scan_local_mask_keys.py` `SafetyPropertyTests`: over 30 random layouts (ROIs over the edge, chromatic shifts, no-ring ROIs) and random
  mask edits, a cell whose hash did not change has identical `reduce_frame` results (mean, std, counts, `n_masked`), and the test is not vacuous (hundreds of unchanged cells,
  dozens of changed hashes, numbers did change in some). Breaking the window to half its size, or making the footprint test never fire, fails it.
* **No rescan for existing files:** the hashes of the 49 cubes that have masks changed once. `stamped_by_previous_formula` recognises a stored hash that equals the previous
  formula's value for the current inputs (then every input is unchanged, so the numbers are current) and `ScanTask` / the staleness check give it the new stamp without scanning
  (`ScanStore.restamp`, hash dataset only). On the real file: the first Fast scan restamped 7,840 cells in 1.8 s with 0 cubes scanned and the numbers untouched; the next one took 0.27 s.
* **Cost of being local:** checking now needs the masks' footprints; the first check in a process (the background check after loading a session) builds them for all masks
  (1.8 s for 411 masks, once; kept as 16 KB tables, not arrays). The older property "the check builds no mask" (section 7 of the earlier notes, `test_masks_are_built_only_for_the_cubes_that_are_scanned_again`)
  holds only where the hash is whole-mask (the test's configuration).
* `SCAN_ALGORITHM_VERSION` is unchanged: no number changed, only which cells a mask edit makes stale.

**Not done, deliberately:** the dirty ledger (not needed), masks resolved lazily inside the workers (would save ~0.3 s of the 0.5 s preparation and remove 400 MB from the
task process; a larger refactor), one `open` per plane in `read_plane_pixels` (~0.15 ms per frame, only for per-image shards), the Tune method and everything that depends on
it (pixel cache, preview, shared-memory fill, commit-from-tile), the Scan button / modes / Ctrl+S (design doc P1: UI only, waiting for the maintainer).
