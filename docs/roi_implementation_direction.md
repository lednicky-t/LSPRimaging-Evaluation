# ROI Implementation Direction

This document describes the intended end-state for the ROI system refactor.

It is the project note for the major transition from "spots and rings" to a general ROI workflow.

## Summary

The app should move from a spot-centric model to a paired ROI model:

- `sROI` = sample ROI
- `rROI` = reference ROI

Each analysis unit is a pair. The current circular spot workflow becomes one template-producing mode inside the broader ROI system.

The new system should support:

- circular ROIs
- rectangular ROIs
- polygons and freehand ROIs later
- manual stamping
- array stamping
- future automatic generation for supported shapes
- versioned storage and backward compatibility

## Core Concept

There are three different things that should not be mixed together:

1. **Template**
   - the editable shape definition from the ROI editor
   - examples: circle, rectangle, polygon, freehand
   - the template describes geometry and shape parameters

2. **Placement**
   - where that template is placed on the current image
   - contains image coordinates, offsets, and enabled state

3. **Pair**
   - the analysis unit
   - links one sample ROI with one reference ROI

This separation keeps the model clean and makes array stamping, movement, and later auto-detection much easier.

## Naming

Recommended naming in the UI:

- `ROI editor`
- `ROI table`
- `Sample ROI`
- `Reference ROI`
- `ROI pair`

Recommended compact code terms:

- `sROI`
- `rROI`
- `pair_id`
- `template_id`

The old spot/ring vocabulary should remain only in compatibility code and in places where it still appears in the analysis pipeline during the transition.

## ROI Editor Workflow

The ROI editor should be the place where the user defines the template shape.

The editor should expose shape-specific tabs:

- `Circles`
- `Rectangles`
- `Freehand` later

The editing tools themselves should stay shared across tabs:

- add
- move
- remove
- array stamp
- eventually copy / duplicate / align / distribute

The active tab decides the current template, while the shared tools decide how that template is used.

## Template Behavior

Templates are the source for ROI stamping.

Expected behavior:

- user edits template parameters in the active tab
- user clicks `Add` and then clicks the image
- the app stamps the current template at the clicked position
- user can stamp a grid with the array tool
- the same template can be reused for many ROIs without duplicating geometry data

Templates should be reusable and referenceable by ID.

## Pair Behavior

A pair should represent the sample/reference relationship used by analysis.

Recommended pair semantics:

- the pair has one human-readable name
- the pair has one pair ID
- the pair has a sample color swatch
- the sample and reference placements belong together
- moving the pair moves both members together
- deleting the pair removes both members together

The pair is the row in the ROI table.

## Current Compact Table Direction

Keep the table compact and summary-oriented.

Suggested visible columns:

- `ID`
- `Name`
- `Color`
- `Position`
- `Offset`
- optional `Shape` pictogram

The status should be a thick vertical bar on the row edge, not a text column.

Status colors:

- green: ready
- yellow: partial or caution
- red: warning or invalid
- gray: disabled

Hovering the status bar should explain the reason.

For the table-specific details, see:

- [`docs/roi_table_direction.md`](roi_table_direction.md)

## Shape Representation

The app should be able to represent ROI geometry in two forms:

1. **Parametric shape**
   - circle, rectangle, polygon, freehand
   - editable and versionable

2. **Binary mask**
   - derived from the shape
   - used for display, export, and analysis where useful

The parametric shape should be the canonical source of truth.

The binary mask should be a generated artifact that can be saved for preview or export.

## Storage Direction

The implementation should support versioned storage at two levels:

- project/session storage
- ROI export/import storage

Recommended approach:

- keep the canonical ROI definition in JSON
- save derived mask images separately as human-readable PNGs
- keep CSV only as a compact summary or legacy export

This avoids locking the system to a flat table format that cannot represent polygons or future shapes well.

### Suggested project layout

Inside the project/session data, use separate ROI assets:

- `rois.json`
- `roi_templates.json`
- `roi_pairs.json`
- `roi_masks/`
- `roi_previews/`

The exact file names can change later, but the principle should remain:

- one canonical JSON model
- separate readable mask/preview exports
- version metadata on every stored format

## Editing Tools

The editor tools should be shape-agnostic wherever possible.

The tools should operate on:

- templates
- placements
- pairs

and should be able to:

- add
- move
- delete
- stamp arrays
- later auto-generate from shape-specific logic

The same command path should be used for:

- manual single stamping
- manual array stamping
- future recognition-driven placement

That is important for consistency and reduces duplicated code.

## Array Workflow

Arrays should duplicate the active template, not a special-case circle model.

Expected behavior:

- user selects the template tab
- user sets array rows, columns, and spacing
- user clicks the image
- the app stamps a grid of ROIs from the active template

The array tool should work for:

- circles
- rectangles
- future supported shapes

## Movement Workflow

Movement should act on the ROI pair or stamped ROI instance, not on the template itself.

Expected behavior:

- moving one pair keeps sample and reference linked
- moving an array entry moves that entry as a unit
- template geometry remains unchanged unless the user edits the template

## Future Shape Tools

The tabs can later grow shape-specific helpers.

Examples:

- auto-fit a circle
- auto-fit a rectangle
- draw polygon vertices
- freehand tracing
- snap to image features
- convert detected structures to templates

These are later tools. The first implementation goal is the shared data model and the shared stamping workflow.

## Migration Direction

Migration from the current spot system should happen in stages:

1. rename the user-facing terms
2. introduce the ROI template and pair model
3. make the ROI editor create template-based placements
4. make the table show pairs instead of spots
5. make analysis consume ROI pairs
6. preserve compatibility with old circular spot files

The old circular workflow should remain usable while the migration progresses.

## Compatibility Rules

The app should maintain explicit versioning for:

- app version
- workspace/session schema
- ROI export schema
- processing profile schema

Each format should be able to evolve independently.

Backward compatibility should be preserved through:

- format version tags
- migration helpers
- compatibility readers
- conversion to the new ROI model on load

## Practical Acceptance Criteria

The ROI refactor is moving in the right direction when:

- the editor tabs create reusable templates
- manual add stamps the active template into the image
- array stamping works from the active template for multiple shapes
- move/remove operate on ROI pairs or stamped ROI instances
- the table is compact and pair-oriented
- the status bar clearly communicates row health
- the app can save and reload ROI data without losing shape fidelity
- old spot-based data can still be loaded

## Open Questions To Resolve Later

These are intentionally deferred:

- exact internal mask format
- exact pair-reference structure
- whether a reference ROI can be shared by multiple sample ROIs
- detailed polygon/freehand editing behavior
- whether the table should show a small shape pictogram, text, or both
- whether pair rows should expose a side detail panel

Those should be decided after the core model is stable.
