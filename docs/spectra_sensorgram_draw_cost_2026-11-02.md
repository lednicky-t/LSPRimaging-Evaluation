# Spectra / Sensorgram draw cost, and what is left in the Image panel (2026-11-02)

Performance review requested by the maintainer. Nothing in the app was changed; these are measurements and proposals.

## Spectra / Sensorgram: the GUI-thread draw is the bottleneck, not the fits

The fits already run off the GUI thread (Sensorgram in the helper process, `analysis/trace_pool.py`). The *drawing* runs on the GUI
thread and is far more expensive. Measured offscreen with the real plot classes (`SensorgramPlot.show_series`,
`SpectraPlot.show_spectra`), synthetic data at real scale (160 ROIs, 41 wavelengths). Scripts:
`bench_plots.py`, `bench_variants.py`, `bench_pen.py` (session scratchpad; easy to recreate from this note).

| What | 400 cubes | 2000 cubes |
|---|---|---|
| Sensorgram, 160 lines: `show_series` (GUI thread) | 1.7 s | 4.6 s |
| Sensorgram, 160 lines: one repaint | 0.25 s | 0.8 s |
| Sensorgram, 160 lines: cursor move (repaint) | 0.43 s | 1.1 s |
| Spectra, 160 ROIs: `show_spectra` per cube change | 2.2 s | - |
| Spectra, 10 ROIs | 0.12 s | - |

Compute times from the logs (session 20261026-154455): `spectra.compute` 212 ms for 160 ROIs (thread in the app process),
`sensorgram.stages` gather 241 ms + fits 383 ms for 160 new ROIs.

### Where the time goes (cProfile of one 160 x 400 redraw, 1.85 s)

* ~1.1 s: per-point marker bookkeeping (`ScatterPlotItem.addPoints/updateSpots/_keys`), the dots on every point
  (`symbol="o"` in `sensorgram/plot.py:224`), recomputed ~3x per item (construction, add, view-range change).
* ~1.0 s: removing and re-adding every item to the scene each redraw (`PlotItem.addItem` -> `changeParent/_updateView`).
* 0.26 s: painting `connect="finite"` curves (pyqtgraph draws them as separate segments, `drawLines`).

### Candidate fixes (plain pyqtgraph, 160 lines; build = setData+autoRange+events, paint = one full render)

| Variant | 400: build / paint | 2000: build / paint |
|---|---|---|
| A today (recreate, dots, finite) | 1631 / 271 ms | 4619 / 803 ms |
| B reuse items, `setData` (dots) | 678 / 266 | 2070 / 973 |
| C recreate, no dots | 642 / 163 | 1064 / 644 |
| D reused curves, `connect="all"`, `skipFiniteCheck`, no dots | 191 / 161 | 824 / 1575 |
| F reused, peak downsampling + clip to view | 313 / 170 | 668 / 586 |

**Pen width dominates the paint** (Qt's raster engine has a fast path only for 1 px lines):

| 160 curves | 1.5 px | 1.0 px | 1.5 px antialiased | 1.0 px antialiased |
|---|---|---|---|---|
| 400 points | 140 ms | 17 ms | 653 ms | 30 ms |
| 2000 points | 602 ms | 55 ms | 2302 ms | 65 ms |

Today: Sensorgram lines 1.5 px, Spectra fit line 2 px, both not antialiased.

### Other findings

* Spectra recomputes every selected ROI on every cube change (`spectra/panel.py:364`), in a thread of the app process (GIL
  contention, the Sensorgram freeze of 2026-10-24); with the template shift it also rebuilds each ROI's reference template
  every time (`scan_view.py:98`). Nothing is cached per cube.
* `cosmetic_changed` (e.g. a ROI colour) schedules a full recompute in both panels (`spectra/panel.py:369`,
  `sensorgram/panel.py:341`); a redraw of the last result would do.
* Import cycle: importing `panels.sensorgram` first fails (`sensorgram.panel` -> `live_toggle` -> `image.general_group` ->
  `image/__init__` -> `analysis_tab` -> `live_toggle`). The app's import order hides it.

## Implemented the same day (maintainer: "do all"; uncommitted)

* `panels/plot_item_pool.py`: items are reused between redraws (taken, given new data, the rest hidden; a hidden item is out of the auto-range).
  `_items` on both plots is now the list of items drawn.
* Sensorgram (`sensorgram/plot.py`): lines are `PlotCurveItem`, 1 px antialiased (`LINE_WIDTH`, was 1.5 px); `connect="all"` +
  `skipFiniteCheck` when a trace has no NaN, `connect="finite"` otherwise (a gap stays a gap); the dots on the points only up to
  `DOTS_MAX_LINES` = 10 lines, as a separate item out of the auto-range (it would widen the range by its radius); z order fixed by kind.
* Spectra (`spectra/plot.py`): the fitted curve is 2 px up to `FIT_WIDE_MAX_ROIS` = 10 ROIs, 1 px antialiased above; one item for every
  ROI's metric marker. Display choices of the View menu unchanged.
* Spectra panel: cache of computed spectra per (cube, ROI, formula, metric) for the present scan (`_CACHE_MAX` 4000, LRU, emptied by
  `results_changed`); template references cached per ROI (`scan_view.compute_spectra(templates=...)`); fits in the helper process
  (`scan_view.prepare_spectra` + `fit_spectra`, `TracePool.run_spectra`; in the thread while the process is not started or a Sensorgram run is
  going); a ROI colour / group change redraws without computing (`_RECOMPUTE_COSMETIC_CHANGES`).
* Import cycle: `style_general_icon_button` moved to `panels/icon_button_style.py` (re-exported by `image/general_group.py`).

Measured after (same offscreen benchmark, 160 ROIs; noisy machine, medians of 5):

| What | before | after |
|---|---|---|
| Sensorgram `show_series`, 400 cubes | 1686 ms | 80-90 ms |
| Sensorgram one repaint, 400 cubes | 246 ms | 39-44 ms |
| Sensorgram cursor move, 400 cubes | 433 ms | 60 ms |
| Sensorgram `show_series`, 2000 cubes | 4642 ms | 204 ms |
| Spectra `show_spectra` | 2248 ms | 370-630 ms |

Not changed in numbers: the process gives bit-identical spectra for the polynomial, Gaussian and template fits
(`tests/unit/test_lspri_analysis_trace_pool.py::SpectraInProcessTests`). What is left in the Spectra draw at 160 ROIs is per-item pyqtgraph
overhead (a points item with its dots and a fit curve per ROI).

## py-spy profile of a real Play (same evening, maintainer's dataset, OME-Zarr, Sensorgram + Spectra docked)

`py-spy record --pid <app> --duration 20 --rate 200 --threads --format raw`, summarised per thread. Play ran at ~16.5 fps (target 29.6),
GUI thread 83-85 % busy, `gui_cpu_ms` ~50 per frame. GUI thread, by the first app file under the event loop:

| Where | share of GUI samples |
|---|---|
| Image canvas paint (`timed_canvas`: image blit, overlay layer blit `overlay_cache.draw`) | 20 % |
| Other pyqtgraph views repainting under their moving cursor (plan bar 5 %, axes 4 %, dots 3 %, step-stats table 3 %, curves 2 %) | 20 % |
| Cube / wavelength sliders `DataAxisSlider.paintEvent` (every tick and label drawn in Python, every frame) | 17.5 % |
| `image/panel.py` (redraw glue, highlight, no-data layer) | 15 % |
| Qt's own C++ only (layout, native widgets) | 14 % |
| timeline strip 4 %, slideshow 2 %, Spectra 2 %, Histogram 2 %, task indicator 2 % | |

Reader threads: ~60 % of their samples at `plane_reader._fast_read_zarr_plane:291` (the chunk read itself). Render thread: half in
`prepare_frame_display` lines 126 / 135 (the 8-bit conversion and the RGBA merge).

Fixed after the profile (uncommitted):
* `DataAxisSlider`: rail, ticks, labels, gap breaks and marks drawn once into a pixmap (`_static_picture`, keyed on size, theme, enabled,
  zoom window, ticks version, cached ticks and marks); only the handle is drawn per paint. 7.1 ms -> 0.16 ms per value change (offscreen,
  400 ticks). A zoomed slider still redraws every frame (its window follows the value).
* Sensorgram: the plan bar, the step-stats table and both axes use Qt's `DeviceCoordinateCache`; Spectra: both axes. A cursor move (full
  viewport repaint, 6 lines, 8 steps, table on) 31 -> 7 ms. The table is told to repaint when the legend moves or changes (it reads the
  legend's rows while painting).

**Confirmed in the app** (maintainer, same dataset and layout): Play 16.5 fps -> 21-25 fps.

## Image panel (Play): what is left

From `playback.summary` (session 20261102-162648, 160 ROIs, 10x): fps 12.5-15 at target 23-30, GUI busy 76-79 %,
`gui_cpu_ms` 51-63 per cycle but the timed `d_*` stages add up to ~25 ms (`d_paint_view_ms` 16.5). **About half of the GUI
thread's CPU per frame is not attributed to any stage.** Plane reads 36-48 ms for a 4 MB plane (3 per frame, 2 readers) vs
~12 ms decode standalone (testing7 analysis), and `pic_ms` 16-24 ms for ~6 OpenCV passes: both are inflated by waits for
the interpreter lock while the GUI thread is busy (`lspri_play_pic_ms_gil_handoffs`).

Options, in order: (1) a sampling profile of a Play (py-spy, attaches to the running app, no code change) to name the
unattributed ~25 ms; (2) try an OpenGL viewport for the image view (GPU scales the picture; driver risk on lab PCs);
(3) move reads + render into a separate process with shared-memory frames (removes the lock contention for the whole
render pipeline; large); (4) free-threaded Python (no GIL) only once PyQt6 supports it.
