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

Not done (possible next steps): the Fast scan still resolves all 411 masks to build its plan although it only scans the stale cubes (resolve only those); `snapshot_source` on the GUI thread is 0.2-0.6 s; `dataset.file_stats` (8-12 s, background) is the interpreter lock being re-taken after every directory entry.

## The Qt warning "QFont::setPointSize: Point size <= 0 (-1)"

Seen twice in two sessions, once per session, with the pointer over the Analysis ribbon tab and a QToolButton focused. The logged origin is Qt's own event loop (no Python frames). A headless probe of the whole Image panel (every visible widget with a tooltip: enter, tooltip event, focus, in every ribbon tab, real fonts, app theme) produced no such warning, so the trigger is not a tooltip of the panel's widgets and was not found. Qt ignores the call, so nothing is wrong on screen; `applog/setup.py` `BENIGN_QT_WARNINGS` logs exactly this message at DEBUG (still in the file, with its origin fields) instead of WARNING. If it ever matters, its origin fields in the jsonl are the starting point.
