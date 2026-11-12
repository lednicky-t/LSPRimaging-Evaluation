# Bulk sensitivity (S_bulk), 2026-11-12

What it is: per ROI or group, the slope of the step means against the refractive index (RI) of each step's solution, in nm/RIU. The steps come from the
**S_bulk set** of the plan; the RIs from the imported RIs.txt (`docs/refractive_indices_2026-11-12.md`). Built steps 1-3 of 4; step 4 (the plot) not built.

## Maintainer decisions (2026-11-12)

- The set is like B1/S1 but takes **any number of steps**: Sensorgram > Experimental plan > Evaluation > S_bulk button (add the bar's selection / remove / clear),
  or right-click > "Use for S_bulk (Sb)" (whole selection; checked = all in, clicking takes them out). Saved in the session (schema 1.8, plan block key `"bulk"`).
- The ROI / Groups table's S_bulk column follows the display mode: μ = slope, SEM / SD = the slope's uncertainty from the steps' SEMs / SDs.
- Weighting: 1/σ² by default (the statistically best line when σ is right), "Equal" as a comparison. Remembered (`sensorgram/bulk_weighting`).
- No reference value exists (every sensor differs), so the maths is checked on synthetic data only.
- A small plot (shift vs RI, error bars, fit line) toggled from the S_bulk menu: **step 4, not built**; needs its screen description first.

## The maths (`analysis/bulk_sensitivity.py`, module docstring has the formulas)

- Weighted: w = 1/s², s² = u_y² + S²·u_RI² (effective variance, the RI file's Std column; iterated until S settles). u_data = 1/sqrt(Σw(x−x̄w)²).
  u = u_data · max(1, sqrt(χ²_red)) (Birge ratio): the scatter raises u when the steps disagree with the line more than their error bars, never lowers it, and is not added on top.
- Equal: u = max(u_data, OLS standard error). The Birge factor on an unweighted line over-stated u (88 % coverage); measured before choosing.
- Coverage on synthetic data (5 steps, 4000 runs): 73 % weighted / 71 % equal for honest error bars; 61 % weighted when the error bars are 3× too small (u_data alone: 27 %).
- Two steps: slope and u_data, no χ²_red. Fewer than two distinct RIs: no number, the tool tip says why.

## Where the code is

| What | Where |
|---|---|
| Fit, uncertainties | `analysis/bulk_sensitivity.py` (`fit_bulk`, Qt-free, in the `lspri-science-no-qt` contract) |
| One row from its step numbers | `analysis/step_table.py` (`bulk_values`, `BulkPoint`, `OP_BULK`): two fits, SEM (value + `sem`) and SD (`sd`) |
| The set | `plan/state.py` (`PlanEditsState.bulk`), `plan/edits.py` (`add_to_bulk`, `remove_from_bulk`, `clear_bulk`, chip `BULK_CHIP = "Sb"`, `describe_role`), `selection/experiment_plan_module.py` (`bulk_steps`, commands, reason `"roles"`) |
| Sensorgram button | `panels/sensorgram/bulk_group.py` (`BulkControls`), wired in `step_overlays.py` (`_show_bulk`); icon `plan_icons.bulk_picture` |
| Right-click | `panels/sensorgram/plan_actions.py` (`_add_use_for_bulk`) |
| Table column | `selection/step_table_module.py` (`set_bulk_shown`, `_bulk_column`, the `OP_BULK` branch of `_compare_cell`, `set_bulk_weighting`, `bulk_points`, export record `bulk_sensitivity`); a comparison column with op `"bulk"` and pair 0, so the header subscript, hiding, freezing and memory are the comparison columns' |
| Table button, header menu, cell tip | `panels/roi_table/step_controls.py` (`bulk_button`, `_toggle_bulk`; header arrow = "Remove this column" only), `panels/roi_table/step_columns.py` (`_bulk_tooltip`) |
| Weighting hand-over | `app_rewrite._follow_plan_bar` |
| Tests | `tests/unit/test_lspri_bulk_sensitivity.py`, `tests/integration/test_lspri_bulk_set.py`, S_bulk tests in `test_lspri_step_table.py` and `test_lspri_plan_bar_gestures.py` |

## Measured minus predicted (μ − μ*), 2026-11-12

Does a step follow the bulk line, or does it shift more (binding, a higher-RI layer)? Three more display modes of the ROI / Groups table: **μ − μ\***,
**μ − μ\* ± u**, **(μ − μ\*) / u**. Maintainer decisions: measured − predicted (positive = more than bulk); every step shown alike, also the steps in the fit
(their residual); a step whose solution has no RI shows **NA** (the tool tip says to add it to RIs.txt: there is no step manager yet); the S_bulk and S/N
columns are blank in these modes.

- Step column: μ* = the row's own line at the step's RI (S_bulk × RI + intercept, `bulk_sensitivity.line_weights`). Difference column (S − B): μ* = S_bulk × (RI_S − RI_B), the intercept cancels.
- u: the excess is a linear combination of independent step means, Σ gᵢ yᵢ, so u² = Σ gᵢ² varᵢ exactly (`bulk_sensitivity.excess`): a step that is one of the fitted points is
  correlated with the line and gets the residual's smaller u (σ²(1 − h) for equal weights), not √(SEM² + u_line²). A step outside the fit adds (slope × its RI Std)².
  Raised by the same factor as S_bulk's u (`BulkFit.inflation`). With two fitted steps their own residuals are 0 ± 0 (the line passes through both): (μ − μ*)/u shows NA.
- Coverage on synthetic data (5 steps fitted, 4000 runs, everything on the line): |excess| ≤ u in 73.8 % (a step outside the fit), 73.0 % (a fitted step), 73.7 % (a difference).
- Code: `analysis/bulk_sensitivity.py` (`line_weights`, `excess`), `analysis/step_table.py` (`bulk_excess`), `selection/step_table_module.py` (`_with_excess`,
  `_row_bulk` = one fit per row, cached by the row's numbers; `CellValue.excess / excess_u / excess_note / predicted`), `panels/roi_table/step_columns.py`
  (`DISPLAY_EXCESS*`, `_excess_text`, `_excess_lines` in every step / difference tool tip), `analysis/export_tables._excess_table` (export: excess, u, excess/u, n).
- The prediction is only as good as the RI of the step's own solution: for a protein step, measure the protein solution, not only its buffer.

## The S_bulk plot (step 4), 2026-11-12

A **floating window**, not a panel (maintainer: a rare check): Sensorgram > Experimental plan > Evaluation > S_bulk > "Show S_bulk plot" (window title "Bulk sensitivity"; the pop-up's heading is "Step for S<sub>bulk</sub>", tool tip "Bulk sensitivity"). x = RI (at the
stamped wavelength), y = the step mean as the Sensorgram's y axis says (shift with Subtract on). Per line: filled points + SEM bars for the steps in the S_bulk set, hollow
points for the other steps with an RI (their distance from the line is the table's μ − μ*), the fitted line with a band = its own uncertainty, the legend
"name: S_bulk = … ± … nm/RIU, χ²red …". "Lines": the lines drawn on the Sensorgram, or **All groups** (the ROI table's group rows, with a "Calculate groups" button
when they are not calculated or out of date). Save data writes `s_bulk_points` (every step with its excess and u) and `s_bulk_fits`. Size, place and the Lines choice
are remembered.

- Code: `panels/sensorgram/bulk_plot.py` (`BulkPlotWindow`, `lines_from_series`), wired by `app_rewrite._wire_bulk_plot` (a child window of the main window, so it
  closes with the app; updated only while shown; theme switch via `window.bulk_plot`).
- While it is open the Sensorgram's lines carry their per-step numbers even with its step table off (`StepOverlays.set_numbers_for_plot`, part of `stats_wanted`); it
  is told of every refresh by `StepOverlays.bulk_inputs_changed`. Group rows: `StepTableModule.group_numbers`.
- At most 12 lines are drawn (the rest are named in the status line).

## Known limits

- The step mean is the whole step (as the step columns): the arrival transient of a new solution is in it. Trimming (`step_stats.StepWindow`) is not wired.
- The SEM counts cubes as independent; with smoothing it is small, χ²_red then comes out large and the Birge factor takes over.
- RI at the refractometer wavelength stamped at import (589.3 nm for the lab's J357), no temperature correction; both said in the column's tool tip.
- The trace unit is assumed nm (as the comparison columns assume); an energy metric would make it eV/RIU.
