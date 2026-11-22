# LbL (layer-by-layer) series, 2026-11-22

Maintainer's request: a dataset of PAH / PSS bilayers (each bilayer shifts the absorbance peak) should be analysed like the bulk sensitivity, but as surface sensitivity.

## What was built

- **S_bulk icon** (`plan_icons.bulk_picture`): a bold S with "bulk" as subscript (also used by the ROI table's S_bulk switch). S_bulk pop-up got a **"Refractive indices..." link** (`BulkControls.ri_requested`, wired in `app_rewrite` to `FileMenu.manager.present("plan")`: Dataset Manager, Experiment plan page, where the RI table is).
- **LbL button** (`panels/sensorgram/lbl_group.py`, Evaluation group after S_bulk): select the start step on the plan bar, press "Use selected step as start", set "Steps per cycle". The series uses the start step and every n-th step after it (`plan/lbl.py`: start 1, period 6 -> steps 1, 7, 13 ...).
- **Chips**: the plan bar shows the cycle number (0 = start, 1, 2 ...) as a neutral chip on those steps (roles "0", "1", ...; no colour, by request). `edits.describe_role` words the hover.
- **State** (`plan/state.py` `LblSeries`, key `"lbl"` in the session's `"plan"` block, **session schema 1.15**, additive, written only when set). Commands on `ExperimentPlanModule`: `set_lbl`, `set_lbl_period`, `clear_lbl`, `lbl_steps` (one undo step each). Removing the start step clears the series (notice says so); removing other steps shifts the count (the period is a count of steps *now in the plan*).
- **Numbers** (`analysis/lbl_series.py`, pure): per line and cycle, the step mean and SEM from the Sensorgram's step numbers (nothing is refitted). *Accumulated* = mean_k - mean_0; *per cycle* = mean_k - mean_(k-1) (none at cycle 0). Uncertainty = the two SEMs in quadrature (NaN if a SEM is missing; accumulated points share cycle 0, so their errors are correlated; bars are per point).
- **Plot** (`panels/sensorgram/lbl_plot.py`, floating window like the S_bulk plot, wired in `app_rewrite._wire_lbl_plot`): Lines (Sensorgram lines / All groups), Shift (Accumulated / Per cycle), point size, export (CSV table `lbl_points`). Colours are the lines' own Sensorgram / group colours. Everything remembered (`sensorgram/lbl_plot_*`, `sensorgram/lbl_period` = default steps per cycle).

## Not built / open

- No ROI-table column for the LbL numbers (the plot and its CSV only).
- Only the repeated step per cycle is used (maintainer's choice); the steps between (PAH, PSS) are not drawn.
- Unseen by eye: the S_bulk text icon (7 px subscript may be hard to read; tune `plan_icons.bulk_picture` sizes), the LbL layer icon, the pop-up layout, the plot.
- Tests: `tests/integration/test_lspri_lbl_series.py`.
