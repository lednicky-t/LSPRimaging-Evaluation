# Mask tab: Eraser (2026-10-29)

Maintainer request: "add to the tools also an eraser with an appropriate icon. Simple: when clicked on a mask pixel it removes it from the
mask. The pixels need to be connected, forming a grain."

**Screen purpose**: remove one blob of the mask by clicking it. **Main action**: a left click on a masked pixel of the image.
**Layout**: the seventh entry of the Manual-edit picker (eraser icon, last, after Draw); its row holds one passive "click on the image"
icon whose tooltip explains the tool; there is nothing to set. While the Mask tab is open and the Eraser is picked, the cursor over the
image is a cross. **States**: armed (Mask tab open + Eraser picked + no canvas tool such as Crop active) / not armed (clicks do what they
always did, e.g. select a ROI). **Messages** (status line): "Eraser: removed a grain of N px from the mask." / "no mask pixel under the click"
/ "the click is outside the area selection" / "the image changed, click again".

## What it does

- **The grain** is the set of masked pixels that touch each other and contain the clicked pixel. **8-connected**: touching at a corner is
  enough (`raster_tools.connected_grain(mask, row, col, diagonal=True)`; `diagonal=False` gives the 4-connected grain, not used by the UI).
  This was my choice for "connected, forming a grain"; a hole inside a blob is not a grain.
- **Found in the mask as drawn** (`resolve_external_mask`: after crop / rotation / flip), because that is what is clicked; then mapped back
  to raw pixels like an area selection (`area_selection_to_raw`: exact with no rotation, a 3x3 closing fills the one-pixel holes a rotation
  would leave) and taken out of the mask in effect at the frame on screen with the same call Subtract uses (`MaskModule.apply_candidate`).
- **Scope**: follows the State group. Persistent: the new mask is authored at this frame and carries on to the later cubes. Individual:
  this frame only.
- **Area selection**: limits it like every other mask tool. A click outside the selection does nothing; only the part of a grain inside the
  selection goes.
- **Not on the undo stack**, like the other tools' Add / Subtract (`MaskModule` docstring: only the Mask-row commands and Auto mask are).
- A click on an unmasked pixel, a right or middle click, or a click off the image changes nothing. The click does **not** also select the
  ROI under it.
- Exact pixel only: a click one pixel beside a blob does nothing (no tolerance). Easy to add if clicking small blobs when zoomed out is
  fiddly.

## Where

`panels/mask_grain_eraser.py` (`MaskGrainEraser.erase_at(x, y)`, widget-free), `panels/image/mask_edit_panels.py` (`EraserEditPanel`),
`panels/image/interaction.py` (`CanvasInteraction._click_erase`, the cross cursor, constructor arguments `eraser_armed` / `erase_at`),
`panels/image/panel.py` (`_eraser_armed`), `panels/image/mask_tab.py`, `image_tools/mask_edit_tool.py` (`ERASER`), icon `eraser_icon` in
`panels/image/mask_edit_tool_icons.py` (the vendored Tabler eraser, like Draw's pencil).

## Cost and checks

A click costs 30-45 ms on a 1275 x 799 mask with 300 blobs (label 6-7 ms, mapping back to raw 23 ms, 37 ms with a 1.42 deg rotation), so
it runs on the GUI thread. `tests/unit/test_lspri_mask_connected_grain.py` (7) and `tests/integration/test_lspri_mask_eraser.py` (13, a real
panel, clicks through `_on_scene_clicked`): exactly the blob goes, a corner contact is one grain, both scopes, an area selection, a rotated
image (every raw pixel of the blob goes, the other blob is untouched), only armed on the Mask tab with the Eraser picked, the ROI under the
click is not selected, the cursor. Not looked at by eye in the running app.

## Not done / ideas

- A hover preview of the grain under the mouse (like Morphology's hover) would show what a click will remove.
- A 4-connected option; a tolerance of a pixel or two around the click.
- Erasing a grain by dragging through several.
