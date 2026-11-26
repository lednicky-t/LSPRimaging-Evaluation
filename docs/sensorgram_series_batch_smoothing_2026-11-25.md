# Sensorgram: why `build_series` took 21 s, and the batched smoothing (2026-11-25)

## What the log showed

Session log `logs/lspri-20261125-215352-40204.jsonl`, one Sensorgram run (160 ROIs, 2655 cubes, 27 wavelengths, a 60-cycle layer-by-layer pump plan):

| stage | time |
|---|---|
| gather | 0.16 s |
| polynomial centroid fits (helper process) | 11.4 s (once per ROI, then cached) |
| **`build_series`** (`sensorgram.stages` `series_ms`) | **20.8 s** (~130 ms per ROI) |
| from compute end to the task indicator closing | ~5.6 s (the GUI thread: `_on_result` -> `_draw_last`; was not timed) |

Earlier sessions show the same per-ROI cost for a single ROI with nothing new to fit (`series_ms` 75-195 ms), so it repeats on **every** change of a smoothing, baseline or display setting, not only the first time.

## Cause

"Smooth inside steps" cuts each trace into one run per plan step (`step_stats.membership_segments`) and `statistics._smooth_by_runs` called `scipy.signal.savgol_filter(mode="interp")` once per run **per ROI**. A call has a fixed cost of ~0.43 ms (scipy re-solves its coefficients and its two edge least-squares fits every call), whatever the run length. A 240-step plan therefore costs ~100-130 ms per ROI; 60 steps 26 ms; one whole-trace call 0.5 ms. Hampel (0.2 ms), the drift fits (0.2-0.6 ms) and `step_statistics` (vectorized) are not the problem.

## Fix

`statistics.filter_traces(matrix, settings, segments)` does spike rejection + smoothing for **all ROIs at once** (`[roi, cube]`), calling `savgol_filter(..., axis=1)` once per run for all ROIs. `scan_trace._build_lines` uses it, then `apply_baseline_with_fit` per ROI (the baseline half of `apply_statistics_with_fit`, which is now filter + baseline and unchanged in behaviour). `apply_statistics` / `trace_matrix` (ROI position tuning) still go row by row.

NaN rule is unchanged: a ROI row with gaps is filled by interpolation, filtered, and its NaN positions restored; an all-NaN row stays all-NaN.

## Before / after (synthetic 160 x 2655, 240 steps + gap cubes + one-cube runs + NaN rows; script was in the session scratchpad)

| setting | old row by row | new batch | max abs difference |
|---|---|---|---|
| Savgol 15/2, inside steps | 29 650 ms | 190 ms (x156) | 1.0e-12 nm |
| Savgol 15/2, whole trace | 94 ms | 10 ms | 6.8e-13 nm |
| Savgol 31/3 + Hampel + pchip anchors | 23 119 ms | 325 ms (x71) | 1.0e-12 nm |
| moving average 15, inside steps | 1271 ms | 1392 ms (not batched) | 0 |
| running median only | 25 ms | 27 ms | 0 |

NaN patterns and baseline values identical in every case. The 1e-12 nm is float rounding (the edge fits run as one multi-column least squares). No stored number changes (statistics are display-only, `SCAN_ALGORITHM_VERSION` untouched). Pinned by `tests/unit/test_lspri_trace_statistics.py::TestFilterTracesBatch`.

## Still open

- **Moving average inside steps** is still ~1.3 s for 160 ROIs / 240 steps (one `np.convolve` pair per run per ROI). Not batched: `np.convolve(mode="same")` centring for even windows differs from `ndimage` filters, so a batch needs care. Do it only if it is felt.
- **Found, 22:35 session (`lspri-20261125-223559-36508.jsonl`)**: `series_ms` 941 ms (was 20 800) in the real app. `sensorgram.draw`: first draw 11.0 s, of which `steps_refresh` 10.96 s (`show_series` only 60 ms); the next two draws 0.33 / 0.36 s, again almost all `steps_refresh` (0.31 / 0.35 s). The first draw lay inside the 35.6 s `scan-results-check` hashing (32.8 s of Python CPU, same process); the later ones after it. Reading: `StepOverlays.refresh` is *not* cheap with 241 plan steps (0.3 s uncontended; its docstring says "a few steps"), and under the hash thread the GUI thread's many small calls each wait for the GIL (~35x slower). Not yet proven: `sensorgram.steps_refresh` / `sensorgram.stats_table` stage records were added to split the 0.3 s (and the 11 s) by stage.
- (earlier note) **The ~5 s after the compute** is GUI-thread work in `_on_result` -> `_draw_last`. Not measured: the app cannot be driven here. `_draw_last` now logs one `sensorgram.draw` record (stages `prepare`, `axes_and_marks`, `show_series`, `order_table`, `drift_and_window`, `steps_refresh`, `status`, `lines`). Read it after the next run with 160 ROIs: `python -m lspr_imaging_app.applog grep --event sensorgram.draw FILE.jsonl`. Earlier measurements put the draw of 160 lines at 1.7-4.6 s (`spectra_sensorgram_draw_cost_2026-11-02.md`), so `show_series` is the first suspect.
- During the run, the scan's out-of-date check (`scan-results-check`, 37.5 s of hashing in Python) was running at the same time and shares the GIL with the Sensorgram thread; it slows the series stage somewhat but was not the main cause.

## Second finding: the draw after the compute was `build_bar_steps` (22:50 session, `lspri-20261125-225036-39392.jsonl`)

`sensorgram.steps_refresh` (new stage timing): first draw 4718 ms, of which **`build_bars_ms` 4658**; the draw after the hashing check ended: 270 ms, `build_bars_ms` 219. Everything else in the draw (`show_series` 37 ms, `plot_set_plan` 4 ms, evaluation 50 ms, stats table 0.2 ms) is small.

Cause: `plan/bar_model.build_bar_steps` called `section_levels` once per step (240) and each call filtered every line (16 x 2655 points) with five full-length numpy operations: ~19 000 calls. Numpy releases the GIL on arrays this long, so while the scan-results-check thread (27-35 s of Python hashing in the same process) held it, every call waited to get it back: 0.22 s became 4.7 s.

Fix: `section_levels_per_step` filters/sorts each line once and finds each step with `searchsorted`; `section_levels` is kept as the one-step reference. Result **bit-identical** (test `SectionLevelsPerStepTest`, plus a 240-step x 16-line comparison with NaN gaps, an unsorted line, ties, empty / outside / NaN / reversed bounds); `build_bar_steps` on 240 realistic steps: ~32 ms (240 `np.percentile` calls are most of it). Measure again in the app: `sensorgram.steps_refresh` `build_bars_ms`.

Rule of thumb from this: a loop of many small numpy calls on arrays of > ~500 points is slow *only while another Python thread is busy*, because each call hands the GIL away; do the full-length work once per line, not once per step.
