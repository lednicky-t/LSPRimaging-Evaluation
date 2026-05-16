# ROI Table Direction

This note captures the compact ROI table workflow for the new paired ROI model.

## Goal

Replace the current spot table with a compact ROI pair table that stays readable even when there are many entries.

The table should describe one ROI pair per row:

- sample ROI, or `sROI`
- reference ROI, or `rROI`

The table is only a summary and navigation surface. The actual template editing remains in the ROI editor tabs.

## Table Layout

Keep the visible columns minimal:

- `ID`
  - pair id
- `Name`
  - user-defined pair label
- `Color`
  - sample ROI color swatch
- `Position`
  - pair anchor or sample position in `x, y`
- `Offset`
  - offset between `sROI` and `rROI`
- `Shape`
  - optional compact pictogram or short summary

Avoid adding more geometry columns unless they are clearly needed later.

## Row Status

Use a thick vertical bar at the leading edge of each row to show pair state.

Suggested colors:

- green: all good
- yellow: partial / caution / needs attention
- red: warning / invalid / missing data
- gray: disabled

The bar should be the primary status signal instead of a text column.

Hovering the bar should show a tooltip explaining the state.

Possible examples:

- `Ready for analysis`
- `Reference ROI missing`
- `Template out of sync`
- `Disabled by user`

## Shape Representation

Shape should stay compact.

Prefer pictograms first, text second:

- circle icon for circular templates
- rectangle icon for rectangular templates
- polygon or freehand icon later

If a short text summary is useful, keep it terse, for example:

- `circle d=24`
- `rect 18x12`
- `rect 18x12 + off(0,14)`

## Color Rules

Use separate color handling for sample and reference areas:

- sample ROI color can be per row or per pair
- reference ROIs can share one global reference color chosen from the existing image overlay selector

This keeps the table simple while preserving visual consistency in the image view.

## Pair Semantics

The row should represent the pair as a single analysis unit.

Useful compact identifiers:

- `s1 / r1`
- `s2 / r2`

If a future workflow intentionally allows one reference ROI to serve multiple sample ROIs, that should be explicit in the model and visible in the table summary.

## What The Table Should Not Do

The table should not become the ROI editor.

Avoid putting these directly into the main table unless there is a strong reason:

- full shape geometry
- all template parameters
- mask editing controls
- per-shape configuration widgets

Those belong in the ROI editor tabs or in a detail panel.

## Implementation Direction

The table should eventually support:

- compact row rendering
- status bar tooltips
- selection synchronization with image overlays
- pair-level add, remove, and enable/disable actions
- compatibility with template-based stamping and arrays

The row visuals can be extended later with pictograms or more shape-specific badges without changing the basic compact layout.
