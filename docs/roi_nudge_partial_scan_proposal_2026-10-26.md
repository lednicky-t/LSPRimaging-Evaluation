# Nudging one ROI: re-scan only what changed (proposal, 2026-10-26)

Status (2026-10-27, latest): **3A (buttons), 3B + 3C (local cell hash, subset scan, `write_cells`), 3D (chunk reads of an unrotated
OME-Zarr), the `crc32c` dependency, the chunk-read limit 1.0, the writer thread and Performance mode (warm worker pool) are built; E (per-ROI staleness in the panels) is not.** Results are at the
end of this file (sections 7 to 11). Maintainer's answers: background-on fallback to today's behaviour is
fine for now; a first version limited to unrotated Zarr is fine; the one-time rescan after the hash change was asked about ("why would the
hash change need a rescan?": every stored fingerprint was made with the old formula, so the recomputed ones differ even though the values are
right) and is **to be confirmed when step B starts**; go-ahead for step A, done. Asked by the maintainer: (1) Fast scan should be a button, not a toggle; (2) after a *small
move of one ROI*, only that ROI should be recomputed, and reading should use the Zarr chunks so that only the chunks around it are read.
Engineering priority served: 1 (the partial result must equal the full result exactly), then 5 (speed).

## 1. What happens today

* `scan_fingerprint.cell_hashes` puts `job.rois.key` (a digest of **every** ROI's geometry) into the hash of **every** cell. Moving one
  ROI by 1 px therefore changes all 160 x 314 hashes: every cube is stale, Fast scan re-reads everything (2-4 min).
* A stale cube is re-read whole: `scan.py` `_Worker.run_cube` reads every full plane, rotates it, and reduces all ROIs.
* Why the hash is that coarse: the reference ring of a ROI leaves out the sample apertures of *all* ROIs
  (`exclude_all_sample_rois`), so in principle a neighbour can matter. In practice it rarely does (next section).

## 2. What a nudge really changes (checked on the test session, 160 ROIs, `2026-09-27_143549`)

Sample disk 33.7 px across, ring 49.5 / 59.9 px, nearest neighbour **75.4 px**. ROI k can change ROI j's cell only if k's sample disk
touches j's ring: centre distance < 16.85 + 29.94 = **46.8 px**. Pairs closer than that, even with 20 px of margin: **0**. So on this
dataset a nudge changes **exactly one ROI's cells** (all 314 cubes x 26 wavelengths x 2 sides of that ROI), nothing else.

Dependencies that stay global (and why the proposal must handle them):

| Input | Depends on other ROIs? | Consequence |
|---|---|---|
| Reference-ring exclusion | only ROIs whose sample disk reaches this ring | local hash = own geometry + those neighbours |
| **Background removal on** | yes: `roi_exclusion_mask` cuts every ROI out of the estimate, and the baseline is a whole-frame median | a nudge changes the background everywhere, slightly: **no local shortcut**, fall back to today's behaviour |
| Ignore masks, chromatic affine, frame roles | no (per frame, already in the cube part of the hash) | unchanged |
| Free-form (mask) ROIs | their shape is not a disk | treat as interacting with everyone (today's behaviour), rare |

Background is **off by default** and off in the test session. This matches the 2026-10-19 decision: partial reads only with the
background off. With it on, the status line should say so ("Background removal is on: it depends on every ROI's position, so all ROIs are
re-read").

## 3. Proposal

### A. Fast scan becomes a button (small, UI only)
Analysis tab: `[Scan]` (read every cube again) and `[Fast scan]` (read only what is out of date), a normal button each. While one runs,
it is the red Stop and the other is disabled. `FAST_SCAN_KEY` and its `ui_state.bind` go (nothing to remember). Touches
`panels/image/analysis_tab.py`, `app_rewrite._wire_analysis_scan`, tests `test_lspri_analysis_scan_row.py` (3 tests), the CLAUDE.md line
that says "Fast scan toggle". `run_requested` carries `only_stale: bool`.

### B. Hash per ROI, local (changes how "stale" is decided)
* `cell_hashes`: the cube part keeps frames (wavelength column, file name, affine, ignore key) and the recipe; **`rois.key` leaves it**.
* Per ROI j add `local_key(j)` = own geometry row + the geometry rows of the ROIs k whose sample disk reaches ring j
  (`|c_j - c_k| <= ring_out_j + sample_r_k + 2 px`; test in source space, which is the space the discs are defined in, so the chromatic
  affine does not enter). New function in `frame_reduce.py` next to `build_roi_set`, vectorised (160 x 160), pure.
* Background on with "exclude ROIs" on: `local_key(j)` = the whole-layout key (today's rule), so correctness is never weaker than now.
* Cost: the hash formula changes, so **every stored cell reads as stale once** (one full scan, ~3 min), as in the 2026-10-24 key change.
  Option to avoid it (compute the old hash, compare, rewrite the new one): not recommended, more code for a one-time 3 min.
* `scan.h5` format unchanged. `state/layout_id` per cube now means "a layout that reproduces every stored cell of this cube"; still true
  because an unchanged cell depends only on geometry that is identical in the old and the new layout.

### C. Scan only the stale cells
* `stale_cube_positions` -> also return the stale ROI indices per cube. `CubeJob` gets `roi_subset: tuple[int, ...] | None`.
* Worker: `PixelLayout` for a subset is a **slice of the cached full layout** (its arrays are sorted by ROI), so exclusion still uses all
  sample disks and results are identical; `needed_flat` shrinks to the subset's pixels. `reduce_frame` works on the sliced layout.
* Group cubes by (layout, subset) so a worker keeps reusing one sliced layout per wavelength.
* Few ROIs: use worker processes, not the task thread (a Python-heavy loop next to the GUI starves it; see the 2026-10-24 fit freeze).
* `ScanStore.write_cells(cube_pos, roi_idx, ...)`: same crash-safe order as `write_cube` (zero those hashes, write those rows, hashes last).

### D. Read only the chunks around the ROI (OME-Zarr)
* Chunk set per (layout, subset, affine) from `needed_flat` -> pixel rows/cols -> chunk indices (50 px export: **median 6 of 442 chunks per
  ROI, max 9**; 1.4 % of the plane). Cached like layouts.
* New `dataset/io.py` reader next to `_fast_read_zarr_plane`: read the shard index **once per cube** (the 27 wavelengths of a cube share
  one shard file; the index of the test export is 442 x 27 x 16 B = 191 KB with a CRC check, too much to repeat per frame), then read
  only those byte ranges and decode them. Falls back to the whole-plane read on anything unexpected, as the plane reader does.
  Must apply the export's `valid_mask` (NaN where no source) exactly as the plane path does.
* Fill a canvas (NaN elsewhere), gather the ROI pixels. When the chunk set exceeds a share of the plane (start near one third; **set by
  measurement**), read the whole plane: the benchmark showed many region reads lose to one plane read.
* **Only without rotation** (v1). The rotated OME-Zarr export has identity geometry, so processed pixel = stored pixel and the result
  is exact. With a rotation active (raw TIFF or raw Zarr) Lanczos4 needs an 8 x 8 neighbourhood per pixel and the warp of a *window* may
  not equal the full-frame warp bit for bit (OpenCV computes source coordinates in 1/32-pixel steps from rounded terms that depend on
  the window origin; I believe this, I have **not** verified it here). So v2 only after an experiment, and TIFF has no chunks at all.

### E. What the user sees
Sensorgram rings and dimmed Spectra currently work per cube (`ScanResultsModule.stale_cubes`). After B every cube is "stale" for the
nudged ROI only. Phase 3: per-ROI staleness so only that ROI's trace is marked.

## 4. Expected effect (estimate, not measured on the scan path)
Benchmark 2026-10-20: region read 2.1 ms vs 18-22 ms whole plane (zarr) vs 23-32 ms TIFF. Per frame today (1 process): read 15, prepare
15, reduce 10 ms. A one-ROI frame: read ~2, gather <1, reduce <1 ms. 8,164 frames x ~4 ms = ~33 s on one process, **~10-15 s with 4
workers plus start-up**, against ~110-170 s now. TIFF datasets gain little (read and rotation stay).

## 5. Tests that must exist before it is used
1. Partial scan of subset S == the same cells of a full scan of the same layout, **bit for bit** (mean, std, n_valid, n_nominal, n_masked), at 1 and 2 workers and any completion order.
2. Hash: nudging an isolated ROI changes only its hashes; nudging it into reach of a neighbour changes the neighbour's too; background on changes all; moving out of reach changes the neighbour once.
3. Chunk reader == plane reader at the same pixels (incl. `valid_mask`, edge chunks, partial last chunk, missing chunk).
4. Crash safety of `write_cells` (kill between steps leaves hashes 0).

## 6. Phases and open decisions
1. A (buttons). Small, independent.
2. B + C (cell-level staleness, subset scan, TIFF and Zarr alike; gain = skips layout/reduce work only). Needs OK for the one-time rescan.
3. D (chunk reads, identity geometry only). Needs the measurement of the switch point.
4. E (per-ROI staleness in the panels).
5. Later, only if wanted: rotation case (experiment first).
Questions for the maintainer: background-on fallback as in section 2; v1 limited to unrotated Zarr; the one-time rescan; whether several nudged ROIs should be one run (yes by design, any subset works).

## 7. Built 2026-10-27: local cell hash and subset scan (steps 3B + 3C)

**Where.** `frame_reduce.py`: `RoiSet.subset`, `PixelLayout.restrict` (a slice of the cached full layout, so the ring still leaves out every
ROI's sample disk), `LayoutCache.get(subset=)`, `roi_row_digests`, `local_roi_keys` (own geometry + the neighbours whose sample disk can touch
its ring; margin 2 px; a free-form ROI reaches everything; no ring or no sample = no interaction; exclusion "none" = no neighbours).
`scan_fingerprint.py`: `cell_hashes` uses the local key, except `layout_is_global` (background removal on **and** excluding the ROIs from its
estimate), where it is byte for byte the old whole-layout hash, so those sessions keep their stored hashes and need no rescan.
`scan.py`: `CubeJob.roi_subset`, `CubeResult.roi_subset`. `scan_arrays.place` fills only the subset's rows. `scan_store.py`:
`stale_cell_matrix`, `ScanStore.write_cells` (same crash-safe order as `write_cube`; the other rows of the cube are never touched).
`scan_task.py`: Fast scan computes each stale cube for its stale ROIs only (`SUBSET_MAX_SHARE` = 0.5: above that, the whole cube); the status
line says "1 of 160 ROIs x 314 cubes ..."; `analysis.scan_start` / `scan_done` log `rois_scanned`, the prepare event `stale_cells`.

**One-time effect.** With the background off (the default and this test session) every stored hash is different from before, so the first
Fast scan after updating reads every cube once (about 3 min here); sessions with background removal on and ROIs excluded keep theirs.

**Tests.** `tests/unit/test_lspri_scan_local_staleness.py` (21): subset == full bit for bit for every method, with an ignore mask, a chromatic
affine, overlapping ROIs and a free-form ROI; keys and hashes change only where they should; `place`; `write_cells` incl. a failure half way.
`tests/integration/test_lspri_analysis_scan_task.py` (+6): after a 1 px move the file equals a fresh full scan bit for bit (with the real
rotation, 1 and 2 worker processes, a neighbour that comes within reach, background that depends on the ROIs, more than half stale, a
cancelled refresh that resumes).

**Real data** (TIFF test dataset, session 2026-09-27_143549, 314 cubes x 160 ROIs x 26 wavelengths, rotation 1.46 deg, background off;
`scratchpad` scripts, read-only, nothing written to the session):
* subset (1 ROI, and 4 ROIs) == the same ROIs of the full scan, **identical** in mean, std, n_valid, n_nominal, n_masked on cubes 0, 157, 313
  and (nudge check) 0 and 157;
* a 1 px move of ROI id 54 changes the hash of exactly that ROI's column, in all 314 cubes, and no other column (nearest neighbour 75 px,
  reach 49 px).

**Speed on TIFF (single process, warm, laptop with ~25 % noise), ms per frame:**

| | read | prepare (rotation) | reduce | total |
|---|---|---|---|---|
| all 160 ROIs | 27.0 | 24.3 | 15.7 | 66.9 |
| 1 ROI | 23.6 | 22.3 | 0.4 | 46.3 |
| 4 ROIs | 23.5 | 23.1 | 0.6 | 47.2 |

So on TIFF a one-ROI refresh saves about a third (the reduction), as expected: the read and the whole-frame rotation stay. The large gain
needs step 3D (chunk reads on the unrotated OME-Zarr export), where read and rotation both almost disappear.

## 8. Built 2026-10-27: chunk reads of an unrotated OME-Zarr (step 3D)

**Where.** `dataset/io.py`: `ZarrPlaneGrid`, `ome_zarr_plane_grid(path)` (None for a TIFF, a foreign Zarr, an export the fast reader does not
understand), `partial_read_plan(grid, pixels)` (chunks to fetch + where each pixel sits on a padded canvas; made once per layout and kept by the
worker), `read_plane_pixels(path, plan)` (fetches those chunks through the shard index, decodes them, gathers the pixels as float32, applies the
export's `valid_mask`; None on anything unexpected, then the plane is read whole). A shard's index is read and CRC-checked **once per shard and
process** (key = path, size, mtime, so a rewritten shard is read again). `image_tools/geometry/transform.py`: `spatial_transform_is_identity`.
`analysis/scan.py`: `ScanSettings.partial_reads` (not in the recipe or the hash: same numbers either way), `PARTIAL_READ_MAX_SHARE`,
`CubeResult.partial_frames`; the worker uses chunk reads only when the background is off, rotation / flip / crop are off, and the plane
belongs to an export of this app; the log (`analysis.scan_cube`, `scan_done`) and `ScanSummary.partial_frames` say how many frames used it.
Not changed: the whole-plane reader, the Image panel, the export.

**Exactness (priority 1).** `tests/integration/test_lspri_zarr_partial_reads.py` (16 tests, 201 sub-cases): real exports from the app's writer
(per image and per cube shards, lz4 and none, rectangular 16 x 12 and 24 x 24 chunks with partly-outside edge chunks, a 6 degree rotated
export with a `valid_mask`) read bit for bit like the whole plane for 8 pixel sets each (single pixels, edges, unsorted, empty); only the asked
chunks are decoded; a damaged index CRC and a rewritten shard behave; a scan with chunk reads equals one without, with 1 and 2 worker
processes, a ROI near the NaN corner, and when the reader returns nothing; with background on, a rotation, or a TIFF it falls back; one
`ScanTask` run moves a ROI and the file equals a whole-plane full scan. A deliberately wrong gather index is caught (mutation check).
Real export of the test dataset (50 px chunks, 806 x 1288, 314 cubes x 27 wavelengths): 36 of 36 (plane, ROI count) combinations identical.

**Speed on the real export (this laptop, noisy, ~25 %; measured BEFORE the `crc32c` package was installed, current numbers in section 9):**

| | whole plane (existing reader) | chunk reader |
|---|---|---|
| 1 ROI (5 of 442 chunks) | 80.6 ms per plane | 0.8 ms |
| 16 ROIs (15 % of the chunks) | 80.6 | 4.9 |
| 160 ROIs (94 %) | 80.6 | 11.5 |

On exports with 128 px chunks (88 per plane) the whole-plane reader takes 31.3 ms and the chunk reader 1.4 ms (1 ROI) to 8.3 ms (80 % of the
chunks); with 256 px chunks 16.8 ms against 2.0 to 11.6 ms. **No crossover on any of the three exports**: the chunk reader is faster at every
share, so `PARTIAL_READ_MAX_SHARE = 0.3` is not a speed limit but a memory one (the read plan of a 160-ROI layout is ~4.6 MB per wavelength,
~120 MB per worker) and keeps full scans on the proven reader. Raising it to 1.0 would make full Zarr scans faster too (done the same day, section 9).

**A Fast scan after a 1 px nudge, real Zarr export, through the real `ScanTask`** (63 of 314 cubes, 1,701 frames, 160 ROIs):
first scan 114.6 s (15 frames/s, whole planes); Fast scan 12.1 s (145 frames/s, 1 of 160 ROIs, all 1,701 frames read as chunks), **9.5 times
faster**; the refreshed ROI equals a whole-plane scan of the same layout on 8 sampled cubes. Compute part of a one-ROI refresh over **all 314
cubes** (8,478 frames): 36.8 s with the automatic worker count (230 frames/s), busy per frame read 7.1 ms, prepare 0.0, reduce 0.6. The whole
task (plan, hashing, worker start-up, writing) adds a few seconds to that; scaling the 63-cube run by 5 would say ~60 s but counts the fixed
start-up five times. The whole task over all 314 cubes was not run.

**What is left in the read time: the index check.** `_crc32c` of `_zarr_export_worker.py` is a pure-Python loop because the `crc32c` package is
not installed (not declared in any pyproject either): ~180 ms for the 186 KB index of this export, paid once per cube. With the check bypassed
(experiment only, not in the app) the read goes from 6.8 to 3.0 ms per frame, so the check is 56 % of it. A fast CRC32C (the `crc32c`
package, or a vectorised version) would cut the one-ROI refresh of all cubes from ~37 s to roughly 20 s, and the same check costs the
**existing whole-plane reader** ~180 ms on every plane of this export (measured 80 ms per plane in total; it may fall back to the zarr API, not
checked). Not changed: a new dependency or integrity-check code is the maintainer's call.

## 9. Same day, later: `crc32c` installed and declared, limit raised to 1.0, the index check marked as a known bottleneck

**Maintainer's decisions (2026-10-27):** (1) add the `crc32c` package, and mark the shard-index check as a bottleneck to solve later, "maybe there
is another cheaper method"; (2) raise `PARTIAL_READ_MAX_SHARE` to 1.0.

**crc32c.** `crc32c>=2.9,<3` in `apps/LSPRi/eva/pyproject.toml` (with the reason), `crc32c` in its `requirements.txt` and in the PyInstaller
`hiddenimports`; installed in the venv (2.9.post0, a wheel exists for Python 3.14 on Windows). `_crc32c` was already written to use it when present
and to fall back to a pure-Python loop when not (a missing package never breaks a read). Checked: the checksum of the real shard index of the test
export is identical from the library, from the slow loop and as stored in the file (`0x2210b7d4`); 27 us instead of 113 ms for that 186 KB index.
Pinned in a test (`Crc32cTests`): the standard check value `0xE3069283` for `"123456789"`, and the fallback equals the library on random data.

**The index check, marked.** Comment block "KNOWN BOTTLENECK" above `_shard_index` in `dataset/io.py` (what it costs with and without the package
and three cheaper ways to be sure the index is sound, none done). It is also measured on every run: `io.shard_index_seconds()`, per cube
`CubeResult.index_seconds` / `crc_seconds`, and `analysis.scan_done` logs `busy_index_s` and `busy_crc_s` (the per-cube DEBUG event `index_s`,
`crc_s`), so the share is visible on any machine, also one without the package. **Measured with the package** (63 cubes, 7 workers, one ROI):
index 0.81 s and CRC 0.70 s of 22 s read + reduce busy time, about 4 %; the CRC itself is ~30 us per shard, the 0.7 s is mostly the one-time
import of the package (~0.33 s per worker process, measured in fresh processes). Without the package it was 56 % of the read.
The "25 %" in the request is therefore not what is left once the package is installed; the marker stays because an install without it is back
to the slow loop and the log will say so.

**Limit 1.0 (`PARTIAL_READ_MAX_SHARE`).** A full 160-ROI layout is now read as chunks too. Alternating A/B on the real export, one process,
26 distinct chromatic affines like the real session, steady state per frame (cubes 2-12 of 12, 44 cubes per arm): whole planes 33.7 ms (read
17.7, prepare 1.6, reduce 14.4), chunks 29.1 ms (read 14.7, prepare 0.0, reduce 14.4): **14 % faster**. Memory of one worker (peak working
set, 26 layouts): +266 MB with whole planes, +322 MB with chunks: **+56 MB**, peak 472 MB, still under the 600 MB per worker that
`scan._SCAN_WORKER_BYTES` reserves. The gain is smaller than section 8 suggested because the whole-plane reader got faster as well once the
package replaced its pure-Python CRC (80.6 -> 23.8 ms per plane); the chunk reader remains faster than it at every share (13.0 against
23.8 ms at 94 % of the chunks). Bit-identity at the full layout: test `test_a_full_layout_is_read_as_chunks_too...`; lower the constant again if
a machine runs short of memory.

**Numbers now (real export, `ScanTask`, 63 of 314 cubes, 7 workers, 1,701 frames):** first scan of all 160 ROIs 30-48 s (was 114.6 s with 2
workers: the worker count differed between the runs, so not all of that is the code); Fast scan after a 1 px nudge of one ROI 12.5-14.9 s (was
12.1 s with 3 workers), the refreshed ROI equal to a whole-plane scan. Compute part of the one-ROI refresh over all 314 cubes (8,478 frames):
**20.6 s**, 411 frames/s (was 36.8 s). Breakdown of the 12.5 s nudge run from its log: plan + hashing 0.4 s; read 20.3 s, reduce 1.7 s busy
summed over 7 workers (about 3 s of wall time each); **writing the 63 cubes 3.2 s** on the task thread (`write_s`), and the rest is start-up of the
7 worker processes and imports, not separated.

**Next bottlenecks seen (not worked on):** (1) `write_s`: ~50 ms per cube whatever the subset, serial in the task thread and done between the
results the pool hands over (the pool is only topped up when the consumer is back), about 16 s for 314 cubes; one open per task result or a
writer thread would hide it. (2) Worker start-up and imports (the `crc32c` import alone is 0.33 s per process). (3) Reading under contention:
busy read per frame is 3.0 ms in one process and 9-12 ms with 7 workers (the laptop saturates a shared resource, as in plan doc section 11).

## 10. Same day, last: writing the scan file off the collector's thread

**Problem (measured).** One cube write: 17 ms on an idle machine, of which opening and closing the file 7.8 ms and gzip of the two float64
arrays 8 ms; ~50 ms with 7 workers busy. The collector wrote each cube itself, and `scan_cubes` hands the pool new work only when its consumer is
back, so the workers waited: in the 63-cube one-ROI refresh the writes were 3.2 s of 12.5 s.

**Built.** `scan_store.ScanStore.write_batch` (several cubes, one opening, the crash-safe order for the whole batch: all their hashes to 0, then
all values, then the real hashes; `write_cube` / `write_cells` are one-item wrappers) and `scan_store.ScanWriter` (a plain `threading.Thread`
and a queue; takes whatever is waiting, up to 16 cubes, per batch; an error is raised to the collector by its next `submit` or by `finish`;
`finish` drains, so a Cancel or any stop keeps every cube that was finished). `scan_task` submits instead of writing; the log has `write_s` (the
writer's busy time), `write_wait_s` (what the collector waited for it at the end) and `write_batches`. The file format is unchanged.

**Tests.** `tests/unit/test_lspri_scan_local_staleness.py::WriteBatchAndWriterTests` (7): a batch (whole and partial cubes) writes the same file
as the cubes one by one; a cube twice in a batch is refused; a failure half way leaves every cube of the batch marked not computed and the others
untouched; the writer's file equals a direct write; cubes that arrive during a batch go together in the next one; the error reaches the
collector; finish with nothing queued. `tests/integration/test_lspri_analysis_scan_task.py`: the two cancel tests, which patched the old per-cube
writers and would now race, hold the collector until the first cube is on disk (deterministic: exactly one cube saved, the next run finishes the
rest); a disk error ends the run with "Could not save the scan file".

**Result, real Zarr export, `ScanTask`, one ROI nudged by 1 px (log values):**

| cubes | before (per-cube writes) | with the writer |
|---|---|---|
| 63 | 12.5 s, `write_s` 3.2 s serial | 10.6 s, `write_s` 1.8 s in 9 batches, `write_wait_s` 0 |
| **314** | not measured | **13.3 s** (677 frames/s, 7 workers), `write_s` 6.8 s in 36 batches overlapped with the reading, tail wait 0.17 s |

First scan of all 160 ROIs over all 314 cubes: 87.9 s (98 frames/s, 6 workers), `write_s` 8 s in 124 batches. The refreshed ROI equals a
whole-plane scan of the same layout on 40 sampled cubes. (Earlier numbers with whole-plane reads and the slow CRC: ~115 s for 63 cubes.)

**The next bottleneck: starting the worker pool.** In a one-ROI refresh the first result arrives after 5.5-6 s and the other 62 cubes within 1.1 s
(`scan_cubes` alone, 63 cubes). Each worker process spends 2.1-2.5 s importing (989 modules, among them zarr, scipy, cv2 and PIL; one idle
machine, one process) and 6-7 of them start at once on 4 cores. So a short refresh is mostly start-up: ~6 of the 10.6 s for 63 cubes, ~6 of
13.3 s for 314. Options, none done: (a) a pool that stays warm between scans (roughly 150 MB per idle worker; changes the pool's lifetime, so
ask first); (b) fewer workers for a small job, since each needs about 2.5 s of start-up (a cost-aware cap in `lspr_core.perf`, whose rules say
worker counts come from measured throughput); (c) lazy imports in the worker path (a refactor of `dataset/io.py`, perhaps 0.5-1 s per worker);
(d) start the pool before the plan is built (0.4 s).

## 11. Performance mode: a warm worker pool, chosen in the Analysis tab (maintainer: "try 1, as a toggle or pick-up menu ... performance mode vs normal mode")

**What the user sees.** Analysis tab, a new group **Mode** between Fast scan and Scan file: one icon button with an instant-popup menu, `Normal` and
`Performance` (checkmark on the current one). The face is the current mode: an amber bolt for Performance, a struck-through dim bolt for Normal
(two Tabler icons vendored for it, `bolt.svg` and `bolt-off.svg`). The tooltip says what the mode does, what it costs (about 150 MB of memory
per worker while idle, processor load while the workers start) and, for Performance, whether the workers are starting, ready (how many) or could
not be started. The same text is in the tab's "i". Remembered across restarts (`analysis/mode`); disabled while a scan runs. The status line of a
scan that used the pool ends in "performance mode: first cube after X s". A mode is a name, not a yes/no, so more settings can be tied to it later
(`scan_pool.uses_warm_pool` is the one place that says what a mode means).

**What it does.** `analysis/scan_pool.py` `WarmPool`: worker processes started when Performance is switched on (on a thread of its own, the GUI never
waits; a remembered Performance mode starts them when the startup tracker reports the app loaded, or 20 s after launch, so the dataset is not
slowed down) and kept until the mode is switched off or the app quits. `scan_cubes(warm=pool)` runs a scan on them instead of making a pool; the
workers end after a scan only in Normal mode. Between scans `trim()` asks every worker to drop its caches (best effort), so an idle worker holds the
imports and a little more. Each scan has a run number and the settings travel with every task: a worker that gets a task of a new run builds a fresh
`_Worker`, so nothing a scan computed can depend on an earlier scan (pinned by a test with other settings in between).

**Safety.** (1) Workers end themselves when the application that started them is gone (`scan._start_parent_watch`, psutil, every 5 s, the start
time is compared too): Windows does not stop child processes with their parent, an idle warm pool would otherwise survive a crash or kill. Tested
for real: a helper process starts a pool and is hard-killed, the workers end. (2) `atexit` and `ScanTask.shutdown` (`aboutToQuit`) close the pool.
(3) A worker that dies while idle makes the pool "broken": the next scan discards it and starts a new one (tested by killing one). (4) A pool that cannot be
started is reported on the status bar and scans start their own workers as in Normal mode. (5) A mode chosen while a scan runs is applied after it.

**Measured, real OME-Zarr export, `ScanTask`, one ROI nudged by 1 px, then Fast scan (quiet machine, 8 threads; the first numbers of the day were
taken under background load and are not comparable):**

| cubes | Normal | Performance |
|---|---|---|
| 63 | 5.8 s | **1.1 s** (first cube after 0.17 s) |
| 314 | 12.1 s | **7.8 s** (1,180 frames/s; first cube after 0.41 s, `write_s` 6.4 s overlapped) |

Starting the pool takes 3.8-5.8 s, once. In both modes the refreshed ROI equals a whole-plane scan of the same layout on 8 / 40 sampled cubes. For
all 314 cubes what is left is compute (busy read 33.7 s + reduce 5.1 s spread over the workers) and the tail of the writes; the gain of the pool is the
fixed cost, so it is large for a refresh of a few cubes and moderate for all of them.

**Cost.** Idle workers: 114-115 MB each right after the start (7 workers: 800 MB), 155-157 MB each after a scan with trimming (7 workers: 1,089 MB;
the Python heap does not give everything back). The Mode text says about 150 MB each. The automatic worker count asks the machine for free memory,
which the idle pool has taken a little of (runs with 5 or 6 workers instead of 7 were seen on a memory-tight machine).

**Not done.** An idle timeout that closes the pool after some minutes without a scan; the Preferences dialog does not mention the mode; no other
job (export, mask scan, Sensorgram fits) uses the pool.
