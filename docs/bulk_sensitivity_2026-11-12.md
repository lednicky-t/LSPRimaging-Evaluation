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

## Known limits

- The step mean is the whole step (as the step columns): the arrival transient of a new solution is in it. Trimming (`step_stats.StepWindow`) is not wired.
- The SEM counts cubes as independent; with smoothing it is small, χ²_red then comes out large and the Birge factor takes over.
- RI at the refractometer wavelength stamped at import (589.3 nm for the lab's J357), no temperature correction; both said in the column's tool tip.
- The trace unit is assumed nm (as the comparison columns assume); an energy metric would make it eV/RIU.
