# LSPRi Evaluation rewrite: whole-app audit and fix plan (2026-10-07)

Scope: the rewrite tree (`src/lspr_imaging_app/{analysis,dataset,diagnostics,image_tools,panels,roi,selection,storage,undo}`,
`app_rewrite.py`; about 37,800 lines). The old `gui/`, `io/`, `processing/`, `domain/` generation was read only to
understand coupling; it is not edited. Focus asked for: clean-up, optimization, correct code maintenance, architecture.

Nothing here was verified by driving the GUI. Every number is a headless measurement (pure functions, or an
offscreen Qt model/panel timed with no screenshots and no clicking). Everything fixed is committed on the
`rewrite` branch of this repo (local commits, nothing pushed); the matching tests live in the umbrella repo.

## 1. Result in one screen

Fixed in this session (details in sections 3 and 8):

| | What | Before | After |
|-|------|--------|-------|
| F1 | ROI overlay applied the chromatic affine twice (circle drawn off the measured region) | 1.94 px off for a 1.2 / -0.8 px shift | exact (equals the measured circle) |
| F1 | ROI overlay drawing, 2000 ROIs, per redraw | 531 ms | 33 ms |
| F2 | Histogram redraw, 1000 ROIs on a 2048x2048 plane (ROI masks were rebuilt on the GUI thread on every redraw, incl. every ROI selection click) | 4.4 s | 0.15 s: masks cached, built on a background thread when there are many ROIs |
| F3 | ROI table rebuild, 1500 ROIs | 1.3 - 2.9 s | 0.2 - 0.3 s (same row heights) |
| F4 | A failed session autosave was only logged | silent for the user | shown in the status bar |
| F5 | `ImagePanel._build_toolbar_and_layout` | 554 lines, 228 statements | 7 methods, longest 61 statements |
| F6 | `build_main_window` | 630 lines, 195 statements | short sequence over 12 named helpers |
| F6 | `AppSettings` hand-wired fields | 37 | 11 (the 26 per-panel ones are rows of one key table in `ui_state`; old settings files migrate on load) |
| F9 | `mypy` on `analysis/` | about 35 errors | none (12 files); `AnalysisEngine` takes its eleven reads as required arguments |
| F5 | `ImagePanel` | 2,640 lines | 2,357 lines: click/drag routing in `image/interaction.py`, ROI overlay in `image/roi_overlay.py` |
| F11 | Undo / redo | built, but unreachable (Edit menu empty) | Edit -> Undo / Redo, Ctrl+Z and Ctrl+Y / Ctrl+Shift+Z |
| F8 | `image_tools` sub-modules independent (documented rule) | broken by 1 module | holds (`import-linter`) |
| F14 | Pre-existing test failures | 6 | 0 (all were stale tests or missing fonts, no product bug) |

Needs you (section 6): the two code generations side by side (D3), fractional weighting (D4), whole dead files
(D5). D1 (undo), D2 (settings), D6 (engine constructor), D7 (histogram masks) and the first steps of D8 (`ImagePanel`)
were decided on 2026-10-07 and are done.

## 2. How it was audited

- `ruff` (default rules clean; extended rules for size, complexity, error handling), `vulture` (dead code),
  `radon cc/mi` (complexity), `import-linter` with a scratch contract file (layering), `mypy` (types). The maintainer
  approved all four on 2026-10-07. Raw outputs are scratch files, not committed.
- A reachability script (which modules the rewrite entry point reaches, which only the old one reaches).
- An AST scan for access to another object's private attributes.
- Reading `app_rewrite.py`, the autosave, the analysis engine constructor, and the Image / Histogram / ROI-table paths.
- A micro-benchmark for every performance claim; the old and new code compared on the same inputs.
- Test baseline: every LSPRi test file in its own process (a single-process run of all of them stalled at about
  27%, consistent with the known multi-file crash, `lspri_rewrite_image_panel_test_crash_2026_10_02`; not chased).

## 2b. Where the rewrite stands today (2026-10-07, checked against the code)

The 2026-09-30 status doc is out of date. Now:

- **Built, with real UI**: Dataset (folder row, summary, experimental plan, export); Image panel (ribbon tabs View,
  Image tools = crop/rotate/flip/measure, Mask, ROIs, Chromatic, Background; ROI overlays in their own colours,
  labels, area selection, cursor readout, scale bar); Histogram panel; ROI / Group table (grouped tree, drag and
  drop, px/um toggle); task indicator; sessions with autosave; layout presets; remembered UI state; Edit -> Undo / Redo (from 2026-10-07).
- **Backend built, nothing in the UI calls it**: `AnalysisEngine.run_analysis` (no Run/Stop controls anywhere, so the
  app cannot compute a result), `RoiToolbox.detect_rois` (ROIs can only be added by hand), `set_detection_settings`,
  fractional pixel weighting (`rasterize_fractional`, weighted reductions), `analysis/statistics.py`.
- **Scaffold only** (`NotImplementedError`): Spectra and Sensorgram panels.

## 3. What is already good (keep it)

- **Layering holds.** Domain modules never import `panels`; pure-computation files import no Qt; panels never import
  `h5py`/`zarr` (`import-linter`: 4 of 5 contracts kept; the fifth is D3).
- **Encapsulation is unusually clean.** Only 3 places in 37,800 lines reach into another object's private attribute.
- **Threading invariants are respected.** No `QThreadPool` near the dataset/zarr layer; no curve fit on the GUI thread.
- `ruff` default rules: zero findings. No `print`, no debugger calls, 2 `TODO` in the whole tree.
- Error handling is mostly deliberate fallbacks with a comment.

## 4. Findings

Priority = which item of the engineering priority order (root `CLAUDE.md`) the fix serves.
"Done" = fixed and committed; "Decide" = needs the maintainer.

| ID | Priority | Finding | Evidence | Status |
|----|----------|---------|----------|--------|
| F1 | 1 Correctness | **ROI overlay applied the chromatic affine twice.** `RoiToolbox.display_position()` already returns the transformed centre; `transformed_circle_points()` then transformed the circle, centre included, again. At a non-reference wavelength the drawn circle, reference rings and selection ring sat off the measured region by about the affine's translation; click hit-testing used the correct centre, so the drawn and the clickable circle disagreed. The old app passed the *source* centre. Tests used only the identity affine. | `panels/image/panel.py` (old lines 1798-1815). Example affine (1.2, -0.8) px shift: 1.94 px offset on a 20 px ROI. | **Done** (B1) |
| F2 | 5 Performance (and a documented invariant) | **Histogram redraw rasterized every ROI at full image size on the GUI thread, every redraw.** Two full planes per ROI (4 MB each at 2048x2048) for a region of about 1000 px. Breaks "per-ROI masks at their own bounding box" and "no heavy work on the GUI thread". | `panels/histogram/panel.py` (old `_resolve_roi_masks`). 458 ms at 100 ROIs, 1.4 s at 300, 4.2 s at 1000 per redraw. | **Done** (B2: reach boxes; B12: cache + background thread) |
| F3 | 5 Performance | **ROI table rebuild froze the GUI for seconds.** `expandAll()` asked the delegate for a size hint per cell; it called the base class, which re-formats every cell's text (about 95,000 `model.data` calls at 1500 ROIs). | `panels/roi_table/delegate.py`. 312 ms at 300 ROIs, 2.9 s at 1500, 3.0 s at 3000 per structure change. | **Done** (B3) |
| F4 | 2 Data integrity | **A failed session autosave was only logged.** Disk full or a locked file left the user believing ROIs and masks were saved. | `storage/session_autosave.py` `_save_now`. | **Done** (B4) |
| F5 | 3 Maintainability | **`ImagePanel` is a 2,640-line class**: 105 methods, 122 instance attributes, 22 constructor parameters. Its `_build_toolbar_and_layout` was 554 lines. | `panels/image/panel.py`; plan in `panels_cleanup_plan_2026-10-06.md` item 3. | Builder split **done** (B6); interaction router (B16) and ROI overlay (B17) **done**. Optional rest: D8 |
| F6 | 3 Maintainability | **`build_main_window` was 630 lines**: persistence wiring (about 30 call sites), the reference-frame save/restore state machine (a one-element list as a mutable flag), dock layout, session wiring, auto-reopen. `AppSettings` has **37 fields**, hand-wired (the 2026-09-30 doc predicted this at 5). | `app_rewrite.py`, `storage/app_settings.py`. | Split **done** (B5). Registry **done** (B14) |
| F7 | 3 | **Dead code.** Whole files with no importer: `workflow/mask_settings.py`, `workflow/mask_highlight_actions.py` (+ its test), `image_tools/chromatic/landmark_autotrack.py` (1,092 lines, superseded by `auto_landmarks.py`), `analysis/statistics.py` (269 lines, waiting for the Statistics UI). Small items: the rewrite tree is clean (one orphan helper removed; the rest is public accessors or planned features, kept). | `vulture` + reachability + a name-based cross-reference. | Orphan helper **done** (B8). Files: **Decide** (D5) |
| F8 | 4 Modularity | **`image_tools` sub-modules were not independent**, against this app's `CLAUDE.md`: `chromatic/auto_task.py` imported Geometry, Background and `preprocess.py`. | `import-linter` independence contract. | **Done** (B7): moved to `image_tools/chromatic_auto_task.py` |
| F9 | 4 Testability | **Typing was blind inside `AnalysisEngine`**: missing constructor callables became `_unwired()` typed `object`, so about 35 mypy errors clustered there. | `analysis/engine.py`. mypy 289 -> 238 errors. | `_unwired` typed `Any` (B8); required callables, `ProvenanceStore` as a Protocol, `mypy analysis/` clean (B15) **done** |
| F10 | 3 | **Stale references.** 74 mentions of the retired `AGENTS.md` in 47 source files; `CLAUDE.md` pointed at an out-of-date status doc. | grep. | **Done** (B9) |
| F11 | (feature gap) | **Undo/redo cannot be reached from the app.** `undo_manager` has `changed`, `can_undo`, labels and every command feeds it, but nothing binds Ctrl+Z / Ctrl+Y and the Edit menu is created empty. The ROI-table docstring says "Ctrl+Z undoes it like any other". | grep for `undo_manager.undo` outside tests: no hits; `app_rewrite.py` `_build_menu_bar`. | **Done** (B13) |
| F12 | 3 | **Two generations side by side.** `dataset/io.py` (1,942 lines) is a verbatim copy of `io/dataset.py`; `roi/detection.py` differs from `processing/roi_detection.py` by about 70 lines; `storage/measurement_export*.py` (1,907 lines) and most of `storage/workspace.py` (1,061 lines) are reachable only from the old app but sit in the new folders; the rewrite still imports old `domain.models`, `io.*`, `processing.roi_math`, `gui.app_theme`. Fixes must be made twice. | `import-linter` "rewrite does not import the old generation": broken in 9 places. | **Decide** (D3) |
| F13 | 2 | Two OME-Zarr fast-read fallbacks returned `None` silently (a decode failure quietly turned the fast path off). | `dataset/io.py`. | **Done** (B8): debug log lines |
| F14 | 4 Testability | **Six tests failed before the audit** (verified on the untouched commit `aae8fdb`). None was a product bug: `test_deactivating_drops_a_pending_point` and `test_general_group_sits_left_of_the_ribbon_with_cursor_and_picker` still assumed UI that was changed on purpose (View is the first tab; General is the ribbon's pinned column); `test_checker_is_opaque_...` still expected one decimal (readout is whole numbers since 2026-10-03); `test_size_fields_have_a_compact_fixed_width` and both `width_budget` tests measured text widths with the Windows-offscreen stand-in font (0 families, much wider glyphs), which is why the Dataset section "measured" 367 px against a 320 px budget. `tests/_qt_fonts.py` exists for exactly this and only one test used it. | per-file baseline, section 7. | **Done** (B10) |
| F15 | 6 | Fractional pixel weighting is still not wired into `compute_cell`; `weighted_median` has no test and no caller. | `analysis/tasks.py:90`. | **Decide** (D4), unchanged since 2026-09-30 |
| F16 | note | `main()` sets `logging.basicConfig(level=DEBUG)`: every library logs at DEBUG in the preview app. | `app_rewrite.py` `main`. | Fine for a dev preview |
| F17 | note | Three hand-written "plain thread, report to Qt, cancel" implementations (`AnalysisWorker`, `ChromaticAutoDetect`, `ImageRenderer`). Each has its own semantics; a fourth copy should reuse one helper. | | none |
| F18 | note | **Docstrings carry history**: 26% of lines are docstrings (6% `#` comments), many with dated narrative. The reasoning is valuable but belongs in the build log; a reader meets dozens of lines before the first statement. | line count. | none, not touched |

Measured and **no action needed**: session save is 16 / 52 / 145 ms at 200 / 1000 / 3000 ROIs on the GUI thread
(once per 2.5 s pause), under the 1 s rule, so moving it off-thread is not justified (the autosave docstring asked for
exactly this number). About 80 `union-attr` and 25 `override` mypy errors are PyQt6-stub noise
(`docs/code_health_tools.md`); not chased.

## 5. Plan and what was done

Commits are local to the `rewrite` branch of `apps/LSPRi/eva`; nothing is pushed.

- [x] **B1 (F1)** `roi/overlay_geometry.py` (Qt-free): outline = display centre + circle bent by the affine's linear
      part, vectorized. `RoiToolbox.display_positions()` for all centres at once; `ImagePanel._draw_roi_overlay` and
      `roi_at` use them. Tests: identity affine equals the old output exactly; a non-identity affine puts the circle
      on the measured mask; a nudge wins for its wavelength only; a panel-level test (the drawn circle is where a
      click selects it). *Commit `e945a56`.*
- [x] **B2 (F2)** `roi/rasterize.py` `union_roi_masks()`: each ROI is rasterized inside its own reach box
      (mask-geometry ROIs keep the old path); `transformed_annulus_mask` shares the box helper. Tests: exact boolean
      equality with the old per-ROI OR (circles, rings, own rings, "none", masks, corner-clipped, outside, random,
      identity and chromatic affine). *Commit `e945a56`.*
- [x] **B3 (F3)** `RoiTableDelegate.sizeHint` answers for column 0 only. Test: row heights for ROI and group rows
      unchanged, other columns return no height. *Commit `e945a56`.*
- [x] **B4 (F4)** `SessionAutosave.save_failed(str)` shown in the status bar for 30 s. Tests: a failed write reports,
      a successful one does not. *Commit `3d3a468`.*
- [x] **B5 (F6)** `build_main_window` split into `_Modules`/`_build_modules`, `_SettingsWriter`,
      `_ReferenceFramePersistence`, `_wire_highlight_range_persistence`, `_build_image_panel`/`_histogram_panel`/
      `_workflow_panel`, `_build_status_bar`, `_build_dock_layout`, `_restore_window_state`,
      `_wire_quit_persistence`, `_auto_reopen_last_dataset`. Signal connection order kept: the last-dataset write
      must precede the highlight-range restore, and the persistence object connects through closures because PyQt holds
      a bound method of a plain object only weakly. *Commit `153b25e`.*
- [x] **B6 (F5)** `ImagePanel._build_toolbar_and_layout` split by ribbon tab. *Commit `a24d5c5`.*
- [x] **B7 (F8)** `auto_task.py` moved to `image_tools/chromatic_auto_task.py`. *Commit `8bbea4c`.*
- [x] **B8 (F7, F9, F13)** `@instrumented` on the five `restore_*` methods (the convention is every public mutating
      method); `_unwired` typed `Any`; debug lines in the zarr fast-path fallbacks; the orphan
      `AreaSelectionTool._clamped` removed. *Commit `8bbea4c`.*
- [x] **B9 (F10)** `AGENTS.md` -> `CLAUDE.md` in 47 files, checked at AST level that only docstrings and comments
      changed; `apps/LSPRi/eva/CLAUDE.md` "Read first" updated; build-log entry. *Commit `8baa8ee`, docs commit.*
- [x] **B10 (F14)** Two stale tests updated, one stale expectation fixed, real fonts loaded in the two text-width
      test files. *Umbrella commit.*
- [x] **B12 (F2, was D7)** `panels/histogram/roi_masks.py` `RoiMaskProvider`: the union is cached under
      (ROI generation, shape, affine, ring defaults); up to 40 ROIs are built inline, more on a plain
      `threading.Thread` from a snapshot of the ROIs (they are edited in place on the GUI thread), latest request wins,
      cancellable (`union_roi_masks(cancelled=...)`); `HistogramPanel` invalidates on `RoiToolbox.geometry_changed` and
      draws the ROI curves when `ready` fires. Why a cache matters: the Image panel re-renders on every ROI selection
      click and each render redraws the Histogram. Measured at 1000 ROIs, 2048x2048: `_redraw` about 150 ms cached or
      not (was about 4.4 s). Not wired to the task indicator: not a user-started task. *Commit `ff07e92`.*
- [x] **B13 (F11, was D1)** `_wire_edit_menu`: Edit -> Undo / Redo with labels from the stack ("Undo Move ROI"),
      application-wide shortcuts (so also from floating panels), a status-bar note of what was undone. Guards: refused
      while an analysis runs (undoing a ROI delete/reorder renumbers ids and the engine would wait for the run on the
      GUI thread), and `UndoManager` does nothing while a gesture batch is open (batch begin/end/cancel now emit
      `changed`). A focused text field keeps its own Ctrl+Z. *Commit `273f10c`.*
- [x] **B14 (F6, was D2)** 26 `AppSettings` fields (Histogram display options, Image view range / ribbon tab /
      overlays / background view, Chromatic tab values, highlight range) moved into `ui_state`:
      `storage/ui_state_keys.py` is the one table (key, default, type, retired field), read with type validation.
      `AppSettings` keeps 11 app-level fields. A settings file from the previous build is migrated on load (schema
      1.0 -> 1.1, additive: each old field is copied unless its new key is already set; composite values only when
      complete; old fields vanish on the next save). Verified on a copy of the real settings file (26 legacy fields,
      all migrated, original untouched). Panels are unchanged. Note: once the app has saved a 1.1 file, an older build of
      the rewrite reading it falls back to defaults for those 26 values (it ignores unknown keys). *Commit `0afb510`.*
- [x] **B15 (F9, was D6)** The eleven module reads of `AnalysisEngine` are required constructor arguments
      (`chromatic_affine_between` stays the one optional read); `_unwired` is gone. The one test that built a bare engine
      uses `_build_analysis_engine`; a new test pins that every read is required. `ProvenanceStore` (a documented-only
      class with a raising method) became a `typing.Protocol` so the planner accepts the real store, and
      `next_version` takes a `Mapping`. `mypy lspr_imaging_app/analysis`: no issues. *Commit `843d9c4`.*
- [x] **B16 (F5, was D8 step 2)** `image/interaction.py` `CanvasInteraction`: scene click/move handling, the
      per-tool right-click menus, the left-drag dispatch (crop, measure, select-area) and arrow/Esc keys move out of
      `ImagePanel` as they were (first a pure move, then `on_scene_clicked` - 72 lines, complexity rank D - became a
      dispatch table with one small handler per tool). `ImagePanel` keeps thin delegates under the old names. *Commits
      `bba9546`, `377e563`.*
- [x] **B17 (F5, was D8 step 3)** `image/roi_overlay.py` `RoiOverlay` owns the pyqtgraph items (sample curves per
      colour, reference rings, selection highlight, labels) and the display style; the panel decides when to draw and
      still wires the ribbon controls and persistence. The label item is attached in a second step so the stacking order
      of overlay items is unchanged. A dangling call to a removed method (`_clear_roi_curves`) slipped past `ruff` and was
      caught by four tests; an AST scan for undefined `self.<attr>` reads now comes back empty. *Commit `35278f4`.*
- [x] **B18 (was D2 follow-up)** Every hand-wired UI-state key is named once, in `storage/ui_state_keys.py`
      (`HAND_WIRED_KEYS` plus the composite names for the reference frame, ROI table sort/collapsed/widths); a widget's own
      `bind(...)` key stays next to the widget. A unit test fails if a panel or `app_rewrite.py` spells a key as a
      literal in `.get()` / `.set()`. *Commit `710d673`.*
- [x] **B19 (docs)** `apps/LSPRi/eva/CLAUDE.md` has a short "where the rewrite stands" section (what is built, what has a
      backend but no UI, what is scaffold, how to run the tests). *Commit `843d9c4`.*
- [x] **B11** Final per-file run, `ruff`, scratch `import-linter` contract, re-measure (section 7).

## 6. Decisions needed from the maintainer

- ~~**D1 Undo/redo in the Edit menu (F11).**~~ Decided and done (B13).
- ~~**D2 Settings registry (F6).**~~ Decided and done (B14).
- **D3 Cut-over plan for the old generation (F12).** Until `run.py` / `main.py` start the rewrite, every fix to
  `dataset/io.py` or `roi/detection.py` must be made in two places. Decide when the old app is retired, and whether
  the old-only `storage/measurement_export*.py` / `workspace.py` move out of the new folders now.
- **D4 Fractional weighting (F15).** Where the toggle lives and whether it joins the provenance fingerprint.
- **D5 Delete whole dead files (F7).** `workflow/mask_settings.py`, `workflow/mask_highlight_actions.py` plus
  `tests/integration/test_lspri_rewrite_mask_highlight_actions.py`, `image_tools/chromatic/landmark_autotrack.py`
  (check `tools/chromatic_landmark_lab.py` first), `analysis/statistics.py` (keep if the Statistics UI is next).
  Root rule: no deleting files without approval.
- ~~**D6 `AnalysisEngine` constructor (F9).**~~ Decided and done (B15).
- ~~**D7 Histogram ROI masks.**~~ Decided and done (B12).
- **D8 Further `ImagePanel` splitting (F5), optional.** Steps 2 and 3 of `panels_cleanup_plan_2026-10-06.md` item 3
  are done (B16, B17). What is left, in order of value: the mask / histogram-highlight tint overlays and the chunk
  grid (`_update_mask_overlay`, `_update_highlight_overlay`, `_update_chunk_grid`), `refresh_theme` (85 lines), the
  Cube / wavelength navigation (`_build_navigation_bar` and its handlers). Each is a pure move with the same tests;
  none is urgent. They cannot be checked visually from here, so do them when you can look at the result.

## 7. Test baseline and results

Per file, each in its own process, offscreen.

- **Before** (commit `aae8fdb`, 127 files): 1550 passed, 6 failed in 5 files (F14). The 6 failures reproduce on the
  untouched commit.
- **After** (132 files, final run on `fc15ca3`): **1615 passed, 0 failed, 0 errors**. That is the 1550 that passed, the 6
  that were fixed, and 59 new tests: overlay geometry (8), union masks (5), edit menu / undo (8), histogram ROI mask
  provider (9), UI-state key table and migration (21), plus cases in the Image panel, histogram panel, ROI table,
  autosave and analysis engine files (8).
- Tools after: `ruff` clean; `import-linter` scratch contract 4 of 5 kept (was 3 of 5; the fifth is D3); functions over
  50 statements 24 -> 23 (the two largest, 228 and 195, are gone; the biggest left is the verbatim `dataset/io.py`
  export copy); `radon` D-or-worse functions 11 -> 10; `mypy` on the rewrite tree 289 -> 239 errors, with `analysis/` and
  every module added in this audit clean (the rest is PyQt6 stub noise and the old-generation ports); `radon mi`:
  `roi/toolbox.py` slipped from A to B (two small additions), `panels/image/panel.py` is still C (2,640 -> 2,357 lines).

## 8. Execution log

| Batch | Commit (apps/LSPRi/eva, `rewrite`) | Verified by |
|-------|-----------------------------------|-------------|
| B1-B3 overlay, histogram masks, ROI table | `e945a56` | overlay and union unit tests, ROI table (81), Image + Histogram panel (126) |
| B4 autosave failure | `3d3a468` | session autosave (17) |
| B7-B8 move, typing, logging | `8bbea4c` | analysis engine, area selection, chromatic tab/auto, session, visual settings restore |
| B5 `build_main_window` split | `153b25e` | ui_state, visual settings restore, 3 workflow panel files, autosave |
| B6 `ImagePanel` builder split | `a24d5c5` | 12 Image-panel-related files (only the 3 old failures, then fixed in B10) |
| B9 `AGENTS.md` -> `CLAUDE.md` | `8baa8ee` | AST comparison: only docstrings/comments changed |
| Audit doc, CLAUDE.md pointer, build log | `1ba8fb6` | - |
| B12 histogram ROI masks | `ff07e92` | provider (9), histogram panel (39), repeated 8x for flakiness |
| B13 undo / redo | `273f10c` | edit menu (8), drag-undo, canvas tools bar, ROI renumber / commands, session |
| B14 settings -> `ui_state` | `0afb510` | key table + migration (21), app settings (7), visual settings restore (15), workflow panel files; real settings file migrated on a copy |
| B15, B19 engine constructor, CLAUDE.md | `843d9c4` | analysis engine (27), ROI table (81), `mypy analysis/` clean |
| B18 hand-wired key names | `710d673` | guard test, ui_state, ROI table, histogram, Image panel |
| B16 interaction router | `bba9546`, `377e563` | crop, rotate, measure, area selection, canvas tools bar, Image panel (268) |
| B17 ROI overlay | `35278f4` | Image panel, ROI overlay controls, histogram, rotate |
| typing of the new modules | `fc15ca3` | the same files again |
| B10 six old failing tests; all new tests | umbrella commit (tests are in the umbrella repo) | final per-file run above |
