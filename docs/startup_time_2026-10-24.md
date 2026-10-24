# Startup time, 2026-10-24: 44.7 s -> 7.1 s, and what is left

Measured from the session logs of the test dataset (314 cubes x 27 wavelengths, 160 ROIs, 411 ignore masks, OME-Zarr), same machine.

| Launch log | "ready" | What dominated |
|---|---|---|
| 21:22 | 44.7 s | Sensorgram fits: the helper process died, fallback in the window's process took 39 s (waiting, not computing) |
| 21:37 | 11.2 s | helper worked; scan check 4.3 s; scan.h5 load 2.9 s |
| 21:55 | 7.1 s | scan check 3.9 s (3.35 s of it `build_scan_plan`) |

## Diagnostics added (all stay in the code)

* `analysis/trace_pool.py`: the helper process writes a `faulthandler` dump to `logs/lspri-<session>.jsonl.pool<n>.log`; a dead helper is logged with its **exit code** (`sensorgram.pool_fallback`, `sensorgram.pool_warmup_failed`: `exit_codes`, `crash_dump`); `sensorgram.pool_ready start_ms`; `sensorgram.trace_run` (where, fit settings, `wall_ms`, `cpu_ms` in the thread, `prep_ms` / `fit_ms` / `per_cube_ms`).
* `analysis/scan_trace.py`: `sensorgram.stages` (`gather_ms`, `fits_ms`, `series_ms`).
* `analysis/scan_results.py`: `analysis.scan_loaded cpu_ms`; `analysis.scan_checked` with `cpu_ms`, `plan_ms`, `hash_ms`, `compare_ms`, `snapshot_ms`, `start_wait_ms`, `handover_ms`.
* `applog/setup.py`: Qt warnings carry their origin (`stack`, `popup`, `focus`, `under_cursor`).
* The cause of the one helper crash (launch of 21:22) is **still unknown**: it did not recur and could not be reproduced (plain script, launcher-style pipes and job object all start the helper in 1.45 s). If it recurs the exit code and dump say why.
* The helper is now started when a dataset is being reopened (`SensorgramPanel.start_helper_early`, called in `build_main_window`), not when the scan is first drawn.

## The scan check no longer builds the ignore masks (option c)

`build_scan_plan` resolved all 411 ignore masks (decode, rotate/crop, chromatic warp; 400 MB) only so that `cell_hashes` could hash them. The processed-space mask is a function of (authored pixels, geometry, chromatic warp between the authored frame and this frame); the geometry is in the recipe. So the fingerprint now hashes the **authored pixels' digest and the warp** (`FrameJob.ignore_key`, `scan_jobs.ignore_key_of`; `MaskChange.content_digest` makes the digest from the stored bits without decoding), and the check calls `build_scan_plan(..., resolve_masks=False)`. A scan still resolves the masks (`resolve_masks=True`, the default); `scan_cubes` refuses a plan made for checking.

Measured on the real session (read-only, `hash_compare.py`-style script): `build_scan_plan` 2.1-3.3 s -> 0.25 s; `cell_hashes` 0.63 -> 0.41 s.

**One-time effect.** The hash of every cube that has a masked frame changed (49 of 314 here); the 265 cubes without a mask kept exactly the same hash (checked). Against the stored `scan.h5` the check therefore reports those 49 cubes as out of date once: *Fast scan* re-reads just them. Their numbers are unchanged (the scan computation was not touched). `SCAN_ALGORITHM_VERSION` was deliberately **not** bumped: it would have made all 314 cubes stale for a change that alters no number.

A mask edit, or a change of the chromatic warp that carries a mask to a frame, still changes the hash (pinned by `tests/unit/test_lspri_rewrite_scan_jobs.py` `IgnoreMaskKeyTests`). A hash can differ without the resolved mask differing (e.g. the same mask drawn twice with different frames): that is a harmless extra rescan, never a missed one.

Not done at the time: the Fast scan still resolved all 411 masks to build its plan although it only scans the stale cubes; `snapshot_source` on the GUI thread is 0.2-0.6 s; `dataset.file_stats` (8-12 s, background) is the interpreter lock being re-taken after every directory entry. Follow-up of the same day below.

## Follow-up 2026-10-24 (later): the three open items

**1. Scan masks only for stale cubes: DONE** (`scan_task.py` `_compute`). The task first builds the cheap plan (`resolve_masks=False`, ~0.2 s), hashes it, finds the stale cubes, and only then builds a second plan with `cubes=<stale>` and the masks resolved; that second plan is what runs. The hashes are the same either way (same `ignore_key`s): checked on the real session (`hash identical: True`) and pinned by `test_masks_are_built_only_for_the_cubes_that_are_scanned_again` in `tests/integration/test_lspri_analysis_scan_task.py`. Measured on the real session (plan build): one stale masked cube + one unmasked = 58 ms (before: 1.6-2.0 s for the full plan). **No gain in the one-time rescan after the key change**: all 411 masks sit on exactly the 49 stale cubes, so those 49 still need all of them (1.7-1.9 s either way). A scan with nothing stale no longer builds any mask (it did, before).

**2. `snapshot_source` on the GUI thread: measured, not changed.** Headless on the real session: 180-190 ms warm (680 ms the first time), 500 ms in the app's log with the GUI busy. cProfile: 8164 frames x (`affine_for` -> `fitted_affine_for` 0.11 s, `is_spectral` -> `role_of` 0.09 s, `resolve_mask_change` ~0.04 s, `record_path`, array copies): no single hot spot, about 20 microseconds of Python per frame. It runs once per debounced edit that can change a stored number (every ROI nudge), so it is a ~0.2 s hitch per edit rather than a startup cost. Real cure = cache the per-frame tables (wavelengths / spectral flags / paths / affines / mask refs) and rebuild each only when its module changed, so a ROI edit re-reads almost nothing. That needs a change counter on Chromatic, Mask and FrameRoles (three modules, public surface): **waiting for the maintainer's go-ahead.**

**3. `dataset.file_stats`: measured, not changed.** Plain walk alone: 2.6 s (8905 files in 17286 directories, 10.5 GB), stable over 3 runs. With another thread holding the interpreter lock the same walk did not finish in 190 s: every directory entry gives the lock up and has to wait to get it back, so the time is set by how busy the GUI thread is, not by the disk. Effect on the user: only the size / date label in the info bar stays at "..." for 8-12 s; nothing waits for it. Options, cheapest first: leave it; walk in the existing spawn helper-process pattern (`trace_pool.py`; ~2.6 s regardless of the GUI, but +1.5 s process start and more machinery for a label); remember the answer per dataset (`sessions/` lives inside the zarr folder and changes, so the cache key needs care). **Waiting for the maintainer's choice.**

## The Qt warning "QFont::setPointSize: Point size <= 0 (-1)"

Seen twice in two sessions, once per session, with the pointer over the Analysis ribbon tab and a QToolButton focused. The logged origin is Qt's own event loop (no Python frames). A headless probe of the whole Image panel (every visible widget with a tooltip: enter, tooltip event, focus, in every ribbon tab, real fonts, app theme) produced no such warning, so the trigger is not a tooltip of the panel's widgets and was not found. Qt ignores the call, so nothing is wrong on screen; `applog/setup.py` `BENIGN_QT_WARNINGS` logs exactly this message at DEBUG (still in the file, with its origin fields) instead of WARNING. If it ever matters, its origin fields in the jsonl are the starting point.
