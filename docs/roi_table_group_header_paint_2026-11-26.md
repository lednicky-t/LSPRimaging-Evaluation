# ROI table: group headers painted every step column (2026-11-26)

**Report:** the ROI / Groups table was very laggy in the maintainer's current session.

**Evidence (log `logs/lspri-20261126-095914-16508.jsonl`, DEBUG):** 160 ROIs, 16 groups, **241 step columns**. `roi_table.slow_paint` 0.8-2.5 s for a 651x589 viewport on
nearly every repaint (hover, selection). One structural `roi_table.rebuild` took 1147 ms (view 715, select 329, model 101). With 8 step columns a viewport paint was 27-31 ms
(`roi_table_performance_2026-11-19.md`), so the cost is the column count, but not through the cells: Qt only paints the cells in the viewport.

**Cause:** a group header is one cell spanning every column, and Qt does not clip what a delegate draws in it. `RoiTableDelegate._paint_group_step_tints` and
`_paint_group_steps` looped over all 241 step columns for each of the 16 headers, on every paint (a model lookup, a fill, a text draw each; twice with frozen columns).
`_group_label_edge` listed all step columns too, only to read the first.

**Fix (`panels/roi_table/delegate.py`):** `_step_columns_on_screen(view)` (columns inside the viewport, via `header.visualIndexAt`) for the tint and number loops;
`_first_step_column` for the label edge. `_step_columns_of` (all columns) stays for tests. Pinned by `tests/integration/test_lspri_roi_table_label_highlight.py`.

**Measured (2026-11-26, `STEPS=241 python tools/roi_table_timing.py`, 160 ROIs / 16 groups, quiet machine; all 7 pixel hashes identical before and after):**

| | before | after |
|---|---|---|
| paint of the viewport (group headers with step numbers) | 114 ms (old code patched back in) | 36-42 ms |
| `expandAll` + spans (the view part of a structural rebuild) | 247-288 ms | 58-70 ms |
| forced view reset (`restore_view=True`) | 288 ms | 61 ms |

The session itself saw 0.8-2.5 s per paint and 715 ms for the view part (more headers on screen, a cold start); the tool's table is the same size but lighter.

## Structural rebuild (same day)

Profile of `expandAll`: ~44 000 `sizeHint` calls per call (every row x every column; Qt asks all of them, shown or not), each also costing Python `model.index` / `hasIndex` /
`rowCount`. Two changes in `model.py`: `index()` no longer goes through `hasIndex` (same answers, pinned by a test against `hasIndex` over a grid including out-of-range
cells), and `RoiTreeModel.layout_pass()`, a context manager `RoiTablePanel._rebuild` wraps around `expandAll`: inside it `index()` answers only for the fixed columns. The step
columns cannot change a row's height (`RoiTableDelegate.sizeHint` says 0 for every column but 0), so the heights are the same (tested). A group-row / ROI-row height difference (14 vs
10 px + font) rules out `setUniformRowHeights`.

**Decimals (same day, done):** `model_decimals_ms` (`RoiTreeModel._refresh_decimals`: the median spread of every step cell, to pick one number of decimals for the whole table) ran on
every rebuild, also a numbers-only one: 54 ms in the real session before any numbers existed (~42 000 lookups for 160 ROIs, 16 groups, 241 steps), more once they do. Sampling the
median would have been fast but could change the decimals shown, so the result is kept **exact** and the work is skipped when nothing it depends on changed. Key =
(`_numbers_version`, display mode, grouped / linked, step refs, (roi id, group id, role) of every row). `_numbers_version` goes up in `refresh_numbers`, `set_step_columns` and
`mark_numbers_changed`, which `StepColumnControls._on_numbers_changed` calls **before** its hidden-panel early return (a change that came while the panel was hidden must not be
missed at the catch-up rebuild; pinned by a test that fails without that line). Every change of the step table's values goes through `numbers_changed` (`_invalidate`, `clear`,
a finished job, `set_bulk_weighting`, `_on_ri_changed`) or `columns_changed` (-> `set_step_columns`); a new path that changes values without either must bump the version.
In the tool (fake cells, 241 steps): in-place rebuild 92 -> 2.5 ms, pixel hashes identical; the real gain is the real cells' lookup cost.
**Not explained:** `select_ms` 329 ms of the one slow rebuild in the log; the tool's selection costs 22 ms, so it may have been the cold start.

# Per-column display: decimals and notation (same day)

Right-click a numeric column's header (or a number cell) -> **Display**: More decimals (n -> n+1), Fewer decimals, Automatic decimals, Normal / Scientific.
`number_format.py` (pure) writes the numbers; `column_display.py` is the menu; `RoiTreeModel.set_column_display` keeps the settings by column key (fixed column number, step
reference, comparison pair + operation: they follow their step when steps are renumbered), remembered as `roi_table/column_display`. Columns: x, y, D_s, d, w, W, H, θ, Area and
every step / comparison column. **Display only:** tool tips keep the full numbers, a copied cell its plain text, the cell editor its plain number. **Decimals** = digits after the point;
in scientific notation an explicit number is the digits of the mantissa (3 -> `5.958e+02`), automatic keeps the resolution the plain number had (`595.773` at 3
decimals -> `5.95773e+02`, `0.087` -> `8.7e-02`). A `mean ± spread` cell writes each number with its own exponent. The ratio display mode (excess / u) is 1 decimal unless set.
With no setting every cell is written by the same code path as before (pixel hashes unchanged). Tests: `tests/unit/test_lspri_number_format.py`,
`tests/integration/test_lspri_roi_table_column_display.py`.

# Label-only highlight (same day)

Right-click menu, "Highlight only label" (`RoiTreeView.label_only_highlight`, remembered as `roi_table/label_only_highlight`). Label cells = #/ROIs and
Label, whichever the view mode shows (Geometry shows Label too since the follow-up below); a group header tints its name stretch up to the first step column; if neither has width the whole row is tinted
so a selection still shows. Hover tint stays row-wide. The mode also turns `allColumnsShowFocus` off: Qt's focus frame of the current row spans every column and would
leave a faint whole-row tint.

# Follow-ups the same day (maintainer)

- **Label-only highlight keeps the focus frame, over the label cells only.** Qt draws the current row's frame across the whole row (`setAllColumnsShowFocus`), which is switched off
  in that mode; `RoiTreeView._draw_label_focus` draws the same frame round the label cells (`label_focus_rect`: #/ROIs and Label, whichever have width; a group header's name stretch via
  `delegate.group_label_edge`; the whole row if no label cell has width). Pinned by `LabelFocusFrameTest` (QApplication's style is replaced by a recorder in the test).
- **Engineering notation removed** (too like scientific): Normal and Scientific only; a saved "engineering" reads as normal.
- **Display menu icons are number templates**, painted (`menu_icons._number_template`): `#.##` for the Display entry and Normal, `#.##` over `E0` for Scientific (Java / ICU
  `DecimalFormat` writes scientific as `0.###E0`; Excel as `0.00E+00`). Two lines because six characters do not fit a 16 px icon; `QMenu` has no `iconSize` in PyQt6 to widen the box.
- **Toolbar icon row:** the Group / Ungroup, colour and Delete buttons are gone (right-click menu and keys remain: `_group_selected_or_new`, `_ungroup_selected`, `_color_selected`,
  `_delete_selected`). Left: move up / down, the Geometry / Simple toggle, the step controls, the unit toggle.
- **Geometry view shows the Label column too** (maintainer: in Geometry the group names stood under x / y with no "Label" header, since the column was hidden by design). `rows.VIEW_MODE_COLUMNS[VIEW_GEOMETRY]`
  now starts `(COLUMN_ID, COLUMN_NAME, COLUMN_X, ...)`; Label can still be hidden with Hide column. Tests that pinned the old Geometry layout were updated (freeze tests hide Label in their setUp to keep the
  x / y / Type layout they are about).
- **Selection lock in the icon row:** `_lock_toggle` (one flag with the ROIs tab's button, `SelectionModule`); while locked a selected row is framed, not tinted (`RoiTreeView.set_selection_locked`,
  `_draw_locked_selection_frame`; also switches `allColumnsShowFocus` off). Group-only toggle button and the greying of the Geometry controls in that view: `toolbar_toggles.py`.
