# ROI resize drag: cost per step (2026-11-19)

Maintainer report: "I am getting some freezing, not smooth action" while resizing a ROI (LMB held). Nothing was changed for this; the numbers and the options are
below. Benchmark: `tools/bench_roi_resize_drag.py` (offscreen, real `ImagePanel` and `RoiGestures`, 1200 x 1000 image, one selected circle dragged).

## What the app's own log says (today's three sessions, 162 ROIs, one ROI dragged)

- A step is **~20 ms apart (median 19-23 ms, p90 28-50 ms)**: 45-50 steps per second. The toolbox write (`RoiToolbox.resize_roi`, `roi.geometry_write`) is
  **0.2 ms** and every listener on `geometry_changed` together is 0.1-0.2 ms (max 5 ms). Undo, signals, the session and the scan check are not the cost.
- The gaps of 450-600 ms inside a drag are **not** the mouse standing still (first read wrongly, corrected the same day): 88 of 123 gaps are 450-549 ms and
  none are 150-449 ms, so a stop of ~100 ms or more is always followed by about half a second before the next step. See "Lag after a stop" below.
- The new arrow cursor costs 0.15 ms per step (a new angle's cursor is built once: 0.2 ms median, 1.9 ms max; cached 7 us).

## Where a step goes (benchmark, ms per step)

| ROIs | overlay rebuild (`_redraw_now`) | viewport repaint | step total (about) |
|---:|---:|---:|---:|
| 1 | 0.8 | 1.9 | 5 |
| 162 | 3.0 | 12 | 16-25 |
| 500 | 7.7 | 33 | 40-66 |
| 1500 | 28 | 103 | 130-250 |

The cost grows with the **total** number of ROIs, not with the one being dragged: `RoiGestures._redraw_now` forces `_draw_roi_overlay()`, which rebuilds the
arrays of every ROI (`layers.invalidate()`), and the change invalidates the whole overlay, so Qt repaints every ROI. `SampleCirclesItem.paint` (about half of the
repaint at 500 ROIs) draws every circle on every paint, whatever part of the screen is dirty (it does not look at the exposed rectangle). The layer cache
(`overlay_cache.py`) deliberately caches nothing while the epoch changes at every step, which a drag does (`_roi_revision`).

## Lag after a stop (the maintainer's report: "when I stop for a moment and then move, still holding LMB, it lags for hundreds of ms") - FIXED 2026-11-19

Cause: every panel that rebuilds on a ROI change waits for ~100-150 ms of quiet (a debounce timer). During continuous movement the timer keeps restarting, so
nothing runs; a pause of the mouse lets it fire in the middle of the drag, and the GUI thread is busy for hundreds of ms. Full main window, offscreen, 162 ROIs,
event-loop time blocked after the stop (`tools` scratch script, GUI thread sampled with `applog/sampling.py`): 109 ms with everything visible, 88 ms with the
Histogram hidden, 73 ms with the ROI / Groups table hidden, **9 ms with both hidden**; the table's `_rebuild` and paint and the Histogram's
`union_roi_masks` / `redraw_counts` (a Python-heavy worker thread, which also competes for the interpreter lock) were what ran. The Export tab's estimate refresh
(`export_tab._refresh_estimates`, debounced the same way) was checked and costs 1 ms. On the maintainer's data (8,478 planes) the gap is ~500 ms, and **hiding the
ROI / Groups table by hand removed it**, so the table is the cause there; neither the table nor the Histogram logs a timing, so the log cannot split it further.

Fix: `HiddenPanelGate.set_held` (a second reason to wait, independent of "hidden"); `RoiGestures.edit_running` (True when a move / resize / rectangle handle / border
drag starts, False when it ends) -> `ImagePanel.roi_edit_running` -> `RoiTablePanel.set_edit_running`, wired in `app_rewrite.py`. The table does not rebuild during
the drag and catches up once on release. Re-measured the same way: 39 and 63 ms blocked (largest pass 22 ms, was 50), equal to hiding the table (45 ms).
Not done, deliberately: the Histogram (no evidence on the maintainer's machine that it matters; one line `histogram_panel.set_edit_running`-style if it ever does),
Spectra, Sensorgram, step table, signal map. The panels show the old numbers during a drag (they already never updated while the mouse moved) and the new ones on release.

## Options for the cost per step (not built; the maintainer chooses)

1. **Drag preview layer** (largest win, the real fix). At drag start the picture of everything except the dragged ROIs is frozen (the layer cache's own
   mechanism, kept for the whole drag), the dragged ROIs are drawn live in a small item of their own (circle + ring, rectangle + band + handles), and one full
   redraw happens on release. A step then costs about the 1-ROI row above (~5 ms) at any ROI count. Touches `RoiOverlay`, `overlay_cache.py`, `RoiGestures`;
   antialiasing of the frozen part is blended through a transparent layer (already accepted for Play, `overlay_cache.py` docstring).
2. **Partial redraw**: rebuild only the changed rows, invalidate only their rectangle, and cull circles outside the exposed rectangle in
   `SampleCirclesItem.paint`. Smaller, but every overlay item (rings, fill, labels, selection, handles) must do the same or the whole view repaints anyway.
3. **Cheaper look while dragging only**: no antialiasing, no fills / labels, 1.0 px pen (the module notes Qt strokes 1.5 px ~5x slower than 1.0 px). Quick, but
   the picture changes during the drag and the cost still grows with the ROI count.

Whatever is built: measure with the benchmark before and after (CLAUDE.md, performance rule), keep the final picture after release identical to today's.
