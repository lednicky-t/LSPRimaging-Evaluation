# CLAUDE.md - LSPRimaging Evaluation

Loaded when working under `apps/LSPRi/eva/`. This directory is its own git repo (submodule): commit here first (root Submodule Workflow).

This file is the authoritative LSPRi rule set. Root rules still apply, except where this file states an exception. `docs/rewrite_rules_and_decisions_2026-09.md` is the design record (the reasoning), not a second rule set.

## Read first

- `docs/whole_app_audit_2026-10-07.md`: where the rewrite stands (section 2b), known issues, and the decisions still open. Supersedes the 2026-09-30 status doc below, which is out of date.
- `docs/rewrite_status_and_plan_2026-09-30.md`: the original stage-by-stage plan (history; its "what is built" claims are stale).
- `docs/rewrite_build_log_2026-09.md` is 436 KB. Search it by date or keyword. **Never read it in full.**

## Status and scope: the rewrite exception

LSPRi is being rebuilt from scratch on the `rewrite` branch. This is an approved exception to the root "do not rewrite the architecture" rule. Scope is LSPRi Evaluation only: no hardware control, and no shared infrastructure for a future acquisition app. Evidence: `docs/rewrite_feature_inventory_2026-09.md`. Structure and event design: `docs/rewrite_architecture_sketch_2026-09.md`. Read it before any structural decision this file does not cover.

### Where the rewrite stands (2026-10-07; detail and open decisions: `docs/whole_app_audit_2026-10-07.md`)

- **Built, with UI**: Dataset (Workflow panel); Image panel (ribbon tabs View, Image tools, Mask, ROIs, Chromatic, Background, Export (OME-Zarr, moved from the Workflow panel 2026-10-20; "Rotate/crop" exports the processed image as uint16 + `valid_mask`, values rounded onto the camera step: `docs/rotated_zarr_export_interpolation_2026-10-20.md`); ROI overlays, area selection, cursor readout, scale bar); Histogram panel; ROI / Group table; task indicator; sessions with autosave; layout presets; remembered UI state; Edit -> Undo / Redo; Edit -> Preferences (2026-10-19, replaced the Options menu; app-wide settings only, panel settings stay in their panel; scope, what is planned and what is deliberately not exposed: `docs/preferences_dialog_2026-10-19.md`).
- **Session carried with the export (2026-10-22)**: the Export tab's *Session* button (on by default) moves ROIs, masks, chromatic correction and mask pointers into the exported pixels' coordinates (a shift by the frame origin; masks resampled once with the app's own warp; nothing refitted or recalculated) and saves them as a session of the exported dataset; the app then asks whether to open it. Pure part `storage/session_carry.py`, task `storage/session_carry_task.py`; record: `docs/session_carry_export_2026-10-22.md`. The fast libtiff TIFF decoder (`dataset/io.py`, verified against tifffile at run time, `LSPR_TIFF_DECODER=tifffile` switches it off) is now **on for every thread**, not only the render thread (2026-10-22; 1.7x on one thread, 3.8x on four, `docs/export_speed_2026-10-22.md`). The export itself got faster the same day (libtiff decode and a fused rotate-and-round in the workers, byte-identical output) and reports its time and rate in the status bar and in the question box after a carried export: `docs/export_speed_2026-10-22.md`.
- **Mask-tab Scan and Auto mask (2026-10-17)**: *Scan* finds dark, sharp objects that crossed the field (particles, hairs, bubbles) and marks those frames with pink pointers on both Mask rows; `MaskPointersModule` (code name since 2026-10-22; the detector and its `TransientEvent` keep "transient") holds the list (cosmetic, never a mask, saved in the session, schema 1.4). *Auto mask* is the separate step that turns the standing particle pointers into individual masks (contour grown by N px, merged with the mask in effect, one undo step; `mask_auto_task.py`, on a worker thread, also from a pointer's right-click menu). Only what sits on the image border and goes nowhere is an `artefact` (never masked); a particle that arrives and rests is a particle at rest and is masked (plan doc section 12, with its known limit). The PNG group also has *Export all masks* (one named PNG per mask + `masks_index.csv`). Read `docs/auto_transient_scan_and_mask_plan_2026-10-16.md` (sections 9-11), `docs/auto_transient_mask_exploration_2026-10-16.md` and the audit `docs/transient_scan_automask_slider_audit_2026-10-17.md` (lifecycle rules: a scan or an Auto mask run belongs to the dataset, session and mask it started with); command line: `tools/scan_transients.py`.
- **Backend built, no UI calls it yet**: `AnalysisEngine.run_analysis` (so the app cannot compute a result), `RoiToolbox.detect_rois` (ROIs are added by hand), fractional pixel weighting, `analysis/statistics.py`.
- **Scaffold only** (`NotImplementedError`): Spectra and Sensorgram panels. Both need analysis results, so the Run controls come first.
- **Tests**: run one pytest process per test file (all files in one process stalls); offscreen Qt on Windows has no fonts, so a test that measures text width calls `tests/_qt_fonts.py` `load_system_fonts()`.

## Which generation to edit

**GUI work targets the new generation:** `src/lspr_imaging_app/panels/`, `roi/`, `analysis/`, `undo/`, `selection/`, `storage/session*`, started from `src/main_rewrite.py` (`app_rewrite.py`). Edit the old `gui/` package only if the maintainer asks.

Open question: `run.py` and `src/main.py` still start the **old** app. Changes to the new panels will not show when you run `run.py`. Whether that is intended is not yet confirmed; ask before changing the launcher.

## Non-negotiable invariants

These come from real incidents. Violating any of them will likely reproduce a bug that already cost investigation time. Source: `docs/rewrite_feature_inventory_2026-09.md` section 3.

- **Never let a `QThreadPool` worker touch, even indirectly via a blocking wait, an OME-Zarr read.** Root-caused to a native `STATUS_HEAP_CORRUPTION` crash. Use plain `threading.Thread` (or a small pool built on it) for anything dataset/zarr-adjacent. The one safe exception: a single-thread `QThreadPool` for HDF5-only writes that need strict ordering.
- **Never run a curve fit, or any other nonlinear computation, on the GUI thread.** This includes anything framed as a "live preview". Display code reads already-computed values; it never computes.
- **Never pool pixels across ROIs before computing sample/reference ratios.** Every ROI's reduction stays independent through the whole pipeline.
- **Always average already-fitted per-ROI values, never average raw spectra and fit once.** Nonlinear fits don't commute with averaging.
- **Cache per-ROI analysis masks at that ROI's own small bounding box, not full-image-plane size.** Full-size caching measured 8-14 GB RAM at realistic ROI counts.
- **Masks are authored and stored in raw pixel space**, forward-transformed into processed/wavelength space at each use site via Chromatic's `warp_mask()`, never the other way around.
- **Rotation resamples with Lanczos4** (2026-10-20; bilinear smoothed the pixel noise by ~28 % and moved ROI ratios by ~720 ppm). Only the slideshow preview uses bilinear while Play runs (`fast_rotation`); the frame is redrawn exactly when Play stops. Measurement and cost audit: `docs/rotated_zarr_export_interpolation_2026-10-20.md`.
- Rotation, flip, and crop are **not display-only**. When applied, they resample the actual pixel grid every downstream calculation reads. This is deliberate (detection quality over spatial-transform purity), not an oversight.

## Module boundaries

- `dataset/`: dataset load and convert; acquisition metadata.
- `image_tools/{geometry,mask,chromatic,background}/`: independent modules. Only Chromatic's `affine_for()` and `warp_mask()` are called from outside.
- `roi/`: the ROI Toolbox, one backend with several front doors (Workflow sub-tabs, Image panel, ROI/Group table) that call the same command API. Follow `docs/roi_system_roadmap.md`; don't re-derive an ROI model.
- `analysis/`: store, recompute planner, background workers, reduction math (`reduction.py`). Computes only on an explicit `run_analysis(scope)`.
- `selection/`: the one intentionally shared state (current cube, wavelength, ROI). Not a license to add more.
- `panels/`: display only. Read query interfaces; turn gestures into command calls; own no domain state.

**Rule:** no module reads or writes another module's internal state. Coordinate through a documented interface method or a signal.

## Communication

- Each module is a `QObject` with typed `pyqtSignal`s. No string-keyed event bus.
- Every change is one of two types. `CosmeticChange`: redraw only (color, label, grouping, display mode); never triggers recompute. `ComputationalChange`: the value may now be wrong (geometry, mask, chromatic, background, reduction method); the recompute planner reacts to it.
- Public mutating methods use the shared `@instrumented` helper (timing and DEBUG logging to the session log).
- **Heavy work reports progress and can be cancelled.** Anything that may take more than ~1 s on realistic data (reading cubes, detection, tracking, fitting, analysis, export) must:
  - be a pure function (no Qt) taking `progress: Callable[[float, str], None] | None` (overall fraction 0..1 plus a short message) and `cancelled: Callable[[], bool] | None`; check cancellation at every progress call and stop with a `Cancelled` exception;
  - report per unit of work (image, step, ROI), never per pixel, and split the 0..1 range into stages weighted by *measured* stage times (reference implementation: `tools/chromatic_landmark_lab.py`, `StageProgress`);
  - be wrapped by its owning module, which re-emits progress as a typed signal; panels only display it (progress bar or busy indicator, plus a Cancel button) and never compute.
  - App-wide indicator: `panels/task_indicator.py` `TaskIndicator`, a permanent status-bar row (spinner, progress bar, start time, elapsed/ETA, Cancel; keeps the last result until dismissed; History popup; every finished run is logged). A task joins it by emitting `task_progress(task_id, label, fraction, message)` (fraction < 0 = unknown) and `task_finished(task_id, outcome, message)` (always, once per run; outcome `completed`/`failed`/`cancelled`), then three connects in `app_rewrite.py` (report, finish, cancel). Reference: `ChromaticAutoDetect`. `analysis_progress(float)` and `export_progress(int, str)` are older ad-hoc shapes; migrate them when touched.
- Each display panel has its own ~100 ms redraw-coalescing timer. Reacting to every event immediately is not the same as reacting promptly.
- **A display panel does no work while it is hidden** (closed dock, behind a tab, window minimised): it keeps the data it is handed (cheap), skips computing and drawing, and catches up once when shown again. Use `panels/visibility_gate.py` `HiddenPanelGate` (`defer()` at every redraw entry, `set_panel_visible()` connected to the dock's `visibilityChanged` in `app_rewrite.py`). Done for the Histogram, the ROI table and the Log panel; **every new display panel (Spectra, Sensorgram, ...) must do the same.** Measured 2026-10-14: a Histogram redraw is ~30 ms of GUI time per 1.2 MP frame.
- **Heavy numpy work of a display panel does not run on the GUI thread** (2026-10-14). Pattern: gather inputs on the GUI thread, compute on a worker, draw on the GUI thread. Histogram: `compute.redraw_counts` on an `AnalysisWorker` (newest request wins; `_on_counts_ready` draws; a result from before a clear, or that arrives while hidden, is dropped). Image panel: the value range and, for a frame with NaN pixels, the ready-made 8-bit picture come from the render thread (`panels/image/display_picture.py`, byte-identical to pyqtgraph's own and pinned by `tests/integration/test_lspri_display_picture.py`). Frames, masks and regions handed to a worker are shared, not copied: never modify them in place. A control that needs the *redrawn* result (a Y refit) must wait for the draw, not assume `_redraw()` finished.
- A handler on `cube_changed` / `wavelength_changed` / `image_rendered` runs on every slideshow step: it must be O(1) in the dataset size (no scans over all records or timings; use `DatasetModule` queries, which are indexed). Two of these cost 25 ms and 18 ms per step on a 314-cube dataset before they were found (docs build log, 2026-10-14).

## Logging and diagnostics

Design record: `docs/logging_design_2026-10-14.md`. Package `applog/` (Qt-free), viewer `panels/log/` (Help -> Log Console, Ctrl+Shift+L).

- Use stdlib `logging.getLogger(__name__)`; everything is captured (ring buffer for the panel, JSON Lines file in `logs/`, console only at WARNING unless `LSPR_CONSOLE_LOG` is set).
- For anything you may later want to *analyse*, use the structured helpers: `log_event(logger, "domain.action", level, **fields)` and `with timed(logger, "domain.action", **fields) as t: ...; t.set(n=...)` (nested blocks record parent ids). Event names `domain.action`; field names carry units (`load_ms`, `wl`, `mb`).
- Levels: ERROR = failed, user should know; WARNING = recovered; INFO = user-level milestones (dataset loaded, task finished, play summary); DEBUG = per-call timings, aggregated per outer unit of work. Never log per pixel or per ROI; never pass an expensive call as a log argument.
- Heavy or slow-feeling code gets stage timing when it is written (one aggregated event per frame / cube / run), not after a complaint. `@instrumented` commands are timed automatically.
- Read a saved log without the app: `python -m lspr_imaging_app.applog summary|grep FILE.jsonl`.
- `configure_logging()` runs only in `main()`; tests never call it (and must not rely on log files).

## Remembering UI state

Every user-changeable control is remembered across restarts unless the maintainer says otherwise (stated 2026-10-05). For a checkbox, checkable button, spin box or combo box, add one line: `ui_state.bind("area/name", widget)` (`panels/ui_state.py`; the store is passed in as an optional `ui_state` argument, or `restore_ui_state(store)` on a panel). For state outside a widget use `store.get`/`store.set`. A value wired by hand (not a widget `bind`) is one row in `storage/ui_state_keys.py` (key, default, type) plus its wiring line in `app_rewrite.py`: never a new `AppSettings` field. `AppSettings` holds only app-level state (last dataset, theme, window/dock layout, open Workflow section) and `ui_state` itself; a settings file from before 2026-10-07 is migrated on load. Values live in `AppSettings.ui_state`, written after a short pause. State that only means something for one dataset (highlight range, manual reference frame, fixed display range) is saved together with the dataset folder and restored only for that dataset. Armed canvas tools and the area-selection mode are deliberately not restored (see `docs/rewrite_build_log_2026-09.md`, 2026-10-05).

## Analysis rules

- `run_analysis` runs **only** on explicit user action (scope: all ROIs, or the current selection). Selecting ROIs or changing panels must never trigger computation; display reports "not yet analyzed".
- The recompute rule is deliberately simple: any change to a cell's inputs recomputes that cell. No materiality or pixel-selection comparison.
- Formula, Fit method, and Metric settings are **never** stored or put in provenance. They are recomputed live from RAM.
- One ongoing HDF5 file per session; each cell (ROI by cube) has its own provenance record.
- **Fractional pixel weighting (sketch section 6a) is partly built.** The raster (`roi/rasterize.py`, `rasterize_fractional`) and weighted reductions (`analysis/reduction.py`) exist and match unweighted results when weights are equal. **Not wired yet:** `analysis/tasks.py` `compute_cell` still calls the binary `rasterize_sample`/`rasterize_reference`.

## Undo/redo

- One shared `undo.undo_manager`. Import it directly, as modules import `diagnostics_hub`.
- Each mutating command pushes one `undo.FunctionCommand` (label plus `undo()`/`redo()` closures over its own module's state). Not a deep clone of module state.
- `begin_batch(label)` / `end_batch()` merge a burst (one mouse drag) into one entry.
- **Selection is never undoable.**
- **Reachable from the UI** (2026-10-07): Edit -> Undo / Redo, Ctrl+Z and Ctrl+Y / Ctrl+Shift+Z, application-wide (`app_rewrite.py` `_wire_edit_menu`). Refused while an analysis runs (undoing a ROI delete or reorder renumbers ids and the engine would wait for the run on the GUI thread); `UndoManager` does nothing while a gesture batch is open.
- Wired: `RoiToolbox`, `GeometryModule`, `ChromaticModule`, `BackgroundModule`.
- **`MaskModule` is wired only for the Mask rows' commands** (delete / make individual / make persistent a mask, maintainer OK 2026-10-16) **and the Auto mask batch** (`set_mask_changes`: all the masks made from the scan's pointers are one undo step, maintainer OK 2026-10-17): painting, loading and clearing masks are still not undoable, so each of these commands carries a version token and its undo is skipped, with a message, if the mask changed after it (`image_tools/mask/module.py` docstring). Masks are stored compressed (`MaskChange`, a few KB each; `.mask` is read-only), which is what makes a batch of hundreds cheap to undo. Check in before wiring anything more.

## Sessions

A **dataset** is raw images plus acquisition metadata, never written to. A **session** is one independently reproducible working copy of everything derived from it (ROI table, masks, settings, `analysis/data.h5`). `storage/session_coordinator.py` knows the active session; `app_rewrite.py` wires it to the modules. Full history: the design record's Sessions section.

**Autosave never writes on the GUI thread** (2026-10-17, maintainer rule: saving has no business there). Two steps: the GUI thread reads the modules and `prepare_session` copies them into plain data (several module queries return *live* mutable objects, so this must not move to the worker); one plain `threading.Thread` runs `SessionWriter.write` (PNGs + JSON; it remembers masks already on disk, so an unchanged save is ~20 ms). `flush()` waits until the data is on disk; `set_root()` does not, so code that reads a session folder back calls `autosave.wait_until_written(root)` first. A new field in the session must go through `prepare_session`, never be read from a live module by the writer. Record: `docs/session_autosave_blocking_analysis_2026-10-17.md`.

## Testing rules

- Pure-computation files must import and test with **zero Qt, GUI, or dataset dependency**: `analysis/tasks.py`, `analysis/provenance.py`, `analysis/planner.py`, `analysis/reduction.py`, `roi/rasterize.py`, `roi/detection.py`. A test of one of these that needs a `QApplication` means something is wired wrong.
- `plan_recompute()` needs a deterministic unit test per locality rule (ROI-local geometry, neighbor-exclusion adjacency, mask-local, chromatic or background global, reduction-method global, cosmetic or range changes that touch nothing).
- A weighted reduction must match its unweighted counterpart exactly when all weights are equal.
- Root numerical-tolerance and simulated-hardware rules apply.

## Pitfalls

- **ROI coordinates are in processed image space** (after rotation, flip, crop). Mixing spaces gives silently wrong results; say which space you are in. Masks are the exception (raw space). Details: `docs/image_tools_coordinate_spaces.md`.
- **`image_tools_enabled` must not be persisted as off.** It is turned off while the crop/rotate tool is active so the full image shows. If persisted, crops silently fail to re-apply on reload.
- **Fixed-width Qt text fields:** measure the rendered margin, not `.text()` or `.width()` alone. `field.width() - QFontMetrics(font).horizontalAdvance(worst_case_text)` near zero means clipping risk; add a few explicit pixels. Size once for worst-case content. Example: `panels/histogram/highlight_range_controls.py`. History: build log 2026-09-29/30.

## What NOT to do without checking in again first

- Don't add back the materiality or pixel-selection recompute optimization. If it becomes a measured problem, bring a proposal with a before/after measurement.
- Don't switch the analysis store back to per-settings-version files.
- Don't run analysis on ROI selection, panel navigation, or anything other than an explicit `run_analysis`.
- Don't put ROI or group business logic in a panel or view. Panels call the Toolbox command API and hold no copy of ROI or group state.
- Don't use `QThreadPool` for anything touching the dataset/zarr layer, even indirectly.
- Don't design shared infrastructure for a future acquisition app.
- Don't make a new session copy a prior session's settings or ROIs. New sessions start blank (maintainer decision, 2026-09-26). **One approved exception (2026-10-22):** the OME-Zarr export's Session button carries the session into the exported dataset (`storage/session_carry.py`, `docs/session_carry_export_2026-10-22.md`): a user-started derivation, written once into the exported dataset's own `sessions/` folder, with `derived_from.json` recording the source. There is still no duplicate / clone command.
- Don't build session rename, duplicate, or delete, or an importer for the stable app's pre-existing `analysis/roi_table.json`, `measurement_backup.h5`, or `processing_profile.json`.
- Don't wire more of `MaskModule` to undo than the Mask rows' commands and the Auto mask batch without checking in (painting needs stroke batching).

## References

- `docs/rewrite_status_and_plan_2026-09-30.md` (read first); `docs/rewrite_architecture_sketch_2026-09.md`; `docs/rewrite_feature_inventory_2026-09.md`.
- `docs/rewrite_build_log_2026-09.md` (search only); `docs/rewrite_rules_and_decisions_2026-09.md` (design record).
- `docs/roi_system_roadmap.md`; `docs/analysis_provenance_store_design_2026-09.md`; incident writeups `docs/qthreadpool_zarr_crash_investigation.md`, `docs/analysis_caching_architecture.md`.
- `undo/manager.py`: undo design (its module docstring has the full reasoning).
