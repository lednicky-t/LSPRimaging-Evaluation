# ROI Editor Branch Plan

This document captures the next major branch of the project: moving from circular spot ROIs to a more general ROI system with paired sample and reference areas.

## Current State

The app currently models the analysis world around detected circular spots:

- `DetectedSpot` stores center, radius, and display colors.
- `SpotGroup` stores named collections of spot IDs.
- The spot table is effectively a flat summary of circles plus a reference-ring style background.
- The absorbance pipeline builds spot and ring masks from circles.

The codebase already has an ROI-shaped abstraction in `RoiDefinition`, but it is not yet the main user workflow.

## Target Direction

The new model should treat each analysis unit as a paired ROI:

- one sample area
- one reference area

The old spot/ring workflow should remain as one creation mode, not as the only data model.

## Proposed UI Direction

Rename the current spot editor to `ROI editor`.

Split the editor into two paths:

1. Circular ROI path
   - keeps the current circular spot detection workflow
   - remains the fastest way to create many ROIs from arrays
   - preserves the existing move/add/remove style editing tools

2. General ROI path
   - supports arbitrary shapes
   - supports custom sample and custom reference areas
   - uses the same core editing actions where possible

The editing icons and tool affordances can stay shared between both paths.

For the compact table and pair-row presentation, see:

- [`docs/roi_table_direction.md`](docs/roi_table_direction.md)

For the full end-to-end ROI implementation direction, see:

- [`docs/roi_implementation_direction.md`](docs/roi_implementation_direction.md)

## Proposed Domain Model

Introduce a shape-oriented ROI model with a stable pair structure.

Suggested shape:

- `RoiPair`
  - `roi_id`
  - `name`
  - `sample_shape`
  - `reference_shape`
  - `enabled`
- `ShapeSpec`
  - `kind`: circle, ellipse, rectangle, polygon, freehand
  - geometry data appropriate to that kind
  - display metadata if needed

This keeps the analysis meaning explicit:

- sample shape = measured region
- reference shape = local background or comparison region

## Recommended File Strategy

Use a layered storage strategy.

### Canonical storage

Use versioned JSON as the source of truth for full ROI geometry.

Why:

- it can represent arbitrary shapes
- it can be versioned cleanly
- it preserves backward compatibility better than CSV
- it fits the existing JSON-based workspace/profile storage style

### CSV export

Keep CSV only as a human-readable summary/export format.

CSV is still useful for:

- quick inspection in spreadsheets
- simple metadata interchange
- legacy compatibility

But CSV should not be the only format for custom shapes.

### Sidecar option

If we want a simple transition path, keep a compact table export plus a sidecar JSON:

- `roi_table.csv` for summary rows
- `roi_geometry.json` for full shape data

This is a good transitional bridge, but the long-term canonical format should still be JSON.

## Recommended Migration Path

Phase 1. Rename and reframe

- rename spot editor to ROI editor
- rename spot table to ROI table in UI text
- keep old spot vocabulary only in compatibility code

Phase 2. Introduce new internal model

- add `RoiPair` and `ShapeSpec`
- adapt analysis code to consume ROI pairs
- keep current circular detection as one shape producer

Phase 3. Split the editor UI

- circular ROI tab
- custom shape tab
- shared toolbar actions where possible

Phase 4. Replace persistence

- versioned ROI JSON for full fidelity
- CSV summary export for quick sharing
- import compatibility for older spot files

Phase 5. Compatibility and cleanup

- migrate older `DetectedSpot` and `SpotGroup` data into the new ROI model
- keep old readers behind compatibility functions for a while

## Risks To Plan For

- The analysis pipeline currently assumes circular sample/ring masks.
- Spot selection and grouping logic will need a new ROI-aware abstraction.
- Import/export will need versioning from the start to avoid format lock-in.
- Undo/redo snapshots will need to capture ROI geometry, not just IDs and colors.

## Implementation Principles

- Prefer a shape-agnostic internal model early.
- Keep circular ROI creation as a first-class fast path.
- Preserve backward compatibility wherever possible.
- Make the storage format versioned before adding too many new fields.
- Keep user-facing names consistent and deliberate.

## Suggested First Milestones

1. Rename the UI and docs to ROI terminology.
2. Add the `RoiPair` domain sketch in code.
3. Define the versioned ROI JSON schema.
4. Add a custom-shape editor tab.
5. Migrate absorbance mask construction to use ROI shapes.
6. Add import/export compatibility tests.
