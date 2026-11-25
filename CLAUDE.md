# CLAUDE.md - LSPRimaging Evaluation

Loaded when working under `apps/LSPRi/eva/`. This directory is its own git repo (submodule): commit here first (root Submodule Workflow).

This file plus the `lspri-*` rule files below are the authoritative LSPRi rule set. Root rules still apply, except where stated. `docs/rewrite_rules_and_decisions_2026-09.md` is the design record (the reasoning), not a second rule set.

## Topic rules (load by themselves)

Detailed rules live in the umbrella repo's `.claude/rules/` and load automatically when you touch matching files. Read one by hand if your change affects its topic from elsewhere.

| File | Topic | Loads for |
|---|---|---|
| `lspri-scan-roi.md` | scan, cell hash, ROI shapes, rims, sample / reference, tuning, analysis rules, pixel-index vs view coordinates | `analysis/`, `roi/`, `domain/`, `tools/` |
| `lspri-export-parallel.md` | worker counts, export, TIFF decoder | `io/`, `dataset/`, `plane_reader.py`, scan, export |
| `lspri-mask.md` | Mask Scan, Auto mask, Feature map, mask undo | `image_tools/`, mask panels |
| `lspri-image-panel.md` | selection tools, rails, layers, Esc, canvas drawing | `panels/image/`, `selection/area*` |
| `lspri-roi-table.md` | table speed, frozen columns, step columns | `panels/roi_table/`, `selection/` |
| `lspri-plots-plan.md` | Spectra, Sensorgram, fits, spectral correction, plan, RI, plot looks | `panels/sensorgram/`, `panels/spectra/`, `plan/`, `analysis/` |
| `lspri-sessions-undo-state.md` | sessions, autosave, undo, remembered UI state | `storage/`, `undo/`, `app_rewrite.py` |
| `lspri-panels-wiring.md` | task indicator, launch, hidden-panel gate, shortcuts, menu icons, logging details | `panels/`, `app_rewrite.py`, `shortcuts.py`, `applog/` |

## Read first

- `docs/README.md`: index of all docs by topic.
- `docs/whole_app_audit_2026-10-29.md`: latest audit (dead code, cut-over inventory, doc drift, open decisions). Start here for clean-up questions.
- `docs/rewrite_status_by_feature_2026-10-29.md`: full per-feature record (what was built, when, numbers, open points). **Search by feature name; never read whole.**
- `docs/spectra_analysis_methods.md`: the maths from scan to metric. Read before changing it.
- `docs/rewrite_build_log_2026-09.md` is 436 KB. **Search only, never read in full.**
- History only: `docs/rewrite_status_and_plan_2026-09-30.md` (stale), `docs/old_generation_cutover_plan_2026-10-29.md` (done except phase 3: renaming shared `io/`, `processing/`, `gui/`).

## Status and scope

LSPRi was rebuilt on the `rewrite` branch and replaced the original app on 2026-10-29 (tag `legacy-final`). The rewrite exception to the root "do not rewrite the architecture" rule ended with the cut-over: further large restructuring needs a new approval. Scope is LSPRi Evaluation only: no hardware control, no shared infrastructure for a future acquisition app. Structure: `docs/rewrite_architecture_sketch_2026-09.md` (read before any structural decision not covered here).

- **Built**: Dataset (File menu + Dataset Manager), Image panel (ribbon tabs Image tools, Mask, ROIs, Chromatic, Background, Analysis, Tuning, Export; selection toolbar and View rails), Histogram, ROI / Group table, Spectra, Sensorgram, task indicator, sessions with autosave, layout presets, remembered UI state, Undo / Redo, Preferences, F1 shortcuts. Per-feature docs: the topic rule files and `docs/README.md`.
- **Not built yet**: editable experiment step manager; cube / ROI scope for Run and the background toggle in Analysis; command line; pump-plan overlay; faster Gaussian-fit sensorgram. ROI tuning *Tune toggle / pixel cache / preview* is **shelved**: no build without a new go-ahead.
- Much of the work since 2026-10-29 is **unseen by eye** (the full record says which).
- **Old names in comments** ("ported from `gui/x.py`", "`develop`/`main`", "the stable app") mean the original app: `git show legacy-final:src/lspr_imaging_app/gui/x.py`. Removed rewrite files (`analysis/engine.py`, `store.py`, `planner.py`) are before commit `a6411fd`. Leave history notes; fix a comment that says a removed file *is* the current owner.

## Where the app starts

`src/main.py` (used by `run.py` and the launcher) shows the launch screen and calls `app_rewrite.main` (console script `lspri-evaluation` calls it directly); `app_rewrite.build_main_window` wires everything. `gui/`, `io/`, `processing/` still hold a few shared modules under old names. Check reachability with `python tools/import_reachability.py`.

## Non-negotiable invariants

From real incidents (`docs/rewrite_feature_inventory_2026-09.md` section 3). Breaking one will likely reproduce a costly bug.

- **Never let a `QThreadPool` worker touch, even indirectly via a blocking wait, an OME-Zarr read** (native `STATUS_HEAP_CORRUPTION`). Use plain `threading.Thread` for anything dataset/zarr-adjacent. Only exception: a single-thread `QThreadPool` for ordered HDF5-only writes.
- **Never run a curve fit or other nonlinear computation on the GUI thread**, including "live previews". Display code reads computed values, never computes. A long Python-heavy fit loop runs in a **process**, not a thread (GIL starvation froze the GUI).
- **Never pool pixels across ROIs before computing sample/reference ratios.** Every ROI's reduction stays independent through the whole pipeline.
- **Always average already-fitted per-ROI values, never average raw spectra and fit once.**
- **Cache per-ROI analysis masks at the ROI's own bounding box**, not full-plane size (full-size measured 8-14 GB).
- **Masks are authored and stored in raw pixel space**, forward-transformed at each use via Chromatic's `warp_mask()`, never the reverse.
- **ROI coordinates are in processed image space** (after rotation, flip, crop); say which space you are in. Stored positions are pixel-index, the canvas is view space (0.5 px apart; rules in `lspri-scan-roi.md`). Details: `docs/image_tools_coordinate_spaces.md`.
- **Rotation resamples with Lanczos4** (bilinear moved ROI ratios ~720 ppm); only the Play preview uses bilinear. `docs/rotated_zarr_export_interpolation_2026-10-20.md`.
- Rotation, flip, crop are **not display-only**: they resample the pixel grid every calculation reads. Deliberate.
- **Bump `SCAN_ALGORITHM_VERSION` when a change can alter a stored number**; keep "same cell hash => same numbers".
- **A list of ROIs that goes into a calculation is made with `RoiToolbox.sample_ids(ids)`** (reference ROIs have no row).

## Module boundaries

- `dataset/`: load and convert; acquisition metadata.
- `image_tools/{geometry,mask,chromatic,background}/`: independent. Only Chromatic's `affine_for()` and `warp_mask()` are called from outside.
- `roi/`: the ROI Toolbox, one command API behind several front doors. Follow `docs/roi_shapes_and_linking_plan_2026-11-18.md`; don't re-derive an ROI model.
- `analysis/`: scan (`scan*.py`, `frame_reduce.py`), display queries (`query.py`, `scan_view.py`, `statistics.py`), workers, reduction math, reference `tasks.compute_cell`. Computes only on explicit Scan / Fast scan.
- `selection/`: the one intentionally shared state (current cube, wavelength, ROI). Not a license to add more.
- `panels/`: display only. Read query interfaces, turn gestures into commands, own no domain state.

**Rule:** no module reads or writes another module's internal state; coordinate through a documented method or a signal.

**Import layers (`lint-imports`, CI):** `app_rewrite` > `panels` > `selection` > `dataset` > `io` > `storage` > `analysis` > `roi` > `image_tools` > `plan` > small helpers; import only downward. If the check fails, move the thing down or pass it in; no exception without asking. `docs/import_layering_2026-11-02.md`.

**Size budgets:** `panels/image/panel.py`, `roi/toolbox.py`, `app_rewrite.py`, `panels/roi_table/panel.py` are gated by `tests/unit/test_lspri_panel_size_budget.py`. Raise a budget only on purpose, with the reason in the commit; new behaviour goes in its own file with a small builder.

## Communication and heavy work

- Each module is a `QObject` with typed `pyqtSignal`s. No string-keyed event bus.
- Every change is `CosmeticChange` (redraw only, never recompute) or `ComputationalChange` (value may be wrong; cell hashes mark stored numbers stale, nothing recomputes by itself).
- Public mutating methods use `@instrumented` (timing + DEBUG log).
- **Heavy work (> ~1 s on realistic data) reports progress and can be cancelled**: a pure function (no Qt) taking `progress: Callable[[float, str], None] | None` and `cancelled: Callable[[], bool] | None`, checking cancellation at every progress call and raising `Cancelled`; progress per unit of work (image, step, ROI), stages weighted by measured times (reference: `tools/chromatic_landmark_lab.py` `StageProgress`); its owning module re-emits a typed signal and joins the `TaskIndicator`; panels only display.
- **Logging:** `logging.getLogger(__name__)`; for analysable events `log_event(logger, "domain.action", level, **fields)` / `with timed(...)`; field names carry units (`load_ms`). ERROR = user should know, WARNING = recovered, INFO = milestones, DEBUG = timings aggregated per unit of work. Never log per pixel or per ROI. Slow code gets stage timing when written.

## Tests

- One pytest process per test file. Offscreen Qt on Windows has no fonts: a test measuring text width calls `tests/_qt_fonts.py` `load_system_fonts()`.
- **A UI change updates its tests in the same piece of work.** Tests live in the umbrella `tests/`: grep it for the widget or attribute you changed (stale tests once kept CI red for three days).
- Pure-computation modules stay Qt-free (contract `lspri-science-no-qt` in `.importlinter`).

## What NOT to do without checking in first

- Add back the materiality or pixel-selection recompute optimization (bring a before/after measurement instead).
- Switch the analysis store back to per-settings-version files.
- Run analysis on ROI selection, navigation, or anything but an explicit Scan.
- Put ROI or group business logic in a panel or view.
- Use `QThreadPool` for anything touching the dataset/zarr layer.
- Design shared infrastructure for a future acquisition app.
- Make a new session copy a prior session's settings or ROIs (new sessions start blank; only exception: the OME-Zarr export's Session carry, `storage/session_carry.py`). No duplicate / clone command.
- Build session rename, duplicate, delete, or an importer for the former app's `analysis/roi_table.json`, `measurement_backup.h5`, `processing_profile.json`.
- Make loading or clearing masks undoable.

## References

`docs/rewrite_architecture_sketch_2026-09.md`; `docs/rewrite_feature_inventory_2026-09.md`; `docs/rewrite_rules_and_decisions_2026-09.md` (design record); `docs/analysis_provenance_store_design_2026-09.md`; incidents `docs/qthreadpool_zarr_crash_investigation.md`, `docs/analysis_caching_architecture.md`; `undo/manager.py` docstring (undo design).
