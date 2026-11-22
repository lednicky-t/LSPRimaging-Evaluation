# Cost of re-ordering the plots (legend order, draw order, legend corner), 2026-11-22

Maintainer: "the reordering plot data plotting (overdrawing over each other) is kind of slow". Measured, nothing changed yet.

## What happens today

Every change of the Legend menu (order key, direction, value, corner) or of the Draw order goes `PlotLookControls.settings_changed` -> `SensorgramPanel._on_view_changed` -> `_draw_last` -> `SensorgramPlot.show_series`
(the LbL / S_bulk windows: `_look_changed` -> `redraw`). That is a **full redraw** of every line, although a reorder changes only the stacking z of the items and the legend rows. (The legend corner added the same day
takes the same path: it only moves the legend.)

## Numbers (offscreen, real classes, synthetic lines; scripts were in the scratchpad: `reorder_bench.py`, `panel_bench.py`, `windows_bench.py`)

| Case | Today (redraw) | Repaint of the unchanged scene | Stacking z only (prototype) + repaint |
|---|---|---|---|
| Sensorgram 160 lines x 400 cubes (`_draw_last`) | 87-100 ms; with the repaint 130-150 ms | 33-43 ms | 4 ms; 26-34 ms |
| Sensorgram 160 lines x 2000 cubes | 195-205 ms | | |
| Sensorgram 20 lines x 400 cubes (legend shown) | 75-90 ms | | |
| LbL window, 10 / 40 groups x 24 steps | 76-93 / 83-90 ms | | |
| S_bulk window, 10 / 40 groups x 24 steps | 101-110 / 116-120 ms | | |

Where it goes (cProfile, per redraw):
- **Sensorgram, many lines (no legend):** `autoRange` ~47 of ~81 ms (pyqtgraph measures every curve's bounds again, 2 x 160 `dataBounds`, although the data are the same), `setData` of 160 lines ~16 ms, pen objects ~7 ms.
- **Sensorgram, <= 24 lines (legend shown):** `_rebuild_legend` ~48 of ~56 ms. `LegendItem.addItem` calls `updateSize()` for **every row added**, and `_Legend.updateSize` activates the layout each time: n rows = n layout passes (120 `updateSize` calls per 5 redraws of 20 rows, ~110 ms of 240 ms).
- **LbL / S_bulk windows:** the same legend fill (~22 ms for 12 rows); S_bulk also recomputes `bulk_values` / `line_weights` / `_band` for every line (~35 ms) on a reorder; `item.clear()` and a rebuild of all items.

## Options (not built; ordered by risk)

A. **Legend filled with one layout pass** (`plot._Legend`: skip `updateSize` while the rows are added, one call at the end). Same rows, same sizes (a test compares the geometry); removes most of the legend cost in all three plots.
B. **Restack only (Sensorgram):** when only the order keys / draw mode / corner changed and the result is the same, keep the items, set their z (`pool.take(..., z=)` already knows each item's base z), rebuild the legend, skip `setData` and `autoRange`: ~130-150 -> ~35-45 ms at 160 lines. Needs a per-line list of items from `show_series`, and the rule for "nothing but the order changed" (compare the new `SensorgramViewSettings` with the old one field by field, same `_last`).
C. The same for the windows (they `clear()` and rebuild; cache per-line items and, for S_bulk, the per-line numbers).

The floor is the repaint of 160 antialiased lines (~35-40 ms); only a smaller number of lines or non-antialiased drawing lowers it.

## Built (same day, A + B + C)

- **A** `plot._Legend.add_entries`: the rows are added with one layout pass (`updateSize` is skipped while filling). Used by the Sensorgram's `_rebuild_legend` and the windows' `fill_legend`.
- **B** `SensorgramPlot.restack(series)` + `can_restack(series)`: `show_series` remembers per line its items with their base z (`_take_stacked`) and its layered-export parts; a reorder sets `z = base + dz` and the parts' order, rebuilds the legend (and with it the table's rows and the corner), and skips `setData` / `autoRange`. `SensorgramPanel._reorder_last` uses it for every view change and for a new plan-bar step; **`can_restack` is true only for the same line objects and a view that differs from the drawn one only in `SensorgramViewSettings.differs_only_in_order`** (legend sort key / direction / value, draw mode, legend corner, cursor toggle). Anything else, other lines, or nothing drawn yet: `_draw_last` as before. A new option that only moves rows belongs in that method; every other option draws again.
- **C** `plot_window_draw.DrawnState` (the look drawn with, the entries, the items per line) in both windows; `_look_changed` re-orders (`_apply_order`) when `can_reorder`, else `redraw`. `LegendEntry.value_at` lets a moved cursor re-read each line's number at its new place without drawing. `set_lines` / `set_steps` / `set_weighting` drop the record.
- Found on the way: `FillBetweenItem.setCurves` sets the band's z to -1, so in a full redraw all bands of the Sensorgram shared one z and did not follow the Draw order; the plot now sets `_Z_BAND + dz` after `setCurves` (a band lies behind every line and with its own line's place, as in the windows).

Measured after (same scripts; flip of the legend direction, the whole signal -> paint, offscreen): 160 lines x 400 cubes **~140 -> ~23 ms**; 160 x 2000 **~210 -> ~70 ms**; 20 lines (legend shown) ~75-90 -> ~35-50 ms (the legend still builds one label per row; reusing the rows was left out: pyqtgraph's layout bookkeeping makes it fragile); LbL / S_bulk windows (10-40 groups) ~76-120 -> ~41-63 ms. A full redraw with a legend is also cheaper (one layout pass).
Tests: `tests/integration/test_lspri_reorder_fast_path.py` (fast path == full redraw for 9 kinds of change, with and without a legend; no `setData` during a restack; validity rules; panel and window routing; one-pass legend == row by row).
