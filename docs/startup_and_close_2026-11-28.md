# Startup and close: what the logs show (2026-11-28)

Read-only investigation, no code changed. Source: `logs/lspri-202611*.jsonl` (20 launches, 26-27 Nov; machine 4 cores / 8 threads). Latest launch `lspri-20261127-233120-45388` used for the timeline. Earlier work: `startup_time_2026-11-11.md`, `startup_gui_thread_audit_2026-10-24.md`, `scan_check_and_fit_speed_2026-11-26.md`.

## Startup (latest launch, seconds after `app.start`)

| t | what |
|---|------|
| 0 - 4.7 | `build_main_window` (GUI thread): Image panel 1.09, ROI table + Spectra + Sensorgram 1.41, menus + status bar 0.91, dock layout 0.81, Log console 0.20 |
| 4.7 - 7.3 | dataset scan (`dataset.scan` 2.66 s, 71,685 planes) |
| 7.4 - 11.8 | session load, first image (`render.frame` 1.35 s, `pic_ms=1172`) |
| 13.9 - 17.7 | plane reads of cube 0 take 1.2-2.0 s each (normally 50 ms): interpreter-lock contention with `scan-results-load` / `scan-results-check` |
| 16.8 | `roi_table.rebuild` 907 ms on the GUI thread (160 rows, `select_ms=645`) |
| ~15 - 21.6 | scan staleness check: `snapshot_ms=3347` **on the GUI thread**, then 2.3 s in its thread (hash 2.6 s, plan 1.8 s) |
| 22.6 - 35.1 | Sensorgram fits, 160 ROIs x 2655 cubes, 12.5 s in the helper processes |
| 36.4 | `app.startup_done` (31.7 s after the log started) |

Over the 20 launches: ready 15-120 s (typical 20-35), window build 1.7-8.7 s (typical 2-4), scan-check `snapshot_ms` 1.9-8.2 s.

**Every one of the last 12 launches reports `stale_cubes=2655 of 2655`** (all cubes stale, every launch). If the file is simply out of date it needs one rescan; if it stays all-stale after a rescan, the hash does not reproduce and that is a correctness bug, not a speed issue. Not checked.

## Close

The log ends at `warm worker pool closed in X s` in every session, so only that part of the close is timed. Values over 20 sessions: 0.3-0.5 s in 11, 0.6-1.8 s in 5, **4.0 / 4.2 / 4.3 / 12.1 s in 4**. Closing an idle 4-worker pool headlessly takes 0.19-0.29 s (3 runs); killing the workers takes 0.02-0.03 s. So the slow closes are not the normal exit of an idle worker; cause not identified (candidates: low-priority workers starved while the machine is busy, workers paged out after a long idle).

Not timed at all: the other `aboutToQuit` slots (autosave flush waits for disk), interpreter teardown (trace-pool helper join at exit, zarr cleanup), and process exit.

## Built the same day (all four candidates; not yet seen in a real launch)

1. **Quit timing** (`quit_timing.py` `QuitClock`, wired in `app_rewrite.main`): `app.quit`, `app.quit_slots_done`, `app.exec_returned`, `app.exit` (with the threads still alive), each with `since_quit_ms`. `ThreadedTask.shutdown` logs a task that needed >= 50 ms to stop; `SessionAutosave.flush` logs itself when >= 50 ms. Slots are deliberately **not** wrapped (a wrapper keeps the receiver alive and calls it after Qt would have disconnected it; sip crash-on-close history).
2. **Warm pool killed at quit** (`WarmPool.shutdown(kill=True)`, used by `ScanTask.shutdown`): idle pool 0.21 s -> 0.02 s, whole headless process about 0.3-0.6 s shorter (3 runs each). The 4-12 s outliers could not be reproduced headlessly, so whether they go away is for the next real logs (`warm worker pool closed in X s (workers ended at once)`). Reads the private `executor._processes` (as `trace_pool.py` does); without it the old waiting close is used.
3. **Frame-table cache for the snapshot** (`scan_jobs.py` `FrameTableCache`, `frame_cache_warmer.py`): measured on the real dataset (71,685 frames, 160 ROIs, quiet machine): snapshot **1688 ms -> 63 ms** with a warm cache, **0 differences in 71,685 frames** against a snapshot without it (paths, affine bytes, mask refs and digests, warp bytes). The cache is dropped by `dataset_loaded / dataset_cleared / active_session_changed / roles_changed / reference_frame_changed / chromatic_model_changed / mask_changed` (`app_rewrite._build_modules`; the same signals that start the staleness check, plus the ones that replace the dataset or session). The first fill after a launch or an invalidation is read **in 15 ms steps** (`FrameCacheWarmer`): 130 steps, 2.2 s wall, **longest GUI-thread block 18 ms** (was one 1.7-3.3 s freeze). A change during a fill starts it over with a new source. Total work is slightly more than the one-shot read (2.1 s vs 1.7 s), spread out.
4. **Scan check waits for the image** (`ScanResultsModule.set_start_gate`, `ImageFirst(ImagePanel.image_work_pending)`, `ImageRenderer.read_ahead_pending`): the check does not start while a frame is pending or neighbour planes are queued / being read (cap 5 s, `ImageFirst`). Trade-off, unmeasured: the check, and the Sensorgram compute that follows it, start up to about 1.5 s later; the neighbour-plane reads should no longer take 1.2-2 s each.

Tests: `test_lspri_scan_jobs_frame_cache.py`, `test_lspri_frame_cache_wiring.py`, `test_lspri_frame_cache_warmer.py`, `test_lspri_quit_timing_and_start_gate.py`, two cases added to `test_lspri_analysis_warm_pool.py`.

**To check in the next real launch** (`python -m lspr_imaging_app.applog grep FILE --event ...`): `frame_cache.warmed` (DEBUG: steps, total_ms), `analysis.scan_checked snapshot_ms` (expect < 200), `dataset.plane_read` of cube 0 right after the first image (expect ~50 ms, not 1.2-2 s), the four quit marks and `warm worker pool closed ... (workers ended at once)`.

## Not done

- The all-stale result (`stale_cubes=2655 of 2655` every launch) is not looked into.
- `menus and status bar` (0.9 s) and `dock layout` (0.8 s) are not profiled.
- Dataset open (2.7 s) and the Sensorgram fits (12.5 s, no disk cache by the maintainer's decision) are untouched.

## First real launch and close (log `lspri-20261128-112206-13628`, same day, later)

Checked against the "To check" list above.

- **Worked:** ready in 17.1 s (the five logs before: 26-38 s, one 120 s); `snapshot_ms=95` (was 2.4-8.2 s); `frame_cache.warmed` filled in 164 steps, 3.4 s, off the critical path; the scan check waited for the image (`start_wait_ms=3941`); build 2.44 s (menus 0.38 s, dock layout 0.44 s). **Quit: `app.quit` -> `app.exit` 219 ms**, pool closed in 0.0 s, no error or warning, empty `.pool*.log` files.
- **`dataset.file_stats` 41 s** (earlier logs 41-126 s). It is the file system, not the GIL: the walk alone, in a separate process, takes **21 s** for this dataset (89.1 GB, 71,720 files in 146,042 directories; 3 runs, warm cache). Walking with a thread pool (split into 2,661 sub-trees): 4 workers 10.5 s, 8 workers 8.8 s. So at best 2x, and it competes with startup. Only the size label waits for it. Scripts were scratchpad-only (`walk_bench.py`, `walk_par2.py`).
- **First image `pic_ms` 1463 ms** (the five logs before: 68-135 ms, one 564 and one 1172). The picture step is a handful of native calls, 6 ms when the process is quiet; with two threads running Python it takes 450-830 ms, because every native call waits up to the 5 ms switch interval for the GIL (`pic_bench.py`, scratchpad). So something ran Python at that moment; the log shows the `session-load` thread (11:22:11 -> 13:96) overlapping the read.
- **The first image drawn is the blank-state frame** (cube 0, wavelength index 0, 13.9 s). The real one (wl 650) is rendered at 17.05 s, 3 s after the session was applied (14.09 s). Between them the log is empty. `scan_checked handover_ms=2407` says the **GUI thread was busy about 2.4 s** after `scan_loaded` (14.87 s): that work is not logged anywhere. Earlier logs show the same pattern (handover 0.1-3.9 s).
- **Open:** (1) find what the GUI thread does 14.1-17.0 s (a stall watchdog that logs the main thread's stack when it is blocked > 300 ms would show it); (2) file_stats: cache the last size per dataset and refresh in the background, or start it after "ready"; (3) `stale_cubes=2655/2655` is most likely the stored scan.h5 of 2026-11-22 being out of date (see `ignore_mask_noise_step1_2026-11-24.md`); a new Scan should clear it, not yet tried.

### Built the same day (uncommitted)

- **GUI-thread stall watchdog** (`applog/gui_stall.py`, started by `launch_wiring.start_gui_stall_watchdog` right before `app.exec()`): a 50 ms GUI timer beats, a plain thread looks every 100 ms; a beat more than 300 ms late is a stall; one DEBUG `gui.stall` record when it ends (`dur_ms`, `samples`, `where` = the application function most samples were in, `others`, `stack`). Read it with `python -m lspr_imaging_app.applog grep FILE --event gui.stall`. Next launch: look at what covers 14-17 s. A stall that never ends is not reported (faulthandler's job).
- **File size label** (`DatasetModule.set_file_stats_memory` / `hold_file_stats_walk`, `launch_wiring.wire_file_stats_after_startup`, saved under `dataset/file_stats` in the UI state, newest 12 datasets): the last count is shown at once, the new count starts when the start-up has ended (or after 60 s), and replaces it. First time ever for a dataset: `...` until the count is in (~21 s idle). A failed count (0 bytes) never overwrites a good one.
- Tests: `test_lspri_applog_gui_stall.py`, `test_lspri_gui_stall_wiring.py`, `test_lspri_dataset_file_stats_memory.py`, `test_lspri_file_stats_after_startup_wiring.py`, `test_lspri_file_stats_keys.py`.

## Second look: logs `lspri-20261128-115553-21972` (launch, Scan, quit) and `lspri-20261128-122950-27572` (launch after the Scan)

- **Scan cleared all-stale:** after the Scan (796 s) `stale_cubes=0`. The all-stale result was the old scan.h5, not a hash bug. `hash_ms` is still 1.3-2.3 s per launch.
- **Quit 232 ms** again (`app.quit_slots_done` 103 ms). **File size:** the count started at the end of start-up (11:56:13 -> done 11:56:35, 21.6 s alone; 42 s in launch 2 while the app was busy) and the size is saved (`dataset/file_stats`, 89,096,551,007 bytes).
- **`gui.stall` found the 14-17 s gap** (start-up 16.7 s and 21.3 s; first image was fast now: `pic_ms` 76-80 ms):
  1. **2.7-4.3 s** `analysis.scan_store.describe_file:216` <- `app_rewrite._wire_analysis_scan.show_saved_scan:1187` <- `SessionCoordinator.bind_dataset`. It opens scan.h5 with h5py on the GUI thread while the scan-results thread is reading the same file (`scan_loaded ms=4020`): h5py has one global lock, so the GUI thread waits for it. Before the first session is applied.
  2. **2.8-3.2 s** `dataset.chunk_plan.affine_reach:223` <- `ExportTab._refresh_estimates` <- `gather_export_roi_layout`. All fitted chromatic affines (~71,685) x 160 ROI centres on the GUI thread, once per launch. Benchmark (scratchpad `reach_bench.py`, synthetic 71,685 affines x 160 centres): 2.11 s now; **0.29 s** when only the convex-hull vertices of the centres are used, same two numbers exactly (the largest |linear function| over a point set sits on its hull). Not built: a numeric change, needs the maintainer's go-ahead.
  3. **0.5-0.65 s** `frame_naming.wavelength_decimal_precision:34` <- `SessionAutosave.seed_mask_versions`. `_frame_naming` hands it one wavelength per cube and wavelength (71,685 values) and it rounds them four times. Because the list repeats each wavelength 2,655 times, `len(set(rounded)) == len(list)` can never hold, so **the result is always 3 decimals** for a multi-cube dataset, not the 0 the docstring expects. An exactly equivalent fast path exists (duplicates in the raw list -> return `max_decimals`); changing the *result* would rename mask files (data format): not touched, flagged.
  4. 0.4-0.8 s: a garbage-collector pause during the frame-table warm-up (`gc_trace._on_gc`, 1 sample: the watcher itself was held), and 0.4-0.5 s `RoiTreeModel._repaint_steps`; ROI table paints 0.5-1.3 s while selecting (`_draw_locked_selection_frame`).

### Built after the second look (uncommitted, not yet seen in a launch)

1. **Scan file line off the GUI thread:** `analysis/scan_file_line.py` `ScanFileLine`, used by `show_saved_scan` in `app_rewrite._wire_analysis_scan`. A path with no file says "No scan yet." at once; otherwise "Reading the scan file..." until a plain thread has read it. Expected gain: the 2.7-4.3 s block before the first session is applied.
2. **`affine_reach` on the convex hull of the ROI centres** (`dataset/chunk_plan.py` `hull_vertices`). Before/after on the real LbL session (69,030 fitted affines, 160 real ROI centres, 12 hull corners): **2.185 s -> 0.262 s, shift 5.071996719719529 and stretch 1.0081539147383225 bit for bit the same**; 200 random ROI subsets x 2,000 random affines of the session: largest difference 0.0 (scratchpad `reach_real.py`). Only the export's crop margin uses it, no scan number. The theory (the largest |linear function| over a point set is at a hull corner) is exact in real arithmetic; in floating point a non-corner point could in principle exceed the corner by one rounding step (1e-16 relative), which never showed up.
3. **`wavelength_decimal_precision` fast path** (`frame_naming.py`): a list with a repeated wavelength can never become all-different by rounding, so it returns `max_decimals` at once, the same answer the loop reached after four roundings of 71,685 values. Pinned against a copy of the old loop on 300 random lists (`test_lspri_frame_naming_precision.py`).
- **Not changed, on purpose:** with the app's list (one wavelength per cube and wavelength) the answer is always 3, so mask files are named `mask_cube0000_wl570.000_persi_v1.png`. Changing it to 0 would make the app look for `wl570` and not find the saved masks (session format change: needs a migration plan and the maintainer).
- Tests: `test_lspri_scan_file_line.py`, `test_lspri_frame_naming_precision.py`, `AffineReachHullTests` in `test_lspri_rewrite_chunk_plan.py`.

## Third look: log `lspri-20261128-125116-38984` (first launch with the three stall fixes) and the next fix

- **Start-up 13.2 s** (launches before: 16.7 s, 21.3 s). The scan-file block (2.7-4.3 s) and the naming block (0.5-0.65 s) are gone from the `gui.stall` list; `affine_reach` fell from 2.8-3.2 s to 0.79 s (0.26 s alone; the rest is contention). First image: `pic_ms` 541 ms (first plane, cold), the real image 1 s later.
- **Critical path now:** window 2.4 s, dataset 1.3 s, GUI stalls (below) ~4 s before the session even starts to load, then the Sensorgram fits 7.7 s (4 workers, no disk cache by decision) -> ready.
- **New top stall, 1.39 s:** `ExportTab._on_dataset_loaded` -> `probe_ome_zarr_export_shape` read the first plane on the GUI thread (CRC32C of the shard index, `shard_crc._crc32c`). **Fixed:** the probe now runs on a plain thread (`export-shape-probe`), the estimates fill in when it is done, and the Export button is disabled with the tip "reading the dataset's image size..." until then (about 1.5 s after the load). Tests: `test_lspri_export_tab_probe_async.py`; `test_lspri_rewrite_export_chunk_tools.py` waits for the probe in `setUp`.
- **Still open (from the same log):** 1.05 s native Qt block (`RoiTablePanel._expand_all` / `DataAxisSlider._paint_static`, nothing of ours in the stack), 0.79 s `affine_reach` (a second look: the Export estimates could wait until the Export tab is shown), 0.44 s GC pause inside the frame-table warm-up, 0.55 s ROI table group paint (`_paint_group_steps`). The Sensorgram fits (7.7 s) are now the biggest single item.

### Sensorgram fits: disk copy (same day, uncommitted, maintainer's go-ahead after the earlier "no disk cache")

The fits (7.7 s of the 13 s start-up) are now kept in `sensorgram_traces.npz` next to `scan.h5` (`analysis/trace_disk_cache.py`, wired in `TraceStore.runner()`: the Sensorgram, the step table and the data checks all go through it). Decisions asked and answered: separate file (no `scan.h5` schema change), strict key, current setup only (3.4 MB for 160 ROIs x 2655 cubes).

- **Validity, per scan and setup:** the file holds the traces of one scan and one setup. They are used only if the **scan fingerprint** (`scan_trace.scan_fingerprint`: the paired scan's mean values, valid counts, cell hashes, ROI ids, cube numbers and the spectral axis / band widths / passband moments) *and* the setup (formula, fit, order, window, metric, metric range, template cubes, `CACHE_VERSION`, hash of the source of the nine fit modules) are equal. A rescan (of anything), a moved ROI, a new pairing or spectral correction -> every requested ROI is fitted again and the file is replaced; changed fit code -> same, nobody has to remember a bump. Only the ROIs that are not in the file go to the helper process.
- **Why one fingerprint per scan, not one per ROI (first version, replaced the same day):** hashing 160 ROIs' arrays one by one is ~1,600 numpy / hash calls, each of which gives up the interpreter lock and waits to get it back (5 ms or more while the GUI thread runs Python). Measured: 0.34 s quiet, 0.6 s with a GUI-like thread 50 % busy, 3.1 s at 90 %, 23 s next to a thread that never sleeps; **1.2 s in the real launch**. Packing ROIs in chunks of 16 helped little (2.3 s at 90 %). The scan fingerprint is ~10 big calls: **0.31 s quiet, 0.33 / 0.37 / 0.41 s at 50 / 90 / 100 % busy** (275 MB hashed, `scan_fp_bench.py` in the scratchpad), once per scan object. Price: a rescan of one ROI fits all of them again (a scan takes minutes, the fits 8 s).
- **Other measurements** (synthetic, real sizes: 160 ROIs x 2655 cubes x 27 wavelengths): read 14 ms, write 18 ms (own thread, atomic replace), file 3.40 MB. Against 7.7-9.7 s of fits.
- **First real launch with the cache (log `lspri-20261128-133933-38544`):** `sensorgram.trace_cache hits=160 fitted=0`; fits 9.7 s -> 1.4 s (1.2 s of it the per-ROI hashing above); start-up 15.04 -> 13.21 s. The rest of the gain is hidden because **"ready" now waits for the scan check**: image gate released 43.5 s, then `frame_cache.warmed` (128 GUI-thread steps, 2.74 s, to 46.4 s), then the check 2.9 s (ready 49.4 s), while the Sensorgram was done at 47.5 s. What "ready" is for: see "What 'ready' means" below.
- **Results are bit-identical** (float64 stored; pinned by `test_lspri_trace_disk_cache.py`). A damaged or foreign file is ignored with a WARNING (`sensorgram.trace_cache_unreadable`), a failed write with a WARNING; neither stops the Sensorgram. Deleting the file is always safe.
- **Not covered:** the response-shape metric (computed in milliseconds without a fit, never goes through the runner) and the Spectra panel's per-cube fits.
- Tests: `test_lspri_trace_disk_cache.py` (19).

### What "ready" means (`app.startup_done`, checked in code 2026-11-28)

Not only a log line. `StartupTracker` (`startup_tracker.py`, built from `app_rewrite._build_startup_tracker`) is the status-bar **"Starting up" spinner**, and its "completed" does two more things (`app_rewrite._wire_startup_calculation`):

1. **The ROI / Groups table's step columns are calculated** (`roi_table_panel.calculate_at_startup()`), on purpose after everything else: "the numbers are not out of date as they arrive, and the fits the Sensorgram already made are reused" (maintainer's request 2026-11-06).
2. **The Performance-mode scan workers start** (`start_pool`): started earlier they took the CPU from the fit helper (11 s instead of 1.2 s, 2026-11-11).

Steps, asked in order, all true on 3 polls in a row (3 x 200 ms = 0.6 s of the reported seconds is this settling): dataset opened; session read; first image drawn; **scan results settled** (`scan_settled`: file read *and* the staleness check finished, unless the check is held back because Spectra and Sensorgram are hidden); Spectra / Sensorgram not calculating. So "ready" waiting for the scan check is by design: the step columns must not be calculated before the app knows which cubes are stale. Making "ready" skip the check would start the step-column calculation earlier but on numbers not yet known to be current (a behaviour change, not done).

### The last three open items (same day, uncommitted)

1. **1.05 s block "in Qt" (`gui.stall` at 12:51:22):** it starts the moment the 1.39 s probe stall ends, so the GUI thread was blocked ~2.4 s in one piece and part of it was events queued behind the probe. The two functions named in its three samples were measured alone: `DataAxisSlider._static_picture` for 2,655 ticks = **18-23 ms** (offscreen, scratchpad `slider_bench.py`), the ROI table rebuild for 160 ROIs = 331 ms (`select_ms` 241). Neither explains a second. Nothing changed; re-measure in the next launch now that the probe is off the GUI thread.
2. **Export estimates only while the tab is shown** (`ExportTab(lazy_estimates=True)`, passed by `ribbon_build`): the chunk counts / alignment (`affine_reach` over every fitted chromatic model, 0.3-0.8 s) are worked out when the tab is shown (`showEvent`), not at every launch and ROI edit while it is hidden. With the chunk grid switched on the image they are still followed at once (the grid needs the layout). Default stays eager (tests, other callers). Tests: `test_lspri_export_tab_lazy_estimates.py`.
3. **GC pause 0.35-0.44 s and ROI table paints 0.3-0.8 s: measured, deliberately not changed.** A full collection with the real dataset loaded costs ~130 ms for the dataset's 1.12 million objects alone (`gc_bench.py`; the app's total, 350 ms, also holds Qt, session and frame tables). `gc.freeze()` would make that 0 ms but the frozen objects can never be freed when they sit in a reference cycle, and it would only save the ~130 ms of the two full collections during start-up: not worth a leak risk. The slow ROI table paints are 20 ms in a quiet moment (12:51:23) and 150-330 ms right after start-up while the machine is busy (4 helper processes, fits); no single function stood out, so no code change without a profile of such a paint.
