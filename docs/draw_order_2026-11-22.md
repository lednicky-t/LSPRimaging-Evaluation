# Draw order of the lines (which line lies over which) - 2026-11-22

Maintainer request: in the Points & lines menu of every plot, a way to decide the stacking of overlapping lines: **Auto**, **Link to the legend order**, or **Manual** (a table of the lines,
drag to reorder, right-click Move entries like the ROI table, Up / Down buttons). The manual order is kept in the session.

## Rules (asked 2026-11-22)

- **First = in front.** One rule for the Legend mode (the legend's first row is in front), the Manual table (its first row is in front) and "To top" (= bring to front), as the ROI table's Move
  entries read. Consequence: with the Legend mode at its start (Group, ascending) the *first* line is in front; before this feature the *last* line was. Only overlaps look different.
- **Legend** is the start mode of every plot. **Auto** = roughest line at the back, smoothest in front (the median of the absolute second differences of the points, so a ramp or drift scores
  0 and a step edge is ignored; S_bulk: the scatter of the step means about the line's own fit). Ties keep the lines' own order, the later one in front. This heuristic is a choice of mine
  (the request said "some automatic ... or ideal order based on the data"); the place to change it is `draw_order.roughness` / `back_to_front`.
- **Manual**: a line that is not in the saved list (a new one) is in front of the listed ones; a name that is no longer a line is ignored (and dropped at the next edit).
- Only the stacking changes. The legend, the lines' own order, the exports, the Sensorgram's step table and every number are untouched. Display only.

## Where it lives

| File | What |
|------|------|
| `panels/sensorgram/draw_order.py` | Pure: `roughness`, `back_to_front` (indices, back first), `manual_rows`, `moved`, `can_move`. Tests: `tests/unit/test_lspri_draw_order.py`. |
| `panels/sensorgram/plot_look.py` | The "Draw order" row of the Points & lines menu (Auto / Legend / Manual + "Order..." button, enabled in Manual), remembered per plot; `attach_line_order`, `manual_order`, `lines_changed`, `open_line_order`. Shared by all three plots. |
| `panels/sensorgram/line_order_dialog.py` | The Line order table: swatch + name rows, drag and drop, To top / Up / Down / To bottom buttons and right-click menu (only what can be done), Own order. Writes the module at every change. |
| `panels/sensorgram/plot_window_draw.py` | `stack_lines` (z per line for the LbL / S_bulk windows: all items of a line get its z), `rows_of`, `LegendEntry.key / rough`. |
| `panels/sensorgram/plot.py` | Sensorgram: `_stacking` -> lines are drawn back to front with `dz` added to each kind's z (`_Z_SPREAD`); `set_manual_order`; `set_order_step` returns True when a redraw is needed (Legend mode + a value-at-step legend order). |
| `panels/plot_item_pool.py` | `take` now also gives a *reused* item the asked z (nothing else uses a changing z). |
| `selection/plot_line_order_module.py` | `PlotLineOrderModule`: the manual list per plot (`sensorgram`, `lbl`, `bulk`), `changed(plot)`, `state()` / `restore_state()`. |
| `storage/session.py`, `app_rewrite.py` | Session **schema 1.16**: optional top-level block `"plot_line_order"`; wired like the spectral correction (capture, apply, autosave signal, coordinator). |

Tests: `tests/integration/test_lspri_draw_order_plots.py` (stacking in the three plots, the table, the module, the session file).

## Session format (maintainer chose "in the session file")

Block `"plot_line_order": {"sensorgram": [names], "lbl": [names], "bulk": [names]}`, first = in front. **Additive**: written only when some plot has a list (a session that never used it is
byte-identical to before apart from the version number), a 1.15 file has none and every plot starts at its own order (no migration needed), a damaged block is skipped with a warning, an older
build ignores it. Names: the Sensorgram uses `Series.key` ("roi:5", "group:Layer A"); the windows use the group's or ROI's name (`short_label or label`). A renumbered ROI or a renamed group drops
out of the list and counts as new.

## Notes for the next change

- The windows set the z of a line's items *after* drawing (`item.items[first:]`); a new kind of item drawn per line must be added inside the line's slice of items.
- Not built: undo for the table (drag it back; "Own order" clears); a Spectra panel draw order (not a `PlotLookControls`).
- Unseen by eye.
