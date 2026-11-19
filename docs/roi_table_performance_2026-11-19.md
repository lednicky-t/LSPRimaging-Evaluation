# ROI / Group table performance (2026-11-19)

The maintainer: "the ROI/Group table is getting a bit slowish, can you check the performance, and whether it can be improved?" Method: `.claude/skills/performance-work`: measure first,
profile what is slow, change one thing, measure again, and show that nothing visible changed. Tool: `tools/roi_table_timing.py` (headless; run it on a quiet machine, a test run in the
background makes every number 2-3 times larger: the first round of numbers of this investigation were taken that way and are not used).

**Setup.** 160 ROIs (circles, rectangles, pixel maps, 16 references linked to samples) in 16 groups, a 560 x 700 table, once without and once with 8 step columns of made-up numbers
(`STEPS=8`). Times are the minimum of several runs. Nothing was looked at: the pictures are compared as hashes (below).

## What was slow, and why

| What | Before | Cause |
|---|---|---|
| select all ROIs (a click on the image, Ctrl+A) | **70 ms** (98 with step columns) | `QItemSelectionModel.selectedRows()` asks the Python model for `flags()` of **every cell of every selected row** (about 1,900 calls, 27,000 over the 14 calls of one profiling run), each building a flag combination with `\|` in PyQt's Python enum code (~40 µs each); the toolbar update called it up to three times per change; and 160 one-row ranges were handed to Qt to merge |
| any rebuild (an edit, a link, a rename, a colour) | **21 ms** (36 with step columns) | every rebuild reset the model, so the view collapsed everything and `expandAll()` + the group spans asked the Python delegate for the height of **every cell of every row** (~1,900 `sizeHint` calls) |
| sort | 22 ms (39) | the same reset |
| repaint of the visible rows | 37-42 ms (43) | per cell: 6-7 separate `index.data()` calls (Python round trips), two `QColor`s and a `QPen` made for the hairlines, enum arithmetic for the state |
| select one ROI | 2.6 ms | fine |
| move / resize / edit in the toolbox | 0.3 ms | fine (the cost is the rebuild after it) |

## What was changed

1. **`flags()` is a table of constants** (`model._ROI_FLAGS`, `_GROUP_FLAGS`), not an expression. Tested against the old code on every cell of the grouped, flat and Linked tables.
2. **Selecting.** `_view_selection` reads the selection's ranges instead of `selectedRows()` (the view selects whole rows, so a row is selected when a range holds its first column);
   `_update_toolbar_state` reads it once (or is handed it); the `set(ids)` that was rebuilt for every row inside a loop is made once; `_apply_selection_to_view` selects **runs** of rows as one
   range (`RoiTreeModel.roi_index_runs`: 16 ranges for a grouped table of 160 ROIs, not 160).
3. **A rebuild that lists the same rows in the same groups updates the cells in place** (`RoiTreeModel._layout_of`, `_adopt`): the new rows and headers go into the old nodes and the model says
   `dataChanged` with the role list `[VALUES_ONLY_ROLE]`; `RoiTreeView.dataChanged` then only repaints (Qt's own handler would measure every cell of the range again: that cost 40 ms, found
   by profiling the first version of this change). Nothing is reset, so the open groups, the selection, the scroll position, Qt's current row and an open cell editor stay, and the panel puts
   nothing back (`RoiTablePanel._rebuild` does the `expandAll` / spans / selection restore only when `model.take_reset_flag()` says the model was reset, or when the caller says the thing that
   changed *is* the view state: `restore_view=True`, used by the saved collapsed set and the Groups-only lock). A new, removed or moved row, a new column, another sort order or the Linked
   view still reset.
4. **Painting.** One `PAINT_ROLE` answers everything a ROI cell needs (`(text, dim, stale, alignment, tint)`) instead of six or seven roles; the hairline pens are made once per colour
   (`delegate._line_pens`); the state test and the font no longer build enums / copies per cell.

## Result (same machine, quiet, best of three)

| | before | after | |
|---|---|---|---|
| rebuild (an edit, a link, a rename) | 21.4 ms | **2.2 ms** | 9.7 x |
| rebuild, 8 step columns | 36.1 ms | **6.3 ms** | 5.7 x |
| select all ROIs | 70.4 ms | **4.7 ms** | 15 x |
| select all ROIs, 8 step columns | 97.8 ms | **5.5 ms** | 18 x |
| sort | 21.9 ms | **2.3 ms** | 9.5 x |
| sort, 8 step columns | 39.3 ms | **6.0 ms** | 6.5 x |
| repaint of the visible rows | 37-42 ms | **27-31 ms** | 1.3 x |
| repaint, 8 step columns | 43 ms | **33 ms** | 1.3 x |

A value edit now costs about **2.5 ms of rebuild + one repaint (30 ms)**, was 22 ms + 40 ms.

## It looks the same (shown by pixels, nothing was looked at)

The tool hashes `QTreeView.grab()` in seven states: fresh, 30 selected, 30 selected and scrolled, 1 selected, sorted by x descending, back to # with nothing selected, and picked cells
(the cell marks of the copy / paste feature). **All seven are identical before and after, with and without step columns** (`5afad6f1dd87`, `4b179c7d6551`, `4f3627b3f6a7`, `5452e9cbaa0e`,
`cfec84d451d2`, `5afad6f1dd87`, `7196026130f1` without step columns). A diagnosis on the way: the first version of the in-place rebuild differed in one row of the "fresh" picture; the cause was
Qt's *current row* (the faint focus tint of the keyboard-focus row): a reset forgets it, an update keeps it, which is what a keyboard user wants. The check clears the current row first.
Tests: `tests/integration/test_lspri_roi_table_performance.py` (18: in place vs reset, no row measured again, no reset for a value change and a reset for each structural one, the selection
paths, `flags` against the old code on all cells, `PAINT_ROLE` against the separate roles on all cells with step columns, in-place pixels equal reset pixels; a mutation that disables the
in-place path fails 5 of them).

## Behaviour that changed on purpose

- After an edit that changes only numbers, the table **no longer scrolls to the first selected ROI** (it did, after every rebuild); nothing moved, so nothing needs revealing. A rebuild after
  a structural change still reveals.
- Qt's current row, the scroll position, which groups are open and an open cell editor survive such an edit (a reset used to throw them away).

## Not changed, and not measured

- A structural change (a new / deleted ROI, grouping, a new column) still resets and pays `expandAll` (about 20 ms for 160 ROIs, 30 ms with 8 step columns). Making the delegate's `sizeHint`
  cheaper per call, or avoiding the per-cell question, would be the next step; the height is the same for every ROI row, but a group header is 4 px taller, so Qt's uniform-row-height
  shortcut is not available without changing how it looks.
- `StepColumnControls.fit_if_auto()` (formats every number of every step column to fit the widths; 6-8 ms for 8 columns, `model.step_texts`) and `schedule_state()` (is every row on screen
  calculated?) run after each rebuild. They need the real step module and a scan, so they were **not measured**; they are the next suspects if the table still feels slow with step columns on a real dataset.
- Painting is still about 30 ms for a full viewport: Qt's own drawing and the delegate's per-cell Python are about half each. A repaint is only a full viewport after a rebuild, a resize or a theme change.

## Where to look in the log (stage timing, added the same day)

Cheap, always-on stage timing at DEBUG level (the log file has DEBUG whatever the console shows; `docs/logging_design_2026-10-14.md`). Read a saved log with
`python -m lspr_imaging_app.applog summary logs/<file>.jsonl` (the timing table has count, mean, p95 and max per event) or
`python -m lspr_imaging_app.applog grep logs/<file>.jsonl --event roi_table.rebuild`. **One record per unit of work, never per ROI or per cell** (`applog.StageTimer`: a mark is one clock
reading, about 0.3 µs; with DEBUG off the record is never built). Measured: a rebuild is 5.6 ms with the timing off and 5.7-6.4 ms with the records on, 160 ROIs and 8 step columns.

| Event | When | `dur_ms` and fields |
|---|---|---|
| `roi_table.rebuild` | every rebuild of the table | `dur_ms` = the whole rebuild; `rows`, `step_columns`, `reset` (False = updated in place); stages `model_ms` (the model's work, split in `model_rows_ms`, `model_decimals_ms`, `model_tree_ms`, `model_apply_ms`), `view_ms` (open / close groups again: only after a reset), `select_ms` (selection put back), `toolbar_ms` (buttons and counts), `columns_ms` (which columns show), `fit_ms` (the step columns' widths) |
| `roi_table.fit_step_columns` | each time the step columns' widths are fitted | `columns`, `rows`; stages summed over the columns: `title_ms`, `format_ms` (every number written as its cell shows it), `measure_ms`, `set_ms` (Qt's resize signals run in there) |
| `roi_table.step_state` | the refresh button's light is brought up to date (after a rebuild, an expansion, a setting; coalesced) | `dur_ms`; `rois`, `groups`, `missing` (rows with no up-to-date numbers) |
| `roi_table.slow_paint` | only a paint over 20 ms (`view._SLOW_PAINT_MS`; a full viewport is 27-31 ms here) | `ms`, `width`, `height` of the repainted region |

A sample (160 ROIs, 8 step columns, a numbers-only edit):
`roi_table.rebuild dur_ms=6.06 rows=160 step_columns=8 reset=False model_ms=5.76 view_ms=0 select_ms=0.08 toolbar_ms=0.15 columns_ms=0.04 fit_ms=0 model_rows_ms=1.47 model_decimals_ms=3.84 model_tree_ms=0.19 model_apply_ms=0.19`.
**It already shows the next thing to look at: with step columns on, 3.8 of the 6.1 ms is `model_decimals_ms`**, the pass that formats every step number to choose one number of decimals for the
table (`RoiTreeModel._refresh_decimals`); it runs again at every rebuild even when no number changed. (`fit_ms` is 0 here only because the tool does not switch the real step controls on.)
Reading a slow one: a big `view_ms` = a structural change paid `expandAll`; a big `model_decimals_ms` or `fit_ms` / `format_ms` = number formatting; a big `step_state` = the "is every row
calculated" walk. `LOG_DEBUG=1 python apps/LSPRi/eva/tools/roi_table_timing.py` switches the records on in the timing tool and prints the last of each kind.

## Rules for the next change to the table

- Never ask `QItemSelectionModel.selectedRows()` / `selectedIndexes()` of this table: they call `flags()` for every cell. Read `selection()` ranges (`RoiTablePanel._view_selection`).
- A new thing the model must change without changing which rows are listed goes through `RoiTreeModel._rebuild` (it updates in place when it can); a change that adds or removes rows or
  columns resets by itself (`_layout_of` holds the column count, the arrangement and the ROI ids under each header). Anything the view must re-apply after a reset belongs in the reset branch
  of `RoiTablePanel._rebuild`; a change whose point *is* view state calls `_rebuild(..., restore_view=True)`.
- A new per-cell fact the delegate needs goes into `RoiTreeModel._paint_data` (one answer per cell), not into another role asked per cell.
- Re-run `tools/roi_table_timing.py` before and after; the hashes must not move for a change that is only about speed.

## Test order (found on the way, not caused by this change)

`test_lspri_roi_table_freeze.py::WhatIsDrawnTest` (two tests) fails when run **after** `test_lspri_log_panel.py`, `test_lspri_rewrite_edit_menu.py` or `test_lspri_roi_edit_hold.py` in the same
process; alone, or in the repo's one-process-per-file runner (`tools/run_tests_per_file.py`), it passes. **Verified on the committed code** (a `git archive` of HEAD of both repos): the same two
tests fail after `test_lspri_roi_edit_hold.py`. Something those files leave behind changes how Qt draws the focus frame (a difference of 23 colour levels against a tolerance of 4). Not
investigated further.
