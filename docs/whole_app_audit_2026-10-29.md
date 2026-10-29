# LSPRi Evaluation: whole-app audit (clean-up, maintenance, documentation), 2026-10-29

Scope: `apps/LSPRi/eva` (the rewrite plus the old generation that still sits beside it). Follows the 2026-10-07 audit (`whole_app_audit_2026-10-07.md`); that file's open items are re-checked in section 3.

## 1. How this was done, and what was not

**Done (all read-only except section 6):**

- `ruff check` with the project's own settings on `src/` and `tools/`; then once with a broader rule set (`B, SIM, PERF, C4, RUF, UP, PLW, RET, ARG`) as a diagnostic only, nothing fixed.
- An **import graph** (my own small script, standard library only). It reads every `import` in `src/lspr_imaging_app`, follows them (also the lazy `__getattr__` maps in `__init__.py` files), and answers three questions: which modules does the rewrite (`main_rewrite.py` -> `app_rewrite.py`) reach, which does only the old app (`main.py` -> `app.py`) reach, and which does neither reach (an *orphan*: nothing imports it).
- Name searches for the specific dead-code candidates, `python -X importtime` on the rewrite, a check that every file named in CLAUDE.md / README / docs exists, a check that every test imports modules and names that exist, a check that all docs are valid UTF-8.

**Not done:**

- No test run. The only code change is a module docstring and comments in `pyproject.toml`; no behaviour changed.
- The app was not launched (root rule: no driving GUI apps).
- `vulture`, `radon`, `pytest-cov`, `import-linter`, `mypy` were not run (root rule: name the tool, wait for a go-ahead; see decision D9).
- The "which module no test mentions" check is name-based and weak (small Qt ribbon widgets are tested through the Image panel); I did not act on it.

**Limits to keep in mind:** the import graph cannot see a module loaded from a string (`importlib.import_module("...")`). I searched for that pattern and found none. A module the graph calls an orphan was then checked by name in `src`, `tests`, `tools` and `docs` before it went on the list below.

## 2. Summary

The code is in good health where it was checked: lint clean, the thread rules hold, nothing fails silently, tests and doc pointers resolve, git is in sync. The real clean-up debt is **one third of the code base is the old app** (still needed until the cut-over), a handful of **dead modules and functions**, an `ImagePanel` that has **grown back to 3,167 lines**, and **documentation that drifted**: CLAUDE.md for this app is 44 KB and contradicted itself, and the README, TODO and CHANGELOG describe only the old app.

| Measure | Number |
|---|---|
| Python modules / lines under `src/` (incl. the two `main*.py`) | 421 / 131,848 |
| Reached by the rewrite | 356 |
| Reached **only by the old app** | 57 modules, **44,776 lines (34 %)** |
| Shared by both | 37 modules |
| Orphans (reached by neither) | 7 real ones, 1,506 lines (section 3a) |
| Test files `test_lspri*` | 259 (77 named `rewrite`); 58 import the old `gui`, 10 the old `io.dataset`, 16 `processing.*` |
| Docs in `docs/` | 96 files before this audit, no index (now added) |
| App CLAUDE.md | 44 KB, about 11,000 tokens loaded in every session in this folder (root CLAUDE.md: 2,400 tokens) |

## 3. Findings

### a. Dead code

**A1. Seven orphan modules** (nothing imports them; no UI reaches them):

| Module | Lines | Notes |
|---|---|---|
| `image_tools/chromatic/landmark_autotrack.py` | 1,092 | Replaced by `auto_landmarks.py` (its own docstring says so). One test, `test_lspri_rewrite_nan_handling.py` (line 40), still imports `prepare_registration_image` from it. Removing it leaves most of `roi/detection.py` (517 lines; `detect_rois` and helpers) used only by tests; only `ignored_pixel_mask` is live (used by `background/estimate.py`). |
| `panels/image/chunk_layout_dialog.py` | 223 | `export_tab.py` says in its docstring: "`chunk_layout_dialog.py` is no longer used". |
| `panels/image/cube_spin_box.py` | 33 | Unused. |
| `panels/image/guided_value_spinbox.py` | 37 | "Ported verbatim" copy; the old app has the real one in `gui/widgets.py`. Unused in the rewrite. |
| `panels/workflow/form_rows.py` | 39 | Unused. |
| `gui/theme.py` | 39 | Old generation; a re-export. |
| `processing/chromatic_utils.py` | 43 | Old generation; mostly imports from `processing/chromatic.py`. |

(`applog/__main__.py` also has no importer, correctly: it is `python -m lspr_imaging_app.applog`.)

**A2. Four dead functions in `dataset/io.py`, 247 lines.** `calibrate_zarr_read_overhead_ms` (85), `calibrate_analysis_worker_count` (116), `estimate_ome_zarr_export_chunk_plane_read` (20), `estimate_ome_zarr_export_dataset_total_read` (26). Only the copies in the old `io/dataset.py` are called (from `gui/`). They belong to the old Export section and to the I/O-wait tuner that `lspr_core.perf` replaced. No test imports them from `dataset/io.py`; none sets shared state.

**A3. The old analysis engine path, about 1,900 lines.** `AnalysisEngine` (`engine.py`, 814 lines) with `tasks.py` (575), `store.py` (216), `planner.py` (141) (about 1,750 lines; `worker.py`, which I first counted in, is used by the Histogram panel, the renderer and `DatasetModule` and stays). No UI starts it (the scan redesign replaced it), but `app_rewrite.py` still builds it and two tests exercise it (`test_lspri_rewrite_analysis_engine.py`, parts of the session-autosave test). It is the "goes at the cut-over" item in CLAUDE.md. Two of its helpers are still shared: `provenance.py` (constants the scan imports) and `reduction.py` (the scan's maths).

**A4. The old generation: 57 modules, 44,776 lines.** `gui/` 43 files / 37,291 lines (`main_window.py` alone is 8,601), `processing/` 7 / 2,992, `storage/` 2 / 1,907, `io/dataset.py` 1,794, `app.py` 605, `win_job_object.py`, one `domain` file. 37 modules are shared with the rewrite; nine of them sit in packages that carry old names. *(Correction, see the cut-over plan: they do not have to move before the old code is deleted, because nothing the rewrite imports is among the 57; renaming them is optional tidying.)* The nine: `io/` (`_zarr_export_worker`, `image_naming`, `legacy_metadata`, `metadata_import`, `pump_plan`), `processing/` (`preprocess` 966 lines, `roi_detection` 501, `reference_selection`), `gui/app_theme.py`, and the shared parts of `storage/workspace.py` (the rewrite uses only `save_acquisition_metadata_sidecar` and the acquisition-metadata loaders; `load_processing_profile`, 327 lines, has no caller outside `gui/`). 72 functions (2,158 lines) are identical between the two generations (ignoring whitespace and docstrings), e.g. the whole landmark tracker (`landmark_autotrack.py` vs `processing/chromatic.py`).

**Status of the 2026-10-07 audit's open items:** D3 (cut-over): not started; `run.py`, `src/main.py` and `LSPRImaging.spec` still start the old app. D4 (fractional weighting): still not wired into the scan (`frame_reduce.py` uses the binary raster). D5 (dead files): `mask_settings.py` and `mask_highlight_actions.py` are gone; `analysis/statistics.py` turned out to be live (Sensorgram); `landmark_autotrack.py` is still there (A1).

### b. Maintainability

**B1. `ImagePanel` is 3,167 lines with 178 methods** (`panels/image/panel.py`, 3,497 lines in the file). The 2026-10-07 audit split out the interaction router and the ROI overlay; the panel has since regrown with every new tab and tool. Next biggest classes: `RoiToolbox` 1,499 lines / 69 methods, `ImagingMeasurementExportWriter` 1,130 / 26 (`storage/measurement_export.py`), `RoiTablePanel` 1,005 / 76, `PanelContainer` 692, `SensorgramPanel` 644 / 45, `DatasetModule` 642. I have not studied where the seams are; that is a design task, not a quick edit.

**B2. 49 functions of 4,345 are over 100 lines.** Longest in the rewrite: `_export_ome_zarr_dataset_to_path` 396, `load_processing_profile` 327 (old format), `compute_cell` 241 (old engine), `scan_transients` 219, `panels/spectra/panel.py` `__init__` 210, `migrate_to_schema7` 190, `ImagePanel.__init__` 186, `build_main_window` 183. Mostly construction code or one long numeric routine; nothing urgent.

**B3. `ruff` with the project's rules: clean.** The broader diagnostic set found 469 style hints, nearly all noise (35 `zip` without `strict`, 28 quoted annotations, 20 needless lambdas). One is worth knowing: **121 `# noqa` comments that suppress nothing** (`RUF100`). Harmless clutter; auto-fixable; touches many files, so I left it (D9).

**B4. Silent failures: none.** No bare `except:`; 3 `except Exception: pass` were each read: removing an atexit hook, setting a numpy array read-only, falling back from tifffile to Pillow for an image size. 67 `except Exception` in total, the others log or have a reason comment. One `print(` (the log-summary command line tool, correct). Three `TODO`s (ROI shapes). No `processEvents`, no `time.sleep` on a GUI path.

**B5. Thread rules hold.** `QThreadPool` appears only in docs and comments of the rewrite (every real use is a plain `threading.Thread`).

### c. Dependencies and packaging

- **C1. `apps/LSPRi/eva/requirements.txt` is out of sync and unpinned.** It lacks `psutil`, `platformdirs`, `lspr-io` and `zarrs`, and has no version ceilings (the root pinning rule); `pyproject.toml` is the real list (and the root `requirements.txt` installs the app with `-e apps/LSPRi/eva`). Nothing found that reads the app's file. Two sources of truth is the problem (D6).
- **C2. `lucide` is used only by the old `gui/`** (`main_window.py`, `main_window_icons.py`). The rewrite takes icons from `lspr_ui`. It goes with the cut-over (also from `LSPRImaging.spec`'s hidden imports).
- **C3. `pydantic` is declared here but imported only by `lspr_core`** (which should declare it; this app gets it through `lspr-core`). Harmless; mention only.
- **C4/C5 (fixed, comments only):** `pyproject.toml` said `zarrs` was "DISABLED as of 2026-09-07"; the code re-enabled it on 2026-09-08 (`dataset/io.py`, `_configure_zarr_codec_pipeline`, in both generations). The `psutil` comment said "diagnostic only" and pointed at the old `gui/`; the rewrite uses it in `analysis/scan.py` for the worker-process watch.
- **C6.** `LSPRImaging.spec` (PyInstaller) builds `src\main.py`, the old app. Correct today; goes into the cut-over checklist.

### d. Performance

**D1. Import cost of the rewrite: 2.24 s** (`python -X importtime`, warm cache, offscreen, one run on this machine; the launch screen covers it). Largest pieces: `scipy.optimize` 0.50 s, `scipy.signal` 0.34, `scipy.stats` 0.26 (these overlap each other), `zarrs` 0.27, `zarr` 0.22, `pyqtgraph` 0.17, `lspr_core` 0.16, `numpy` 0.07. **I found no cheap win.** A *lazy import* (load a library at first use instead of at start-up) would only move the time: the usual start (reopen the last dataset, draw the Sensorgram) uses `zarr`, `zarrs` and `scipy.optimize` within seconds anyway. Not recommended.

**D2. Known performance items that are waiting for a decision** (from the earlier notes, not re-measured today):

- `snapshot_source` on the GUI thread, 0.2-0.5 s per ROI edit; fix needs change counters on Chromatic, Mask and FrameRoles (`startup_time_2026-10-24.md`, "Follow-up", item 2).
- `dataset.file_stats` (size / date label stays "..." for 8-12 s; nothing waits for it): leave, helper process, or per-dataset cache (same doc, item 3).
- CRC32C check of every zarr shard index (about 4 % of a read with the `crc32c` package; cheaper ideas in the comment above `_shard_index` in `dataset/io.py`).
- Play prep and `pic_ms` (`play_prep_cost_2026-10-21.md`, `testing7_playback_analysis_2026-10-14.md`): several fixes were taken up later; I did not check which of the five proposals in the testing7 doc are still open.
- Unknown cause of one Sensorgram helper-process crash (`startup_time_2026-10-24.md`): the diagnostics are in place, nothing to do until it recurs.

### e. Documentation

**E1. App CLAUDE.md (fixed in part).** It said "Spectra and Sensorgram panels: scaffold only (`NotImplementedError`)" and "`analysis/statistics.py`, `RoiToolbox.detect_rois`: no UI calls it". The first is false: the panels are built (5,800 lines, no `NotImplementedError`). In the second, `statistics.py` is live in the Sensorgram, but `RoiToolbox.detect_rois` really is unused by any UI (only tests call it; ROIs come from the ROI tab), so it stays in the list. *(Correction: an earlier version of this audit wrongly said the method no longer exists.)* It also said "read the 2026-09-30 status doc first" at the bottom while calling it stale at the top, and its "Analysis rules" described the old engine as if current. Corrected (section 6). **Still open: its size** (D5): 44 KB, two single lines of 9,400 and 4,300 characters ("Analysis redesign in progress", "Experiment steps and the plan bar") that are design records, not rules.

**E2. `app_rewrite.py` module docstring (fixed).** It claimed every panel is an empty placeholder, every method raises `NotImplementedError`, and that splash, dataset restore, layout persistence and window-state restore "do not exist yet". All exist.

**E3. README, TODO, CHANGELOG describe only the old app.** README now has a short "The rewrite" section (how to start it, where the rules are); TODO has a scope note (every item is about `gui/`); CHANGELOG is untouched (D8). `workflow/panel.py`'s docstring still says every section is a placeholder: only **Outputs** is (it shows "Outputs - not built yet.").

**E4. No docs index (fixed).** `docs/README.md` now groups all 96 docs by topic, titles read from the files.

**E5. Pointers.** Every `docs/*.md` named in CLAUDE.md, README and the docs exists. Backticked `.py` names in older docs that no longer exist (deleted modules, scratch benches) are history and left as written.

**E6. Umbrella repo, outside this app (not changed):** root CLAUDE.md says LSPRimaging Acquisition "does not exist yet", but `apps/LSPRi/acq` is a submodule with a scaffold (own README: "early scaffold, under active development", its own tests); `TASK_rotation_fill_handling.md` (task brief of 2026-10-03, the NaN work looks finished: tests and the inventory doc exist, not checked item by item) sits in the umbrella root; `LSPR_SUITE_AI_BRIEFING.md` (1,466 lines) was last changed 2026-08-23.

### f. Checked and fine

Ruff clean; 261 test files import only existing modules and names; all docs, source and config are valid UTF-8; every source file parses; umbrella `main` and eva `rewrite` match their remote-tracking branches (I did not fetch) with nothing stashed; `src/.mypy_cache` (27 MB) and `src/.import_linter_cache` are git-ignored leftovers of earlier tool runs (safe to delete by hand, I did not).

## 4. Decisions for the maintainer

| # | Decision | Recommendation |
|---|---|---|
| D1 | Delete the 7 orphan modules (A1; `landmark_autotrack.py` needs the one test line adjusted first) | **Done 2026-10-29** (section 7). |
| D2 | Delete the 4 dead functions in `dataset/io.py` (A2, 247 lines) | **Done 2026-10-29** (section 7). |
| D3 | The old engine path (A3, ~1,900 lines + its tests) | **Done 2026-10-29** (cut-over phase 2e: engine, store and planner removed; `compute_cell` kept as the reference; `docs/old_generation_cutover_plan_2026-10-29.md`, section 10). |
| D4 | The cut-over of the old generation (A4) | **Done 2026-10-29 except phases 2e / 2f**: see section 10 of `old_generation_cutover_plan_2026-10-29.md`. The export no longer blocked it once the maintainer confirmed that nothing uses the old app. |
| D5 | Shrink app CLAUDE.md from 44 KB to about 15 KB | **Done 2026-10-29** (section 7), but the result is 32 KB, not 15 KB: the moved section was 25 KB of 46 KB, and the other 21 KB are rule sections. Going lower would mean trimming rules line by line. |
| D6 | `apps/LSPRi/eva/requirements.txt` | Delete it (the root file already installs the app from `pyproject.toml`), or sync it. I prefer deleting. |
| D7 | Split `ImagePanel` (B1) | **After D4**; first a short design of the seams. |
| D8 | Versioning: `version_rewrite.py` still says 0.1.0 after 316 commits since the tag; `CHANGELOG.md` has no rewrite entry | Decide the rewrite's version scheme before the cut-over. |
| D9 | Advisory tools | **`vulture` run 2026-10-29** (section 7, results below); **run again on the new tree and acted on the same day** (cut-over phase 2f: `storage/workspace.py` 1,067 -> 85 lines, 17 unreferenced definitions; `docs/old_generation_cutover_plan_2026-10-29.md`). `RUF100` auto-fix (121 useless `noqa`) still open: low value, touches many files. |
| D10 | Umbrella-level drift (E6) | Fix root CLAUDE.md (LSPRi acq exists), move or delete `TASK_rotation_fill_handling.md`, refresh or retire the AI briefing. Lands in the umbrella repo. |
| D11 | Which known performance item (D2) to take up first | Your call; none is urgent. |

## 5. Not changed

No code behaviour, no data format, no dependency, no file deleted, nothing committed or pushed.

## 6. What was changed in this pass (documentation and comments only, all uncommitted in the `apps/LSPRi/eva` repo, branch `rewrite`)

- `CLAUDE.md`: "Read first" lists this audit and the docs index; the "Where the rewrite stands" heading; the two false bullets replaced by an accurate "old analysis path" bullet and a "not built yet" bullet; a note on the "Analysis rules" block; the bottom reference to the stale status doc.
- `README.md`: new section "The rewrite (in development)"; one line on where the Qt tests are and one pytest process per file.
- `TODO.md`: scope note.
- `pyproject.toml`: the `zarrs`, `psutil` and `dataset/io.py` comments (no version or dependency changed).
- `src/lspr_imaging_app/app_rewrite.py`: module docstring.
- `docs/README.md` (new) and this file (new).

## 7. Execution log

### D1 and D2: dead code removed (2026-10-29, maintainer: "all of it"; uncommitted)

- **Deleted 7 files (1,506 lines):** `image_tools/chromatic/landmark_autotrack.py`, `panels/image/chunk_layout_dialog.py`, `panels/image/cube_spin_box.py`, `panels/image/guided_value_spinbox.py`, `panels/workflow/form_rows.py`, `gui/theme.py`, `processing/chromatic_utils.py`. Before deleting, each was searched by name in `src`, `tests`, `tools` and `packages`: only comments mentioned them.
- **Removed 4 functions from `dataset/io.py`** (247 lines of code; 256 with blank lines and the `import math` they alone used): `calibrate_zarr_read_overhead_ms`, `calibrate_analysis_worker_count`, `estimate_ome_zarr_export_chunk_plane_read`, `estimate_ome_zarr_export_dataset_total_read`. The old app's copies in `io/dataset.py` are untouched. The file keeps its CRLF line endings.
- **One test class removed** from `tests/unit/test_lspri_rewrite_nan_handling.py` (*umbrella repo*): `TestRegistrationImageIsNaNAware` tested only the removed landmark tracker. The live finder's NaN handling stays pinned by `test_nan_corners_from_rotation_do_not_break_it` in `test_lspri_rewrite_chromatic_auto.py`. A comment in the test file says so.
- **Comments updated** where they named a removed file: `affine.py`, `auto_landmarks.py`, `model.py`, `module.py` (chromatic), `roi/rasterize.py`, `roi/toolbox.py`, `panels/image/export_tab.py`, `export_roi_layout.py`. Dated docs under `docs/` still mention the removed files; that is history and was left alone.
- **Result:** `src/` went from 421 modules / 131,848 lines to 414 / 130,076. The import graph now finds one module that neither app imports: `applog/__main__.py`, the command-line log tool, which is correct. `ruff` is clean; `app_rewrite` and the old `app` both still import.
- **Follow-on, not done (needs its own decision):** with `landmark_autotrack.py` gone, `roi/detection.py`'s `detect_rois` and its helpers are used only by three tests in `test_lspri_rewrite_nan_handling.py` (the live part of that file is `ignored_pixel_mask`, used by the background estimate). `RoiToolbox.detect_rois` (takes ready-made results) is likewise called only by tests.
- **Repos:** the deletions and comment edits land in the **eva submodule** (`apps/LSPRi/eva`, branch `rewrite`); the test edit lands in the **umbrella** repo (`tests/`). Nothing committed yet.

### D1/D2 verification (2026-10-29)

Twelve test files, one pytest process each, all passed (237 tests, 0 failures): `test_lspri_rewrite_nan_handling`, `rewrite_chromatic_auto`, `export_fast_paths`, `fast_tiff_decoder`, `transient_detection`, `export_workers`, `pump_plan_import`, `rewrite_ome_zarr_export_frame`, `rewrite_ome_zarr_export_metadata`, `rewrite_slider_ribbon`, `session_carry_export`, `zarr_partial_reads`. These are the files that import `dataset/io.py` plus the two chromatic / NaN files. The full suite was not run (one app, no `packages/` change).

### D5: CLAUDE.md shrunk (2026-10-29, uncommitted)

- **Moved verbatim, by script (not retyped):** the whole "Where the rewrite stands" section (24,954 bytes, 13 bullets) to `docs/rewrite_status_by_feature_2026-10-29.md`.
- **Left in `CLAUDE.md`:** a summary with one line per feature and a pointer to its doc, plus a new block "Rules that come from these features" (about 20 rules in 8 groups: parallel work, mask Scan / Auto mask, the scan and what it stores, Spectra / Sensorgram, experimental plan, ROI position tuning). Each rule was taken from the moved text; rule-word sentences of the original were listed and compared against the new text (24 sentences; the 3 not matched mechanically were checked by hand: two are in the new text, one is history).
- **Result:** `CLAUDE.md` 46,032 -> 32,532 bytes (-29 %, about 3,400 tokens less per session in this folder). Added "Read first" pointer and fixed the pointer in the "Analysis rules" note.
- **To review:** the extraction is my judgement of what is a rule and what is history. `git diff CLAUDE.md` plus the moved doc show everything; if a rule you rely on is missing, the original sentence is in the moved doc.

### D4: cut-over plan written (2026-10-29, nothing executed)

`old_generation_cutover_plan_2026-10-29.md`: facts found, parity gaps, five phases with exit checks and rollback, risks, and eight decisions. New tool `tools/import_reachability.py` (standard library only) prints which modules the rewrite reaches, which only the old app reaches, and which neither reaches; the plan uses it as the exit check of every phase. Findings that changed earlier statements in this audit: eva `main` is still the untouched old app so the merge is a fast-forward; deleting the old code cannot break the rewrite's imports (the shared modules need not move first); 71 test files (not 84) need sorting, and one of them (`test_lspri_acq_zarr_compat.py`) is a cross-app guard that must be ported, not deleted.

### D9: vulture run (2026-10-29, read-only, nothing changed)

`vulture 2.16`, confidence 60 % (the lowest; Qt code gives many false positives), two runs.

**Run 1: all of `src/`.** 377 findings. 147 are in old-only modules (leave with the cut-over), 17 are Qt overrides. Of the 213 left, 124 have names that also occur elsewhere in `src` (mostly name collisions; not examined), 59 are used only by tests or tools, 30 occur nowhere else. I read the 30:

- **Dead today, small and certain (about 25 lines):** `scan_view.roi_ids_in`, `DatasetModule.is_exporting`, `background_tab.is_background_applied`, `ribbon_fields.style_ribbon_check`, `task_indicator.running_task_ids`, the property `needs_attention` in `roi/toolbox.py`, and five unused constant tuples (`WINDOW_MODES`, `METRIC_RANGE_MODES`, `SENSORGRAM_DISPLAY_MODES` in `analysis/settings.py`, `ALIGNS` in `plan/bar_model.py`, `FIELD_NAMES` in `roi_table/rows.py`).
- **Unused machinery left behind, a decision rather than a deletion:** the per-cube **Auto reference cache** of `ReferenceFrameModule` (`_auto_wavelengths`, `set_auto_wavelength`, `auto_wavelengths`, `discard_auto_wavelength`, the Auto branch of `resolve`). Since 2026-10-11 the Auto "Find reference" result is applied as a fixed manual reference (`reference_auto_task.py`), so nothing fills this cache in the app; only `test_lspri_rewrite_find_reference_and_cube_display.py` does. Checked: this is **not** a stale-data bug (the cache is always empty in the app), just dead machinery with a test that keeps it alive.
- **Intentional headroom, keep:** `reduction.weighted_median` (fractional pixel weighting is built but not wired).
- **False positives, keep:** `cursor_overlay._proxy` and `_scene_view` (references that keep pyqtgraph objects alive), `roi_table/view.drawBranches` and `sensorgram/device_item.viewRangeChanged` (Qt / pyqtgraph overrides), unread label attributes in `background_tab.py`, `frame_tools._stacked`, `view_tab._mirrors`, `panel._drag_roi_id` (harmless leftovers).
- The 59 "only tests use it" are mostly test hooks (button visibility, arrow buttons, `is_open`, `point_count`...). A few are intentional: `trace_fit.trace_from_input_per_cube` (the reference path the fast path is compared with), `template_diagnostics` ("on request only"), `roi_tuning_run.estimate_cost` ("a run-time estimate is still missing"). Not examined one by one.

**Run 2: only the 356 modules the rewrite reaches** (so that uses by the old app no longer hide anything). 302 findings, **82 of them newly visible**: code that stays alive today only because old-only code calls it. After removing Qt overrides and dataclass fields, **42 unused functions, methods and classes, 1,478 lines**:

| File | Items | Lines |
|---|---|---|
| `storage/workspace.py` (roi table and processing profile save / load, old formats) | 7 | 498 |
| `dataset/io.py` (`compare_ome_zarr_summaries`, `dataset_load_plane`, `dataset_load_plane_roi`, `dataset_plane_shape`, `describe_new_ome_zarr_export`, unused import `_tifffile_imwrite`) | 5 | 138 |
| `roi/detection.py` `detect_rois` + `processing/roi_detection.py` `detect_rois` + `RoiToolbox.detect_rois` | 3 | 316 |
| `processing/preprocess.py` (`create_figure_mask`, two patch helpers duplicated in `image_tools/geometry/transform.py`) | 3 | 103 |
| `panels/histogram/compute.py` `estimate_roi_intensity_range` | 1 | 82 |
| `image_tools/geometry/transform.py`, `analysis/reduction.py`, `domain/models.py` (`AnalysisState`, `FormulaSpectrumResult`), `image_tools/chromatic/affine.py`, `roi/rasterize.py`, `domain/exclusions.py`, others | 23 | about 340 |

Many of these are still called by tests (for example `detect_rois` by 24 mentions, `load_processing_profile` by 15); those tests go or are ported in the same step. This list is the work list of cut-over phase 2f. 17 more findings were Qt overrides and dataclass fields read through serialisation (not dead).

**Limits:** vulture matches by name, so a name that is reused hides dead code (the 124 in run 1) and a call made through a string is invisible. Treat every line above as "look at it", not "delete it".
