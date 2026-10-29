# Cut-over plan: replacing the old LSPRi Evaluation app with the rewrite (2026-10-29)

**Status: plan only. Nothing in this file has been done.** It answers audit decision D4 (`whole_app_audit_2026-10-29.md`). Every phase that deletes or changes behaviour needs your yes first; section 9 lists the questions.

Words used here. *Old generation*: the original app (`gui/`, `io/dataset.py`, `processing/`, `app.py`, started by `run.py` and by the Suite card "LSPRimaging Evaluation"). *Rewrite*: the new app on the `rewrite` branch. *Cut-over*: the day the Suite card starts the rewrite and the old code is removed. *Shared module*: a file both apps import.

## 1. The short version

- **The mechanics are easy.** eva's `main` branch is still the old app exactly as of 2026-05-16, and `rewrite` is 356 commits ahead with none behind. Merging is a fast-forward: no conflicts are possible.
- **Deleting the old code cannot break the rewrite's imports.** The 57 old-only modules (44,776 lines) are, by construction, the ones the rewrite never imports (checked including imports inside functions and lazy `__init__` maps). The 37 shared modules can stay exactly where they are. (The audit said they "must move" first; that was too strong. Renaming them is optional tidying, Phase 3.)
- **The real blocker is a missing feature, not code.** The rewrite cannot export results. Spectra and Sensorgram have no export of any kind, and the Workflow panel's *Outputs* section is an empty "not built yet" label. The old app writes a documented measurement file (schema 7). Until the rewrite can get numbers out, it cannot replace the old app. Section 3, P1.
- **Real work sits in three places:** the export feature (P1), sorting about 71 test files into delete / port (Phase 2b), and small, careful changes to the Suite launcher (Phase 1b).
- **Safety net:** a git tag on the old app's last commit, and the launcher already supports pointing at a legacy checkout (`LSPR_LEGACY_IMAGING_ROOT`).

**Update (maintainer, 2026-10-29): the old LSPRi Evaluation app is not used.** **Confirmed by the maintainer the same day: nothing needs the old app.** Consequences:

- Results export is **not a parity gap**: nobody loses an export they use. P1 no longer blocks the cut-over; export stays a feature of the rewrite, to be designed later (`results_export_design_2026-10-29.md`, which now holds your answers).
- P2 (old session / profile files cannot be opened) and the schema-7 measurement file (C2) stop mattering: nobody produces it, nothing in this repo reads it. My recommendation for C2 becomes **retire it** (the two writer files go with the old generation in Phase 2a).
- C5 shrinks to the git tag; the staged approach (C8) is less about safety than about keeping the commits reviewable.
- What still gates the cut-over: your own checklist on a real dataset (P5), the decision on UI scale / shortcuts dialog / workflow notes (C3), the version number (C4), and the launcher changes (Phase 1b).

## 2. What was checked (facts the plan stands on)

| Fact | Evidence |
|---|---|
| eva `main` = old app, untouched since 2026-05-16 (`d05e91b`); `rewrite` = 356 commits ahead, 0 behind, 602 files changed (+152,980 / -15,004) | `git rev-list --left-right --count main...rewrite` |
| Local `develop` is an ancestor of `rewrite`: no commit is lost by merging `rewrite` | `git merge-base --is-ancestor` |
| The umbrella repo already pins eva at a `rewrite` commit, so the Suite ships rewrite code today; the card "LSPRimaging Evaluation" runs `src/main.py` (old), a second card "(Rewrite Preview)" runs `src/main_rewrite.py` and is labelled "Scaffold - not functional" (stale: the rewrite is far past that) | `apps/suite_launcher/src/suite_launcher/targets.py` 220-262 |
| Old-only: 57 modules / 44,776 lines: `gui` 43 / 37,291, `processing` 7 / 2,992, `storage` 2 / 1,907 (`measurement_export` + `schema7`), `io/dataset.py` 1,794, `app.py` 605 + `win_job_object` 123 + `main.py` 5, `domain/roi_editor_tools` 59 | `tools/import_reachability.py` (new, in this repo) |
| Shared: 37 modules / 9,211 lines. Nine carry old package names: `gui/app_theme`, `io/` (`_zarr_export_worker`, `image_naming`, `legacy_metadata`, `metadata_import`, `pump_plan`), `processing/` (`preprocess` 966 lines, `reference_selection`, `roi_detection` 501) | same tool |
| Nothing outside eva imports `lspr_imaging_app` (no other app, no package). One umbrella tool does: `tools/dark_frame_analysis.py` uses the old `io.dataset` | search of `apps/` and `packages/` |
| 261 LSPRi test files: 34 touch only old code, 37 mix old and shared/new, 13 touch only shared modules by their old names, 177 touch none of it. 31 import `app_rewrite` / `version_rewrite` / `main_rewrite` | `tests/` scan |
| CI (`tests.yml`) runs `python -m unittest discover -s tests` with submodules checked out. Every deletion must therefore arrive together with its tests, in the right repo order, or CI turns red | `.github/workflows/tests.yml` |
| The Suite bundle builds only the launcher and the updater (`build/`); eva's own `LSPRImaging.spec` is used by no workflow | `.github/workflows/`, `build/` |
| The launcher backs up / resets the old app's settings from the Windows `QSettings("LSPR", "LSPRImaging")`; the rewrite keeps its settings in `lspri_eva_rewrite_settings.json` in the shared config folder | `app.py` `settings_targets()`, `storage/app_settings.py` |
| `lspr_io` and the schema docs mention the measurement-export format only in comments / docs; no other app reads files the old app exports | search of `packages/`, `apps/`, `docs/schemas/` |

## 3. What the rewrite cannot do yet (parity)

| # | Gap | Evidence | Needs |
|---|---|---|---|
| **P1** | **Export of results.** No way to get spectra or sensorgram numbers out. The old app wrote the measurement file (`storage/measurement_export.py`, schema 7, documented in `docs/imaging_measurement_export_format.md` and `docs/schemas/`), with compact / upgrade tools | No export code in `panels/spectra`, `panels/sensorgram`; `panels/workflow/panel.py` `_build_outputs_section` is a placeholder | **Blocker.** Own design (what to write: CSV per ROI and group, an HDF5 file, both; what scope), and a decision on the schema-7 writer (C2) |
| P2 | Old files cannot be opened: `roi_table.json`, `measurement_backup.h5`, `processing_profile.json` from the old app | Decided earlier: no importer (eva `CLAUDE.md`, "What NOT to do") | Nothing to build; tell users, keep the old app runnable from a tag |
| P3 | Found in the old app, not found in the rewrite: the **UI scale** setting (`ui/scale_factor`), a **keyboard-shortcuts** dialog, **workflow notes** | Zero matches in the rewrite's code | Your call per item: drop or port (C3) |
| P4 | Launcher integration points at the old app: settings backup / reset, start-menu entry, the extra Preview card, `test_launcher.py` expectations | `targets.py`, `app.py`, `start_menu_shortcuts.py`, `tests/integration/test_launcher.py` | Phase 1b |
| P5 | Anything you use in the old app that I cannot see from the code | | **Your personal checklist (Phase 0).** I cannot drive the app (root rule), so nobody has compared the two on real data yet |

Items that look like gaps but are not: manual and automatic ROI placement (the ROI tab's Array section replaces "Detect ROIs"), session new / load (sessions with autosave), "Stack to Zarr" (the Export tab), exclusions (ignore masks).

## 4. The plan

Each phase ends with an exit check, and each can be undone by reverting its commits. Repo order is always the same (root rule): commit in the eva submodule first, then bump the pointer, together with test changes, in the umbrella.

### Phase 0. Decide and prepare (no code change)

1. Answer section 9 (C1 to C8).
2. Tag the old app: `git tag legacy-final d05e91b` in eva and push the tag. (A tag is only a label; it changes nothing.)
3. Write your own parity checklist from daily use of the old app (P5) and walk the rewrite through it on one real dataset. Anything missing joins section 3.
4. Decide the release version of the cut-over (C4).

**Exit:** C1 to C8 answered, tag pushed, checklist empty or accepted.

### Phase 1. Make the rewrite the app (reversible; the old code stays in the tree, unused)

**1a. Close P1** (results export). A separate feature; the design proposal is `results_export_design_2026-10-29.md` (CSV plus a settings record, one new folder per export inside the session; its section 9 holds the questions only you can answer); the cut-over does not start before it is done, unless you decide otherwise (C1).

**1b. Switch the entry points** (small):
- eva: `src/main.py` and `run.py` start what `main_rewrite.py` starts (launch screen first); console script `lspri-evaluation` points at `lspr_imaging_app.app_rewrite:main`; drop `lspri-evaluation-rewrite`; fold `version_rewrite.py` into `version.py` (one version, C4). Its users are `app_rewrite.py` (window title, log header, `QApplication` version), `analysis/scan_store.py` and `analysis/store.py` (they stamp the app name and version into the files they write) and the launcher's `version_file`; no test imports it. The name "LSPR Imaging (Rewrite Preview)" is written into new `scan.h5` files as provenance text (`app_name`, `created_by`); nothing reads those fields back as a compatibility key (checked), so older files stay valid and new ones simply carry the new name.
- umbrella launcher: remove the Preview card (`lspri_eva_rewrite`), rewrite the text of the `lspri_eva` card, change its settings target from `QSettings` to the file `lspri_eva_rewrite_settings.json` (and cover `perf_profile.json`), update `tests/integration/test_launcher.py` (expects both card keys today) and the start-menu entry if needed.

**Exit:** launcher tests and the rewrite's entry tests pass; you start the Suite card by hand and see the rewrite. **Rollback:** revert the commit.

### Phase 2. Remove the old generation (the big deletion)

Do it in separate commits so each is easy to review and to revert.

- **2a. Old-only modules.** Delete the 57 files (list in appendix A): all of `gui/` except `app_theme.py`, `windows_titlebar.py` and `__init__.py`; `processing/` except `preprocess`, `reference_selection`, `roi_detection`; `storage/measurement_export*.py` (only after C2); `io/dataset.py`; `app.py`; `win_job_object.py`; old `main.py` content; `domain/roi_editor_tools.py`. **Exit check:** `python tools/import_reachability.py` shows 0 modules "only the old app".
- **2b. Tests (71 files: 34 that touch only old code + 37 that mix old and shared code; the other 13 stay).** Never bulk-delete; sort each file by one question: *does the behaviour it tests still exist in the rewrite?*
  - **Port** (change the import to the rewrite's twin): the OME-Zarr export / load tests that use `io.dataset` and have a counterpart in `dataset/io.py` (50 same-named functions exist there): `test_lspri_ome_zarr_acquisition_metadata`, `..._export_compression`, `..._incomplete_export`, `..._shard_crc`, `test_lspri_dataset_folder_discovery`, and the **cross-app guard `test_lspri_acq_zarr_compat.py`** (proves eva reads the acquisition app's zarr; deleting it would lose the only check). `test_lspri_ome_zarr_adaptive_export` tests the I/O-wait tuner that no longer exists in the new `dataset/io.py`: delete.
  - **Check for a rewrite twin first, then delete or port:** `test_lspri_roi_math`, `test_lspri_roi_array_geometry`, `test_lspri_roi_histogram`, `test_lspri_trace_statistics`, `test_lspri_roi_rasterize`, `test_lspri_analysis`, `test_lspri_reduction_*` (old `processing.*` maths; the rewrite's counterparts are `analysis/reduction.py`, `frame_reduce.py`, `roi/rasterize.py` and the `rewrite_*` / `analysis_scan_*` tests). Delete only where the twin's tests pin the same behaviour.
  - **Delete:** tests of old window behaviour (`gui.main_window`, `analysis_controller`, `layout_state_controller`, `overlay_manager`, `worker`, ... about 55 files). The behaviour they pinned is gone with the window.
  - **Keep, import unchanged:** the 13 that import shared modules by old names (renamed in Phase 3 if at all).
  - **Pointer to fix:** `.claude/rules/gui.md` names `test_lspri_preferences_dialog.py` (a test of the old dialog) as the pattern for directly callable widgets; repoint it to `test_lspri_rewrite_preferences.py`.
- **2c. Tools.** `apps/LSPRi/eva/tools/array_lab.py` and `detect_spectral_outliers.py` (import old `processing.*`) and umbrella `tools/dark_frame_analysis.py` (imports old `io.dataset`): port to the rewrite's modules or delete each.
- **2d. Packaging.** Delete `LSPRImaging.spec` and the app's `requirements.txt` (audit D6); remove the `lucide` dependency (only the old `gui/` imports it) and its hidden import; delete or replace `TODO.md` (every item is about the old app).
- **2e. The old analysis engine** (audit D3, about 1,900 lines): `analysis/engine.py`, `tasks.py`, `store.py`, `planner.py`, `worker.py`, the engine construction in `app_rewrite.py`, and their tests (`test_lspri_rewrite_analysis_engine.py`, parts of `..._session_autosave.py`). `provenance.py` and `reduction.py` stay: the scan imports constants and maths from them. **This touches code the scan depends on, so its check is numeric:** `SCAN_ALGORITHM_VERSION` must not change, `test_lspri_scan_local_mask_keys.py` and the scan hash tests must pass, and `tools/scan_golden_check.py` should agree.
- **2f. Leftovers inside shared modules.** Measured with `vulture` on 2026-10-29 (audit, section 7, "D9"): once the old code is ignored, **42 functions, methods and classes (1,478 lines)** are unused, among them `storage/workspace.py` roi-table and processing-profile save / load (498 lines), five helpers in `dataset/io.py` (138), three copies of `detect_rois` (316), two patch helpers duplicated in `processing/preprocess.py` and `image_tools/geometry/transform.py`, and `domain/models.py` `AnalysisState` / `FormulaSpectrumResult`. Tests still call many of them and go or are ported in the same step. Also decide the unused per-cube Auto reference cache of `ReferenceFrameModule` (the audit explains why it is not a bug). One commit, reviewed as a list.

**Exit (all of 2):** import tool: 0 only-old, 0 unexplained orphans; `ruff` clean; every touched test file passes, one process per file; then one full run of the LSPRi tests (many apps are not touched, so `packages/` tests are not needed). **Rollback:** revert the commit of the bucket.

### Phase 3. Tidy names (optional, later, mechanical)

Move the nine shared modules with old names to rewrite-style homes (`io/` and `processing/` pieces into `dataset/` and `image_tools/`; `gui/app_theme.py` and `gui/windows_titlebar.py` into `panels/`), rename `app_rewrite.py` to `app.py` and `main_rewrite.py` to `main.py`, update the 31 test files that import `app_rewrite` and the 13 that use old names. Pure renames, nothing computed changes. Not needed for the cut-over; do it once the cut-over has been in use for a while.

### Phase 4. Documentation

- eva `CLAUDE.md`: remove the "rewrite exception", "Which generation to edit", "Status and scope" and the old-engine "Analysis rules" block; keep the rules. README, CHANGELOG (entry for the cut-over version), `docs/README.md`, `.claude/rules/gui.md` pointer.
- umbrella: root `CLAUDE.md` (app table; and the LSPRi Acquisition row, which says "does not exist yet" but is a scaffold submodule), `LSPR_SUITE_AI_BRIEFING.md` (its section 4, "LSPRimaging Evaluation App - Full Detail", describes the old app and is the biggest stale block), `docs/architecture/apps/LSPRi/eva/README.md`, launcher card text, `TASK_rotation_fill_handling.md` (referenced by the docstring of `test_lspri_rewrite_nan_handling.py`; fix the docstring if it moves).
- Old-app docs under `docs/` stay as history; mark the ones that describe the old app (for example `bulk_analysis_performance_investigation.md`, `live_preview_analysis_slowdown_investigation.md`).

### Phase 5. Merge and release

1. In eva: `git merge --ff-only rewrite` into `main`, tag the version chosen in C4, push `main` and the tag. Keep the `rewrite` branch for a while.
2. In the umbrella: bump the submodule pointer to the new `main` tip (today it pins a `rewrite` commit). The auto-release workflow then bumps the Suite Launcher version on push to `main` (`auto-release.yml`); no change needed there.
3. Check CI (`tests.yml`) is green on the umbrella.

**Rollback:** the `legacy-final` tag still holds the old app; point `LSPR_LEGACY_IMAGING_ROOT` at a checkout of it.

## 5. Order and dependencies

```
Phase 0 (decide, tag) -> 1a (export feature) -> 1b (entry points) -> your manual pass in the Suite
                                                                   -> 2a..2f (delete, in this order) -> 4 (docs) -> 5 (merge, release)
                                                                   -> 3 (renames), any time after 2
```

2e (engine) depends on nothing else but is the only step that can change numbers, so do it last inside Phase 2 and check it against the scan hash tests. 2f needs the `vulture` go-ahead.

## 6. Risks

| Risk | What reduces it |
|---|---|
| Something the old app did that you rely on is missing | Phase 0 checklist on a real dataset; the tag and the launcher's legacy-root setting keep the old app available |
| Users with in-progress old sessions cannot continue them | Decided earlier (no importer). Say so in the release note; the tag runs the old app unchanged |
| CI red between the eva push and the umbrella bump | Always the same order and the same day: eva first, then umbrella with the test changes and the pointer in one commit |
| A hidden dependency on old code (for example a module loaded from a string) | The import tool follows imports inside functions and lazy maps; a search found no `import_module` strings; every phase ends with the import tool plus per-file tests |
| Deleting a test deletes the only check of something (the zarr compatibility guard is one) | Phase 2b sorts by "does the behaviour still exist", never by directory; port instead of delete where a twin exists |
| Phase 2e changes a stored scan number | `SCAN_ALGORITHM_VERSION` untouched, hash tests and the golden check must pass |
| Several things at once make a failure hard to place | One commit per bucket; Phase 3 renames only after the cut-over has been used |

## 7. Relative size (not hours)

Phase 0 small. 1a **large** (a feature). 1b small to medium. 2a, 2c, 2d small (scripted, mechanical). 2b **medium** (71 files, a judgement each). 2e medium. 2f small to medium. 3 medium, mechanical. 4 medium (many files, little thinking). 5 small.

## 8. Ready to cut over when

1. ~~Results can be exported from the rewrite~~ (no longer a condition: the old app is not used, see the update at the top).
2. You worked one real dataset through the rewrite without needing the old app.
3. `legacy-final` is tagged and pushed.
4. Launcher card, settings backup and start-menu entry point at the rewrite and their tests pass.
5. The version and the release note are decided.

## 9. Decisions needed from you

| # | Question | My recommendation |
|---|---|---|
| C1 | Is exporting results a condition for the cut-over? | **Yes.** Design the *Outputs* section first; a per-ROI / per-group CSV plus the Sensorgram traces may be enough as a first version |
| C2 | The schema-7 measurement file (documented in `docs/schemas/`): keep the writer, replace it with the new export, or retire it? | Replace it with the new export; keep the schema docs as history. Needs your yes because it is a documented format |
| C3 | UI scale, keyboard-shortcuts dialog, workflow notes: drop or port? | You decide per item; none blocks the cut-over |
| C4 | Version of the cut-over release and the CHANGELOG policy (the rewrite is still 0.1.0 after 356 commits; the old app is 0.2.1) | One version line from here on, a minor bump such as 0.3.0, not 1.0 |
| C5 | Keep the old app runnable? | Tag only, no maintenance |
| C6 | Rename modules (Phase 3) now or later? | Later |
| C7 | Remove the "(Rewrite Preview)" card in Phase 1b? | Yes |
| C8 | One go or staged? | Staged: 1b first, use the Suite for a while, then Phase 2 |

## Appendix A. The 57 old-only modules (lines)

- **`gui/` (43 files, 37,291 lines):** `main_window` 8,601; `analysis_worker_mixin` 4,112; `analysis_tasks` 2,023; `analysis_controller` 1,909; `main_window_icons` 1,885; `layout_builder` 1,772; `chromatic_controller` 1,721; `widgets` 1,229; `mask_controller` 1,216; `dataset_controller` 969; `overlay_manager` 842; `image_interaction_controller` 810; and 31 smaller ones. Regenerate the full list with `python tools/import_reachability.py --list`.
- **`processing/` (7, 2,992):** `chromatic` 1,695; `analysis` 440; `roi_array_geometry` 320; `trace_statistics` 193; `roi_math` 175; `roi_histogram` 89; `roi_rasterize` 80.
- **`storage/` (2, 1,907):** `measurement_export` 1,461; `measurement_export_schema7` 446.
- **`io/dataset.py` 1,794; `app.py` 605; `win_job_object.py` 123; `domain/roi_editor_tools.py` 59; `main.py` 5.**

The tool prints these groups and totals at any time and is the exit check of every phase: `python tools/import_reachability.py`.
