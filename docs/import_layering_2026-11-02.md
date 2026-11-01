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
| 9 | `roi` | ROI model, detection, rasterising, the ROI toolbox |
| 10 | `image_tools` | geometry, mask, chromatic correction, background |
| 11 | `plan` | the experimental plan |
| 12 | `threaded_task`, `diagnostics`, `gil_tuning`, `version` | small helpers that use the floor below |
| 13 | `progress`, `applog`, `format_versions`, `undo`, `shortcuts`, `change_events`, `wavelength_color`, `image_features`, `thread_priority`, `domain`, `processing` | leaf helpers that import nothing of the app |

Packages on the same floor (written with `|` in the file) must not import each other either.

How the order was chosen: of 561 imports between packages, a search over orders found the one with the fewest upward imports (16). I then checked that every one of the 16 has a reason and a fix (below). Other plausible orders (for example storage above selection, or analysis above dataset) gave 17 or more.

## What to do when the check fails

- **"X is not allowed to import Y"**: the new import goes upward. Usually the thing you need sits on the wrong floor. Move it down (a small data class, a file-naming helper), or pass it in as an argument or callback, as the scan plan already does with its callables. Do not add it to the exceptions list unless the maintainer agrees: that list is debt.
- **"not listed as layers"**: you added a new top-level package or module. Decide its floor and add it to `layers` in `.importlinter`.
- **"No matches for ignored import"**: you fixed one of the exceptions. Delete that line from `ignore_imports`. (The tool fails on purpose so the list can only shrink.)

## The 16 exceptions, grouped by the fix (each is a to-do, none is a bug)

| Fix | Removes | Size |
|---|---|---|
| Move `FrameNamingScheme` (file naming) out of `analysis/provenance.py` to a lower floor | 2 (mask export and mask export task; the same import also has a line in the independence contract) | small |
| Move the plain ROI classes (`roi/model.py`) and `ignored_pixel_mask` (`roi/detection.py`) below `image_tools` | 4 | medium: `roi/model.py` is imported in 27 places, but moving a file is mechanical |
| Move `image_tools/roi_feature_task.py` up into `roi/` (or `panels/`) | 4 (its `roi.model` import would also go with the ROI-model move) | small |
| Pass the plane loader into the scan instead of importing `dataset/io.py` (or move `analysis/worker.py` and `storage/workspace.py` down) | 1 | small to medium |
| Move `_ome_zarr_root` into `io/` for the export worker | 1 | small |
| Split `plan/` into plan data and `PlanModule` | 2 | medium |
| Move `ExportStats` and `FrameRolesState` down, or pass values in | 2 | small |

## Limits of the tool (what this does not check)

- A layer cannot be a package inside another layer, so "`roi/model.py` below `image_tools`, the rest of `roi/` above" cannot be written as a rule; it is handled as exceptions until the file is moved.
- It checks who imports whom, not what they do. "Panels own no domain state" and "analysis computes only on an explicit Scan" stay review rules.
- Pure-science code staying free of Qt is not checked here. A separate contract (a list of the currently Qt-free modules that must stay Qt-free) is a possible follow-up.

## How it was checked

Run on the real tree (4 contracts kept, 0.5 s); on a scratch copy with an added `dataset -> panels` import (reported, with the importing line); on a scratch copy with an unassigned new package (reported by the exhaustive check); with an exception line that matches no import (the tool errors); and in a clean virtual environment with only `import-linter` installed and `PYTHONPATH` set to the four `src` folders, which is how CI runs it.
