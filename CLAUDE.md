# CLAUDE.md - LSPRimaging Evaluation

Own git repo (submodule, branch `rewrite`): commit here first. This file plus the topic rules below are the LSPRi rule set; root rules apply unless stated. Design reasoning: `docs/rewrite_rules_and_decisions_2026-09.md`.

## Topic rules

In the umbrella's `.claude/rules/`; each loads by itself when matching files are touched. Read one by hand when a change elsewhere affects its topic.

- `lspri-scan.md`: cell hash, stored numbers, sample / reference pairing, scan threads, analysis rules, ROI tuning
- `lspri-roi-model.md`: shapes, rims, links, pixel-index vs view coordinates
- `lspri-roi-table.md`: table speed, frozen / rim / Type columns, step columns
- `lspri-plots-plan.md`: Spectra, Sensorgram, spectral correction, plan, RI, plot looks
- `lspri-image-panel.md`: selection tools, rails, layers, Esc, canvas drawing
- `lspri-mask.md`: mask Scan, Auto mask, Feature map, mask undo
- `lspri-sessions-undo-state.md`: sessions, autosave, undo, remembered UI state
- `lspri-panels-wiring.md`: launch, task indicator, hidden-panel gate, shortcuts, menus, logging
- `lspri-export-parallel.md`: worker counts, export, TIFF decoder

## Read first

- `docs/README.md` (doc index); `docs/whole_app_audit_2026-10-29.md` (latest audit, open decisions).
- `docs/spectra_analysis_methods.md`: the maths from scan to metric. Read before changing it.
- **Search only, never read whole:** `docs/rewrite_status_by_feature_2026-10-29.md` (per-feature record), `docs/rewrite_build_log_2026-09.md` (436 KB).

## Status

The rewrite replaced the original app on 2026-10-29 (tag `legacy-final`); large restructuring now needs a new approval. Scope: LSPRi Evaluation only (no hardware control, no shared infrastructure for an acquisition app). Structure: `docs/rewrite_architecture_sketch_2026-09.md`.

- **Not built**: editable step manager; cube / ROI scope for Run and the background toggle; command line; pump-plan overlay; faster Gaussian-fit sensorgram. ROI tuning preview is **shelved**.
- Much work since 2026-10-29 is **unseen by eye**.
- "Ported from `gui/x.py`", "`develop`/`main`", "the stable app" in comments mean the original app (`git show legacy-final:src/lspr_imaging_app/gui/x.py`); removed rewrite files are before `a6411fd`. Leave history notes; fix comments naming a removed file as the current owner.
- Entry: `src/main.py` (launch screen) -> `app_rewrite.main`; `app_rewrite.build_main_window` wires everything. `gui/`, `io/`, `processing/` hold a few shared modules under old names. Reachability: `python tools/import_reachability.py`.

## Non-negotiable invariants

From real incidents (`docs/rewrite_feature_inventory_2026-09.md` §3).

- **No `QThreadPool` worker may touch an OME-Zarr read, even via a blocking wait** (heap corruption). Use `threading.Thread`. Only exception: a single-thread `QThreadPool` for ordered HDF5-only writes.
- **No curve fit or nonlinear computation on the GUI thread**, including "live previews"; display code only reads computed values. Long Python-heavy fit loops run in a **process**.
- **Never pool pixels across ROIs before sample/reference ratios**; each ROI's reduction stays independent.
- **Average fitted per-ROI values; never average raw spectra and fit once.**
- **Cache per-ROI masks at the ROI's own bounding box** (full-plane: 8-14 GB).
- **Masks are authored and stored in raw pixel space**, forward-warped at each use (`warp_mask()`).
- **ROI coordinates are processed image space** (after rotate / flip / crop); say which space you're in (`docs/image_tools_coordinate_spaces.md`). Stored = pixel-index, canvas = view, 0.5 px apart (`lspri-roi-model.md`).
- **Rotation resamples with Lanczos4** (bilinear moved ratios ~720 ppm); only Play preview uses bilinear (`docs/rotated_zarr_export_interpolation_2026-10-20.md`). Rotate / flip / crop resample the real pixel grid, deliberately.
- **Bump `SCAN_ALGORITHM_VERSION` when a change can alter a stored number.**
- **ROI lists feeding a calculation come from `RoiToolbox.sample_ids(ids)`.**

## Modules

- `dataset/` load, convert, metadata. `image_tools/{geometry,mask,chromatic,background}/` independent; only Chromatic's `affine_for()` / `warp_mask()` are called from outside.
- `roi/`: the ROI Toolbox, one command API behind several front doors. `analysis/`: scan, display queries, workers, reduction, reference `tasks.compute_cell`; computes only on explicit Scan.
- `selection/`: the one shared state (cube, wavelength, ROI); don't add more. `panels/`: display only, no domain state, gestures become commands.
- No module touches another's internal state; use a documented method or signal.
- **Import layers (`lint-imports`, CI):** `app_rewrite` > `panels` > `selection` > `dataset` > `io` > `storage` > `analysis` > `roi` > `image_tools` > `plan` > helpers; import only downward. On failure move the thing down or pass it in; no exception without asking (`docs/import_layering_2026-11-02.md`).
- **Size budgets** (`tests/unit/test_lspri_panel_size_budget.py`) gate `panels/image/panel.py`, `roi/toolbox.py`, `app_rewrite.py`, `panels/roi_table/panel.py`. Raise only on purpose, reason in the commit.

## Communication and heavy work

- Modules are `QObject`s with typed `pyqtSignal`s; no string event bus. Public mutating methods use `@instrumented`.
- `CosmeticChange` = redraw only. `ComputationalChange` = value may be wrong; cell hashes mark it stale, nothing recomputes by itself.
- **Work > ~1 s reports progress and can be cancelled**: a pure function taking `progress: Callable[[float, str], None] | None` and `cancelled: Callable[[], bool] | None`, checking cancel at each progress call (raise `Cancelled`), progress per unit of work with stages weighted by measured times (`tools/chromatic_landmark_lab.py` `StageProgress`); the owning module re-emits a typed signal and joins the `TaskIndicator`.
- **Logging**: `logging.getLogger(__name__)`; analysable events via `log_event(logger, "domain.action", level, **fields)` / `with timed(...)`; field names carry units (`load_ms`). ERROR = user should know, WARNING = recovered, INFO = milestones, DEBUG = timings per unit of work. Never per pixel or per ROI. Slow code gets stage timing when written.

## Tests

- One pytest process per file. Offscreen Qt has no fonts: text-width tests call `tests/_qt_fonts.py` `load_system_fonts()`.
- **A UI change updates its tests in the same piece of work**; tests live in the umbrella `tests/` (grep it for the widget you changed).

## Check in first before

- Re-adding the materiality / pixel-selection recompute optimization, or per-settings-version analysis files.
- Putting ROI or group logic in a panel or view.
- Making a new session copy a prior session (sessions start blank; only the export's Session carry is approved); session rename / duplicate / delete; importers for the old app's `roi_table.json`, `measurement_backup.h5`, `processing_profile.json`.

References: `docs/rewrite_feature_inventory_2026-09.md`, `docs/analysis_provenance_store_design_2026-09.md`, incidents `docs/qthreadpool_zarr_crash_investigation.md`, `docs/analysis_caching_architecture.md`.
