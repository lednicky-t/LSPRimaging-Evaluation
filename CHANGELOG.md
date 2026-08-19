# Changelog

This project follows a simple release log so format and workflow changes stay visible.

## Unreleased

### Planned

- ROI-pair domain model and shape specifications
- Versioned ROI JSON schema
- ROI editor split into circular and custom-shape paths
- Backward-compatible import/export migration path

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
