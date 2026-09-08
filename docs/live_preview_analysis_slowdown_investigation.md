# "Live" preview slows down "Start analysis" - root cause (2026-09, not yet fixed)

## Symptom

Toggling the Analysis section's "Live" preview on measurably slows down a
running "Start analysis" sweep, even though the background computation and
the GUI redraw are nominally on separate threads.

## Thread wiring is correct

`_sensorgram_metric_task` (`gui/analysis_tasks.py`) runs on a plain
`threading.Thread` via `FunctionWorker.start()` (`gui/worker.py`) - not
`QThreadPool`, deliberately, per `qthreadpool_zarr_crash_investigation.md`.
It reports back to the GUI thread only through `pyqtSignal` (`.partial`,
`.progress`, `.result` on `WorkerSignals`), which Qt auto-queues across
threads. Nothing `.join()`s or blocks the worker on GUI work, and the
partial-result handling is itself coalesced onto two singleshot `QTimer`s
(`_sensorgram_curve_update_timer`, 100ms; `_sensorgram_live_preview_timer`,
80ms - both set up in `MainWindow.__init__`, main_window.py) specifically so
a redraw doesn't fire once per finished cube.

## The actual bug: the "cheap" live redraw isn't cheap

`AnalysisWorkerMixin._apply_pending_sensorgram_live_preview`
(`gui/analysis_worker_mixin.py:1023`) is documented as a "trimmed
redraw-only path... no fit/metric recompute - the sensorgram loop already
has the metric." That claim is false in the current implementation: at
line 1067 it calls `window._compute_spectrum_series_data(roi_result)` for
**every selected ROI**, which unconditionally routes through
`PlotManager.analysis_fit_result_from_spectrum` ->
`fit_curve_for_method` (`processing/analysis.py:362`) - a real curve fit
(`np.polyfit`-based for "Poly", or `scipy.optimize.curve_fit` - an
iterative nonlinear least-squares solve - for "Gaussian").

This fit:
- runs on the **GUI thread** (inside the 80ms timer callback), and
- runs **once per selected ROI**, every time the timer fires, and
- is thrown away immediately after rendering - it is a completely separate
  computation from the fit `_sensorgram_metric_task` already did on the
  background thread at `analysis_tasks.py:1323` (that fit is on the
  *combined* spectrum used for the metric value; the live-preview fits are
  per individual ROI, so they aren't even literally the same computation -
  see "why not just reuse it" below).

Because CPython threads share one GIL, a nonlinear `curve_fit` running on
the GUI thread (its objective function is a plain Python callable,
`_gaussian_model`, reinvoked many times per fit) repeatedly competes with
the background analysis thread for the GIL and for CPU. That contention is
the slowdown - not a lock, not a `.join()`, just uninstrumented duplicate
scientific work landing on the wrong thread every ~80ms for the duration of
the run.

## Why not just reuse the worker's fit

`_sensorgram_metric_task` computes one fit per cube, on the *combined*
spectrum (`spectrum.formula_values`, potentially averaged across a
multi-ROI group) - that's what the metric value is derived from. The
live-preview redraw draws one curve *per individual ROI*
(`roi_formula_spectrum_results`, i.e. `spectrum.area_roi_results`), which is
a different signal whenever more than one ROI is selected. So the worker's
fit can't just be threaded through and reused as-is for the multi-ROI case
- only the single-ROI-selected case would be a literal match.

## Proposed fix (not yet applied - maintainer chose diagnosis-only for now)

Add a `skip_fit: bool = False` parameter to
`PlotManager.compute_spectrum_series_data` (`gui/plot_manager.py:184`) and
thread it through `MainWindow._compute_spectrum_series_data`
(`main_window.py:6278`). Call it with `skip_fit=True` only from the live-
preview path (`analysis_worker_mixin.py:1067`) - `render_spectrum_series`
already handles `fit=None` gracefully (skips drawing the fitted curve,
still draws the raw points), so this is a small, contained change. The
other (and only other) caller of `compute_spectrum_series_data`
(`analysis_worker_mixin.py:2015`, the normal single-cube spectrum display,
`_apply_absorbance_spectrum_result`) is untouched and keeps fitting as
before.

Effect: during a running sweep with Live on, the spectrum panel shows only
the raw points (no fitted curve) per redraw; the final, fully real redraw +
fit still happens unconditionally once the run completes
(`_apply_cached_sensorgram_result`), same as today. This restores what the
existing docstring already claims the function does.
