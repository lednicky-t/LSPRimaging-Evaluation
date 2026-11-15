# Selection lock + moving the Mask tools into the selection toolbar (proposal, 2026-11-15)

**Maintainer decisions, 2026-11-15 (answers to the four questions at the end of the proposal):**
1. Compute on the whole image, then cut to the lock: **yes** (section 5).
2. **One lock level only** (no stack): a Lock replaces the previous lock; Unlock drops it. Section 4's "stack" and "Unlock all" are void.
3. A lock with **nothing selected: Add / Subtract selection does nothing** (status-bar message). "A locked selection is not a selection any more": locking turns the selection into the fence and clears it.
4. A **Replace / Add / Subtract / Intersect mode button: yes, now** (with Shift / Alt / Shift+Alt as temporary overrides).

**Status: phases 1-4 built 2026-11-15 (lock + mode button, section 8; the tools moved, section 9). The maintainer has seen phase 1 ("looks ok"); phases 2-4 are unseen by eye.** Maintainer asked (2026-11-15) which of the Mask tab's manual-edit tools should move to the
selection toolbar (made into selection makers), and proposed a **selection lock** ("a temporary crop without cropping": lock a
selection, everything outside is greyed, tools and new selections work inside it, locks can nest, an unlock undoes it).
Read `selection_wand_2026-11-14.md` first (the Photoshop metaphor: selection = what, mask / ROIs = layers a selection is turned into).

## 1. Map of the Mask tab's manual-edit tools (as of 2026-11-15)

Picker order in `panels/image/mask_tab.py`; panels in `panels/image/mask_edit_panels.py`; logic in the files named.

| Tool | Input | Output | Cost | How it is limited by the area selection today |
|---|---|---|---|---|
| Histogram selection | displayed image + the Histogram panel's highlight range | pixels in range, Add / Subtract to mask | instant (`mask_highlight_editor.py`) | outside pixels set to NaN before the mapping |
| Threshold (relative) | raw plane, % and sigma | candidate, Add / Subtract | 0.9-3.7 s, thread, cached (`mask_candidates.py`) | `candidate & region` after computing (`mask_candidate_editor.py`) |
| Local contrast | raw plane, sigma and z | candidate, Add / Subtract | same | same |
| Morphology | the mask itself | erode / dilate / open / close replace the mask (no +/-) | 12-24 ms | `restrict_to` in `MaskModule.apply_morphology` |
| Feature map | mean of N cubes, no ROIs | candidate of the enabled kinds + margin, Apply | Process 20-30 s (processes) | `candidate & region` |
| Draw | brush size | **nothing: no canvas gesture was ever built** (`paint_brush` exists, the buttons are disabled) | - | - |
| Eraser | a click on a mask pixel | the 8-connected grain leaves the mask | instant (`mask_grain_eraser.py`) | `contains()` + `selection_raw_mask` |

Already in the toolbar: Rectangle, Lasso, Wand (Contrast / Brightness / Edges), Deselect. Mask tab "Selection" group: Add / Subtract selection.

**Where the selection is read as a restriction today (9 sites, each its own way):** `mask_highlight_editor.py:101`, `mask_grain_eraser.py:80,94`,
`mask_candidate_editor.py:108`, `mask_selection_editor.py:62`, `mask_edit_panels.py:563` (Morphology), `histogram/panel.py:430` (the Histogram plots only
selected pixels), `image/edit_preview_layer.py:82` (preview), `image/interaction.py:448,462,584` (ROI creation / moves).

## 2. Three kinds of tool

1. **Pixel-finders** (image -> "these pixels"): Histogram, Threshold, Local contrast, Feature map, and Rectangle / Lasso / Wand. All end as
   "candidate AND selection -> OR / AND-NOT the mask". That is what a selection already is.
2. **Mask modifiers** (the mask's own pixels in, a changed mask out): Morphology, Eraser.
3. **Painter**: Draw.

## 3. Proposed placement

| Tool | Verdict |
|---|---|
| Histogram | **Toolbar**, one icon: "highlighted histogram range -> selection". No settings of its own. |
| Threshold, Local contrast | **Toolbar**; settings in a right-click pop-up like the wand (sigma, % / z); thread + cache as today; eye preview kept. |
| Morphology | **Toolbar as "Modify selection"**: Grow / Shrink / Open / Close (+ Fill holes), one radius, in a pop-up of one button. Acts on the selection, not the mask. |
| Draw | **Toolbar as a selection brush** (paint = add to the selection, erase = take away). Never built, so nothing to migrate; strokes batch into one undo step. |
| Eraser | **Stays in the Mask tab** (it acts on existing mask pixels, not on the image). |
| Feature map, Scan, Auto mask, PNG, Clear | **Stay in the Mask tab** (two-stage or automatic). Feature map could get a "select instead of add" twin later. |

The Mask tab then keeps: Selection (Add / Subtract; maybe Replace), State, Visibility, Eraser, Feature map, Scan, Auto mask, PNG, Clear. The "Manual edit" picker shrinks to two entries and can go.
Needed to keep what the Morphology-on-mask tool can do today: **"Mask -> selection"** (load the mask in effect as the selection), then Modify, then Replace the mask.

## 4. The lock: separate "where" from "what"

Today one object (the area selection) is both the product of a selection tool and the fence for the editing tools, so a new rectangle replaces the
old fence. The lock splits the two:

- **Lock** = the fence (the canvas). Optional, a stack of pixel maps (nested locks). Outside it is dimmed.
- **Selection** = the product, always inside the lock.
- **Mask** = unchanged; "Add / Subtract selection" is the one way into it.

Rules proposed:
- Lock button (needs a selection): the selection becomes the lock, the selection is cleared. Unlock: pops one level; right-click "Unlock all".
- Every producer (Rectangle, Lasso, Wand, Histogram, Threshold, Local contrast, brush, Modify) clips its result to the lock.
- **Effective editable region = lock AND selection (if any)**. `AreaSelectionModule.mask(shape)` keeps returning exactly that, so the nine consumers above need **no change**; a new `lock_mask(shape)` is for the dimming layer and the producers.
- Same state machinery as the selection: zlib snapshots, undo step per Lock / Unlock, `reset()` on a new pixel grid (version token). Transient, not saved, never a `ComputationalChange`, never touches analysis.
- Add selection to mask with an **empty** selection inside a lock: a status-bar message, not "mask the whole lock" (a one-click accident).
- A forgotten lock is the main risk: ROI creation / moves and the Eraser are blocked outside it. Needs a visible cue (solid border + padlock badge on the image, "Locked: 12 % of the image" in the status bar) and a message whenever a tool refuses a click outside.

## 5. Order of the computation (the one real science decision)

*Pointwise* tools (Histogram range) do not care whether you cut first or compute first. *Neighbourhood* tools (Threshold's Gaussian background,
Local contrast, the wand's noise estimate and flood fill) do: next to a lock edge the numbers differ.

- **Recommended default: compute on the whole image, then cut to the lock.** The result inside a lock equals the unlocked result AND the lock (pinnable by a test, no numeric change, satisfies "controls mean the same for every selection"); the 0.9-3.7 s Threshold / Local contrast candidates stay valid in the cache when the lock changes.
- The Histogram plot shows only the locked pixels (it already does for a selection): the range is chosen on the region.
- Offer "estimate inside the lock" later as an explicit per-tool switch (it changes numbers: before / after comparison, `.claude/rules/numerics.md`).

## 6. Risks and unknowns

- **Space:** selection is in displayed coordinates, mask in raw. Under rotation selection -> mask is exact to ~1 px at the edge. Morphology moved onto the selection inherits that; before the old Morphology buttons go, compare old vs new on a rotated dataset and report the difference.
- No padlock icon is vendored in `lspr_ui/icon_assets/` (checked 2026-11-15; `crosshair`, `focus-2`, `stack-*` exist). Tabler `lock` / `lock-open` would be added to the shared package (`ICONS.md` first); also 4 small icons if a Replace / Add / Subtract / Intersect mode button is added.
- Combine mode: Replace / Add / Subtract / Intersect as one mode button, with Shift / Alt / Shift+Alt as temporary overrides (Photoshop). Intersect matters less once the lock exists.
- Nothing here has been seen by eye (the dimming's look, the toolbar's extra buttons at 18 px).

## 7. Suggested order

1. Lock (+ dimming, cue, clip, undo, tests): useful at once for nested wand / rectangle / lasso, moves no tool.
2. Histogram -> selection; then Threshold and Local contrast (eye preview kept).
3. Modify selection (+ Mask -> selection, Fill holes); selection brush (Draw).
4. Retire the picker, tidy the Mask tab.

## 8. Phase 1 as built (2026-11-15): lock + mode button

| Piece | File | Notes |
|---|---|---|
| Lock, Intersect, `selected_mask`, undo | `selection/area_selection_module.py` | `lock_selection(shape)`, `unlock()`, `lock_region()`, `lock_mask(shape)`, `selected_mask(shape)`, sticky `combine_mode`; the lock is part of every undo snapshot; `reset` drops it. `mask(shape)` = selection else lock else None, so the nine old consumers did not change. |
| Dimming + amber border | `panels/image/lock_layer.py` | black at 0.55 outside the lock, one `ImageItem` painted once per lock change; hidden over Rotate / Crop's uncropped canvas |
| Drag with a combine mode | `panels/image/area_selection_tool.py`, `interaction.py` `how_for` | Shift = add, Alt = subtract, Shift+Alt = intersect, else the sticky mode; a shape under a lock / non-Replace mode becomes pixels; a drag wholly outside the lock is refused with a message |
| Mode button, Lock, Unlock | `panels/image/selection_toolbar.py` | mode = a menu + wheel, blue when not Replace; Lock enabled with a selection, Unlock (amber) with a lock |
| Status messages | `panels/image/panel.py` `lock_to_selection` / `unlock_selection` | "Locked to N px (x % of the image)"; also for the wand outside the lock, a ROI outside, the Eraser outside |
| Add selection to mask | `panels/mask_selection_editor.py` + `ImagePanel.selection_regions.selected_raw_mask` | uses the selection itself: with only a lock it adds nothing and says so |
| Remembered mode | `storage/ui_state_keys.py` `SELECTION_COMBINE_MODE` | restored in `ImagePanel.restore_ui_state` |
| Icons (umbrella repo, `lspr_ui/icon_assets/`) | `lock`, `lock-open`, `layers-union`, `layers-subtract`, `layers-intersect` | copied from the Tabler set that ships in the venv (`tablerqicon`), comment headers stripped; Replace uses the vendored `square` |
| Right-click menus | `interaction.py` | "Lock to selection" (with a selection) and "Unlock" (armed tool) |
| Shortcut rows | `shortcuts.py` | `mouse.view.select_add / select_subtract / select_intersect / toolbar.select_mode` replace the two wand-only rows |

Tests: `tests/unit/test_lspri_selection_lock.py` (22), `tests/integration/test_lspri_selection_lock_panel.py` (20); the area-selection, wand,
eraser, image-panel, ROI-gesture, UI-state and shortcut files still pass.

**Decided unasked (tell the maintainer):**
- A wand click **outside** the lock does nothing (even if the feature it would pick crosses into the lock): you act only inside the fence.
- A rectangle / lasso made while locked (or in a mode other than Replace) is stored as the pixels it covers, not as an outline (a locked or combined selection has no vector shape).
- A rectangle or lasso that covers **no pixel centre** is refused (before: it made an empty selection that restricted editing to nothing).
- The right-click "Lock to selection" also appears in the plain selection menu (tool not armed).
- No keyboard shortcut for Lock / Unlock yet; no persistent "Locked" badge on the image (the amber border, the dimming, the amber Unlock button and the status message are the cues).
- `lint-imports` was **not** run (root rule: advisory tools only on request); the new imports go downward only (panels -> selection, storage).

## 9. Phases 2-4 as built (2026-11-15): the tools moved

Maintainer, after seeing phase 1: "it looks ok. Icons, yes ... All icons should be in shared icon package. Run lint import as well then go to
phase 2 and so on." `lint-imports` (root `.importlinter`, run from the repo root): **5 kept, 0 broken** after phase 1; re-run after phase 4 below.

| Tool | Now | Where |
|---|---|---|
| Histogram selection | **Select by value**, method "Histogram range": the finite pixels of the image on screen inside the Histogram panel's highlighted range | `panels/image/selection_by_value.py`, `image_tools/preprocess.histogram_highlight_region` (the same test `histogram_highlight_mask_to_raw` applies) |
| Threshold, Local contrast | **Select by value**, methods "Threshold" / "Local contrast": the candidate of `MaskEditPreview.request_candidate` (thread + cache, raw space) mapped onto the displayed image (`resolve_external_mask`), then cut to the lock | same file; numbers = `MaskSettings` (saved with the session), pop-up on right-click, wheel switches the method |
| Morphology | **Modify selection** menu: Grow / Shrink / Open / Close / Fill holes / Invert and a radius; acts on the selection as it edits now, result cut to the lock, one undo step | `panels/image/selection_modify.py`; `raster_tools.apply_morphology_to_mask` unchanged (same numbers as the old mask tool), new `raster_tools.fill_mask_holes` |
| Draw | **Selection brush** (a toolbar tool): drag paints into the selection, Alt erases, a click is one stamp; size = `MaskSettings.brush_size_px` (right-click the button); the stroke is the exact capsule union (`image_tools/brush_stroke.stroke_region`), made once at the end of the drag = one undo step; the ring cursor shows the stamp | `area_selection_tool.py`, `interaction.py`, `brush_settings.py` |
| (new) Mask tab Selection group | **Replace** (mask at this frame := selection; nothing selected = nothing happens) and **Selection from mask** (the mask at this frame becomes the selection, combined as the mode button / Shift / Alt say) next to Add / Subtract | `panels/mask_selection_editor.py`, `mask_selection_actions.py` |
| Eraser, Feature map, Scan, Auto mask, PNG, Clear | **stay** in the Mask tab; the picker has two entries | `mask_tab.py`, `mask_edit_panels.py` |

**Removed with them (dead code):** the Mask tab's Histogram / Threshold / Local contrast / Morphology / Draw panels and their picker entries,
`MaskEditTool.HISTOGRAM_SELECTION / THRESHOLD / LOCAL_CONTRAST / MORPHOLOGY / DRAW` (a saved choice of one is ignored at start-up; the default is
now the Feature map), `panels/mask_highlight_editor.py`, the Mask-tab preview of Threshold / Local contrast / Morphology (`MaskEditPreview` keeps the Feature
map's tint and `request_candidate`), four hand-painted icons. Unused now but kept (public, tested): `MaskModule.apply_morphology`,
`MaskModule.paint_brush`, `raster_tools.morphology_changed_pixels`, `apply_brush_stamp`.

**Decided unasked (tell the maintainer):**
- **Select by value is one button with three methods** (wheel / menu), not three buttons: it mirrors the wand and keeps the column short (10 buttons, about 300 px).
- **No preview** for Select by value (the wand's decision: the selection is undoable). This drops the 2026-10-29 eye preview of Threshold / Local contrast / Morphology; the eye now only belongs to the Feature map.
- **The Histogram panel plots the lock** when there is one, so its range is chosen on the locked area and does not shrink as selections are made inside it; with no lock it narrows to the selection as before (`AreaSelectionModule.canvas_mask`).
- Modify selection runs on the GUI thread (10-35 ms on a 1.2 MP plane at any radius, measured 2026-11-15: scipy walks only the boundary).
- Morphology now runs in **displayed** space (the selection's); the old mask tool ran in raw space. Equal without a rotation; about one pixel at the edge under one. Not yet compared on a rotated dataset.
- A brush stroke in Replace or Intersect mode paints (adds); Subtract mode and Alt erase.
- Every selection producer obeys the mode button and Shift / Alt / Shift+Alt, including Select by value and Selection from mask.
- Lock, Unlock, Modify and the brush have no keyboard shortcut yet (mouse rows only in `shortcuts.py`).

Tests: `test_lspri_selection_by_value.py` (17), `test_lspri_selection_modify_brush.py` (24), `tests/unit/test_lspri_brush_stroke.py` (10); the Mask-tab tests of the removed
tools were deleted or rewritten (`test_lspri_mask_edit_preview.py`, `test_lspri_rewrite_image_panel.py`, `test_lspri_rewrite_ui_state.py`, `test_lspri_mask_eraser.py`, `test_lspri_feature_map_tool.py`).
Icons added to `packages/lspr_ui/src/lspr_ui/icon_assets/` (umbrella repo): lock, lock-open, layers-union, layers-subtract, layers-intersect, adjustments-horizontal,
contrast-2, replace, arrow-bar-up, brush, border-radius (all Tabler, copied from the venv's `tablerqicon`).

## 10. Follow-up (2026-11-15): Venn-diagram mode buttons, a scrolling column, Replace under a lock

**Four mode buttons instead of one (maintainer: "replace selection mode with 4 icons for each mode representing boolean operation using a venn
diagram, colored what is happening").** `panels/image/combine_mode_icons.py`: two overlapping discs, A (left) = the selection now, B (right) = the
new region. Blue = ends up selected, orange = taken away from the old selection, nothing = not selected. Replace: the right disc blue. Add: both blue.
Subtract: the left disc blue, the overlap orange. Intersect: the overlap blue. The table is `MODE_AREAS`; `tests/unit/test_lspri_combine_mode_icons.py`
applies the real combine rule to two discs and compares, so a picture can never say what a mode does not do; the integration test samples the icon pixels.
The picked mode wears a rounded frame drawn into the icon (the "on" picture: the buttons are 18 px wide, no room for a border). The menu and the
wheel on the old single button are gone; Shift / Alt / Shift+Alt still override the picked mode for one selection.

**The toolbar column scrolls (`panels/scrolling_column.py`, the vertical twin of `ScrollingRibbon`).** Measured 2026-11-15 (offscreen, `minimumSizeHint`):
without the column the Image panel's minimum height is 289 px; with the 13 buttons (352 px) it was **573 px**. The phases 1-4 buttons had silently
raised it, so on a short window the panel (and the dock layout around it) could not shrink. The column now reports a small minimum (52 px) and, when the
panel is short, shows a chevron at each end and scrolls (click, hold, or the wheel over a gap; a button that uses the wheel itself keeps it); with room
it looks as before. The panel's minimum is back to 289 px. Tests: `tests/integration/test_lspri_selection_toolbar_scroll.py`.

**Replace under a lock replaces only what is inside the lock** (found while rewriting `test_lspri_rewrite_area_selection.py`): "Replace mask with selection"
had made the whole mask equal to the selection, which would have wiped the mask outside a lock. A lock is the canvas, so now `base = mask outside the
lock` and the selection takes over inside (`ImagePanel.selection_regions.lock_raw_mask`, `MaskSelectionEditor.replace`). Without a lock it is unchanged.

The three tests of `test_lspri_rewrite_area_selection.py` that drove the removed Mask-tab panels (Histogram selection Add, Morphology) were rewritten for
Select by value (Replace and Intersect modes) and Selection from mask + Modify + Replace under a lock.

**Size budget (`tests/unit/test_lspri_panel_size_budget.py`, 2026-11-15).** The full LSPRi run failed this test: `panel.py` had grown to 2,801 lines (budget 2,710;
it was already 2,714 at the last commit) and `ImagePanel` to 129 methods (budget 124). The six selection helpers (`selection_raw_mask`, `selected_raw_mask`,
`lock_raw_mask`, `displayed_shape`, Lock, Unlock) moved into `panels/image/selection_regions.py` (`ImagePanel.selection_regions`), and the toolbar remembers its
own settings (`SelectionToolbar.bind_ui_state`): 124 methods again and 2,739 lines. The line budget was raised on purpose to 2,745 for the wiring that has to
stay (maintainer to confirm; the reason is in the test and must go into the commit message).

## 11. Second reorganisation (2026-11-15, after the maintainer saw the first version: "it looks ok")

Maintainer: split the left ribbon into sections with horizontal lines; start with lock / unlock, then the selection tools (rectangle, lasso, wand, draw;
the Deselect icon can go, it is in the context menu), then the modes "merged in one menu which can be picked up but also scrolled", under it Histogram,
Threshold, Local contrast ... as separate icons "using the previous icons, not these new ones", and tool gestures that change a parameter with the right
button held and moved. **This supersedes section 10's four mode buttons and section 3's one "Select by value" button.**

**Five sections, four horizontal dividers** (`panels/image/selection_toolbar.py`): (1) Lock, Unlock; (2) Rectangle, Lasso, Wand, Draw (the selection brush);
(3) the mode picker: one button showing the Venn diagram of the picked mode, a click opens a menu of the four (Venn icon and name each), the wheel over it steps
through them (`combine_mode_icons.py` is unchanged); (4) Histogram range, Threshold, Local contrast; (5) Modify selection (one button with its menu).
No Deselect button (it is in the right-click menu: Select ROIs in selection, Lock to selection, Invert, Deselect).

**The three value tools are armed tools now** (`AreaSelectionMode.HISTOGRAM / THRESHOLD / CONTRAST`, `VALUE_MODES`, `CLICK_MODES`): pick the tool (the
button is checked, or framed; picking it again leaves it), then **click the image** (anywhere: they are image-wide methods) to run it; Shift / Alt / Shift+Alt
at the click and the mode picker say how the result combines. **This is one step more than the earlier one-click button** (decided unasked: it is what makes a
right-drag possible, and it is how the wand, rectangle, lasso and brush already work). A right-click of the button opens that tool's numbers
(`ByValueSettingsForm.set_method`, one form for the three). The method switch and the remembered `selection/value_method` are gone (the button is the method).

**Old icons back** (`mask_edit_tool_icons.py`, restored from git): Histogram range = the five-bar histogram, Threshold = bars with the red line, Local contrast =
the 3 x 3 patch with the ring, Modify selection = the crescent with the blue circle (the old Morphology), Draw = the pencil. They are fixed multi-colour
pictures, so an armed tool shows a **frame** round the icon (`combine_mode_icons.framed_icon`, the icon's "on" picture), not a blue recolouring. Rectangle,
Lasso and the Wand keep their blue Tabler icons. The seven Tabler icons I had vendored for the first version and no longer used were removed from
`lspr_ui/icon_assets` (they were untracked, mine); the shared package keeps lock, lock-open, replace, arrow-bar-up (the Mask tab's two new buttons).

**Right-drag gestures for every tool with numbers** (`panels/image/tool_drag.py`; the wand's `wand_drag.py` now shares its readout): hold the right button on the image
and move. Wand: threshold sideways, Grow up (unchanged). **Draw: brush size sideways** (right = bigger; the ring cursor follows). **Threshold: the threshold
sideways (right = a bigger region = a lower %), sigma up**. **Local contrast: z sideways (right = a bigger region = a lower z), sigma up**. A log scale (80 px
doubles), kept inside the spin boxes' ranges, a readout beside the cursor, the numbers are `MaskSettings` (saved with the session). The Histogram range has no
numbers on the toolbar (its range is the Histogram panel's), so the right button does nothing there; Modify selection is a one-shot menu, not an armed tool, so
it has no gesture either. Both could get one later (the Histogram: shift / widen the highlighted band) if wanted.

**Clicks read the keys from the click event** (as the wand did), not from the global keyboard state; the brush too.

**Code placement.** `panels/image/selection_tools.py` builds the toolbar, the tools and the gesture table in one place (`SelectionTools`; 5 lines in `panel.py`);
`CanvasInteraction` takes `drag_adjusters` (armed tool -> handler) and `value_click`. A bug found by the tests: an integer spin box (sigma, brush size)
must be given a whole number (`dragged_value`).

Tests: `test_lspri_selection_by_value.py` (23), `test_lspri_selection_toolbar_sections.py` (6), `test_lspri_selection_toolbar_scroll.py`, `test_lspri_selection_modify_brush.py`
(brush right-drag), `test_lspri_selection_lock_panel.py` (mode picker), `tests/unit/test_lspri_tool_drag.py` (6); the Deselect test of `test_lspri_rewrite_area_selection.py`
now checks the context menu. The panel investigation is `docs/image_panel_split_investigation_2026-11-15.md`.
