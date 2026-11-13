# Refractive indices of the experiment's solutions (2026-11-12)

Maintainer's request: import the RI of each solution from `RIs.txt` (the lab's file that goes with every experiment), keep
the day each RI was measured and compare it with the experiment day. Editing was added on 2026-11-13 (last section); a solutions
list across experiments is still later work.

## Rules (maintainer decisions)

- **One RI per solution**, applied to every step of that solution (the value is the reservoir's).
- **A step's solution is the first name of its note** (text before the first `_-_` / `_--_`): `20perEG_-_ch1_to_40perEG_-_ch3F_ch4P`
  is a `20perEG` step. F / P (flow / pre-flow) are not interpreted.
- Solutions the experiment does not use are kept in the table; since 2026-11-13 they are shown too (dimmed, steps empty), because they can be edited or removed.
- Age warning: amber when the RI was measured more than N days before the experiment; N in Preferences, default 7.
  Measured *after* the experiment is amber too.
- The refractometer's wavelength is a Preferences value, stamped on the table at import. The lab's instrument is a
  **Rudolph Research J357 with a 589.3 nm LED source** (maintainer, 2026-11-12; the sodium D line), so the default is
  589.3 nm. 0 = "not given" stays possible for another instrument; a table imported with another value keeps it.

## Where things are

| Part | File |
|---|---|
| Parsing, matching, age, payload (pure) | `plan/refractive_index.py` |
| Owner (second saved value of the plan module) | `selection/experiment_plan_module.py`: `ri_table`, `import_ri_file`, `check_ri_file`, `ri_usage`, `experiment_day` |
| Session block `"refractive_index"` (schema 1.7, additive) | `storage/session.py` |
| File -> Import -> Refractive Indices... | `panels/file_menu.py` |
| Table on the Experiment plan page | `panels/dataset_manager/refractive_indices.py` |
| Plan bar hover line | `plan/bar_model.py` (`ri_text`), `panels/sensorgram/step_overlays.py` |
| Plan bar text: RI in ( ) after the step text (opt-in checkbox in the Steps menu of the View tab, "Step labels" heading, remembered; 5 decimals; no RI = text unchanged) | `plan/bar_model.py` (`with_ri`, `show_ri`), `panels/sensorgram/plan_group.py` (`_show_ri`), `step_overlays.py` (`_ri_value`) |
| Preferences (wavelength, age limit) | `panels/preferences_dialog.py`, keys `refractive_index/*` in `storage/ui_state_keys.py` |

## Reproducibility

The session stores a copy of the whole file (every row), its SHA-256, the import time and the wavelength. When the
session opens, `check_ri_file` (called by `_wire_session_coordinator` once autosave is on) imports `RIs.txt` from the
dataset folder only if the session has no table; if it has one and the file's checksum differs, the change is
**reported** (status bar + amber line on the page) and the stored values stay until the user imports again.

File format: tab-separated, header with Sample and RI (required), Std, Temp, N, Date (optional, any order). Dates
`DD.MM.YYYY` or `YYYY-MM-DD`. RI outside 1.0-3.0, a duplicate name or a malformed line stops the import with the line
number. Names match ignoring case, spaces = underscores.

## Bug found on the way (fixed)

`DatasetModule.dataset_cleared` reached `ExperimentPlanModule` (which resets itself) before `SessionCoordinator.unbind`;
the reset scheduled a save while autosave still pointed at the session, and the unbind's hand-over wrote the cleared
plan edits (and RI table) over the saved file. `clear_dataset()` is not called from the UI today, so no user lost data;
`_build_modules` now connects the unbind first. Test: `test_closing_the_dataset_does_not_save_the_cleared_plan_over_the_session`.

## Science notes (not acted on)

- No temperature correction: water's dn/dT is about -1e-4 per K, the size of the RI differences between the dilute
  PAH / PSS / NaCl solutions.
- RI depends on wavelength (dispersion); the refractometer wavelength is recorded, not corrected for.
- Next: bulk sensitivity (shift per RIU) from steps with known RI, and the expected bulk shift of a binding step.

## Editing the table (2026-11-13)

Maintainer's request: make the table in the Dataset Manager editable and recomputable, so a change transfers to the calculations, marked as changed. Answers to
the design questions: edit basically everything (also add and remove rows); a step is matched to its row automatically by name, or by hand when the row was edited;
importing again replaces everything after a warning; a solution with no RI gets one by a new row or by typing into its row.

**Engineering priority served: 2 (data integrity and reproducibility).** The imported copy stays as imported and can always be seen and restored; every number built from
a changed value says so.

### The model (`plan/refractive_index.py`, pure)

- `RiTable.solutions` = the imported copy, never changed. `RiTable.rows` = the **working table** (`RiRow`: name, RI, Std, Temp, N, date, `base` = the imported row it started
  as or None if added, `steps` = None or the steps given by hand). None while nothing was edited (a table nobody edited is saved exactly as before).
  `with_rows` makes it None again when the rows equal the import, and keeps imported rows in the file's order (a restored row goes back to its place).
- A row **without an RI** is pending: it matches nothing until it has one (clearing the RI cell does that; a step solution with no row at all is a placeholder row in the
  view, not a row of the table, until its RI is typed).
- **Which row a step uses** (`row_index_for_step`): a row the user gave that step to by hand (`StepRef`, which stays the same when steps are added or removed), else the row
  named like the first name of the step's note. A row given steps by hand serves **exactly those steps** and is no longer found by its name (a decision: otherwise "by hand"
  would silently also keep the by-name steps). A step belongs to one row by hand; a second one is refused.
- `SolutionRi.edited` is true for a value taken from a row the user changed, added, or gave steps by hand (the value may be as imported: its *use* is not).
- All edits are pure functions (`edit_row`, `set_row_steps`, `add_row`, `add_row_with`, `remove_row`, `revert_row`, `restore_row`) that return the new rows or raise
  `RiEditError` with a sentence for the user: name required and unique (ignoring case, spaces, underscores), RI 1.0 - 3.0 (empty = pending), Std >= 0, N a whole number, date
  `DD.MM.YYYY` / `YYYY-MM-DD`, a decimal comma accepted.

### Owner and recalculation (`selection/experiment_plan_module.py`)

`ri_edit`, `ri_set_steps`, `ri_add_row`, `ri_add_row_for`, `ri_remove_row`, `ri_revert_row`, `ri_restore_removed`, `ri_revert_all`: each one undo step (Edit -> Undo), each emits
`ri_changed`. **Recalculation needs nothing new**: `ri_changed` already makes the S_bulk columns recalculate (`StepTableModule._on_ri_changed`), redraws the bulk plot and the plan bar
and schedules the autosave; the RIs never enter the scan (`scan.h5` cell hashes), so nothing in it goes out of date. An undo or redo is skipped, with a status-bar notice, when an
import replaced the table since (it would bring the old table back over the new import); the same guard as the Mask rows' commands. A table can be started with no file (source name
"entered by hand", empty checksum); reverting all its edits leaves nothing, and the table is gone again.

### Marking (where "changed" shows)

- The table: a changed cell is bold and gold with the imported value in its tool tip, the Status column says `edited` / `added` / `steps by hand` / `no RI` / `removed`, a gold line
  under the title says what was changed, removed rows are shown struck out at the end (*Restore row* brings one back).
- Plan bar hover text: "Edited by you (imported: ...)" / "Added by you".
- The S_bulk column's tool tip: "Uses refractive indices you edited: ...".
- **The export record** (`StepTableModule.export_info()["bulk_sensitivity"]`): `ri_file.table_edited` and `ri_edits` (solution, RI and Std used, what the file had), so an exported
  S_bulk says which of its refractive indices were not the file's.
- Importing a file over an edited table asks first (`FileMenuController.import_refractive_indices`) and lists what would be lost.

### Session format (schema 1.9, additive)

The `"refractive_index"` block may also hold `"rows"` (each: name, ri, std, temp_c, n, measured_on, base, steps as step references). Optional: a 1.8 session loads as unedited and an
unedited table is written without `rows`. An unreadable block is left out with a warning, as before (the whole block, edits included: the imported file offers itself again).

### Not done / to decide

- No temperature or dispersion correction (as before); an edited Std changes the weights of the S_bulk fit, and an edited RI with an unchanged Std keeps the old measurement's Std.
- The S_bulk plot's points do not show the edited mark (its export record does).
- The Dataset Manager's table was checked by tests, not by eye.
