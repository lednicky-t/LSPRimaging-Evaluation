# ROI copy / paste (2026-11-24)

Maintainer request: select ROIs, Ctrl+C, Ctrl+V pastes them at the pointer; same in the right-click menu.

## What is built

- `roi/clipboard.py`: `RoiClip` / `ClippedRoi`, plain frozen data. `RoiToolbox.copy_rois(ids, cube=)` makes one (changes nothing); `RoiToolbox.paste_rois(clip, x, y)` makes new ROIs (one undo step, one `geometry_changed`, plus `links_changed` when copies are linked).
- `panels/image/roi_creation.py`: `copy_action` / `paste_action` (Ctrl+C / Ctrl+V, also Ctrl+Ins / Shift+Ins; context `WidgetWithChildrenShortcut` on the Image panel), `copy_selected`, `paste_at_pointer`, `paste_at(x, y)`. The clip is held here (this app only: not saved, not on the system clipboard).
- Right-click ROI menu (`roi_context_menu.py`): **Copy N ROIs** with a selection; **Paste N ROIs here** when something was copied and a ROI may be placed at the click (uses the click point, not the pointer).
- `shortcuts.py`: `roi.copy`, `roi.paste`, scope "Image view". The ROI table keeps Ctrl+C / Ctrl+V for cells (its scope is its own dock).

## Rules

- **Anchor:** the middle of the box round the copied centres lands on the pointer, so the arrangement is kept. One ROI: its centre.
- **Kept:** shape, rim (including rim-less), role, and links *between ROIs copied together* (remapped to the copies).
- **Not kept:** id, label, colours (a copy gets its own palette colour), group, array membership, timeline edits, links to ROIs that were not copied (the copy then measures against its own rim).
- **Geometry** is the one at the cube on screen when copying.
- **Pixel maps** move by whole pixels (`_at_position`), so a pasted map can sit up to half a pixel from the asked place.
- Ctrl+V with the pointer off the image says so in the status line and pastes nothing. The pasted ROIs become the selection (a locked ROI selection refuses and says so).

## Delete and Ctrl+D (same day; first Space, changed to Ctrl+D at the maintainer's request)

`roi.delete` (Delete) removes the selected ROIs; `selection.deselect` (Ctrl+D) drops the ROI selection (a lock refuses and says so) and the area selection. Both live on `RoiCreation`, scope "Image view" (focus inside the Image panel; text fields keep their keys). Delete does not touch the area selection or the mask (Photoshop clears pixels with Delete; here that is a separate decision: Mask > Subtract).
Ctrl+A: select all ROIs (ROIs tab open) — `roi.select_all`.
