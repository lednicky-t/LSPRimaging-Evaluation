# LSPRi Evaluation: import layering (2026-11-02)

Item C of `maintainability_audit_2026-11-01.md`. The rules are in the umbrella repo's `.importlinter` (contracts `lspri-layers` and `lspri-image-tools-independent`); this file explains them. Run them with `lint-imports` from the umbrella root (under a second); CI runs them in the `lint` job of `tests.yml`.

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
| 12 | `threaded_task`, `diagnostics`, `gil_tuning`, `version` | small helpers that use the floor below |
| 13 | `progress`, `applog`, `format_versions`, `frame_naming`, `export_stats`, `frame_roles_state`, `undo`, `shortcuts`, `change_events`, `wavelength_color`, `image_features`, `thread_priority`, `domain`, `processing` | leaf helpers that import nothing of the app |

Packages on the same floor (written with `|` in the file) must not import each other either.

How the order was chosen: of 561 imports between packages, a search over orders found the one with the fewest upward imports (16; 12 were fixed the same day, see below, so 4 remain). I then checked that every one of the 16 has a reason and a fix (below). Other plausible orders (for example storage above selection, or analysis above dataset) gave 17 or more.

## What to do when the check fails

- **"X is not allowed to import Y"**: the new import goes upward. Usually the thing you need sits on the wrong floor. Move it down (a small data class, a file-naming helper), or pass it in as an argument or callback, as the scan plan already does with its callables. Do not add it to the exceptions list unless the maintainer agrees: that list is debt.
- **"not listed as layers"**: you added a new top-level package or module. Decide its floor and add it to `layers` in `.importlinter`.
- **"No matches for ignored import"**: you fixed one of the exceptions. Delete that line from `ignore_imports`. (The tool fails on purpose so the list can only shrink.)

## Fixed on 2026-11-02 (12 of the 16)

`FrameNamingScheme` and its three helpers moved from `analysis/provenance.py` to the new leaf module `frame_naming.py` (this also made the four image tools fully independent: the independence contract has no exception left). `ExportStats` (with `format_duration` and `format_bytes`) moved from `dataset/` to `export_stats.py` and `FrameRolesState` from `selection/` to `frame_roles_state.py`, both plain standard-library data modules that nothing above or below needs to own. The moves were done with `git mv`, all importers and the tests that imported them were updated, every module of the package imports, and the affected tests pass.

`RoiFeatureTask` (the Mask tab's "ROI features" run) moved from `image_tools/` to `panels/roi_feature_task.py`, which removed its 4 exceptions at once. It reads the `DatasetModule` and the ROI toolbox as well as three image tools, so it can sit neither in `image_tools` (below both) nor in `roi/` (below `dataset`); `panels/` is where its users are (the Mask tab, the Image panel, the app shell). The alternative, handing it the plane loader and shape as callables so that it could live in `roi/`, would have left the task in the wrong package for what it is: a Qt thread wrapper around the modules. Only imports and a docstring note changed; its 17 tool tests, the 30 unit tests of the finder, and the Mask-tab, Image-panel and area-selection tests pass.

The ROI data classes (`AreaRoi`, `AreaRoiGroup`, `RoiArrayGroup`, `RoiMask`, `RoiTimeline`, `AreaRoiDetectionSettings` and the scope constants) moved from `roi/model.py` to `domain/roi_model.py`, and `ignored_pixel_mask` (with its helper) from `roi/detection.py` to `domain/ignored_pixels.py`. Both import nothing of the app, so they fit `domain/`, the leaf package for plain data and rules; `image_tools`, `storage`, `analysis` and the panels all reach them downward now, which removed the 4 exceptions of the image tools. It is a move only: the code of both files is identical to the committed one (checked by comparing the parsed code, docstrings left out), no numbers can change. 27 source files, 3 tool scripts and 41 test files (one of them the shared test helper) got the new import path; nothing is left behind at the old place, and no saved file or pickle names the module path (checked), so older sessions load as before.

## The 4 exceptions left, grouped by the fix (each is a to-do, none is a bug)

| Fix | Removes | Size |
|---|---|---|
| Pass the plane loader into the scan instead of importing `dataset/io.py` (or move `analysis/worker.py` and `storage/workspace.py` down) | 1 | small to medium |
| Extract the plane readers the export worker uses (`_ome_zarr_root`, `_read_ome_zarr_plane_by_path`, the fast TIFF decoder) out of the 2,300-line `dataset/io.py` into a module below `dataset` | 1 | medium: hot path, measure plane-read time before and after |
| Split `plan/` into plan data and `PlanModule` | 2 | medium |

## Limits of the tool (what this does not check)

- A layer cannot be a package inside another layer, so "`roi/model.py` below `image_tools`, the rest of `roi/` above" could not be written as a rule; that is why the ROI classes had to move out of `roi/` (into `domain/`) instead of being given a lower floor of their own.
- It checks who imports whom, not what they do. "Panels own no domain state" and "analysis computes only on an explicit Scan" stay review rules.
- Pure-science code staying free of Qt is not checked here. A separate contract (a list of the currently Qt-free modules that must stay Qt-free) is a possible follow-up.

## How it was checked

Run on the real tree (4 contracts kept, 0.5 s); on a scratch copy with an added `dataset -> panels` import (reported, with the importing line); on a scratch copy with an unassigned new package (reported by the exhaustive check); with an exception line that matches no import (the tool errors); and in a clean virtual environment with only `import-linter` installed and `PYTHONPATH` set to the four `src` folders, which is how CI runs it.
