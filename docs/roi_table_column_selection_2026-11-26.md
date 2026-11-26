# ROI / Groups table: column selection, linked to the Sensorgram's step selection (2026-11-26)

**What the user gets.** A click on a column header selects the column (spreadsheet style: Ctrl+click adds / removes, Shift+click extends). Any column can be selected; the selected
step columns are the steps selected on the Sensorgram's plan bar, **one shared selection**: select steps on the bar and their columns light up (the table also scrolls to the newly
selected one); click step column headers and the same steps are selected on the bar.

**Sorting moved.** A click in the left ~20 px (`view._SORT_ZONE`) of a header sorts (again = reverse); the rest of the header selects. A comparison column already had its menu arrow
in that zone; its menu now also has "Sort by this column". Icon key `sort_column` in `panels/menu_icons.py`.

**Where it lives (engineering priority 3-4: readable, testable; display only, no domain state).**

| What | File |
|---|---|
| Click rules (plain / Ctrl / Shift / anchor), pure | `panels/roi_table/column_selection.py` (`click_columns`, `is_step_key`) |
| Header: press zone, `sort_requested`, `column_clicked`, selected-header paint | `view.py` `SubscriptHeader` |
| Selection state by column **key** (`column_keys.py`), the frame sides, repaint, `changed` signal | `panels/roi_table/column_selection_controller.py` `ColumnSelectionController`, as `RoiTreeView.column_selection` (the view only forwards `is_column_selected`, `column_frame`) |
| Cell wash and frame (ROI rows; group headers for fixed and step columns on screen only) | `delegate.py` `_column_selected`, `_paint_column_frame`, `_paint_group_selected_columns`, `_paint_group_column_frames` |
| Table <-> plan bar glue (announce step refs, set quietly, scroll to a column) | `panels/roi_table/step_link.py` `StepLink`, as `RoiTablePanel.step_link` |
| The wiring (bar index <-> step ref, no scrolling for a table-made selection) | `app_rewrite._follow_plan_bar`, `SensorgramPanel.plan_steps_selected` / `select_plan_steps` |

**The frame.** A selected column is washed lightly and framed (2 px accent): the header has the top and a heavier bottom line, every cell the left / right side. A run of neighbouring
selected columns (neighbours as *shown*, a hidden column does not break the run) has the sides only at its two ends, so Shift-click gives one frame.

**Reveal.** Selecting a step on the bar scrolls the table so the column's middle is in the middle of the room right of the frozen columns (the first / last columns cannot be centred:
the scroll range stops them). A column already fully in view is left where it is.

**A click that selects must not turn the sort arrow.** Qt moves the arrow to the clicked section on any header click; `SubscriptHeader._on_section_clicked` puts it back (found by a test).

**Why by key.** Step columns are renumbered whenever steps come and go; a key (the step's reference) survives that, a hidden column keeps its selection and shows it again when shown.

**Loop guard.** The table setting the bar, and the bar setting the table, never announce back: `set_selected*` are quiet, the bar's `_set_selection` ignores an unchanged selection.

**Known edges / not done.**
- Selection is not remembered across a restart.
- Adding / removing a step on the plan shifts the bar's selected *indices* without a signal; the table's selected columns follow the next bar change, not that shift (the bar selects the
  new step itself after an add, which re-syncs).
- Clicking a fixed column's header replaces the selection, which clears the bar's selection (one selection).
- Nothing acts on the selected columns yet (copy, format, hide, delete are the obvious next uses).
- Dragging across headers to select a range is not there (Shift+click does it).
- Seen by eye: not yet.
