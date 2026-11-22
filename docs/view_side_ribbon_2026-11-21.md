# View controls in a side ribbon (2026-11-21)

Maintainer request: move the Image panel's **View** tab out of the top ribbon into a ribbon of its own, like the selection toolbar on the left of the image but on the **right**, with the same controls and menus, and a right-click that opens a context menu. Edit -> Preferences chooses which of the two shows.

## What was built

- **Preferences -> Image panel -> View controls**: "In the ribbon, on the View tab" (default, what the app always did) or "On the right of the image, as a column of buttons". Applied at once, remembered (`image/view_placement`, `storage/ui_state_keys.IMAGE_VIEW_PLACEMENT`).
- **One set of widgets, two looks.** `ViewTab` is not copied. `ViewTab.set_vertical(True)` turns each row of buttons into a column (a box layout changes direction), hides the captions and draws the dividers horizontally (`RibbonSeparator.set_horizontal`). `view_placement.ViewPlacement` moves the one `ViewTab` between the tab's page (`host`) and the column (`view_ribbon.ViewRibbon`), and hides the ribbon's View tab (`ImageToolRibbon.set_category_visible`) while the column shows. Every control keeps its state when it moves (pinned by a test).
- **The column** (`panels/image/view_ribbon.py`): 26 px wide, 24 px buttons, scrolls with arrows when the panel is short (`ScrollingColumn`, as the selection toolbar). Top to bottom, as the tab left to right: Image (cursor, AR / MR, "...") | All | Masks (histogram, mask) | Chromatic (landmarks) + BG | ROIs (sample, reference, labels) + Groups | Scale bar (the ruler icon; units and colour in its right-click) | Chunks (only with an OME-Zarr).
- **An overlay's icon is its switch; the right-click holds the rest** (2026-11-21, second request, below). `panels/image/right_click_menu.py`: `MenuOpener` (event filter on the button) opens the button's menu on a right-click, **beside the button (to its left) in the column and below it in the tab**; `settings_menu` builds the menu's panel. A press within 0.25 s of the menu closing is the click that closed it (Qt hands it to the button again), so a second click closes the menu instead of reopening it. The openers are the View tab's (`ViewTab._attach`, `menu_for`), so they work in both looks; `set_vertical` tells them which side.
- `shortcuts.py`: row `mouse.toolbar.view_menu`.

## Second request, 2026-11-21: the icon is the switch, the right-click the settings

Maintainer: "make it so the visibility toggle (for every icon menu) is the menu icon itself, toggled by LMB, and RMB is reserved for the context menu, where the rest of the controls will be; put the landmarks' show-all-wavelengths under the landmarks icon; split ROI into sample ROI, reference and labels (settings in the context menu); similar split for the mask (histogram and mask)." Done in **both looks** (the widgets are the same, so a rule per look would be two code paths):

| Icon (left click = show / hide) | Right-click menu |
|---|---|
| Histogram highlight (Masks group) | colour, transparency |
| Mask (Masks group) | colour, transparency |
| Landmarks (Chromatic group) | "Every wavelength" switch (it is greyed while the landmarks are hidden, as on the Chromatic tab) |
| Sample ROIs (ROIs group) | colour, transparency |
| Reference rims (ROIs group) | colour, transparency |
| ROI labels (ROIs group) | the 3 x 3 label place, the text size |
| Group labels (Groups group) | the strip: which ROI, place, text angle, box %, smallest size (its own switch is gone from the strip) |
| Scale bar (Scale bar group, added 2026-11-22) | pixels / micrometers (greyed without a calibration, as before), colour |
| AR / MR (Image group) | the numbers (unchanged); the "..." opens them on either click |

How: the View tab's *copies* of the controls (`MaskOverlayControls`, `HistogramHighlightOverlayControls`, `RoiOverlayControls`) hand their toggle button over (`take_toggle()`); the toggle is the icon in the row, and the control, now only swatch + wedge, goes into the menu. Nothing is copied or synced: it is the very button that reported before. The landmarks and ROI-labels icons are mirrors as before (`mirror_icon_button`, new `tip_suffix`), the every-wavelength switch is the mirror of the Chromatic tab's scope button, moved into the menu. `GroupLabelMenu(click_toggles=True)` is the View tab's instance: a click toggles its `show_button` (hidden in the strip), `popup_menu` is the strip. **The Mask tab and the ROIs tab are unchanged** (their own menus open on a click as before); their copies stay in step as before.

Left over: `panels/image/masks_menu.py` (`MasksMenu`) is no longer used by anything (the ROIs tab still uses `RoiDisplayMenu`); a candidate for deletion, which needs the maintainer's OK. The tab got about 50 px wider (562 px: two more icons under Masks, two under ROIs, one fewer under Chromatic); the panel's minimum width is unchanged, the ribbons scroll. The column is 436 px of buttons.

## Decision that is the maintainer's (open)

**The Histogram and the View tab.** The Histogram plot narrows itself to the area selection / lock only while "the View tab is the open one" (maintainer's spec, 2026-10-03). With the controls in the column there is no View tab, so `ImagePanel.view_controls_open()` says **yes always** in that mode (they are always in view). Consequence: with the column the Histogram follows the selection on every ribbon tab; with the tab it still does so only on the View tab. One line to change if the other reading is wanted: `ViewPlacement.view_controls_open`.

## Not done / unseen

- Unseen by eye (no GUI was driven): column width / spacing, divider look, where a menu lands on a small screen.
- The ribbon's saved open tab "View" while in column mode: the first shown tab opens instead (the saved tab name changes with it).
- Not built: a right-click menu on the column's empty part ("Move to the View tab"); the Preferences row is the one switch.

## 2026-11-22: scale bar + group-label icon

- Scale bar: ruler icon = switch (LMB); right-click = menu with the pixels / micrometers button and the colour swatch. Same mechanism as the other overlays (`take_toggle`, `settings_menu`, `MenuOpener`). The tab got one icon narrower (3 -> 1), the column too.
- Group-label icon (`group_label_menu.label_menu_icon`): the tag is flipped top to bottom, so its tie-on hole is at the lower left, beside the three dots, and the label reads as coming out of the group. It is the same icon on the ROIs tab (one icon, one meaning). The ROI-labels icon (a plain tag) is not touched.
- Unseen by eye (also the second request): where each menu lands, the look of the two-icon Masks row and three-icon ROIs row in the column, and the flipped tag at icon size.

Tests: `tests/integration/test_lspri_rewrite_group_labels.py` (the flipped tag) and `tests/integration/test_lspri_view_ribbon.py`.

## 2026-11-22 (later): order, one chunk icon

Maintainer request: fold the Export tab's chunk-grid icon into the chunk icon's right-click menu; move the Background toggle into the Masks section under the chromatic landmarks; scale bar next to the cursor; the "all visible" buttons on top.

Order now, in the tab and (top to bottom) in the column: **All** | **Image** (cursor, scale bar, AR / MR, "...") | **Masks** (histogram, mask, chromatic landmarks, Background view) | **ROIs + Groups** | **Chunks**. The "Chromatic", "BG" and "Scale bar" sections are gone, so the tab is narrower by their captions and dividers. The Background view is still a mirror of the Background tab's button and is not part of Show all / Hide all (it swaps the image, it does not draw over it).

Chunk icon (`chunk_grid_controls.py`): left click = the dataset's own chunk grid (OME-Zarr only), right-click = a menu with a **copy of the Export tab's grid switch** (`ExportTab.chunk_grid_button`, `chunk_grid_icon_refreshed`; `ViewTab._export_grid_mirror`, the same `mirror_icon_button` as the other copies). The Export tab keeps its own button (it is where the W x H fields are, and the home of the remembered `export/chunk_grid_preview`); the menu copy follows it both ways, and the two grids stay independent (both can show at once). The icon no longer disappears for a TIFF stack: it is **greyed out** instead, and `MenuOpener(also_disabled=True)` lets its right-click menu open anyway, because the export grid applies to any dataset (a TIFF stack is the usual thing to export). Open for the maintainer: remove the Export tab's own grid icon so there is one switch in the app, not two (the Export tab loses the quick preview while typing W x H).

Tests: `tests/integration/test_lspri_view_ribbon.py` (`OrderTests`, the chunk menu), `test_lspri_label_placement_and_all.py` and `test_lspri_rewrite_image_panel.py` (the new order).

## 2026-11-22 (third): the View tab is retired, both side ribbons are rails

Maintainer request: "retire the View tab in the top image ribbon, keeping it only on the right image ribbon, also removing the option from Preferences. Also make those two (left and right) ribbons hideable like the top ribbon. Maybe dockable, so they can be moved left to right (so both the selection and the View ribbon can be on the same side)."

**Retired.** The top ribbon has no View tab, Preferences has no "View controls" row (the whole "Image panel" group went, it held only that), `view_placement.py`, `Preferences.view_placement`, `ImagePanel.set_view_placement` and `ImageToolRibbon.set_category_visible` are deleted. `ViewRibbon` now takes the `ViewTab` for good (`ViewTab.set_vertical(True)` once). A saved ribbon tab "View" (or "Histogram", its name before 2026-10-06) opens the first tab, as any unknown name does. The old key `image/view_placement` may linger in a settings file, unread. (The row look of `ViewTab` was left in at first and deleted the same day: see the fourth section.)

**Rails** (`panels/image/side_rails.py`; builder `ribbon_build.build_side_rails`). The selection toolbar and the View ribbon are each a `SideRail`: a **grip** (a 12 px strip of three dots) over the content. The row under the top bar is `SideRails` = [rails on the left] [image] [rails on the right].
- **Drag the grip** to the other half of the image: the rail docks there when the button is let go; a rubber-band frame shows the side while it is down. A drag is a move of at least `startDragDistance`; less is a click.
- **Double-click the grip**: the rail **rolls up**: only a 14 x 12 px handle is left (the image gets the room), a **click on the handle** rolls it down. The second click of the double-click that just rolled it down is ignored (`SideRail._expanded_at`). Animated from the fourth request (below).
- **Right-click the grip**: Hide / Show the ribbon, Move to the other side (only the side it is not on; icons in `menu_icons.ICONS`: `hide_ribbon`, `show_ribbon`, `move_left`, `move_right`).
- **Order**: left side: selection toolbar at the window's edge, View ribbon next to the image; right side mirrored (View next to the image, toolbar at the edge). Fixed, not draggable within a side.
- **Pop-ups follow the side** (`side_rails.beside_point`, `tooltip_point`): they open over the image, to the right of a rail on the left and to the left of one on the right. `SideRail(on_side=...)` hands the side to the content: `SelectionToolbar.set_side` (also its Layers button), `ViewTab.set_side` (`MenuOpener.set_side`).
- **Margins**: pyqtgraph's own margin on the image is 0 on the side the *selection toolbar* is on (the maintainer's 2026-11-15 "no margin between the toolbar and the image") and 9 px on the other, as today in the default arrangement (so the View ribbon keeps its 9 px gap); it follows a move (`build_side_rails.margins`).
- **Remembered** (`ui_state_keys.RAIL_KEYS`): `image/selection_rail_side` (left), `image/selection_rail_open` (True), `image/view_rail_side` (right), `image/view_rail_open` (True). Restoring saves nothing; a rubbish side falls back to the rail's default.

**The Histogram rule, decided by default, flag for the maintainer.** `ImagePanel.view_controls_open()` is now "the View ribbon is rolled down" (it was "the View tab is the open one", always yes in the column). So: rolled down (the default) = the Histogram follows the area selection / lock whichever tab of the top ribbon is open; **rolled up = the Histogram shows the whole image again**. `view_controls_open_changed(bool)` replaced `view_placement_changed(str)` and makes the Histogram redraw. One line to change if "always follow" is wanted: `ImagePanel.view_controls_open`. **Decided 2026-11-22 (maintainer: "leave it")**: it stays as it is.

Tests: `tests/integration/test_lspri_view_ribbon.py` (rewritten: `ViewRibbonTests`, `RailTests` incl. a simulated drag, `PopupSideTests`, `RememberedRailTests`, `HistogramRuleTests`), and the three tests that pinned the View tab (`test_lspri_rewrite_area_selection.py`, `test_lspri_rewrite_visual_settings_restore.py`, `test_lspri_rewrite_image_panel.py`). Unseen by eye: the look of the grip (dots, hover), the 14 px handle, the drag frame, where the pop-ups land on the left side, the 9 px / 0 margins after a move.

## 2026-11-22 (fourth): the row look is deleted, the rails roll with an animation

Maintainer answers to the open points of the third section: the Histogram rule stays as it is ("leave it"); the unused row look "can be removed"; "do animation".

**The row look is gone** (`view_tab.py`, about 70 lines in all, mostly the `set_vertical` method). The `ViewTab` is built as a column and nothing else: `set_vertical`, `is_vertical`, the six captions (`_captions`, `_all_label`, ...) and `labeled_icon_group` in this file are deleted; a section is now `ViewTab._section(content)` (the content centred across the column and pinned to the top, the same boxes as before, so the layout is unchanged); `_arrange_column` turns each row of buttons top-to-bottom once, at the end of the constructor, and the layout is a `QVBoxLayout`. `MenuOpener` lost its "below the button" mode: `set_beside(beside, toward_right=)` became `set_side(side)` and a menu always opens beside the button, over the image. Not touched: `RibbonSeparator.set_horizontal` (the column uses it), `labeled_icon_group` (the other tabs use it). The section names (All, Image, Masks, ROIs, Groups, Chunks) are only comments now; the tests find a section by its place in the layout (`OrderTests._groups`).

**The animation** (`side_rails.SideRail`): `QPropertyAnimation` on the rail's `maximumHeight`, 160 ms, `InOutQuad`, the top ribbon's way. Rolling up eases the height from where it is down to the handle's 12 px, then (at the end) hides the content and makes the rail a 14 x 12 handle; rolling down shows the content and the column's width at once (the rail is only 12 px high then), then eases up to the whole height of the row, then frees the maximum. A click during a roll turns it round from the height it had reached (not from the end). **The animation is only the picture**: `set_expanded` changes `is_expanded()`, saves, and emits `expanded_changed` (the Histogram rule) at once. `set_expanded(..., animate=False)` is for a restore (`bind_ui_state`) and the tests; a rail that is not on the screen does not animate either. `is_animating()` is the test's handle. The content is squeezed, not clipped, while the height changes (its `ScrollingColumn` shows scroll arrows for a moment, as the top ribbon's `ScrollingRibbon` does).

**Still open:** "4. Yes" to "the order within a side is fixed, not draggable" was ambiguous (make it draggable? or fine as it is?); asked again, not built.

Tests: `test_lspri_view_ribbon.py` `RollAnimationTests` (halfway by `setCurrentTime`, no waiting on the clock; the turn-round; restore and not-shown roll at once); `_wait_for_roll` where a test reads the geometry after a roll. The caption checks in `test_lspri_rewrite_image_panel.py` and `test_lspri_label_placement_and_all.py` became layout-position checks. Unseen by eye: how the roll looks (the squeezed content, the width snap at the start of a roll down / the end of a roll up).
