# Tuning one ROI: scan modes, a pixel cache around the ROI, and a live preview (design, 2026-10-28)

Maintainer's sketch (2026-10-28): one Scan button (Ctrl+S) whose behaviour is set by modes (Normal / Performance workers, plus Fast = changed cells only /
Full = everything); the Fast run optimised as far as possible (preparation, ROI changes, mask changes); for tuning one ROI, read and cache the chunks
around it ("the tuning stays inside one chunk"); question: is the pixel cache another mode, given that it is heavy for many ROIs?
"Don't worry about rewriting built code, I need speed." Measurements behind this: `roi_subset_scan_bottlenecks_2026-10-28.md` (section 7) and the
prototypes in `tools/scan_subset_lab/` (`proto_tile_cache.py`, `prof_window_read.py`, `bench_end_to_end.py`). Nothing of the design below is built yet,
except the uncompressed scan file (section 6).

> **Status, same day, later (maintainer):** "I am not a big fan of the proposed method or I do not understand it well yet ... the first change needs about 12 s, so the cache
> filling has to be triggered by the user ... maybe skip this one, and think in future how to make this automatic rather than user verified. Focus now on P4-5 if they make
> sense on the current method." **P1-P3 (Scan button + modes + Ctrl+S, the Tune toggle with the pixel cache, the preview) are not built.** P4 and P5 (a faster Fast scan,
> mask edits that stay local) are built without any of it: `roi_subset_scan_bottlenecks_2026-10-28.md` section 8. Kept here as the record of the idea and its measurements
> (a trial from a cache was 0.2-0.4 s with identical numbers; filling it 9-12 s per ROI) for when it is taken up again; the open question there is how to fill such a cache
> without the user asking for it.

## 1. What was measured (test export: 314 cubes x 26 wavelengths = 8,164 frames, 160 ROIs, 80 px chunks, one shard file per plane)

| | measured |
|---|---|
| Fast scan of one moved ROI, all cubes, Performance mode (real `ScanTask`) | 6.6-7.3 s: prefix 1.4-1.6 s, workers ~4.5 s, writer tail 0.2 s (uncompressed) |
| window that holds one ROI in every wavelength plus 8 px of tuning room | 81 x 84 px = 6,804 px; touches 4 of 160 chunks |
| cache of that window for ALL 314 cubes | 222 MB as float32 (111 MB as uint16 + valid mask) |
| filling it (8 threads, one process) | 9-12 s: 1.1-1.3 ms per frame; threads barely help (GIL); ~40 % of a read is `open` / `stat` / `read` / close |
| one trial (moved ROI or other diameter) from the cache, all cubes | 0.41-0.47 s on one thread, **0.20-0.24 s on 4 threads**, same numbers |
| are the trial's numbers the scan's numbers? | counts identical; mean and std differences 0.0 (3 trials x 10 cubes x 26 wavelengths x 2 sides) |

So a trial is ~30x faster than a Fast scan, and it costs memory (about 0.1-0.2 GB per ROI for the whole run) and a one-time fill. The reason it is so much
faster: nothing is read, there is no worker pool, no plan, no hashing, no file; it is a handful of large numpy operations over `[cube, pixel]` arrays.

The chunk is not the right unit to cache: the window needs 6.8 k pixels, the four chunks it touches hold 25.6 k (3.8x more). The cache is the pixel window;
the chunks are only what is read to fill it, once.

## 2. My answer to "is the cache another mode?" - no: modes are how Scan runs, the cache is state that belongs to a ROI selection

* **Scan modes** (app-wide, remembered, change what the Scan button does): *Scope* Fast / Full and *Workers* Normal / Performance. They belong in the
  Mode pick-up next to each other. One Scan button + Ctrl+S runs the current scope.
* **Tuning cache** (per selection, explicit): a **Tune** toggle for the selected ROI(s). While on, those ROIs have a cache, their edits give a live
  *preview*, and nothing is stored. It has its own lifetime (tied to the ROIs, the dataset and the image tools, not to a mode), a memory budget, and is
  off by default. Performance mode is not a prerequisite (it only makes the fill faster, section 5).
* **Resource safety is a budget, not a mode:** Preferences gets "Tuning cache (MB)" (default 1,000: about 4-9 ROIs for the whole run). Before filling, the
  button says how many MB the selection needs ("3 ROIs x 314 cubes = 670 MB"). Over the budget it does not fill; it offers "every 2nd / 4th / 8th cube"
  (a choice the user sees and keeps, never silently adapted: the project rule that a control means the same for every selection).
* **Ctrl+S:** nothing else uses it (File has only Exit; autosave exists). Risk: Save habit. Fast scan is idempotent (nothing stale: nothing happens), so Ctrl+S in
  Fast scope is harmless; **in Full scope it should ask first** (minutes of work).

## 3. Three paths for one job

1. **Scan** (official, stored, bit-exact): what exists, with the modes above. Writes `scan.h5`.
2. **Tune** (preview, never stored): cache + trial kernel; the answer to "I moved the ROI, what does the spectrum / sensorgram look like".
3. **Commit:** Scan (Fast) computes the tuned geometry officially. The preview is never promoted to a result by itself: integrity first (priority 2);
   promoting the tile numbers later is possible once the kernel is pinned bit for bit (it was equal in every comparison so far, but a sum in another order
   can differ in the last digit of the std).

## 4. Architecture of Tune

```
ROI edit (drag, arrows, diameter box)  -- ComputationalChange of a tuned ROI -->  TuningSession (QObject, GUI side, owns the lifetime)
                                                                                    |  debounce ~100 ms, newest wins
                                                                                    v
                                         TuningProcess (one helper process, like TracePool; owns the tiles, spawned on first Tune)
                                           fill(roi, cubes, window)  reads the window of every frame, progress, cancel
                                           trial(geometry of the tuned ROIs, other ROIs as they are, affines, ignore-mask crops)
                                           -> arrays [cube, wavelength, side]: mean, std, n_valid, n_nominal, n_masked
                                                                                    |
                                                                                    v
                          ScanResultsModule.set_preview(roi_id, arrays) -> results_changed -> Spectra / Sensorgram redraw "preview" (badge)
```

* **A helper process, not a thread:** 100-200 MB of tiles must not sit in the window's process, a crash must not take the window down, and the project rule
  is that Python-heavy numpy work never runs in a thread next to the GUI (fits froze the window once). The kernel is one process's job; inside it, 4 threads over
  the 26 wavelengths gave 2x.
* **What the cache holds:** raw pixels of the window (uint16 or float32 as stored) and the export's valid mask, per frame. **Not** baked in: ROI geometry,
  masks, chromatic affines, the other ROIs. Those are inputs of each trial. So a mask edit or a chromatic change does not invalidate the cache (the window
  only has to still cover the ROI: checked per trial; if not, a refill of the missing part, ~3 s).
* **Invalidation:** only dataset, geometry (rotation / flip / crop), background switched on, or the window no longer covering the ROI. Background removal on makes
  every pixel depend on the whole frame and on the ROI positions, so Tune is disabled then (greyed out, tooltip says why); so is a TIFF or rotated dataset
  (the same conditions as the chunk reader today; a TIFF cache would have to decode and rotate every frame once: about 7 min single-process, an estimate from
the 24 + 22 ms per frame measured on TIFF earlier, not tried; a later step).
* **Trial semantics = scan semantics:** layouts from `build_subset_layout` (exact), exclusion by the neighbours' sample discs, ignore masks as per-frame crops of
  the window (411 of 8,164 frames have one here), NaN rule, mean in float64, std ddof=1, value NaN unless both sides have pixels. Pinned by a test that runs the
  real `scan_cubes` for the same moved ROI and compares (the prototype did, 30 comparisons).
* **Preview plumbing is small:** Spectra and Sensorgram read a `StoredScan`. A preview replaces one ROI's block of arrays; `scan_view` / `scan_trace` already
  fetch per ROI (`trace_input(stored, roi_id)`, `compute_spectra`), so one accessor on `StoredScan` that consults the overlay is the whole change, plus a
  visible badge ("preview of ROI 81 - not saved") so a preview is never mistaken for a result.
* **Several ROIs:** each tuned ROI has its own tile; a trial for K ROIs costs K x 0.2-0.4 s (they are independent; could use the pool). Selecting more ROIs than the
  budget allows is refused with the MB figure. Adjacent tuned ROIs share neighbours' discs in the exclusion (inputs of the trial), so that stays exact.

**Rule to confirm (eva `CLAUDE.md`: "Don't run analysis on ROI selection, panel navigation, or anything other than an explicit run"):** a live preview
computes after an ROI edit without a Scan press. I read the rule as "no hidden recomputation of stored results", and the Tune toggle as the explicit
action (nothing is stored; untuned ROIs never compute on their own). **Needs the maintainer's yes.** For untuned ROIs the Scan button can show a cheap badge
("1 ROI out of date") instead of computing.

## 5. Making the Fast scan fast (also the commit path of Tune)

Budget today for a one-ROI refresh of 314 cubes (6.6-7.3 s), and what each step buys (**the "today" column is measured, the "after" column is an
estimate from the stage timings; none of it is built**):

| step | today | after | how |
|---|---|---|---|
| 1. **Dirty ledger** | prefix 1.4-1.6 s (snapshot 0.1-0.2, plan 0.2, hash of 160 ROIs x 314 cubes 0.25, `store.prepare` 0.3, masks 0.8) | ~0.2-0.5 s | the modules already emit ComputationalChange events (ROI geometry, mask, chromatic, geometry, background). A small ledger records *which* ROIs / frames changed since the last scan; Fast then hashes only those cells and takes the stored hashes from the already loaded `ScanResultsModule` instead of reopening the file. The ledger is a hint; the hashes stay the truth (a lost ledger, e.g. after a restart, falls back to one full check, 0.3 s) |
| 2. **Writer: hyperslab, not cube by cube** | 1.3 s (uncompressed) overlapped, 4.5 s busy in the app | ~0.2-0.3 s | for a subset refresh write each dataset's changed rows for all cubes in one call per dataset (still hashes-to-0 / values / real hashes) instead of 314 x 5 read-modify-write of whole cube chunks. To be measured before building |
| 3. **Masks only where they matter** | resolved for every stale cube (0.8 s) | only masks touching the stale ROIs' windows | the 0.8 s is decode + hash of 411 masks; with the ledger a ROI-only change can resolve a mask once and crop it, and a mask edit only needs the ROIs whose window meets the edited area |
| 4. **Mask edits stay local (re-stamp)** | any mask edit makes every ROI of the cube stale (the mask key is in every cell hash) | only ROIs whose reads meet the changed area | for ROIs outside the changed area, rewrite the new hash without touching their numbers (correct: their pixels did not change); a *superset* of the touched ROIs is always safe. Needs the old and new mask of the edit (the mask module has both) |
| 5. workers | compute 3.7 s on 5-6 workers; flat past 4 (4 physical cores) | - | nothing to gain in code; the tile path is the answer for repeated trials |

Expected Fast scan of one ROI after steps 1-3: about 4-4.5 s (compute-bound); after Tune exists it is mostly the *commit*, not the loop.

## 6. Built today

* **Uncompressed scan file** (maintainer: "do it, big performance benefits"): `scan_store.py` writes plain chunks; an existing gzip file is converted once in
  `prepare` (copied to a new file, compared array for array, swapped in; on any problem the original stays). Real test file: 96.1 MB gzip -> 74.3 MB plain in
  2.7 s, every array, hash and metadata entry identical, `read_scan` 0.35 -> 0.14 s. A one-ROI refresh no longer grows the file (3-7 MB per refresh before).
  Tests: `tests/unit/test_lspri_analysis_scan_store.py` `UncompressedFileTests` (6).
* Prototype of the tile cache and trial kernel (not wired): `tools/scan_subset_lab/proto_tile_cache.py`.

## 7. Proposed order

1. **P1 - one Scan button + modes + Ctrl+S** (UI and wiring only; Full asks before Ctrl+S).
2. **P2 - TuningProcess, tiles, trial kernel, budget** (the core; tests against `scan_cubes`; fill by threads first: 9-12 s per ROI with progress and Cancel).
3. **P3 - Tune toggle, preview overlay in Spectra / Sensorgram, badge, Preferences budget.**
4. **P4 - Fast scan prefix and writer (steps 1-3 of section 5).**
5. **P5 - local mask re-stamp; faster fill (workers write into shared memory, 2-3 s per ROI, progressive: the preview works on the cubes read so far);
   TIFF / rotated datasets; commit-from-tile.**
P2-P3 give the 0.2-0.5 s loop; P4 improves everything else (including the commit). P1 is small and can come first or last.
