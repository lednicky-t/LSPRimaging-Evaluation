# Sensorgram panel split (2026-10-26)

`panels/sensorgram/panel.py` had grown to ~930 lines. Behaviour-preserving split, done in two steps; **step 1 is built, step 2 is NOT built (do it next time).**

## Step 1 - built: the experiment steps leave the panel

`panel.py` 930 -> 784 lines. New `step_overlays.py` (`StepOverlays`, a `QObject` child of the panel, 201 lines) owns everything that hangs on `ExperimentPlanModule`:

- the plan bar's ribbon group (`PlanBarControls`) and gestures (`PlanBarActions`), and the Step stats group (`StatsTableControls`);
- what the steps add to a request: `step_inputs(stored)` -> (pieces to smooth on their own, which cubes belong to which step);
- `refresh()` (steps -> axis map -> bar steps -> plot, then the table), `show_stats_table(bars, series, has_steps)`, `plan_context()`;
- the redraw rule: a moved / reset / restored boundary asks for a redraw while smoothing-inside-steps or the table is wanted; a note or colour edit never does.

The panel gives it the plot, the dataset, the settings module, the cube display and five callbacks: `drawn` (scan + lines last drawn), `x_axis`, `schedule_redraw`, `defer` (dock hidden), `on_steps_available` (the "smooth inside steps" toggle stays in the panel: it belongs to the Smoothing group). The overlays know nothing of the job, the cache or the baseline.

Also shared now: `device_item.py` `DeviceSpaceItem`, the base of `PlanBarItem` and `StepStatsItem` (visible data rectangle, data -> pixel transform, repaint on view change). Private names changed for tests: `panel._steps.plan_controls`, `.stats_controls`, `.actions`, `.step_inputs`, `.smoothing_follows_steps`, `.show_stats_table`.

## Step 2 - NOT built: the x axis and the baseline selector leave the panel (~130 lines)

What is still in `panel.py` and forms one block, in this order of dependence:

1. **The x axis** (`_x_axis`, `_x_cache`, `_on_axis_changed`, `_window_on_new_axis`): time in seconds or the cube number, cached until the file or the Cube / Time mode changes; on a switch the baseline window keeps its cubes. Extract first, as a small helper (it is also the `x_axis` callback the step overlays get).
2. **The baseline selector** (`_default_baseline_window`, `_snap_window`, `_on_baseline_toggled`, `_on_baseline_dragged`, `_range_decimals`, `_on_y_label_double_clicked`, `_on_x_label_double_clicked`, `_on_band_visibility` / `_color` / `_alpha`, and the `_band_*` state with its part of `restore_ui_state`): needs the settings module, the plot, the x axis helper, the band controls, the Subtract toggle and `_draw_last`.

Entanglements to watch: `_syncing` (the guard while the controls are set from the settings), `_band_visible` read in `_draw_last`, the `_x_cache` key `(id(stored), use_time)` used by `_window_on_new_axis`. Same recipe as step 1: composition (a `QObject` child), callbacks instead of a back-reference to the panel, tests updated for the moved private names, ruff, then the five Sensorgram integration files one process each (`test_lspri_sensorgram_panel.py` is the slow one, ~1.5 min). After step 2 the panel would be ~650 lines: job / cache / drawing / ribbon construction.

Not planned: splitting `_redraw` / `_compute` / `_on_result` (the job and cache machinery) - they share too much state to split without a design change; ask first.
