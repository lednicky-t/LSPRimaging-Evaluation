# CLAUDE.md - LSPRimaging Evaluation

Loaded when working under `apps/LSPRi/eva/`. This directory is its own git repo (submodule): commit here first (root Submodule Workflow).

This file is the authoritative LSPRi rule set. Root rules still apply, except where this file states an exception. `docs/rewrite_rules_and_decisions_2026-09.md` is the design record (the reasoning), not a second rule set.

## Read first

- `docs/rewrite_status_and_plan_2026-09-30.md`: current status and plan.
- `docs/rewrite_build_log_2026-09.md` is 436 KB. Search it by date or keyword. **Never read it in full.**

## Status and scope: the rewrite exception

LSPRi is being rebuilt from scratch on the `rewrite` branch. This is an approved exception to the root "do not rewrite the architecture" rule. Scope is LSPRi Evaluation only: no hardware control, and no shared infrastructure for a future acquisition app. Evidence: `docs/rewrite_feature_inventory_2026-09.md`. Structure and event design: `docs/rewrite_architecture_sketch_2026-09.md`. Read it before any structural decision this file does not cover.

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
- Each display panel has its own ~100 ms redraw-coalescing timer. Reacting to every event immediately is not the same as reacting promptly.

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
- Wired: `RoiToolbox`, `GeometryModule`, `ChromaticModule`, `BackgroundModule`.
- **`MaskModule` is deliberately not wired.** The old app never undo-tracked masks either. Check in before wiring it. See `image_tools/mask/module.py`.

## Sessions

A **dataset** is raw images plus acquisition metadata, never written to. A **session** is one independently reproducible working copy of everything derived from it (ROI table, masks, settings, `analysis/data.h5`). `storage/session_coordinator.py` knows the active session; `app_rewrite.py` wires it to the modules. Full history: the design record's Sessions section.

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
- Don't make a new session copy a prior session's settings or ROIs. New sessions start blank (maintainer decision, 2026-09-26).
- Don't build session rename, duplicate, or delete, or an importer for the stable app's pre-existing `analysis/roi_table.json`, `measurement_backup.h5`, or `processing_profile.json`.
- Don't wire `MaskModule` to undo.

## References

- `docs/rewrite_status_and_plan_2026-09-30.md` (read first); `docs/rewrite_architecture_sketch_2026-09.md`; `docs/rewrite_feature_inventory_2026-09.md`.
- `docs/rewrite_build_log_2026-09.md` (search only); `docs/rewrite_rules_and_decisions_2026-09.md` (design record).
- `docs/roi_system_roadmap.md`; `docs/analysis_provenance_store_design_2026-09.md`; incident writeups `docs/qthreadpool_zarr_crash_investigation.md`, `docs/analysis_caching_architecture.md`.
- `undo/manager.py`: undo design (its module docstring has the full reasoning).
