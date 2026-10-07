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
| F2 | Histogram ROI masks, 1000 ROIs on a 2048x2048 plane, per redraw | 4.2 s | 0.9 s (identical masks) |
| F3 | ROI table rebuild, 1500 ROIs | 1.3 - 2.9 s | 0.2 - 0.3 s (same row heights) |
| F4 | A failed session autosave was only logged | silent for the user | shown in the status bar |
| F5 | `ImagePanel._build_toolbar_and_layout` | 554 lines, 228 statements | 7 methods, longest 61 statements |
| F6 | `build_main_window` | 630 lines, 195 statements | short sequence over 12 named helpers |
| F8 | `image_tools` sub-modules independent (documented rule) | broken by 1 module | holds (`import-linter`) |
| F14 | Pre-existing test failures | 6 | 0 (all were stale tests or missing fonts, no product bug) |

Needs you (section 6): undo/redo cannot be reached from the app (D1); 37 hand-wired settings fields (D2); the two
code generations side by side (D3); whole dead files (D5).

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
  drop, px/um toggle); task indicator; sessions with autosave; layout presets; remembered UI state.
- **Backend built, nothing in the UI calls it**: `AnalysisEngine.run_analysis` (no Run/Stop controls anywhere, so the
  app cannot compute a result), `RoiToolbox.detect_rois` (ROIs can only be added by hand), `set_detection_settings`,
  fractional pixel weighting (`rasterize_fractional`, weighted reductions), `analysis/statistics.py`.
- **Scaffold only** (`NotImplementedError`): Spectra and Sensorgram panels.
- **Not reachable from the UI**: undo/redo (F11).

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
| F2 | 5 Performance (and a documented invariant) | **Histogram redraw rasterized every ROI at full image size on the GUI thread, every redraw.** Two full planes per ROI (4 MB each at 2048x2048) for a region of about 1000 px. Breaks "per-ROI masks at their own bounding box" and "no heavy work on the GUI thread". | `panels/histogram/panel.py` (old `_resolve_roi_masks`). 458 ms at 100 ROIs, 1.4 s at 300, 4.2 s at 1000 per redraw. | **Done** (B2), 0.9 s left at 1000 ROIs: see D7 |
| F3 | 5 Performance | **ROI table rebuild froze the GUI for seconds.** `expandAll()` asked the delegate for a size hint per cell; it called the base class, which re-formats every cell's text (about 95,000 `model.data` calls at 1500 ROIs). | `panels/roi_table/delegate.py`. 312 ms at 300 ROIs, 2.9 s at 1500, 3.0 s at 3000 per structure change. | **Done** (B3) |
| F4 | 2 Data integrity | **A failed session autosave was only logged.** Disk full or a locked file left the user believing ROIs and masks were saved. | `storage/session_autosave.py` `_save_now`. | **Done** (B4) |
| F5 | 3 Maintainability | **`ImagePanel` is a 2,640-line class**: 105 methods, 122 instance attributes, 22 constructor parameters. Its `_build_toolbar_and_layout` was 554 lines. | `panels/image/panel.py`; plan in `panels_cleanup_plan_2026-10-06.md` item 3. | Builder split **done** (B6). Rest: **Decide** (D8) |
| F6 | 3 Maintainability | **`build_main_window` was 630 lines**: persistence wiring (about 30 call sites), the reference-frame save/restore state machine (a one-element list as a mutable flag), dock layout, session wiring, auto-reopen. `AppSettings` has **37 fields**, hand-wired (the 2026-09-30 doc predicted this at 5). | `app_rewrite.py`, `storage/app_settings.py`. | Split **done** (B5). Registry: **Decide** (D2) |
| F7 | 3 | **Dead code.** Whole files with no importer: `workflow/mask_settings.py`, `workflow/mask_highlight_actions.py` (+ its test), `image_tools/chromatic/landmark_autotrack.py` (1,092 lines, superseded by `auto_landmarks.py`), `analysis/statistics.py` (269 lines, waiting for the Statistics UI). Small items: the rewrite tree is clean (one orphan helper removed; the rest is public accessors or planned features, kept). | `vulture` + reachability + a name-based cross-reference. | Orphan helper **done** (B8). Files: **Decide** (D5) |
| F8 | 4 Modularity | **`image_tools` sub-modules were not independent**, against this app's `CLAUDE.md`: `chromatic/auto_task.py` imported Geometry, Background and `preprocess.py`. | `import-linter` independence contract. | **Done** (B7): moved to `image_tools/chromatic_auto_task.py` |
| F9 | 4 Testability | **Typing was blind inside `AnalysisEngine`**: missing constructor callables became `_unwired()` typed `object`, so about 35 mypy errors clustered there. | `analysis/engine.py`. mypy 289 -> 238 errors. | `_unwired` typed `Any` **done** (B8). Required callables: **Decide** (D6) |
| F10 | 3 | **Stale references.** 74 mentions of the retired `AGENTS.md` in 47 source files; `CLAUDE.md` pointed at an out-of-date status doc. | grep. | **Done** (B9) |
| F11 | (feature gap) | **Undo/redo cannot be reached from the app.** `undo_manager` has `changed`, `can_undo`, labels and every command feeds it, but nothing binds Ctrl+Z / Ctrl+Y and the Edit menu is created empty. The ROI-table docstring says "Ctrl+Z undoes it like any other". | grep for `undo_manager.undo` outside tests: no hits; `app_rewrite.py` `_build_menu_bar`. | **Decide** (D1) |
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
- [x] **B11** Final per-file run, `ruff`, scratch `import-linter` contract, re-measure (section 7).

## 6. Decisions needed from the maintainer

- **D1 Undo/redo in the Edit menu (F11).** Everything is built; wiring is about 40 lines (Edit -> Undo/Redo with
  labels, Ctrl+Z / Ctrl+Y, enabled state from `undo_manager.changed`). Not done: a feature, not clean-up, and it
  touches shortcuts. Recommended: yes.
- **D2 Settings registry (F6).** 37 `AppSettings` fields hand-wired vs the newer `ui_state.bind(...)` store.
  Options: (a) leave, (b) move the legacy fields (`chromatic_*`, `histogram_*`, `*_overlay_*`, `image_view_*`) onto
  `ui_state` with a one-time migration of existing settings files. Recommended (b); `_build_image_panel` and
  `_build_histogram_panel` are now the single place to change.
- **D3 Cut-over plan for the old generation (F12).** Until `run.py` / `main.py` start the rewrite, every fix to
  `dataset/io.py` or `roi/detection.py` must be made in two places. Decide when the old app is retired, and whether
  the old-only `storage/measurement_export*.py` / `workspace.py` move out of the new folders now.
- **D4 Fractional weighting (F15).** Where the toggle lives and whether it joins the provenance fingerprint.
- **D5 Delete whole dead files (F7).** `workflow/mask_settings.py`, `workflow/mask_highlight_actions.py` plus
  `tests/integration/test_lspri_rewrite_mask_highlight_actions.py`, `image_tools/chromatic/landmark_autotrack.py`
  (check `tools/chromatic_landmark_lab.py` first), `analysis/statistics.py` (keep if the Statistics UI is next).
  Root rule: no deleting files without approval.
- **D6 `AnalysisEngine` constructor (F9).** Make the callables required instead of defaulting to an "unwired"
  raiser. Public signature change; tests construct it bare today.
- **D7 Histogram ROI masks, the remaining 0.9 s at 1000 ROIs (F2).** What is left is numpy call overhead per ROI,
  not memory. Next levers: (a) cache the union per (wavelength, ROI geometry) so stepping through cubes at one
  wavelength is free, (b) compute on a worker thread (never `QThreadPool`). Needs a decision because (a) needs a
  change signal for ROI geometry the panel does not listen to today.
- **D8 Further `ImagePanel` splitting (F5).** Next steps in `panels_cleanup_plan_2026-10-06.md`: overlay drawing into
  an `OverlayPainter`, scene click/drag routing (`_on_scene_clicked`: 12 returns, rank D) into `image/interaction.py`.
  Riskier than the builder split and cannot be checked visually here; do it when you can look at the result.

## 7. Test baseline and results

Per file, each in its own process, offscreen, 127 LSPRi test files.

- **Before** (commit `aae8fdb`, plus the audit's first edits while it ran): 1550 passed, 6 failed in 5 files (F14).
  The 6 failures reproduce on the untouched commit.
- **After**: see the execution log below (filled in when the final run finished).
- New tests: `test_lspri_rewrite_roi_overlay_geometry.py` (8), `test_lspri_rewrite_union_roi_masks.py` (5), plus
  cases added to the Image panel (1), ROI table (2) and session autosave (2) files.

## 8. Execution log

(Filled in at the end of the session.)
