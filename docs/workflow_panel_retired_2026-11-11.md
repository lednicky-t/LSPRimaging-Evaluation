# Workflow panel retired (2026-11-11)

Maintainer decision: remove the Workflow dock completely. Its actions go to the File menu; what is left to look at
goes into a non-modal **Dataset Manager** window ("a dataset is not looked at often"; may become a dock later).

## Where each part went

| Workflow panel part | Now |
|---|---|
| Dataset folder row (path, Browse, Open in Explorer) | File -> Open Dataset... (Ctrl+O), File -> Show Dataset Folder |
| Load dialogs (several datasets in one folder, load failed) | `panels/file_menu.py` `FileMenu`: **the only listener of `dataset_choice_needed` / `dataset_load_failed`** |
| Session picker (combo + New) | File -> Session (radio list + New Session) and Dataset Manager -> Sessions |
| Experimental plan (Import / Export, summary, current-frame step) | File -> Import / Export, Dataset Manager -> Experiment plan |
| Summary (images, size, cubes, ...) | Dataset Manager -> Overview (plus folder and type) |
| Reference frame readout, Dark / Ignored readouts | removed: the sliders show them (maintainer) |
| Analysis / Outputs sections | removed earlier the same day (empty) |

Type / size / cubes / wavelengths stay visible all the time in the menu bar's info row (`panels/dataset_info_bar.py`).

## What went with the panel

- The Workflow dock and its width constants; `_DOCK_LAYOUT_STATE_VERSION` 4 -> 5 (a saved window layout or custom preset
  from before is dropped once: Qt cannot map a layout with the Workflow column onto the new tree).
- View menu: panel keys are now Image Ctrl+1 ... Sensorgram Ctrl+5 (were Workflow Ctrl+1 ... Sensorgram Ctrl+6).
- Edit -> Preferences: "Arrange the panels when the Workflow stage changes" (nothing left to change stage);
  `LayoutPresetManager.auto_apply`, `STAGE_TO_PRESET`. Presets are still applied from View -> Panel Presets / Ctrl+Shift+1..4.
- `AppSettings` fields `auto_apply_preset_on_stage_change`, `active_workflow_stage`, `expanded_subsections`. Old settings
  files still load (`_decode` drops unknown keys; pinned by `test_a_file_with_the_retired_workflow_fields_still_loads`).
- `panels/workflow/` (collapsible sections, eliding label, free-standing icons, the rows). Summary and Experimental plan
  moved to `panels/dataset_manager/summary.py` and `experimental_plan.py` (git mv).

## Design notes

- The Dataset Manager is built hidden with the main window (`app_rewrite._wire_file_menu`), before any dataset opens, so
  it hears the first `dataset_loaded` like the panel did. `DatasetModule` has no "current dataset" query; adding one was
  not needed.
- Metadata import / export are plain functions (`import_metadata_dialog`, `export_metadata_dialog`) shared by the File
  menu and the page's buttons.
- New remembered UI state: `dataset_manager/size`, `dataset_manager/page` (`storage/ui_state_keys.py`).
- Tests: `tests/integration/test_lspri_file_menu.py` (menu, sessions, manager), `test_lspri_dock_layout.py` (was
  `test_lspri_workflow_dock_resizable.py`).

## Next (not built)

Experiment step manager on the Dataset Manager's Experiment plan page: refractive index per step, auto-import of
`RIs.txt` from the dataset folder (columns Sample, RI, Std, Temp, N, Date; a solution library matched to the step notes
by name). Open question before building: a step can run different solutions in different channels
(`20perEG_-_ch1_to_40perEG_-_ch3F_ch4P`), so RI is per step **and channel**, and ROIs need a channel.
