# The Eraser and the "Manual edit" picker retired; the ROI selection lock (2026-11-16)

Two requests of the maintainer, one session. Nothing here was seen by eye when written; the tests below pass offscreen.

## 1. Eraser and "Manual edit" retired

**Request.** "Remove from manual edit the erase tool ... This would retire the manual edit section, making it the last tool section (feature map)."
Asked on the way: does the Eraser work similar to the wand?

**Eraser vs wand (what was answered).** Both are "click one blob, the whole blob is acted on", but they look at different things. The Eraser reads
the *mask*: the 8-connected masked pixels under the click, exact, no tolerance, removed from the mask in one click, at the State scope
(Persistent / Individual), inside the area selection or lock. The wand reads the *image*: it grows a region of similar pixels (Contrast /
Brightness / Edges) into a selection; to take it out of the mask one more click is needed (Selection group > Subtract). The two agree when the
mask hugs the dark grain; they differ where the mask is larger or smaller than what the wand sees (Feature-map margin, hand-painted pixels).

**What went.** `panels/mask_grain_eraser.py`, `EraserEditPanel`, the Eraser's click routing in `CanvasInteraction` (`eraser_armed`, `erase_at`,
`_click_erase`), the `Manual edit` picker (`mask_edit_tool_picker.py`), `MaskEditTool` / `MaskEditToolModule` (`image_tools/mask_edit_tool.py`), the UI
key `image/mask_edit_tool` (an old saved value is ignored: a test pins it), `eraser_icon`, `roi_features_icon`, tests of the Eraser. `MaskEditPreview.set_tool`
is gone: the Feature map preview is simply what the Mask tab shows.
The Mask tab now reads: Selection | General | State | Visibility | **Feature map** | Scan | Auto mask | PNG. The Feature map's controls sit directly in
their section (`MaskTab._features_panel`, caption `_features_label`).
A click on the image with the Mask tab open now selects ROIs again like on every other tab (the Eraser used to take the click).

**Kept on purpose.** `raster_tools.connected_grain` (+ `tests/unit/test_lspri_mask_connected_grain.py`): nothing calls it any more; it is the natural base of a
"mask grain" wand method if the click-the-mask-blob gesture is missed. The vendored Tabler `eraser` icon: the Clear button and the frame tools still use it.

## 2. ROI selection lock

**Request.** "In the ROI tab, an additional icon (put it somewhere appropriate) that would lock the selection of ROIs, so clicking and so on would not
influence it. Maybe also in the context menu of ROI (Lock selection)."

**Screen purpose**: keep a chosen set of ROIs selected (what Spectra, Sensorgram, Group, Delete ... act on) while clicking around.
**Main action**: one toggle. **Where**: a new captioned section "Selection" at the end of the Image panel's ROIs tab (after Groups, before Tuning), one
checkable lock button (open padlock = free, closed padlock in the accent colour = locked); and a checkable "Lock selection" entry in the Image
canvas ROI menu and in the ROI / Group table's context menu (any right-click there; next to "Groups-only view").
**States**: free / locked. A lock with nothing selected keeps "nothing selected".

**What the lock does.** `SelectionModule.set_roi_selection` (the one method every selecting gesture goes through) changes nothing and emits
`roi_selection_refused`; the ROIs tab forwards it to the status line: "The ROI selection is locked, so it was not changed. Unlock it ...". Covered: a click or
rubber band on the image, a click in the table (the rows snap back to the locked ones), "Select whole group", "Select ROIs in selection", the
selection of a freshly added ROI. Right-click on an unselected ROI no longer selects it while locked: the menu then acts on the locked ROIs and its
entries say how many ("Delete 3 ROIs"); "Select whole group" and "Deselect" are greyed. Moving / resizing ROIs with the mouse still works on the locked
ROIs (the drag only ever acts on selected ones).
**What passes it**: `remap_roi_ids` (a deleted or renumbered ROI must leave or follow the selection) and `restore_roi_selection` (a session load).

**Saved in the session (maintainer, same day: "should be saved in session, to load with a new one").** It was first built as a remembered UI setting; it is not any more
(no `image/roi_selection_locked` key). `SessionState.selection_locked`; the file's `"selection"` block gets `"locked": true` **only when locked**, so a session
without a lock is written byte-identical to before; **session schema 1.9 -> 1.10** (additive: a 1.9 or older file has no key and loads unlocked; the
major-number compare means no reader changes). `app_rewrite.capture_session` reads it, `apply_session` sets it after `restore_roi_selection`, and the lock's own
signal schedules the autosave. A session load therefore brings its own lock: a blank session (new dataset) opens unlocked, a locked one opens locked with the
selection it holds. Tests: `test_lspri_rewrite_session.py` (4 new), the five tests that pinned "1.9" now pin "1.10".

**Decisions I made unasked** (flag them if wrong):
- Not undoable (like the ROI selection itself).
- The lock keeps the ROI selection even after all locked ROIs are deleted (then it keeps "nothing selected"); it never releases itself.
- Icons: the vendored Tabler `lock` / `lock-open` (the area selection's lock button uses the same glyphs, in amber).

**Code.** `selection/module.py` (lock flag, two signals, `restore_roi_selection`), `panels/roi_selection_lock.py` (the words, one source),
`panels/image/roi_tab.py` (button, Selection section), `panels/image/roi_context_menu.py` + `interaction.py` (image menu), `panels/roi_table/panel.py`
(table click snap-back, table menu), `storage/session.py` (schema 1.10), `app_rewrite.py` (capture / apply / autosave signal; session restore uses `restore_roi_selection`).
**Tests.** `tests/integration/test_lspri_roi_selection_lock.py` (16) and `test_lspri_rewrite_session.py`.
**Not built**: a keyboard shortcut for the lock (none was asked; a row in `shortcuts.py` first if wanted).
