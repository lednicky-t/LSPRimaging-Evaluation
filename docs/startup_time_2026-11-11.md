# Startup time, 2026-11-11: where it goes, two fixes

Read from the 20 launch logs of 2026-11-09..11 (test dataset: 314 cubes x 27 wavelengths, 160 ROIs, 412 masks, OME-Zarr). Earlier record: `startup_time_2026-10-24.md`. Scratch tools that made the table (not in the repo): a per-log table of `app.build_stage`, `dataset.scan`, `session_load`, first `render.frame`, `analysis.scan_checked`, `app.startup_done`.

## Where the time goes (seconds from log start; typical launch, range in brackets)

| Stage | Thread | Time |
|---|---|---|
| Window build (`build_main_window`) | GUI | 1.8-2.8 s (10.6 once). Biggest steps: dock layout 0.4-1.3 s, Image panel 0.6-1.4 s, ROI table + Spectra + Sensorgram 0.4 s, Log console 0.3 s |
| Opening the dataset (`dataset.scan`) | worker | **1.2-6.0 s, the largest stage on the path to the first image**; grew from ~1.3 s (11-09) to ~3 s (11-10/11) |
| Session load (masks etc.) | worker | 1-2 s after the dataset |
| First image | | 4-8 s after log start |
| Scan check (`analysis.scan_checked`) | worker | 0.6-3.5 s (plan ~0.5 s, hashes ~1 s), the last step before "ready" |

Imports before the log starts (~2 s) are not in these numbers.

## Fix 1: the pump-plan search stat'ed every file next to the dataset

`io/pump_plan.py` `find_pump_plan_csv` looks for `<run>.csv` in the dataset folder and 3 parents. It called `Path.is_file()` (a disk call) on **every** entry before looking at the suffix: 8497 entries on the test dataset, 0.6 s of the 0.82 s that `load_dataset` takes alone, and much more in the app, where the GUI thread holds the interpreter lock during the window build and every disk call has to win it back (same effect as `dataset.file_stats`, 2026-10-24). Now `os.scandir`, suffix test first (string work), `DirEntry.is_file()` (no extra disk call on Windows). Same file found (checked on the test dataset); `load_dataset` 0.82 -> 0.35 s under cProfile, the plan search 0.60 -> 0.09 s. **Gain in a real launch not yet confirmed**: compare `dataset.scan dur_ms` in the next log (was 1.2-6 s).

## Fix 2: GPU drawing deleted the Image panel's floating widgets

Every launch logged 8 `Unhandled exception: wrapped C/C++ object of type CropSizeControls / MeasureCalibrationControls has been deleted`. Cause: `gpu_view.apply_gpu_drawing` calls `QGraphicsView.setViewport(QOpenGLWidget())`; Qt deletes the old viewport **with its children**, and the crop size fields, the measure calibration controls and the cursor readout are children of the viewport (`panel.py` ~818-853). So with GPU drawing on (the default) those three widgets did not exist, and each pan/zoom raised. The tests never saw it: offscreen Qt has no OpenGL, so the swap never happens. Now `gpu_view.replace_viewport` moves the old viewport's child widgets (place and visibility) to the new one first; test `tests/integration/test_lspri_gpu_viewport_children.py`. Not a speed issue. Unseen by eye: Crop size fields, Measure calibration and the cursor readout should work again with GPU drawing on.

## Fix 3: style sheets set after a panel's widgets existed (window build)

Profiled headless on the Windows platform (window built with blank settings, never shown, like the tests). The "dock layout" step (600 ms
there) was 0.40 s of `setStyleSheet` and 0.20 s of `QDockWidget.setWidget`: setting a style sheet on a widget makes Qt re-style **every
widget inside it**, ~0.14 ms each, and the Image panel holds ~880 widgets. Over the whole build, style sheets cost 0.6-1.1 s (925 calls).

* `PanelContainer.__init__` (`panels/dock_container.py`): `_apply_frame_style` now runs **before** `setWidget`, so the container's own
  sheet (`QDockWidget { color }`) is set while the container is empty. Same look: sheets cascade when the panel is put in. Benchmark
  (synthetic 360-widget panel, app theme active, min of 15): 264 -> 176 ms.
* `ImagePanel` top bar (`#imageTopBar { border: none; }`) and controls bar (`#imageNavigationBar ...`): set right after the bar is created,
  before the ribbon / controls are added. The rules match only the bar, so nothing on screen changes; the top-bar call was ~150 ms.
  The theme-switch hook (`refresh_theme`) still sets them again.

Expected: roughly 0.3-0.4 s off the window build. The machine was heavily loaded during the measurements (the same build took 2.3-5.7 s),
so only per-call numbers are reliable; check `app.build_stage` "dock layout" and "Image panel" in the next log.

**Left as is, by design**: the panel background sheet (`_content.setStyleSheet("background-color: ...")`, 140-490 ms on the Image panel).
It has no selector, so it applies to every widget inside the panel: that is what paints the panel's interior the darker card colour.
Making it cheaper means changing how the panels get their background (a visual change, needs checking by eye). The remaining
`setWidget` walk (Qt re-styles a panel once when it is put into its container) cannot be avoided without building the panels inside
their containers.

## Not changed (candidates, in order of expected gain)

1. **Window build**: Image panel construction 0.6-1.4 s, Log console 0.3 s (not profiled yet); the panel background sheet above.
2. **Scan check before "ready"** 0.6-3.5 s: the image is already on screen; only the "ready" moment waits.
3. **Session load** 1-2 s (412 masks decoded).
4. `load_ome_zarr_dataset` builds 8478 `Path` objects (~0.2 s): small.

## ROI / Groups calculation after start-up: 11 s -> 3.1 s (later on 2026-11-11)

The start-up calculation of the table's step columns (`app_rewrite._wire_startup_calculation`) fitted 159 ROIs in **11 s**
(`step_table.compute fit_ms=10885`, template shift). Alone, on the same scan and settings, the same fits take **1.2 s** in the one helper
process (scratchpad `trace_parallel_check.py`). The difference: a remembered Performance mode started its 7 warm scan workers at the same
moment ("perf run analysis.scan: 7 workers", `analysis.pool_ready` 8 s later); ~1,000 imports each took the CPU from the fit helper, which
runs at low priority.

Fix: `_wire_startup_calculation` now starts the warm pool, only once the ROI / Groups run has ended (`PanelTask.ended`, new: sent for
every run, also one too short to show in the status bar), or at once when there is nothing to calculate; the 20 s fallback for a launch
without a dataset waits the same way. Measured on a bench launch: **ROI / Groups 3.1 s**, warm pool ready 3.7 s after it (was 8 s).
Tests: `tests/integration/test_lspri_startup_pool_order.py`.

Tried first and reverted: fitting over several helper processes (`lspr_core.perf`). The traces were identical (all 50,240 values), but
starting 7 helper processes took 11.75 s against 1.2 s for the fits themselves. Saving the fits to disk was not built (maintainer's choice:
no new saved file).
