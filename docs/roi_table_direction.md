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


## View modes and the Shape column (2026-10-13)

The flat/grouped toggle is gone (the table is grouped whenever groups exist). A view-mode choice replaces it, in the context menu of a right-click on the column headers (no toolbar button). The px/µm toggle is shown only in Geometry:

- **Simple**: `#` (colour dot), Label (group names).
- **Geometry** (default): `#`, x, y, Shape (pictogram), D_s, d_r, D_r (D = outer, d = inner diameter, s = sample, r = reference). A number the shape does not have is blank.

How it is built:

- The model always has all columns (`rows.COLUMN_*`); a view mode only shows or hides them (`rows.VIEW_MODE_COLUMNS`, `RoiTreeView.set_view_mode`, which also moves Shape to sit after y). Edit, copy, paste, fill and step code is unchanged.
- **Tried and dropped (2026-10-13): "vector" cells** (`[x, y]`, `[D_s, d_r, D_r]` drawn in one cell, one number editable at a time via a custom delegate). The app exited with an access violation (0xC0000005) while editing in it; the cause was not proven, but that editor path re-derived model indexes outside Qt's own editor handling, so separate cells were restored as the safer design. If vectors are wanted again, do it as a header/painting change over real separate columns, not as an editor over a composite cell.
- **Shape is derived**, not stored: `rows.shape_of(sample_geometry_type, reference_geometry_type)` (circle+annulus = ring, circle+none = disk, mask = mask, anything else = other). No change to the saved format. `rows.SHAPE_FIELDS` lists the diameter columns each shape has; the others are blank and not editable.
- Saved UI state: `roi_table/view_mode`; the sort is only restored if its column is visible in the saved mode; widths are one per column (8; a saved list of 7 is still read).

Open (`TODO(shapes)` in `rows.py` and `delegate._paint_shape`): rectangle with frame, polygon and freehand do not exist yet; they fall under "other" (blank diameters, `?` pictogram). Ring-centre offsets (`Δx, Δy`) are not stored yet.
