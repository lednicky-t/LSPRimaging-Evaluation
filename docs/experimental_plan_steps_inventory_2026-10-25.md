# Experimental plan steps - what exists, what is missing, what can be reused (2026-10-25)

Read-only inventory. Nothing was changed in code. Written before starting the "plan steps" feature.

Example dataset: `Desktop/Data_PyTest/04_Bulk_sensitivity_pumpplan_0-70percent_to_LbL_with_water_v1_LED_V2`
(`measureing_times.csv`, `metaData.txt`, `RIs.txt`).

## 1. What the legacy CSV contains

- 8478 rows = 314 cubes x 27 images (WL 0 = dark frame, then 470..720 nm). One cube sweep = 9.1 s, one image ~0.35 s.
- Columns: `Image; Measuring time [ms]; Measuring time [s]; Note pump plan`. Time is **elapsed since start**, not a clock time.
- The note repeats on every row of a step. 14 transitions = **12 pump-plan steps + 2 idle segments** ("Pump is not running", 0-5.4 s and 2819-2825 s).
- Steps are ~250 s each (first 300 s, step 9 200 s, last 60 s).
- **Step changes fall mid-cube** (step 2 starts at cube 33, WL 680; step 1 at cube 0, WL 610). A cube that straddles a boundary holds images of two steps.
- Boundary precision = one image interval: the gap before the first image of a new step is ~0.5 s (typical 0.35 s).
- Label quirks: two notations (`H2O_--_ch1F_ch2P` and `30perEG_-_ch2_to_50perEG_-_ch1P_ch4F`); step 12 says `ch2p` (lower case, probably a typo). Treat the label as opaque text. What `F` and `P` mean is not documented anywhere in the code (open question to the maintainer).
- Sample names in the labels (`H2O`, `10perEG` .. `70perEG`) match `RIs.txt` rows: the natural link to bulk-sensitivity (RI per step).

## 2. What the rewrite already does

| Piece | Where |
|---|---|
| Parse CSV -> rows (image, elapsed_ms, comment) | `io/legacy_metadata.py:165` `parse_measuring_times_csv` |
| Collapse to transitions -> `ImagingCommentEvent(acquired_at_unix_ms, comment)` | `io/legacy_metadata.py:194` `_build_comment_events` |
| Model: `comment_events` + `comment_at(t)` | `packages/lspr_core/.../imaging_models.py:71,88,110` |
| Classify/import by content | `io/metadata_import.py` |
| Saved: sidecar `analysis/acquisition_metadata.json` (schema `lspri_acquisition_metadata`, `ACQUISITION_METADATA_VERSION = 1`) and OME-Zarr attrs `lspr_acquisition_metadata` | `storage/workspace.py:398-431`, `dataset/io.py:1903` |
| Display: Workflow > Experimental plan (import/export, "N comment event(s)", live "Cube X, WL Y: comment") | `panels/workflow/dataset_experimental_plan.py` |
| Display: Image panel title bar | `panels/image/panel.py:2884` via `DatasetModule.frame_comment` (`dataset/module.py:488`, O(1)) |

## 3. What is missing

- A step is only (start time, text). No end, no id/number, no label vs comment, no colour, no device state, no idle-vs-step flag, no step -> cubes lookup.
- No step bands on the Sensorgram (listed as "not built: the pump-plan overlay" in `apps/LSPRi/eva/CLAUDE.md`).
- `lspr_io.read_imaging_acquisition_metadata` (`packages/lspr_io/.../imaging.py:132`) sets `comment_events=[]`: a native v6.4 file's plan and runtime record are not read, although the schema has `metadata/experiment_plan` and `experiment_control_runtime` / `flow_events`.
- No reader for a separate plan file (CSV/YAML/HDF5 table) in LSPRi.

## 4. Reuse

- **Already shared, use as is**: `lspr_core.ExperimentPlan` / `ExperimentPlanStep` (`models.py:34,53`: id, label, start_s, end_s, color, comment, free-form `devices` dict), `retime_steps`, `summarize_experiment_plan`; `lspr_io` plan constants, `read_string_table_dataset`, `build_experiment_plan_row_table`. LSPRi eva already depends on lspr-core and lspr-io.
- **Lift into a shared package (small)**: sLSPR eva `io.py:137` `_standardize_plan_rows` and `:434` `_load_embedded_experiment_plan` (CSV/YAML/HDF5 plan -> `ExperimentPlan`, column aliases, HDF5 candidate paths). Pure, no Qt. Touches two apps -> needs approval.
- **Do not import**: `lspr_acq_shell.pump_plan.PumpPlanStep`, `experiment_control_import.py`, `PumpPlanTimelineWidget`. `lspr_acq_shell` pulls in pyserial (`reglo_icc.py:23`) and the device layer, and LSPRi eva scope says no hardware. Copy the **`devices` key names** from `pump_plan.py:163` (`valve`, `switch_position`, `channels[{flow_ul_min, direction}]`) so a plan is interchangeable with sLSPR's; write a thin pyqtgraph band (the baseline band in `panels/sensorgram/plot.py` is a `LinearRegionItem` already).
- sLSPR acq separates the **authored plan** (table) from the **measured run** (`experiment_control_runtime`: events such as `plan_started`, `step_apply`, `step_jump`, `plan_resume`, with `step_index`, `elapsed_in_step_ms`, pump/valve/switch state). Source: `apps/sLSPR/acq/docs/experiment-control/experiment_plan_execution_model.md`. The legacy CSV only has the measured side.

## 5. Proposed build order (not started)

1. Pure function: comment events -> list of `ExperimentPlanStep` (end = next start, last = dataset end; idle flagged), plus `step_at(t)` and a cube-straddle rule. Tests on this dataset's 14 transitions. **No format change** (derive on read).
2. Display: step table in Experimental plan section, current step highlighted from Selection; step bands on the Sensorgram in the active x unit.
3. Rich readers: lift the sLSPR eva plan loader to `lspr_io`; native v6.4 reader fills plan + runtime.
4. Only then a stored `plan` field in `ImagingAcquisitionMetadata` (additive, default None, minor version bump, old files load unchanged; note an older app would silently drop it - pydantic `extra="ignore"`).

## 6. Open decisions

- Is "Pump is not running" a step or "no step"?
- Meaning of F / P; parse sample names (RIs.txt) or keep labels opaque until a rich source provides `devices["sample"]`?
- Cube that straddles a boundary: assign by cube start, by majority of images, or mark as straddling?
- Derive on read now (step 1) or store explicit steps now (format change)?
- OK to touch `lspr_core` / `lspr_io` (shared, multi-app)?

## 7. Decisions of 2026-10-26 (maintainer)

- "Pump is not running" is **no step** (idle stretches are not steps).
- Step label = the **whole comment text** by default. Optional, user-changeable rule: show only the text before the separator (`_-_` / `_--_`, usually the solution name). Note `_--_` does not contain `_-_`, so one separator is not enough; make the separators editable. Remembered like any control. (F = flow, P = preflow: internal nomenclature, not parsed for now.)
- Shared packages (`lspr_core`, `lspr_io`) may be touched. A **stored-format change still needs its own explicit OK**.
- Also parse the plan file that drove the experiment (`<folder name>.csv`, "external FR/Direction" layout): pump channel speeds, directions, valves.

## 8. The plan CSV (`<folder>.csv`) - findings 2026-10-26

Header: `FR1 [ml/min];Direction1;FR2 ..;Direction4;Time;Valves;Notes`. 12 rows = the 12 measured steps, same order; Notes text = the measured "Note pump plan" text. Flow in mL/min (0.04 = 40 uL/min), directions `aclckw`, time `HH:MM:SS`, valves packed `V1cV2cV3cV4c`.

- **Plan time != measured time.** Each step ran ~0.1-0.5 s longer than planned; drift reaches 3.6 s at step 12 (plan 2810.0 s, measured 2813.9 s). Place steps on the time axis from the measured note changes; take content from the plan; join by order and check count/durations.
- **Two defects in this file** (the sLSPR acq parser `lspr_acq_shell/experiment_control_import.py` was run on it and reads both silently wrong):
  1. Row 3, FR4 = `.00E-02` -> parses as 0 (every other preflow channel is 2.00E-02; direction is set, flow is 0).
  2. Row 12 has an extra `;` (12 fields, header 11): everything after is shifted -> duration 0.0 s, valve Open instead of closed, description `V1cV2cV3cV4c`.
  A new parser must report these (row, column, what it did) instead of guessing. Generic check that needs no F/P knowledge: direction set but flow 0/empty.
- Acq parser is not reusable as is (Qt `QRunnable`/`QColor`, `PumpPlanStep` -> `lspr_acq_shell` -> pyserial). Plan: new pure reader in `lspr_io` returning `lspr_core.ExperimentPlan`, copying the helper rules (header detection, `HH:MM:SS`, `aclckw`/`clckw`, packed valves, mL->uL).
- `devices` layout to use (same as `pump_plan.to_core_experiment_step`): `valve`, `switch_position`, `channels[{flow_ul_min, direction}]`. Packed valve string has V1-V4; the acq parser keeps only V1; keep all four.

## 9. Cubes that straddle a step change (measured on this dataset)

12 of 314 cubes hold images of two different notes (a cube sweep is 9 s; a step changes at one instant). Examples (images before/after the change): cube 33 22/5, cube 61 13/14, cube 89 3/24, cube 145 1/26, cube 250 26/1. Options: assign by cube start, by majority, or mark as "mixed" and let the analysis decide. Recommendation: mark as mixed (hides nothing; bulk-sensitivity plateaus can exclude them). Liquid arrival delay at the sample (dead volume) is not in the data; a later "settle margin" option is separate.

## 10. Built 2026-10-26 (steps 1-3 of the plan; uncommitted)

Decisions confirmed: cubes across a step change are **marked mixed**; stored-format change approved.

**Where the code is**

| Piece | File |
|---|---|
| Models `ImagingPlan`, `ImagingPlanStep`; new optional `ImagingAcquisitionMetadata.plan` | `packages/lspr_core/src/lspr_core/imaging_models.py` |
| Steps from notes, join with plan, `StepLookup`, `classify_cube(s)` (pure) | `packages/lspr_core/src/lspr_core/imaging_plan.py` |
| Plan-CSV reader `read_pump_plan_csv` / `is_pump_plan_csv` | `packages/lspr_io/src/lspr_io/pump_plan_csv.py` |
| Find the plan file + `attach_pump_plan` | `apps/LSPRi/eva/src/lspr_imaging_app/io/pump_plan.py` |
| Import classifies the plan CSV by content; `import_metadata_files(paths, base_metadata=None)` | `io/metadata_import.py` |
| Load attaches the plan (sidecar kept if it has settings); Zarr export copies the plan CSV into `Experimental_data/` | `dataset/io.py` |
| Version 1 -> 2, newer file refused | `format_versions.py`, `storage/workspace.py` |
| Summary line "N experiment step(s), settings from ..." | `panels/workflow/dataset_experimental_plan.py` |
| Tests | `tests/unit/test_imaging_plan.py`, `tests/unit/test_pump_plan_csv.py`, `tests/integration/test_lspri_pump_plan_import.py` |

**Stored format (sidecar `analysis/acquisition_metadata.json` and the OME-Zarr attribute `lspr_acquisition_metadata`, version 2)**: `metadata.plan = {time_source, source_file, steps[], warnings[]}`; a step is `{step, label, start_unix_ms, end_unix_ms, planned_duration_s, color, devices}`; `devices` = `{channels: [{channel, flow_ul_min, direction}], valves: {V1..V4: Open|Close}, valve: V1}` (the layout singleLSPR Acquisition uses, so a plan is interchangeable). Times are unix ms like the image timings; start inclusive, end exclusive; idle stretches are absent. `comment_events` stay as they were (the source the steps are derived from). A version 1 file loads unchanged; an app that only knows version 1 ignores `plan` (and would drop it if it re-saved).

**Rules in the code**: step boundaries from the measured notes, never from the plan's durations; settings joined by order only when the step counts agree, else refused with a warning; label = the recorded note; plan-file problems are kept in `plan.warnings` and logged; a plan file that cannot be read stops an explicit import (message names the line) but only logs on dataset load.

**Checked on the example dataset** (read-only): 12 steps, device settings from `<folder>.csv`, 2 warnings (the two file defects), 12 mixed cubes, `step_at` agrees with `comment_at` for all 8478 images, loader writes nothing.

**Known limits**: two successive steps with the same note text cannot be told apart in the notes (count mismatch -> settings not attached, warning says so); a native acquisition file's measured run record is not read yet (`time_source` has room for it); the Sensorgram does not show steps yet; dark frames count in `classify_cube` unless the caller leaves them out.

**Not built (step 4, needs the GUI description approved)**: step table in the Experimental plan section, step bands on the Sensorgram, "label shown" rule (whole note / text before an editable separator list, remembered), mixed-cube marks.

**Tests run (one process per file)**: the 3 new files (25 + 17 + 21 passed) and 16 existing files that touch metadata / dataset load / export / the section (all passed). Not run: `test_lspri_rewrite_image_panel.py` (heavy, known crash class), the full suite.
