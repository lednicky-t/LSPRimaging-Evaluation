# Scan staleness check 18 s -> 1 s, polynomial centroid fit 4x faster (2026-11-26)

Follows `sensorgram_series_batch_smoothing_2026-11-25.md`: the check's Python hashing (27-35 s in the main process) starved the GUI thread, which is why `build_bar_steps` took 4.7 s instead of 0.2 s while it ran. Measured on the real run (`Data_PyTest/LbL/05_LbL_pumpplan...ome.zarr`, 2655 cubes x 160 ROIs x 27 wavelengths, 69,030 frames, one mask, background removal off, session 2026-11-20_174644) with read-only scratch scripts.

## 1. `scan_fingerprint.cell_hashes` (the check's hash stage)

Stages of a check (uncontended): snapshot 1.4 s (GUI thread), plan 0.9 s, **hash 18.0 s**, compare ~0. cProfile: 10.8 million `crop_digest` calls. One authored mask applied to every frame touches 157 of 160 ROI windows in each of the 69,030 frames, and the loop looked at every (frame, ROI) pair.

Fix (same bytes in, same hashes out): what one frame adds to the ROIs depends only on (its mask, its affine, the ROI layout), and the run has ~27 affines, so `window_sets` holds `{roi: digest of the mask pixels in its window}` once per distinct triple. A cube's per-ROI bytes are a function of its sequence of (column, window set), so `pattern_tails` shares them between cubes with the same sequence (all of them, here). Bounded by `PATTERNS_MAX`.

| | old | new |
|---|---|---|
| `cell_hashes` (local masks) | 17.97 s | **1.06 s** (x17) |
| all 424,800 hashes | | **identical** (also `local_masks=False`, unchanged path) |

So stored scans stay current and `SCAN_ALGORITHM_VERSION` is untouched. Pinned by `tests/unit/test_lspri_scan_local_mask_keys.py::WindowSetCacheTests` (digest of a 5-cube synthetic world made by the *old* code; cubes sharing a mask, one with another mask on two frames, one without).

A check is now ~3.4 s end to end (snapshot 1.4 s on the GUI thread, plan 0.9 s, hash 1.1 s) instead of ~20-30 s; the GIL window it holds against the GUI thread is ~7x shorter. Not done: running the hash in a helper process (would need the 69,030-frame snapshot pickled; the cost left does not justify it); a cache of the snapshot across checks (`snapshot_source_cost_2026-11-23.md`, "Still open").

## 2. Polynomial fit with the ranged centroid (`metric_range.range_centroid`, `trace_fit._polynomial_group`)

cProfile of the fit of 160 ROIs x 2655 cubes in one process: `lstsq` 0.17 s of 6.4 s; **92 % was the ranged centroid** (each fitted curve sampled on 400 points, then `np.trapezoid` twice over a 2000 x 400 array, a `weights * x` temporary, a transposed non-contiguous curve matrix and two boolean-index copies).

Fix: the two integrals are one matrix product with `trapezoid_weights` (moved to `metric_range`, still importable from `trace_fit`), weights made in place, rows processed in blocks of `ROW_BLOCK` = 256 (stays in cache), curves built contiguous (`coef.T @ vander.T`), no copy when every cube has a curve. All batch routes using `range_centroid` (polynomial, Gaussian, Voigt) and the one-spectrum route share it.

Real scan, old (HEAD) vs new in separate processes, best of 2:

| fit | old | new | max difference | gaps |
|---|---|---|---|---|
| polynomial, centroid (ranged, the default) | 6.51 s | **1.54 s** (x4.2) | 8e-13 nm | same cubes |
| polynomial, maximum | 0.85 s | 0.86 s | 0 | same |
| Gaussian, centroid | 10.5 s | 7.3 s | 1e-12 nm | same |
| Voigt, centroid | 28.6 s | 24.8 s | 9e-13 nm | same |

Rounding-level differences only (the sums are the same, in another order); fits are never stored, so no scan version bump. In the app the helper process took 12.9-17.4 s for the polynomial run before (low priority, 177 MB of arrays pickled to it, the check running beside it); expect roughly a quarter of that. Look at `sensorgram.trace_run` `fit_ms` after the next run.

## 3. Worker processes for the fits (`trace_pool.py`, same day, after the maintainer's go-ahead)

`TracePool` was "one process, not a pool" by design. It now fans out, with the count from `lspr_core.perf` (`get_runtime().begin("sensorgram.fits", ...)`, Standard = every core but one, capped by free memory at 250 MB per worker; Preferences "Fixed number" and "Tune automatically" apply). Design in the module docstring and `docs/performance_tuning.md` (row "Sensorgram fits"). The point that matters: **a process costs ~1-2 s to start, so extra ones are used only when the work left pays for them** (tasks left x mean compute time of the finished tasks / 3 s, from the worker's own clock; processes already running are free). The polynomial fit (1.5-2 s) therefore never fans out; Gaussian and Voigt do. Extra processes are released after 2 minutes idle; one warm helper stays.

Scaling of the bare fit on this laptop (4 cores / 8 threads, real scan, bit-identical at every count): Gaussian 8.5 s (1 worker) -> 5.4 (2) -> 3.8 (4) -> 3.65 (7); Voigt 28.2 -> 11.6 (4) -> 10.2 (7); polynomial 2.06 -> 1.40 (2) -> 1.02 (4). BLAS threading inside the workers made no difference (`OPENBLAS_NUM_THREADS=1` tried).

The real `TracePool` (warm helper first, as the app does), 160 ROIs x 2655 cubes, wall time of `run`:

| fit | Fixed 1 | Fixed 3 | Standard (7) |
|---|---|---|---|
| polynomial | 2.0 s | 2.0 s (1 process: not worth more) | 2.05 s (1 process) |
| Gaussian | 8.6 s | 5.7 s | 6.6 s cold, 4.4-5 s warm |
| Voigt | 28.5 s | 14.7 s | 13.4-13.9 s cold, 9.8 s warm |

Not tuned further: with the payback rule off (every allowed process at once) the cold Gaussian run was slower (8.3 s: seven processes starting while sharing four cores) and warm runs only 10 % faster.

## 4. Voigt: the curve sampling used the Faddeeva function for a Gaussian (`voigt_fit.profile`)

cProfile of the Voigt fit (25.4 s, one process): **`profile` 13.9 s (55 %)** = the fitted curve sampled on the 400-point grid for the ranged centroid (800k `scipy.special.wofz` calls per batch, ~90 ns each); first-guess Gaussian solve 4.7 s; the Voigt iterations ~5 s. In the maintainer's scan gamma ends at exactly 0 (the lower bound) in 99.6 % of the cubes, and for gamma = 0 `Re w(x) = exp(-x^2)` exactly. `profile` now uses `np.exp` for every row and replaces the rows with a Lorentzian part by the `wofz` value (a batch with none at 0 takes the old expression: same bits).

Real scan, old (HEAD) vs new, separate processes: Voigt centroid **28.7 s -> 14.4 s**, max difference **1.0e-12 nm**, same gaps; Voigt maximum unchanged (it does not sample the curve). Pinned by `tests/unit/test_lspri_analysis_voigt_fit.py::ProfileShortcutTests` (mixed, all-Gaussian, all-Lorentzian, filter band, fitted curve).

## What is left for Gaussian and Voigt (measured, not done: each needs a decision)

- **Gaussian (7 s single process)**: it is the Newton iterations on `[2000 x 26]` arrays, bound by numpy call overhead (`_reduced_terms` 3 s, `_linear_part`, `_bell_at`), and the curve sampling for the ranged centroid (`sampled_curve`, 1.8 s, already in place; the remaining cost is `exp`). One *exact* gain is known: the accepted trial's bell and linear part are recomputed at the start of the next iteration (identical numbers, ~15 % of the time, needs carrying per-row state through the shrinking active set). Not done: modest gain for a fiddly change in the solver. An *analytic* ranged centroid for a Gaussian would remove the sampling (~30 %) but changes the number at the discretisation error of the 400-point trapezoid: a science decision.
- **Voigt (14.4 s)**: 4.7 s is the Gaussian first guess (in energy), solved to full tolerance. A looser first-guess tolerance would save ~3 s but changes the start of the Voigt iterations, so results move within the convergence tolerance (not bit-level): needs the same before/after comparison and the maintainer's yes. The gradient evaluation (`_terms`, 3 s) could use the Dawson function for gamma = 0 rows (cheaper than `wofz`; rounding-level change).
- With workers the fits are now 2-3x faster on this 4-core laptop; a machine with more physical cores gains more (the scaling here is capped by 4 cores).

## Time outside the fits: pool transport (2026-11-26, later)

**Measured (app log, 160 ROIs x 2655 cubes, new `sensorgram.trace_run` fields):** the wall time is not the fit time. With `fit=none` (almost no compute) a run took 1.39 s; the polynomial fit took 3.3 s for ~1.1 s of fitting per worker. Each run sends 191.6 MB (160 tasks of ~1.2 MB, one ROI each, `tasks=160`). `setup_ms` is 0; `back_ms` averages ~50 ms per task although a result is only ~21 kB, so it is waiting, not bandwidth.

**Standalone benchmark** (scratchpad, not in the repo: 188 MB in 160 ROIs to 7 spawned workers, trivial fit; wall ms):

| variant | idle parent | parent thread holding the lock (worst case) |
|---|---|---|
| pickle, 1 ROI per task (the app until now) | 282 | 16 426 |
| pickle, 4 ROIs per task | 371 | 4 696 |
| pickle, 23 ROIs per task | 438 | 1 381 |
| shared memory, 1 ROI per task | 63 | 15 685 |
| shared memory, 23 ROIs per task | - | 898 |
| pickle, 1 ROI per task, switch interval 0.5 ms | - | 331 (1 ms: 9 923: not monotonic, do not trust) |

**Reading:** moving the data costs only ~0.3 s when nothing else runs Python in the parent; the cost is the interpreter-lock hand-offs per task (each can wait a switch interval, 5 ms). Shared memory does not remove that, so it was **not** built. The app's 1.3 s overhead lies between idle and worst case: something in the app competes mildly; which thread is not identified (idea: `py-spy dump --nonblocking` during a fit, never `--native`).

**Change:** `trace_pool.TASK_ROWS = 6 * BATCH_ROWS` (4 ROIs per task at 2655 cubes) instead of one ROI per task. Fit batches inside a task are still `BATCH_ROWS` rows, so numbers are unchanged (tests pin identical traces). Cost: coarser progress / cancel, worse tail. **To verify in the app:** same fits as before, compare `wall_ms`, `send_ms`, `back_ms`, `tasks` in `sensorgram.trace_run`. **Not changed:** the GIL switch interval (`gil_tuning.py`, app-wide, GUI trade-off) and the worker count (`lspr_core.perf`, 7 workers on 4 cores looked oversubscribed: needs an A/B).

**Where the ~2 s gap seems to come from (2026-11-26, log analysis of 21 runs):** each fit run overlaps `session.capture` (GUI thread, 0.2-0.7 s) and `session.write` (autosave thread, 0.65-0.8 s, ~44 MB of JSON), which fire when a fit setting changes; the gap (wall minus fit work per worker) was 0.85-1.3 s in runs with no overlap and 1.7-3.7 s with overlap (correlation 0.74; cause not proven: the saves may take the interpreter lock *or* a core from the workers). 4 workers (Preferences, manual) gave the same wall time as 7 with 1.6x instead of 3.4x the summed fit time (oversubscribed 4-core / 8-thread machine). New log fields to settle it: `pickle_one_ms`, `gil_lag_*_ms` (`_GilProbe`). Test: change a setting, wait for `session.write` to finish in the log, then Run, and compare the gap. If confirmed, the fix is to hold the autosave while a Sensorgram compute runs (session code: needs a check-in).

**Confirmed and fixed (2026-11-26): the session autosave landed inside every fit run.** Log of a restarted session (4 runs): `session.capture` began a constant 2.25 s after the start of the ROI table's 0.85 s repaint (which follows a fit-setting change), i.e. the 2.5 s debounce expired ~0.9 s into the run. New fields showed the cost: worst interpreter-lock stalls (`gil_lag_max_ms`) 560-720 ms with the save overlapping vs <= 43 ms without (about the write's length: the 44 MB JSON encode probably holds the lock in one call; an inference). (The probe's *mean* lag, first read as evidence too, was an artifact: see the correction below.) Pickling one 4.8 MB task takes 9-16 ms (`pickle_one_ms`), 0.4-0.6 s per run, most of the ~1 s that remains.
Fix: `SessionAutosave.set_busy_check(TaskIndicator.has_running)` (`storage/session_autosave.py`, wired in `app_rewrite` next to the indicator's connects): a due save waits while any task that reports to the indicator runs (re-check 0.5 s, at most 60 s, then saves anyway); quit and dataset switches never wait. Logged: `session.autosave_held` (DEBUG), `held_ms` on the `session.capture` that follows, `session.autosave_hold_expired` (INFO). **To verify:** same fits again; expect `session.capture` to start after the run's `task.finished`, `gil_lag_mean_ms` ~1 ms and the gap down to ~1 s. Not changed: the ROI table's 0.85 s repaint, the 44 MB session JSON, `spectra.order_quality` (1.2 s thread) starting with every change.

**Correction, 2026-11-26 (the "9 ms mean lag" after the fix):** the probe's mean / p95 lag (9-10 ms during every fit, 1.2 ms in the first run) is not a thread running Python. Standalone check (scratchpad `bench_probe_artifact.py`): the same probe on an **idle machine with no workers** reads mean 11.2 ms / p95 13.7 ms; with 1, 4 or 7 busy worker processes and an idle parent, 9.8-11.0 ms. A 5 ms wait on Windows returns ~10 ms late whatever runs (system timer granularity, which other programs change). So the earlier "mean 1.2 ms without a save vs 6-16 ms with one" was confounded; what stays valid are the **stalls** (560-720 ms vs <= 43 ms) and the measured **gap** (1.7-2.3 s -> 0.7-1.3 s after the autosave wait). The probe now reports only `gil_lag_max_ms` and `gil_stalls` (wake-ups > 50 ms). Nothing in the app to fix for this.
