# Refractive indices of the experiment's solutions (2026-11-12)

Maintainer's request: import the RI of each solution from `RIs.txt` (the lab's file that goes with every experiment), keep
the day each RI was measured and compare it with the experiment day. Editing and a solutions list across experiments are
later work.

## Rules (maintainer decisions)

- **One RI per solution**, applied to every step of that solution (the value is the reservoir's).
- **A step's solution is the first name of its note** (text before the first `_-_` / `_--_`): `20perEG_-_ch1_to_40perEG_-_ch3F_ch4P`
  is a `20perEG` step. F / P (flow / pre-flow) are not interpreted.
- Solutions the experiment does not use are listed as unused, not shown in the table (they stay in the stored copy).
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
