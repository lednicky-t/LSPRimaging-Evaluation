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
