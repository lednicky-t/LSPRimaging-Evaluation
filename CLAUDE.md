# CLAUDE.md - LSPRimaging Evaluation

Loaded when working under `apps/LSPRi/eva/`. This directory is its own git repo (submodule): commit here first (root Submodule Workflow).

This file is the authoritative LSPRi rule set. Root rules still apply, except where this file states an exception. `docs/rewrite_rules_and_decisions_2026-09.md` is the design record (the reasoning), not a second rule set.

## Read first

- `docs/whole_app_audit_2026-10-29.md`: the latest whole-app audit: dead code, what only the old generation still uses (the cut-over inventory), documentation drift, and the decisions still open. Start here for clean-up and cut-over questions.
- `docs/whole_app_audit_2026-10-07.md`: the earlier audit (where the rewrite stood, section 2b; known issues). Supersedes the 2026-09-30 status doc below, which is out of date. Its dead-file list (D5) is partly done: `mask_settings.py` and `mask_highlight_actions.py` are gone.
- `docs/README.md`: index of all docs by topic.
- `docs/old_generation_cutover_plan_2026-10-29.md`: the plan (not executed) for replacing the old app with the rewrite: what blocks it, the phases, the open questions. Read it before touching `run.py`, `src/main.py`, `app.py`, `gui/`, or the launcher cards.
- `docs/rewrite_status_by_feature_2026-10-29.md`: the full per-feature record that used to be the "Where the rewrite stands" section of this file (25 KB, moved verbatim). Search it by feature name; never read it whole.
- `docs/rewrite_status_and_plan_2026-09-30.md`: the original stage-by-stage plan (history; its "what is built" claims are stale).
- `docs/spectra_analysis_methods.md`: the maths from the stored scan to the metric (formula, fit window, fit, maximum / centroid), where each is in the code, and what the tests showed. Read it before changing any of those.
- `docs/rewrite_build_log_2026-09.md` is 436 KB. Search it by date or keyword. **Never read it in full.**

## Status and scope: the rewrite exception

LSPRi is being rebuilt from scratch on the `rewrite` branch. This is an approved exception to the root "do not rewrite the architecture" rule. Scope is LSPRi Evaluation only: no hardware control, and no shared infrastructure for a future acquisition app. Evidence: `docs/rewrite_feature_inventory_2026-09.md`. Structure and event design: `docs/rewrite_architecture_sketch_2026-09.md`. Read it before any structural decision this file does not cover.

### Where the rewrite stands (summary, kept current to 2026-10-29; open decisions: `docs/whole_app_audit_2026-10-29.md`)

One line per feature. The full per-feature record (what was built and when, measured numbers, design reasons, open points) was moved out of this file on 2026-10-29, verbatim, into `docs/rewrite_status_by_feature_2026-10-29.md`: search it by feature name, never read it whole. The rules that were inside those paragraphs are in "Rules that come from these features" below.

- **Built, with UI**: Dataset (Workflow panel); Image panel with ribbon tabs View, Image tools, Mask, ROIs, Chromatic, Background, Analysis, Tuning, Export; Histogram, ROI / Group table, Spectra and Sensorgram panels; task indicator; sessions with autosave; layout presets; remembered UI state; Edit -> Undo / Redo; Edit -> Preferences (app-wide settings only, panel settings stay in their panel: `docs/preferences_dialog_2026-10-19.md`).
- **Export** (OME-Zarr, Export tab): "Rotate/crop" writes uint16 + `valid_mask`, rounded onto the camera step (`docs/rotated_zarr_export_interpolation_2026-10-20.md`); the *Session* button (on by default) carries ROIs, masks, chromatic correction and mask pointers into the exported coordinates and offers to open the result (`storage/session_carry.py`, `session_carry_task.py`, `docs/session_carry_export_2026-10-22.md`); export speed and the libtiff decoder: `docs/export_speed_2026-10-22.md`.
- **Mask tab**: Scan + Auto mask + Export all masks (`docs/auto_transient_scan_and_mask_plan_2026-10-16.md` sections 9-12, audit `docs/transient_scan_automask_slider_audit_2026-10-17.md`, command line `tools/scan_transients.py`); edit preview and Threshold / Local contrast Add / Subtract (`docs/mask_edit_preview_2026-10-29.md`); Eraser (`docs/mask_eraser_2026-10-29.md`); ROI features tool (`docs/roi_feature_detection_2026-10-29.md`). The 2026-10-29 mask work was unseen by eye when written.
- **Analysis redesign** (`docs/analysis_redesign_plan_2026-10-19.md` sections 15-17 are current, audit `docs/analysis_step1_audit_2026-10-23.md`): step 1 turns pixels into mean / STD / counts per ROI, cube, wavelength and side (`analysis/frame_reduce.py`, `scan.py`, `scan_jobs.py`, `scan_arrays.py`, `scan_fingerprint.py`, `scan_store.py` = `sessions/<id>/analysis/scan.h5`, `scan_task.py`); started from the Analysis tab (Scan, Fast scan, Mode pick-up Normal / Performance). Display: `scan_results.py`, the Spectra panel (`panels/spectra/`, `docs/spectra_analysis_methods.md`) and the Sensorgram panel (`panels/sensorgram/`, fits in a helper process: `analysis/trace_fit.py`, `trace_pool.py`). Fit methods include the template shift (`analysis/template_shift.py`, `docs/shift_estimators_comparison_2026-10-25.md`); polynomial order menu with verdict dots (`analysis/order_quality.py`, `docs/poly_order_quality_study_2026-10-25.md`). Scan speed work: `docs/roi_subset_scan_bottlenecks_2026-10-28.md`, `docs/roi_nudge_partial_scan_proposal_2026-10-26.md`.
- **Experiment steps and plan bar** (`plan/`, Sensorgram "Experimental plan" tab; read `docs/experimental_plan_bar_design_2026-10-26.md` first): select / drag / retype / colour steps, add / remove / unify, baseline `B1` and signal `S1` roles; step statistics (`analysis/step_stats.py`, `plan/stats_table.py`, `docs/step_statistics_backbone_2026-10-26.md`); all the plan code of the panel lives in `panels/sensorgram/step_overlays.py`; record of the "ranges" decision: `docs/plan_steps_as_ranges_2026-10-29.md`.
- **ROI position tuning** (Tuning ribbon tab, `analysis/roi_tuning*.py`, `panels/image/tuning_tab.py`, `tuning_overlay.py`; plan `docs/roi_position_tuning_plan_2026-10-28.md`, results `docs/roi_position_tuning_2026-10-28.md`): lattice of shifted ROI centres scored on two plan steps; Accept moves the ROIs and copies the numbers into `scan.h5`. Built 2026-10-29, unseen by eye.
- **Old analysis path, built but driven by no UI** (checked 2026-10-29): `AnalysisEngine.run_analysis` with `analysis/tasks.py`, `store.py`, `planner.py` and `worker.py` (~1.9k lines). The scan redesign replaced it and it goes at the cut-over; `app_rewrite.py` still constructs the engine (and tells it about ROI renumbering). Also built but unused by the scan: fractional pixel weighting (`rasterize_fractional`, the weighted reductions in `analysis/reduction.py`; the scan's `frame_reduce.py` uses the binary raster), and `RoiToolbox.detect_rois` (takes ready-made detection results; only tests call it - ROIs come from the ROI tab, by hand or through its Array section: detect / refine / place). `analysis/statistics.py` is live in the Sensorgram.
- **Not built yet**: the Workflow panel's *Outputs* section (it shows "Outputs - not built yet."); cube / ROI scope for Run and the background toggle in the Analysis section; the command line (named in the record, not specified); the pump-plan overlay; a faster Gaussian-fit sensorgram. The ROI tuning *Tune toggle / pixel cache / preview* is designed but **shelved by the maintainer**: do not build it without a new go-ahead (`docs/single_roi_tuning_design_2026-10-28.md`). The Spectra and Sensorgram panels are built.
- **Tests**: run one pytest process per test file (all files in one process stalls); offscreen Qt on Windows has no fonts, so a test that measures text width calls `tests/_qt_fonts.py` `load_system_fonts()`.

#### Rules that come from these features

Extracted from the moved paragraphs; the record explains why. Each is a rule, not history.

**Parallel work and export**
- A fan-out job (export, scan, analysis step 1, ROI features) takes its worker count from `lspr_core.perf` (`get_runtime().begin(...)`); the default is **Standard** (every core but one). **A tuned job must write / find identical results at any worker count** (hash test: `tests/integration/test_lspri_export_workers.py`); never tune what is saved. A new fan-out follows `docs/performance_tuning.md` (repo root). Saved counts live in `perf_profile.json` (`perf_setup.py`, installed in `main()` only, so tests never touch it).
- The fast libtiff TIFF decoder (`dataset/io.py`) is on for every thread and verified against tifffile at run time; `LSPR_TIFF_DECODER=tifffile` switches it off.

**Mask Scan, Auto mask, ROI features**
- A scan or an Auto mask run belongs to the dataset, session and mask it started with (lifecycle rules in the audit `docs/transient_scan_automask_slider_audit_2026-10-17.md`).
- Mask pointers (`MaskPointersModule`) are cosmetic and never a mask. Auto mask turns the standing pointers into individual masks as **one undo step**. Only what sits on the image border and goes nowhere is an `artefact` (never masked); a particle that arrives and rests is masked.
- The ROI-features finder looks at the picture, never at the mask; its result is dropped when the ROIs, geometry, background or chromatic model change. Masking the features did not raise SNR on the bulk-step data (`docs/roi_feature_detection_2026-10-29.md`).

**Scan (analysis step 1) and what it stores**
- **Bump `SCAN_ALGORITHM_VERSION` when a change can alter a stored number.**
- A change to what a cell depends on must keep "same hash => same numbers" (pinned on the numbers by `tests/unit/test_lspri_scan_local_mask_keys.py`). A new thing that makes one ROI's pixels depend on another ROI must be added to `frame_reduce.local_roi_keys`, and so to `ring_reach_matrix` (shared with `frame_reduce.build_subset_layout`). The cell hash falls back to the whole layout / whole mask when background removal uses the ROIs or masks in its estimate (`masks_are_global`), when there is no mask resolver, and for free-form ROIs.
- Never remove the `stamped_by_previous_formula` recognition (`ScanStore.restamp`) without a migration: files from before would rescan every cube with a mask.
- The scan file's schema is `lspri_scan` 1.1 (it has the `n_masked` array the Sensorgram's mask marks read); an older scan file is replaced by a rescan.
- Finished cubes are written by `scan_store.ScanWriter` (a thread of its own), never by the thread that collects the workers' results (the pool is topped up only when that thread is back). The scan file holds plain (uncompressed) chunks; `write_batch` never writes a row outside its batch.
- `snapshot_source` runs on the GUI thread, everything else of a scan off-thread (`scan_jobs.py`). A partial read of an OME-Zarr export is bit-identical to a whole-plane read (`ScanSettings.partial_reads` switches it off). Known bottleneck, to be solved later (maintainer, 2026-10-27): the CRC32C check of every shard's index (cheaper ideas in the comment above `_shard_index` in `dataset/io.py`).
- Performance mode keeps a warm worker pool (~150 MB per idle worker); workers end by themselves when the app is gone (`scan._start_parent_watch`); a mode chosen during a scan applies after it.
- **Tools that build the app's modules (`tools/analysis_stage_timing.py` `build_modules`) must keep autosave suspended**: an edit made by a script was once saved into the real test session.

**Spectra and Sensorgram**
- **Never run a fit loop, or any long Python-heavy numpy work, in a thread next to the GUI: use a process** (it starved the GUI of the interpreter lock and froze the window: `docs/sensorgram_fit_freeze_2026-10-24.md`). The helper process falls back to the thread with a WARNING.
- Template shift: a ROI's cubes must never be split (its reference cubes are part of its trace); `fit_spectrum("template")` and a template-less `fit_and_metric` raise, so the method can never fall through to the polynomial; no baseline removal, by decision.
- Smoothing runs inside each plan step (`StatisticsSettings.smooth_within_steps`, default on, saved with the session), so a window never mixes two solutions.
- Step stats show mean ± SD of every line **as drawn**. Narrow steps get shorter text, never a smaller font: the size is the one in the size box for every selection.

**Experimental plan**
- The dataset's measured steps (`ImagingAcquisitionMetadata.plan`) are never written; the user's edits are an overlay in the session (`plan/`, session schema 1.5 / 1.6, block `"plan"`). An edit applies only to the step it was made on (fingerprint: number + measured start + measured note), else it is reported and left out.
- **A "range" is a step of the experimental plan; there is no separate ranges concept** (maintainer, 2026-10-29). A function that needs a stretch of the experiment takes a baseline step and a signal step from `ExperimentPlanModule` (`panels/step_picker.py` is the shared drop-down).
- pyqtgraph gives a right-click to the ViewBox first, so the plan bar asks first through `_MiddlePanViewBox.claim_right_click`; the bar's `shape()` needs the winding fill rule (an odd-even path makes the overlap at a shared boundary a hole).

**ROI position tuning**
- Accept copies the trial's numbers into `scan.h5` **bit-identical to a real scan of the moved ROIs** (pinned by a test): only when the trial's inputs are unchanged, only into a file that fits (`ScanStore.prepare(create=False)`), and not for ROIs next to another ROI or whose new centre is interpolated between lattice points. A trial scans the centre the toolbox stores (0.1 px rounding, `roi_tuning.moved_centre`).

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
  - Launch screen and stage timing (2026-10-24): `launch.py` (`LaunchSplash`, `StageClock`); `main_rewrite.py` shows the card before the heavy imports, so **`launch.py` may import only PyQt6 and `applog`**. `build_main_window` logs `app.build_stage` per step: add `stages.done(...)` after a new step that may be slow. Record: `docs/startup_gui_thread_audit_2026-10-24.md`.
  - Launch: when the last dataset is reopened, `startup_tracker.py` `StartupTracker` shows "Starting up: step N of 5: ..." in that same row until the dataset, session, first image, scan results and Spectra / Sensorgram are all settled (2026-10-24; steps in `app_rewrite._build_startup_tracker`). A new thing that loads at launch adds a `StartupStep` there.
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

*These bullets describe the old engine path (`AnalysisEngine`, one `analysis/data.h5` with per-cell provenance). The scan redesign (the "Analysis redesign" line above) replaced it; the scan's own rules are under "Rules that come from these features". They stay until the cut-over removes the engine. The principle holds for the scan too: nothing computes on selection or navigation, and a changed input makes a result stale, never silently wrong.*

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

**Loading a session never reads on the GUI thread either** (2026-10-22, maintainer's choice: open blank, fill in when ready). `SessionCoordinator.active_session_changed` -> `restore` resets the modules to blank at once and starts `SessionLoadTask` (`storage/session_load_task.py`; status-bar entry "Loading session: Loading masks n of N"); **autosave stays off for that root until the load has been applied**, and a failed or cancelled load leaves it off (a blank state must never be written over the saved one). So code that opens a dataset and then reads session state (ROIs, masks) must wait for the load: tests call `_settle()` in `tests/integration/test_lspri_rewrite_session_autosave.py`. A session with no file yet is still applied synchronously. The shared thread/signal/Cancel wrapper for tasks is `threaded_task.py` (`ThreadedTask`; scan, Auto mask, mask export and session load use it). Record: `docs/session_load_off_gui_thread_2026-10-22.md`.

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

- `docs/rewrite_status_and_plan_2026-09-30.md` (history: its "what is built" claims are stale, see "Read first"); `docs/rewrite_architecture_sketch_2026-09.md`; `docs/rewrite_feature_inventory_2026-09.md`.
- `docs/rewrite_build_log_2026-09.md` (search only); `docs/rewrite_rules_and_decisions_2026-09.md` (design record).
- `docs/roi_system_roadmap.md`; `docs/analysis_provenance_store_design_2026-09.md`; incident writeups `docs/qthreadpool_zarr_crash_investigation.md`, `docs/analysis_caching_architecture.md`.
- `undo/manager.py`: undo design (its module docstring has the full reasoning).
