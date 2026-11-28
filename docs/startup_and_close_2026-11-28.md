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
