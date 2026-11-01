# Changelog

This project follows a simple release log so format and workflow changes stay visible. It says what changed **for a user**; the reasons, design choices and measured numbers are in the dated records listed in `docs/README.md` (the per-feature record is `docs/rewrite_status_by_feature_2026-10-29.md`).

## Unreleased

### Added

- Help -> Keyboard shortcuts: one table now both binds every shortcut and builds the list, so the list cannot drift from the keys that really work (2026-10-30; `docs/keyboard_shortcut_registry_design_2026-10-30.md`). Key matching is now exact: a few unusual modifier combinations that used to trigger a command no longer do (listed in the design doc, section 8).

### Fixed

- Edit -> Undo/Redo no longer tries to refresh itself after its window has been closed (2026-10-30).

## 0.3.0 (2026-10-29): the rewrite replaces the original app

LSPRi Evaluation was rebuilt from scratch (September to October 2026) and replaced the original app. It starts the same way (`lspri-evaluation`, or the Suite launcher). The original app is kept as git tag `legacy-final`; nothing else used it.

### What the app does now

- **Dataset**: TIFF stacks and OME-Zarr datasets; a TIFF stack can be exported to a sharded OME-Zarr (the export carries ROIs, masks, chromatic correction and mask pointers into the new coordinates); an experimental plan can be imported from a CSV file.
- **Docked panels**: Image, Histogram, ROI table, Spectra, Sensorgram, Workflow and Log; the layout and every control you change are remembered between runs; Edit -> Undo/Redo; Edit -> Preferences (including how many CPU cores heavy jobs may use).
- **Image tools**: crop, rotate (Lanczos interpolation), flip and pixel-size calibration; chromatic correction with automatic landmarks; background removal (divide-only); mask editing (threshold, local contrast, morphology, eraser, ROI-feature detection, transient scan with Auto mask) with a preview of the pixels a tool would change.
- **ROIs**: circle arrays are detected automatically (spot-size scan, lattice fit), grouped and edited in a ROI table; Auto crop to the ROI lattice.
- **Analysis**: a scan turns the pixels into numbers per ROI, cube and wavelength (stored in `scan.h5`); Spectra shows them with polynomial peak fits (several fit-window modes) and Sensorgram shows them over time with plan-step statistics, a baseline band and mask marks.
- **Sessions**: your work is saved automatically per dataset and restored when you open it again.

### Removed or not carried over

- The original app's results file (schema 7, "measurement file") is retired; a new export format is deliberately deferred until all functions are in.
- The original app's UI-scale setting, workflow notes and shortcuts dialog (the shortcuts list now exists again as Help -> Keyboard shortcuts, see above).
- The old analysis engine (it was never started by any button).

## 0.2.0 and earlier: the original app (git tag `legacy-final`)

The entries below describe the original app and are kept as written. Their old "Planned" list (ROI-pair model, versioned ROI JSON schema) was never built that way.

## 0.2.0

### Added

- Fully automatic circle-array ROI detection: infers diameter, rows, columns,
  and spacing directly from the image (`processing/roi_array_geometry.py`,
  scale-space blob detection + lattice recovery), and sizes the reference
  ring automatically to match the sample circle's area. Denoises before
  detection so it holds up under realistic sensor noise, not just clean
  synthetic images. Refuses and explains why (via new per-stage diagnostics)
  rather than guessing when no confident periodic array is found.
- One-click histogram highlight-range auto-detection ("wand" tool in the
  ROI tools row): finds the darker ROI population in the reference image's
  bimodal histogram and sets the highlight range automatically
  (`processing/roi_histogram.py`).
- Editable "Highlight [min, max]" readout under the histogram - the
  intensity range can now be typed directly, not only dragged.
- Reorder-by-column tool alongside the existing reorder-by-row: numbers ROIs
  top-to-bottom within each column, left column to right column.

### Changed

- Circles/Rectangles/Freehand in the ROI editor are now vertical accordion
  sections (matching the Image tools/Analysis panel style) instead of
  horizontal tabs.
- Renamed the grid-based ROI detector "Semi-automatic" throughout its
  tooltips/help text, to distinguish it from the new fully automatic finder.

### Fixed

- Semi-automatic detection silently finding 0 ROIs when the histogram
  highlight range didn't cover the sample intensities - the wand tool and
  clearer status messaging address the underlying cause.

### Added (carried over from Unreleased)

- ROI editor branch plan documented in `docs/roi_editor_branch_plan.md`
- Versioning and repo practice guidelines in `docs/versioning_and_repo_practices.md`
- CONTRIBUTING guide and pull request template scaffolding

## 0.1.0

- Initial documented application series
- Circular spot workflow with per-spot ROI evaluation
- Spot table import/export and processing profile persistence
