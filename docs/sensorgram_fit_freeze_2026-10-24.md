# Sensorgram fit froze the window (2026-10-24)

## Report and evidence

Dragging / resizing the fit window in the Spectra panel froze the app for seconds. From the session log `lspri-20261024-183712-36140.jsonl`:

- `sensorgram.compute` (10 ROIs x 314 cubes = 3140 polynomial fits) took 4.1, 4.3, 5.3, 9.8, 19.6 and 19.8 s for identical work.
- The GUI thread was starved while it ran: the task indicator (it stamps times when the GUI thread receives the signal) saw each compute start
  0.7-3.4 s late; a Spectra task whose 63 ms calculation was done at 18:44:52.6 was only *finished* at 18:44:57.06; the 5 s `gc.summary` timer
  (flushed from a GUI timer) fired up to 2.9 s late (`seconds=7.9`).
- At startup two prefetch plane reads that take ~35 ms took 4.0 s, and ended within 0.1 s of the Sensorgram compute ending.

## Cause

The fits ran in a `threading.Thread`. One fit is a dozen tiny numpy calls in Python (`Polynomial.fit`, `convert`, a companion-matrix root search,
evaluations), ~2 ms. Python runs one thread at a time (the interpreter lock). Every Qt call of the GUI thread gives the lock up and has to get it back:
measured next to the old compute, **3.6-5.2 ms per hand-off on average, 38 ms at the 95th percentile, 170 ms worst** (switch interval 5 ms). A
redraw of ~1000 Qt calls therefore froze for seconds. The same mechanism as `docs/` "Play pic_ms GIL hand-offs 2026-10-21" (memory note).
Not explained: the spread 4 s vs 20 s (a busy second thread alone only slowed the fits 1.3-1.9x).

## Fix (maintainer chose both, 2026-10-24)

1. **Batch fit** (`analysis/trace_fit.py`). The cubes of one ROI nearly always share their wavelengths, so the polynomial fits of all of them are
   one least-squares problem with many right-hand sides, and the peaks come from one batched eigenvalue call. Cubes are grouped by their set of
   usable wavelengths (`np.unique(axis=0)`). Same choices as `query.fit_polynomial` + `metric_from_fit` (finite points in the window, order capped at
   half the points, maximum over the two ends and the real stationary points, analytic centroid clipped to the window). The polynomial stays in the
   scaled variable t in [-1, 1] (the old path converted it to powers of nm, which loses digits). Gaussian fit, "no fit", and any cube where the batch
   could not be trusted (non-finite coefficient, vanishing leading coefficient, failed eigenvalue solve) still go through `query.fit_and_metric` one
   cube at a time. `fit_and_metric` moved from `scan_view.py` to `query.py` (the worker must not import h5py); `scan_view` imports it from there.
2. **Helper process** (`analysis/trace_pool.py`, `TracePool`). One process (not a fan-out: no `lspr_core.perf` tuning applies; a Gaussian fan-out
   would take its count from it), started once (`warm_up`, off the GUI thread, when the panel first finds a scan), low priority when Preferences says
   so, told to exit at quit. `scan_trace.compute_traces(..., runner=)` hands it the arrays of the missing ROIs (`TraceInput`, ~65 KB each); progress
   per ROI, cancel every 50 ms (ROIs not started are dropped). A process that cannot start or dies: fall back to the calling thread, WARNING
   `sensorgram.pool_fallback`, give up for the session after the second failure; an error inside a fit is raised to the caller, not swallowed.
   `SensorgramPanel(trace_in_process=False)` computes in the worker thread (tests that spy on the fit).

## Before / after (real scan: 314 cubes x 160 ROIs x 26 wavelengths, absorbance, this laptop)

Numbers (the metric, nm): 20 ROIs x 6280 cubes per case, 32 cases (maximum / centroid; order 2, 3, 4, 6; four fit windows from full range to 4 points).

| | per cube (old) | batch (new) |
|---|---|---|
| 10 ROIs, default fit | 6.4-7.1 s | 56-58 ms |
| 160 ROIs, default fit | ~100 s | 0.84 s in a thread, 1.28 s through the process |
| 20 ROIs x 32 cases | 361 s | 3.2 s (x113) |
| max difference in the metric, order <= 4 | | <= 8.3e-9 nm (maximum: <= 1.5e-10 nm) |
| max difference, order 6 (the old path's conversion is the worse conditioned one) | | 2.4e-7 nm |
| gaps (NaN) that differ | | 0 of 200 960 values |

Window starvation, a GUI-like thread (releases the lock, needs it back) next to 10 ROIs: old per-cube thread, mean wait per hand-off 5.2 ms (p95 38 ms,
worst 170 ms); new batch in a thread 0.01 ms (worst 3.5 ms); new batch in the helper process 0.00 ms (worst 1.0 ms). The helper process takes ~2.2 s to
start, once, in the background.

Scripts (scratchpad, not in the repo): compare against `trace_from_input_per_cube` on `scan.h5`; the lock measurement is a thread calling
`time.sleep(0)` in a loop next to the compute.

## What is not done

- **Gaussian fit**: now fast by default (section below); the "classic" solver and very narrow fit windows (9 points) are still ~2-6 ms per cube.
- The 4 s vs 20 s spread of the old compute was not explained.
- Not driven in the GUI (rule): the maintainer should drag the fit window and watch for stutter.
- Other GUI-thread costs seen in the same log, untouched: `session.capture` 100-250 ms per autosave; `dataset.file_stats` 11.8 s and
  `scan_checked` 7.3 s in the background at startup (they hold the lock too); a Qt warning `QFont::setPointSize: Point size <= 0` (source unknown).

## Tests

`tests/unit/test_lspri_analysis_trace_fit.py` (batch == per cube for all settings, gaps, unsorted wavelengths, tiny windows, edge maximum, flat
spectrum), `tests/unit/test_lspri_analysis_trace_pool.py` (really starts the process: equal traces, progress, cancel, error reaches the caller,
fallback + warning, giving up), `tests/integration/test_lspri_sensorgram_panel.py` (the process-backed panel draws the same series as the thread one).
`test_lspri_analysis_scan_trace.py` compared the polynomial trace bit for bit with `metric_value`; now within 1e-6 nm.

## Gaussian fit: fast solver (built 2026-10-24, same day)

Stock path was `curve_fit` per cube: 1.7-1.9 ms per fit (~87 s for 160 ROIs); a profile showed ~46 model evaluations per fit (MINPACK's
finite-difference Jacobian) and a third of the time in scipy's wrappers. Tried first: analytic Jacobian + `leastsq` per cube, 1.5x, not worth it;
a batched four-parameter Levenberg-Marquardt, 8-10x. **Built: variable projection** (`analysis/gaussian_fit.py`): for a given centre and width the
best amplitude and offset are a straight-line fit of the data against the bell shape (closed form), so only centre and width are iterated and
amplitude and offset are *solved* at every step. All four parameters still come out of every fit, so the plot draws the same curve. The gradient is
exact (the envelope theorem: it equals the gradient of the full four-parameter cost), so it stops at a true stationary point. Cubes of a ROI share
their wavelengths, so one iteration is a handful of numpy operations on a `[cubes, wavelengths]` array, finished cubes leave the batch.

**The Fast / Classic choice was removed the same day** (maintainer: the scipy fit is slower and less precise, so no setting to switch back). There is no
`MetricSettings.gaussian_solver` and no combo in the Spectra panel; a session saved while the choice existed still loads (an unknown field of the block
is ignored; test `SettingsTests`). `gaussian_fit.solve_gaussian` is the first choice for every Gaussian fit: `query.fit_gaussian` (one spectrum, for the
Spectra panel's curve) and the Sensorgram's batch (`trace_fit._gaussian_group`), so the metric the Spectra panel marks is the one the Sensorgram plots.

**scipy stays as the fallback for the spectra the solver cannot fit** (maintainer, the same day: no gaps). A first version without it left those cubes as
gaps (NaN); the maintainer asked for scipy back for exactly these. The cases are fit windows with too few points for a bell: on the real scan, 20 ROIs x 314
cubes per window, the solver gave up on full range 0, 17 points 0, 13 points 2 of 6280, **9 points 1402 of 6280 (22 %)**, 6 points 0 (a bell on nine points is
a parabola, the width runs away). Those cubes go to `curve_fit` (`query.fit_gaussian`, `fast_first=False` so the solver is not tried a second time), which
returns its usual answer (a centre at the parabola's vertex, a width of hundreds of nm), so nothing is a gap. The price is time only in such windows:
9 points ~2.5 ms per cube (160 ROIs ~2 min, in the helper process, the window stays free), every other window 0.03-0.04 ms per cube. The alternative would
be to accept the solver's runaway width instead of asking scipy (`_MAX_SIGMA_SPANS` in `gaussian_fit.py`); not done.

Real scan (314 cubes x 160 ROIs x 26 wavelengths, absorbance, maximum and centroid), first fast solver (Gauss-Newton) vs the scipy fit it replaced, per cube:

| fit window | classic | fast | largest difference in the metric |
|---|---|---|---|
| full range (26 points) | 1.93 ms | 0.15 ms (x13) | 5.3e-4 nm (median 1.2e-4) |
| 17 points | 2.00 ms | 0.14 ms (x14) | 4.1e-4 nm |
| 13 points | 2.25 ms | 0.15 ms (x15) | 3.5e-4 nm |
| 9 points (a bell on 9 points is a parabola) | 6.07 ms | 4.40 ms (x1.4) | 1.0e-3 nm |
| 6 points | 1.43 ms | 0.07 ms (x20) | 1.4e-5 nm |

160 ROIs, default window, through the helper process: 6.3 s (classic ~87 s); 10 ROIs 0.35 s. Which is nearer the truth: against a tight-tolerance
reference (xtol = ftol = 1e-15) classic is off by up to 5.3e-4 nm (median 7.7e-5), fast by up to 3.2e-7 nm (median 3e-8); the sum of squared
residuals is equal to 6 digits. So the Gaussian traces shift by at most ~0.5 pm against the old ones (0.1-1 pm in a narrow window), towards the
better answer; 0 gaps differ in 37 680 values. In a very narrow window the Gaussian is not determined by the data (width runs to infinity): the fast
solver hands those cubes (27 % of them in the 9-point window) to scipy, so they get scipy's answer and the speed-up there is small; a first
version without the width limit "converged" to widths of 4e8 nm there and differed from scipy by up to 0.1 nm.

Tests: `tests/unit/test_lspri_analysis_gaussian_fit.py`.

### Better first guess and neighbour warm start: tried, do not help (2026-10-24, scratch experiments, nothing merged)

Real scan, 6280 cubes (20 ROIs), fast solver; time per fit counts the whole solve, iteration counts are loop passes (cubes leave the batch as they converge).

| start | iterations median / max | time per fit |
|---|---|---|
| current: highest point, sigma = span / 6 | 16 / 41 | 0.083 ms |
| data-based: parabola through the top three points for the centre, width at half height / 2.355 | 17 / 42 | 0.091 ms |
| two stages: every 4th / 8th / 16th cube fitted first (data-based start), centre and sigma interpolated to the others | 10-13 / 32-38 for stage 2 | 0.111 / 0.108 / 0.106 ms **including stage 1** |

Why: (1) the iteration count is set by the *rate* of convergence, not by the distance of the start. With noise left in the residual the
Gauss-Newton step contracts the error ~10x per pass whatever the start, so a start 10x closer saves ~1 pass of ~16. (2) With 300 cubes in a batch,
a pass costs mostly numpy call overhead, so stage 1 on 40 cubes costs nearly as many passes as stage 2 on 300; the second stage saves 4-6 passes
but the first adds ~15. (3) Fitting "simultaneously" has no neighbours at the start; the two-stage version is the way to get them, and it is the
one that loses. Result of the same starting points: all variants reach the same centre to < 3.3e-7 nm.

What did move the time, applied the same day (maintainer: "apply all 3"):

1. **Stopping rule** (predicted gain 1e-12, step 1e-9; was 1e-15 / 1e-12): with Gauss-Newton -30 %, error vs the exact optimum 2e-7 -> 2e-6 nm.
   With the Newton step below the tolerance no longer matters (quadratic convergence); it stays at 1e-12 / 1e-9 because it is the more robust setting
   (a 1e-15 target can sit below the rounding noise of the cost).
2. **Newton step** instead of Gauss-Newton (`gaussian_fit._reduced_terms`): the exact 2 x 2 curvature of the cost of centre and sigma at the best amplitude
   and offset (the Schur complement of the four-parameter Hessian; derivation in the docstring, checked against finite differences by
   `CurvatureTests`), used where it is positive definite, the Gauss-Newton matrix elsewhere, always damped and accepted only if the cost falls.
   Gauss-Newton leaves out the residual terms and converges only linearly when noise is left in the residual (~10x per pass); Newton converges
   quadratically: **17 -> 6 passes** on the real scan, 0.074 -> 0.026-0.035 ms per fit (x2-3), error vs the exact optimum 2.4e-7 nm (better than
   Gauss-Newton at the loosest rule). A first damping of 1 (not 1e-3) and a /10 shrink after an accepted step keep the first Newton step from
   overshooting; a retry of rejected steps inside one pass was tried and removed (slower on real data).
3. **Bigger batches** (`trace_fit.traces_from_inputs`, `BATCH_ROWS = 2000`): the ROIs given together are merged by their wavelengths and fitted in
   batches of up to 2000 cubes (a pass costs mostly numpy call overhead); the helper process gets ~2000 cubes (6 ROIs) per task, progress is reported
   per task. Each cube is still fitted alone: the result is identical to fitting ROI by ROI (0.0 nm difference measured, pinned by a test).

Real scan, 160 ROIs, Gaussian, default window: **2.1 s through the helper process** (was 6.3 s with the first fast solver, ~87 s with scipy);
10 ROIs 0.12 s. In one thread: 1.9 s (maximum), 3.4 s (centroid: the curve of every cube is sampled and integrated). Against scipy `curve_fit`
(20 ROIs, windows of 26 / 17 / 13 points, maximum and centroid, 37 680 values): 63 s -> 2.4 s (x27), 0 gaps differ, largest difference 9.5e-4 nm
(median 6e-5), i.e. scipy's own error; nothing above 1e-3 nm.
