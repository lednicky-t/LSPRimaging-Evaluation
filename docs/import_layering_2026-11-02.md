# LSPRi Evaluation: import layering (2026-11-02)

Item C of `maintainability_audit_2026-11-01.md`. The rules are in the umbrella repo's `.importlinter` (contracts `lspri-layers`, `lspri-image-tools-independent` and `lspri-science-no-qt`); this file explains them. Run them with `lint-imports` from the umbrella root (under a second); CI runs them in the `lint` job of `tests.yml`.

## What a layer rule is

Think of the app as a stack of floors. A package on one floor may use packages on the floors **below** it, never on a floor above. Without this rule, a harmless-looking import can make a low-level package depend on the GUI, and then a "pure" calculation can no longer be tested without Qt, or two packages end up needing each other so neither can be moved or reused alone. The tool is `import-linter`; it reads the source files and checks every `import` line, including imports inside functions and under `TYPE_CHECKING`.

## The floors (top to bottom)

| Floor | Packages | In plain words |
|---|---|---|
| 1 | `app_rewrite` | the one place that builds and wires everything |
| 2 | `launch`, `startup_tracker`, `gui`, `perf_setup`, `roi_geometry_sync` | start-up and wiring helpers (nothing below may import them) |
| 3 | `panels` | everything you see |
| 4 | `selection` | the one shared state (current cube, wavelength, ROI) |
| 5 | `dataset` | loading and converting datasets |
| 6 | `io` | export worker and metadata import |
| 7 | `storage` | sessions, autosave, app settings |
| 8 | `analysis` | the scan, spectra and sensorgram numbers |
| 9 | `roi` | ROI detection, rasterising, the ROI toolbox (the plain ROI classes live in `domain/`, floor 13) |
| 10 | `image_tools` | geometry, mask, chromatic correction, background |
| 11 | `plan` | the experimental plan |
| 12 | `threaded_task`, `diagnostics`, `gil_tuning`, `version`, `plane_reader` | small helpers that use the floor below |
| 13 | `progress`, `applog`, `format_versions`, `shard_crc`, `frame_naming`, `export_stats`, `frame_roles_state`, `undo`, `shortcuts`, `change_events`, `wavelength_color`, `image_features`, `thread_priority`, `domain`, `processing` | leaf helpers that import nothing of the app |

Packages on the same floor (written with `|` in the file) must not import each other either.

How the order was chosen: of 561 imports between packages, a search over orders found the one with the fewest upward imports (16; all 16 were fixed the same day, see below). I checked first that every one of the 16 had a reason and a fix. Other plausible orders (for example storage above selection, or analysis above dataset) gave 17 or more.

## What to do when the check fails

- **"X is not allowed to import Y"**: the new import goes upward. Usually the thing you need sits on the wrong floor. Move it down (a small data class, a file-naming helper), or pass it in as an argument or callback, as the scan plan already does with its callables. Do not add an `ignore_imports` exception unless the maintainer agrees: such a list is debt (there is none today).
- **"not listed as layers"**: you added a new top-level package or module. Decide its floor and add it to `layers` in `.importlinter`.
- **"No matches for ignored import"**: only when an exception list exists (there is none today): you fixed one of the exceptions. Delete that line from `ignore_imports`. (The tool fails on purpose so the list can only shrink.)

## Fixed on 2026-11-02 (all 16)

`FrameNamingScheme` and its three helpers moved from `analysis/provenance.py` to the new leaf module `frame_naming.py` (this also made the four image tools fully independent: the independence contract has no exception left). `ExportStats` (with `format_duration` and `format_bytes`) moved from `dataset/` to `export_stats.py` and `FrameRolesState` from `selection/` to `frame_roles_state.py`, both plain standard-library data modules that nothing above or below needs to own. The moves were done with `git mv`, all importers and the tests that imported them were updated, every module of the package imports, and the affected tests pass.

`RoiFeatureTask` (the Mask tab's "ROI features" run) moved from `image_tools/` to `panels/roi_feature_task.py`, which removed its 4 exceptions at once. It reads the `DatasetModule` and the ROI toolbox as well as three image tools, so it can sit neither in `image_tools` (below both) nor in `roi/` (below `dataset`); `panels/` is where its users are (the Mask tab, the Image panel, the app shell). The alternative, handing it the plane loader and shape as callables so that it could live in `roi/`, would have left the task in the wrong package for what it is: a Qt thread wrapper around the modules. Only imports and a docstring note changed; its 17 tool tests, the 30 unit tests of the finder, and the Mask-tab, Image-panel and area-selection tests pass.

The ROI data classes (`AreaRoi`, `AreaRoiGroup`, `RoiArrayGroup`, `RoiMask`, `RoiTimeline`, `AreaRoiDetectionSettings` and the scope constants) moved from `roi/model.py` to `domain/roi_model.py`, and `ignored_pixel_mask` (with its helper) from `roi/detection.py` to `domain/ignored_pixels.py`. Both import nothing of the app, so they fit `domain/`, the leaf package for plain data and rules; `image_tools`, `storage`, `analysis` and the panels all reach them downward now, which removed the 4 exceptions of the image tools. It is a move only: the code of both files is identical to the committed one (checked by comparing the parsed code, docstrings left out), no numbers can change. 27 source files, 3 tool scripts and 41 test files (one of them the shared test helper) got the new import path; nothing is left behind at the old place, and no saved file or pickle names the module path (checked), so older sessions load as before.

`ExperimentPlanModule` moved from `plan/module.py` to `selection/experiment_plan_module.py`, next to the other small Qt owners of one shared value (`FrameRolesModule`, `ReferenceFrameModule`, `HighlightRangeModule`). It needs the dataset and the step statistics, which sit above `plan/`; what is left in `plan/` is Qt-free and used from below (`storage`, `analysis`). Its lazy import of `assign_cubes` (there to dodge a cycle through `plan`) became a normal import at the top. The code of the module is identical to the committed one apart from that line (12 test files, 408 tests pass).

**The plane readers** moved out of `dataset/io.py` into the new `plane_reader.py` (`io.py` went from 2,290 to 1,640 lines): OME-Zarr reads of a whole plane or only the chunks around some pixels with their shard-index cache, the native TIFF decoder with its tifffile check, and `load_image_array_uncached`. The analysis scan and the Zarr export worker process both read planes, and both sit below `dataset`. The checksum of a shard's index (`_crc32c`) went to the tiny `shard_crc.py`: the readers check what the export worker writes, and `io` sits above the readers.

- **Nothing was rewritten.** All 114 top-level statements of the two committed files (`dataset/io.py`, `io/_zarr_export_worker.py`) are present: 49 moved with identical code, 65 stayed with identical code (compared as parsed code; docstrings and `import` lines left out). The only new statements are the `VALID_MASK_ARRAY` constant (the readers apply the export's valid mask, so they own its name now; `ome_metadata.py` points here) and a reduced copy of the optional `tifffile` import.
- **Speed is unchanged.** Same 900 x 1300 files, committed code against the new code, two rounds each, median per read: TIFF plane 21.7-21.8 ms against 21.6-21.8 ms, OME-Zarr whole plane 17.3-17.6 against 17.4-17.5, partial read of 12 ROI blocks 2.39-2.48 against 2.39-2.46; run-to-run noise is larger than any difference.
- **State lives once.** The shard-index cache, the fast-decoder flags and the cached zarr handles are in `plane_reader` (`dataset/io.py` imports the same objects). A test that patches or resets one must do it where the name is looked up: on `plane_reader` for code in `plane_reader` or the scan, but on `dataset.io` for `_load_image_array_native`, which stays in `io.py` and has its own binding of `_read_tiff_fast`. (One test had that wrong after the move and failed, which is how it showed.)
- **A worker process stays light.** Importing the export worker still loads none of `dataset.io`, `plane_reader`, PIL, zarr or tifffile; it imports `plane_reader` inside the function that needs a plane, as before.
- **Checked by**: 43 test files (about 796 tests: scan, partial reads, worker processes, export, fast TIFF, session carry), a 12-plane export that ran through the real worker processes, and 354 modules importing.

## Exceptions left

None. `.importlinter` has no `ignore_imports` list any more, so an upward import added from now on fails the build at once. If a future change really needs one, it needs the maintainer's agreement, and it goes back on the list as a to-do with its fix written next to it.

## Limits of the tool (what this does not check)

- A layer cannot be a package inside another layer, so "`roi/model.py` below `image_tools`, the rest of `roi/` above" could not be written as a rule; that is why the ROI classes had to move out of `roi/` (into `domain/`) instead of being given a lower floor of their own.
- It checks who imports whom, not what they do. "Panels own no domain state" and "analysis computes only on an explicit Scan" stay review rules.
- Pure-science code staying free of Qt is a separate contract, `lspri-science-no-qt` (added 2026-11-02): the 53 modules of `analysis`, `roi` and `image_tools` that reach no Qt today, the whole `plan` and `domain` packages, and `plane_reader`, `shard_crc`, `frame_naming`, `export_stats` may not import Qt, **not even through other modules**. That is the root rule "keep science out of GUI code" made checkable, and the layers contract cannot see it (a downward import of `threaded_task` is allowed by the floors but reaches Qt). The failure message prints the whole chain, e.g. `frame_reduce -> reduction -> threaded_task -> PyQt6`. A new pure module is added to the list in `.importlinter`; one is never removed without asking.
  **Logging from science code: `from ..applog.events import log_event, timed`, never `from ..applog import ...`** (2026-11-11). The package's `__init__` also loads `applog/setup.py`, whose Qt hooks (imported inside functions, so never run in science code) still count as an import of PyQt6 for the checker: `check_report -> step_diagnostics -> step_stats -> applog -> applog.setup -> PyQt6` broke the contract that way. Fixed by pointing every `analysis` / `image_tools` import at `applog.events` (pure standard library); only `trace_pool` keeps `applog.current`.

## How it was checked

Run on the real tree (4 contracts kept, 0.5 s); on a scratch copy with an added `dataset -> panels` import (reported, with the importing line); on a scratch copy with an unassigned new package (reported by the exhaustive check); with an exception line that matches no import (the tool errors); and in a clean virtual environment with only `import-linter` installed and `PYTHONPATH` set to the four `src` folders, which is how CI runs it.
