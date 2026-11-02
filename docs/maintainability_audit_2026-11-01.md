# LSPRi Evaluation: maintainability audit (2026-11-01)

Read-only audit of the app as it stands after the old-app cut-over (eva `rewrite` at 24c85e9, version 0.3.0). Nothing was changed except this file. Earlier audits: `whole_app_audit_2026-10-07.md`, `whole_app_audit_2026-10-29.md` (their "old generation" findings are now done). Numbers come from own AST scripts, `ruff` with extra rules (read-only) and greps; not measured: `vulture`, `mypy`, `radon`, `pytest-cov`, `import-linter` (advisory tools, need a go-ahead).

## Size

351 modules, 81.7k lines of source. `panels/` is 40.4k lines (half), `analysis/` 10.3k, `image_tools/` 8.8k, `roi/` 5.6k, `dataset/` 4.4k. 196 LSPRi test files, 54k lines of tests (0.66 test line per source line). CI runs in about 7 minutes (3 shards).

## What is healthy

- Thread rule holds: `QThreadPool` appears only in comments; every worker is a plain `threading.Thread` or a process pool.
- Science stays mostly out of Qt: roughly 32 of 38 `analysis/` files and 29 of 42 `image_tools/` files are Qt-free; Qt sits in the `*Module` wrappers.
- Zero bare `except:`, zero mutable default arguments, one `print()`, 8 function-level imports, only 2 module-level import cycles (both inside `panels/`).
- 95 % of public functions are fully type-annotated (110 of 2,406 are not).
- Default `ruff` is clean. Docs index lists all 102 docs. `compute_cell` stays as the golden reference for the scan.
- The 13 silent `except Exception` sites are nearly all labelled safety nets or fallback chains. The OME-Zarr export writer (396 lines) is covered through its public wrapper by 10 test files.

## Findings, in order of how much they will cost later

1. **`ImagePanel` keeps growing.** `panels/image/panel.py` is 3,492 lines (3,393 on 10-28), one class with 178 methods and 117 attributes assigned on `self`, 79 commits since 09-01. Widget building alone is about 610 lines, event handlers 515. Splits so far (interaction router, ROI overlay) were good but the file regrew. `RoiTablePanel` (992 lines, 75 methods) and `RoiToolbox` (1,496 lines, 69 methods) are the next two.
2. **Packages import each other in circles.** At package level `analysis`, `dataset`, `image_tools`, `roi`, `storage` and `plan` all import one another (7 mutual pairs). Apart from the two small `panels/` cycles, individual modules do not cycle, so it works, but nothing says which package may depend on which. `.importlinter` guards only `lspr_core`, `lspr_io`, `lspr_ui`, not this app.
3. **Comments and docs still describe the deleted old app.** 123 mentions of 55 files that no longer exist (`gui/analysis_tasks.py` 8x, `gui/mask_controller.py` 7x, `engine.py` 6x, `store.py`, `fitting.py`, ...). Some are harmless "ported from" notes; some are wrong now, e.g. `analysis/provenance.py:11` says gathering inputs is `engine.py`/`worker.py`'s job (engine deleted in cut-over 2e), `analysis/provenance.py:682` points to `store.py`, `dataset/io.py:131` to `gui/worker.py`. Also: `CHANGELOG.md` ends at 0.2.0 with the old app's "Planned" list; the word "rewrite" is in 135 places in 76 files, in `app_rewrite.py`, the settings file name, `APP_SETTINGS_SCHEMA_NAME = "lspri_rewrite_app_settings"` (a saved-format name, do not rename without migration), and 75 of 194 LSPRi test file names; `gui/` (2 files), `processing/` (1), `domain/` (1) are one-file leftovers; `io/` next to `dataset/io.py` is confusing; eva `CLAUDE.md` is 34 KB, `docs/` is 102 files / 2.2 MB (build log alone 482 KB).
4. **Long, branchy functions.** 39 functions over 100 lines; 58 over ruff's statement limit (50), 50 over complexity 10, worst `scan_transients` (219 lines, complexity 39) and `_export_ome_zarr_dataset_to_path` (396 lines, complexity 33). Seven widget `__init__` methods run 138 to 210 lines each (Spectra, ImagePanel, Sensorgram, HistogramPlot, LogPanel, ExportTab, ViewTab). `app_rewrite.py` is 1,984 lines with fan-out 68 (it imports 68 modules); `build_main_window` 183 lines. A composition root is allowed to be wide, but this one is also where each new feature adds wiring.
5. **Lint rules are thin and not in CI.** `ruff.toml` has no `select`, so only ruff's defaults run. 126 `# noqa` comments exist, 123 of them for rules that are not switched on (`N802` 68, `BLE001` 50, 5 others). CI (`tests.yml`) runs tests only: no ruff, no type check. Pre-commit runs ruff locally, but hooks can abort a commit silently (seen before). 50 `type: ignore[override]` are Qt-stub noise; there is no mypy config. Extra rules would flag: 21 blind excepts without a suppression, 11 `global` statements, 32 `zip` without `strict=`, 25 quoted annotations.
6. **The decoded-plane cache is counted in planes, not bytes.** `dataset/io.py:2083` keeps 160 float32 planes (the comment says about 750 MB at 900 x 1300). Nothing scales it to image size or free RAM; at 2048 x 2048 the same count would be about 2.7 GB. Needs a measurement, not a guess, before changing (performance-aware rule, `lspr_core.perf`).
7. **Tests do not name 75 source modules (8.7k lines).** Mostly GUI widgets (`mask_scan_panels`, `roi_tab`, `view_tab`, `slider_ribbon`, `step_overlays`, ...) plus `threaded_task`, `applog/sampling`. By-name check only; many are probably exercised through their panels. `pytest-cov` would give the real answer.

## Not done / not known

No runtime or numeric checks, no GUI look, no coverage, no dead-code scan on the post-cut-over tree. The `ReferenceFrameModule` per-cube Auto cache found unused on 10-29 was not re-checked.

## Possible order of work (maintainer's choice)

A. Doc-rot sweep: fix the wrong comments, rewrite CHANGELOG from 0.3.0 (cheap, no behaviour change).
B. Lint: add a deliberate `select` list to `ruff.toml`, drop the dead `noqa`s, add ruff to CI (cheap, but touches the umbrella repo).
C. Layering contract for this app in `.importlinter` (needs a go-ahead; gives a guard before any package split).
D. Split `ImagePanel` along its build / handler seams, then `RoiTablePanel`, `RoiToolbox`.
E. Byte-bounded plane cache (needs measurement).

## Done 2026-11-01 (items A and B, maintainer's go)

**A. Doc rot.** 44 comment/docstring corrections for statements that named a removed file or class as current (`AnalysisEngine`, `engine.py`, `store.py`, `gui/worker.py`, `mask_highlight_actions.py`, `tools/array_lab.py`, `io/dataset.py`, ...); the dark-frame rule in `dataset/module.py` now names the callers that enforce it today (scan plan `is_spectral`, reference scoring, chromatic tab, array actions). One paragraph in `CLAUDE.md` ("Old file names in comments") explains that the remaining 113 mentions of old-app files are history notes pointing at tag `legacy-final` and should be left alone. README fixed (`domain/models.py` -> `roi/model.py`), CHANGELOG rewritten from 0.3.0 (the old 0.2.0/0.1.0 entries kept below a heading that says they belong to the original app). Checked by comparing each changed file's code tree before and after: identical apart from comments and docstrings.

**Found while doing it, then deleted on the maintainer's go:** `ProvenanceStore` and `InMemoryProvenanceStore` in `analysis/provenance.py` (about 50 lines, plus the `Protocol` import) had no user in `src/`, `tools/`, `tests/` or any other app or package, and no dynamic lookup; only dated design records and the build log mention them, and those stay as written. The 14 test files that mention provenance pass. The engine and store they belonged to can be read with `git show a6411fd~1:src/lspr_imaging_app/analysis/store.py`.

**B. Lint.** `apps/LSPRi/eva/pyproject.toml` now has `[tool.ruff.lint]`: default rules plus `B` (bugbear), `BLE` (blind except), `SIM115` and `RUF100` (dead `# noqa`); `B905` (zip without `strict=`) is ignored on purpose (48 calls, a behaviour change to decide per call); `tools/` is exempt from `B007`, `B023`, `SIM115` (lab scripts). All 21 undocumented `except Exception` sites now carry `# noqa: BLE001 - reason`; 4 unused loop variables renamed `_name`; 177 dead `# noqa` removed (24 of them had a real reason, kept as a plain comment); 68 `N802` "Qt naming" notes dropped (rule left off: it would flag many unannotated Qt overrides). CI: new `lint` job in `.github/workflows/tests.yml` (ruff 0.15.20, same as the pre-commit hooks). Tests: the per-file runner with the patterns `test_lspri_*scan*.py` and `test_lspri_*mask*.py` (the code the renames touch) passed; the full suite was not run, as only comments, suppressions and three loop-variable names changed.

**Still open from the list:** C (layering contract, needs a go-ahead), D (split `ImagePanel`, `RoiTablePanel`, `RoiToolbox`), E (byte-bounded plane cache, needs a measurement).

## Done 2026-11-02 (item C, maintainer's go)

Layering contract for the app: contracts `lspri-layers` (13 floors, exhaustive, 16 documented exceptions) and `lspri-image-tools-independent` in the umbrella `.importlinter`, run in CI by the `lint` job. Explanation, the floors, the exceptions as a to-do list: `import_layering_2026-11-02.md`. Item D (splitting the big classes) and E (byte-bounded plane cache) are still open; the exceptions list is a natural next, smaller piece of work.

**Exceptions paid down (same day, maintainer's go):** 16 -> 12. `FrameNamingScheme` -> `frame_naming.py`, `ExportStats` -> `export_stats.py`, `FrameRolesState` -> `frame_roles_state.py` (details and what is left: `import_layering_2026-11-02.md`). Two hints in the first version of the list were wrong and are corrected there: the export worker needs three plane readers, not one helper, and `roi_feature_task` cannot move to `roi/` as it needs the dataset. Then `roi_feature_task` moved to `panels/roi_feature_task.py` (placement left to Claude by the maintainer): 12 -> 8. Then the ROI data classes (`roi/model.py` -> `domain/roi_model.py`) and `ignored_pixel_mask` (-> `domain/ignored_pixels.py`), 71 files updated: 8 -> 4. Then the plan module (`plan/module.py` -> `selection/experiment_plan_module.py`) and the plane readers (`dataset/io.py` -> `plane_reader.py`, the shard checksum -> `shard_crc.py`): 4 -> 0. No exception is left; `.importlinter` has no `ignore_imports`.

**Also done the same day (maintainer: "do as much as possible without a visual check"):** a third contract, `lspri-science-no-qt` (59 entries: the pure-science modules may not reach Qt, even indirectly; tried on a scratch copy with a deliberate violation, which it reported with the full chain), and `tests/unit/test_lspri_panel_size_budget.py`, a static budget for `panels/image/panel.py` (3,600 lines, 185 methods; today 3,492 and 178) so that the growth of `ImagePanel` becomes a decision. Item D itself (splitting `ImagePanel`) is not started: it changes what is on screen and needs the maintainer's visual check.

## Done 2026-11-02 (item D, batch 1 of 3; built, waiting for the maintainer's visual check)

`ImagePanel` is being split into helper objects the panel owns, the pattern `RoiGestures`, `CanvasInteraction` and `ArrayActions` already use: the helper does not import the panel, it gets the few values it needs as small functions. Measured first (lines, panel fields shared with the rest, calls into other panel methods, test files naming it): everything except construction (`__init__` and the `_build_*` methods: 864 lines, 105 of the panel's 118 fields, 92 calls out) can be cut along its own seams; construction needs a different design (a navigation-bar object that owns its widgets) and is not part of this round.

**Batch 1, "drawn on the image"** (`panels/image/`):

- `tint_overlays.py`, `TintOverlays`: the mask overlay and the histogram highlight overlay (what is tinted, the cached mask, the six control slots). It owns `mask_state` and the last painted mask; it reads the displayed pixels, their finite range and "the background estimate is shown" through functions.
- `decoration_overlays.py`, `DecorationOverlays`: scale bar, chromatic landmarks (with their shown / every-wavelength state and the "last drawn" key), crop outline, chunk grid and source grid. The panel still creates the canvas items in `_build_canvas`, in the same order (that order decides what paints over what).
- `panel.py`: 3,492 -> 3,232 lines, 178 -> 158 methods in `ImagePanel`; `tests/unit/test_lspri_panel_size_budget.py` lowered to 3,330 lines / 163 methods.

**Proof it is a move, not a rewrite:** each of the 20 moved methods was compared line by line with its committed text. The only differences are names, `self._x` fields that became passed-in functions (for example `self._active_tool.active() in _PREVIEW_TOOLS` -> `self._preview_tool_active()`), and docstring pointers. Import layers: 5 contracts kept; 356 modules import; ruff clean. Tests: 9 new ones drive both helpers WITHOUT a panel (`test_lspri_tint_and_decoration_overlays.py`); 89 test files that touch the Image panel or the app all pass (two of them timed out when five Qt processes ran at once on this machine and passed alone: `test_lspri_rewrite_visual_settings_restore.py` 15 tests, `test_lspri_roi_features_tool.py` 17 tests).

**What a person must look at** (tests cannot see these): mask overlay on / off, colour and opacity (Mask tab and View tab); histogram highlight (drag the range, colour, opacity, off); scale bar while zooming and in the preview tools; landmarks (eye and stack toggles, after a chromatic run); crop outline while the Crop and Rotate tools are active; chunk grid and source grid toggles; Play with all of them on.

**Next:** batch 2 (frame status text, cursor readout, ROI queries, crop and measure controls, about 340 lines) and batch 3 (slider marks and Play, about 350 lines).
