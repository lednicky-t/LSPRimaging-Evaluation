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
