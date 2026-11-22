# Legend row order + plot cursor (2026-11-22)

Maintainer request: the Legend menu of every plot can put its rows in order, ascending or descending (arrows): by **Group** (default), **Name**, or **Value**; Value is the line's mean over the
whole plot, or its value at one step. The LbL and S_bulk plots have no steps, so they get a cursor like the Image panel's; every plot has a Cursor visibility toggle.

## Decisions (asked 2026-11-22)

- Only the **legend rows** (and the Sensorgram's step-table rows, which are the legend's rows) move. Line colours, drawing order and numbers are untouched.
- **One key + one arrow**, not stacked levels. Ties and the default order: the lines' own order (a group's number, a ROI's id), in both directions. A missing number (NaN) is always last.
- Sensorgram "At step" = the **one step selected on the plan bar** (`StepValues.mean[index]` of each line, the numbers the step table already shows). None or several selected: default order and a
  note in the menu ("Select exactly one step on the plan bar").
- LbL / S_bulk cursor = the Image panel's `AxisCursor`, **snapping to a step** (a cycle; a step's refractive index), so the value is a measured one. Steps of one solution share one RI: their
  means are averaged (said in the note). Drawn only while the order reads "At step" and the Cursor toggle is on; hiding it does not change the step the order uses.

## Where it lives (`panels/sensorgram/`)

| File | What |
|------|------|
| `legend_order.py` | Pure (no Qt): `LegendOrder`, `order_rows(names, values, order)`, `natural_key` ("ROI 2" before "ROI 10"). Unit tests: `tests/unit/test_lspri_legend_order.py`. |
| `view_settings.py` | `legend_sort`, `legend_descending`, `legend_value`, `show_cursor` on `SensorgramViewSettings`. |
| `plot_look.py` | The menu rows (Order + arrow, Value of, Cursor, a note row), remembered keys, Reset. **Shared by all three plots**, as the rest of the Legend menu (`PlotLookControls`). |
| `plot_window_draw.py` | `LegendEntry` + `fill_legend` (the windows' sorted legend), `StepCursor` (snapping cursor; remembered place), `mean_of`, `cursor_value_at`. |
| `plot.py` | Sensorgram: `_rebuild_legend` writes the rows in order; `_legend_rank` maps a legend row to its line, so `_legend_rows` hands the step table the rows **in the lines' order**; `set_order_step`, `legend_order_note`; `set_view` hides / shows the cube cursor. |
| `lbl_plot.py`, `bulk_plot.py` | Collect `LegendEntry`s while drawing, `_apply_order` fills the legend and shows the cursor. |
| `panel.py` | `_sync_order_step`: plan-bar selection -> `plot.set_order_step`, and the menu's note. |
| `../axis_cursor.py` | `AxisCursor.set_hidden` (keeps its place). |

Tests: `tests/integration/test_lspri_legend_order_plots.py`.

## Notes for the next change

- `PlotItem.clear()` removes the cursor's line from a window plot: `StepCursor.readd()` must follow every `clear()`.
- A new plot gets the order by calling `fill_legend` (windows) or `order_rows` (own legend), never by sorting its lines.
- Not done: the Spectra panel's own View menu has no Order rows (it is not a `PlotLookControls`); the maintainer said "plots with a label menu", so it was left out.
- Unseen by eye.

## Legend corner (2026-11-22)

The Legend menu of every plot has a **Corner** row: a 2 x 2 block of cells (the ROI-label picker's look), one per corner of the plot area. Default top-left (where the legend always was).

- Pure rule: `panels/sensorgram/legend_corner.py` (`corner_offset` = the signs pyqtgraph's `setOffset` reads: positive = from left/top, negative = from right/bottom; never exactly 0). Picker: `legend_corner_picker.py`. Setting: `SensorgramViewSettings.legend_corner`; one row in `PlotLookControls`, so all plots have it; keys `..._legend_corner` (Sensorgram `sensorgram/view_legend_corner`, windows by prefix).
- Applied by `plot._Legend.place_in_corner`, **only when the corner changes** (so a legend the user dragged stays where it was dropped through every redraw; picking a corner puts it back). Windows: `plot_window_draw.style_legend`. Sensorgram: `SensorgramPlot._place_legend`.
- **Sensorgram with the step table on:** the table owns the legend's place (its rows line up with the cells), so the corner has no effect then; the menu says so (`CORNER_STEP_TABLE_NOTE`, `legend_corner_note_changed`), and the corner is applied again when the table goes off. The table is on by default, so in the Sensorgram the corner shows only after switching it off (Stats menu).
- With the table off, the Sensorgram's bottom corners ignore a plan bar at the bottom (as the top-left ignores a bar at the top): the legend can lie over it.
- Tests: `tests/unit/test_lspri_legend_corner.py`, `tests/integration/test_lspri_legend_corner_plots.py`. Unseen by eye.
