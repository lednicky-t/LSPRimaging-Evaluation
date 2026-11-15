# Selection toolbar and magic wand (2026-11-14)

Maintainer request: a Photoshop-like magic wand to select features by clicking them, with sensitivity controls; "put it
in both select sections (ROI, mask) so the selection can later be made a mask or an ROI". Then: "move the selection
tools separately... selection tools work on the image doing selections. The mask is like a layer (bitmap) over the
image, the ROIs another layer (geometries, ROI maps, sample-reference links)" - the Photoshop metaphor. Placement chosen:
an always-visible toolbar beside the image. All three wand methods at once. Mask edits undoable, painting too.

## What was built

| Piece | File | Notes |
|---|---|---|
| Wand computation (no Qt) | `image_tools/selection_wand.py` | 3 methods, noise units, no size limit; in the `lspri-science-no-qt` contract |
| Pixel-map selection | `selection/area_selection_module.py` | `combine_region(region, replace/add/subtract)`, `region()`; mode `WAND`; every change an undo step, `reset()` for a new grid |
| Pixel-edge outline | `panels/image/area_selection_tool.py` `region_outline` | outlines the smaller side; 13-18 ms on 1275 x 800 |
| Click -> thread -> selection | `panels/image/wand_task.py` `WandTask` | plain thread, prepared-image cache, one pending click |
| Toolbar + settings pop-up | `panels/image/selection_toolbar.py` | Rectangle / Lasso / Wand / Deselect; the wand's settings open on a right-click of the wand button (2026-11-15, was a gear button below it); settings remembered (`selection/wand_*`) |
| Click routing, cross cursor | `panels/image/interaction.py` `_click_wand` | Shift = add, Alt = subtract; rows in `shortcuts.py` |
| Right-drag: threshold + Grow | `panels/image/wand_drag.py`, `image_controls.py` `set_right_drag_handler` | readout beside the cursor, no preview; wheel over the wand button switches the method (`selection_toolbar._ToolButton`) |
| Mask tab "Selection" group | `panels/mask_selection_editor.py`, `panels/image/mask_selection_actions.py` | Add / Subtract selection, one undo step each |
| Undoable mask edits | `image_tools/mask/module.py` `_commit_edit` | apply_candidate / apply_morphology / paint_brush |

The per-tab Select pickers were removed from the Mask, ROIs and View tabs; `panels/image/select_group.py` and
`general_group.AreaSelectionPicker` were deleted 2026-11-15 (maintainer OK).

**2026-11-15 follow-ups (maintainer):**
- Right-click with a selection tool armed: **Select ROIs in selection** - the ROIs inside or partly inside the
  selection replace the ROI selection (`interaction.select_rois_in_selection`, hit test `roi_queries.rois_touching`:
  exact overlap of each ROI's sample disc with the selected pixels' squares; an inverted selection takes the ROIs
  touching the outside). Disabled with no selection.
- The toolbar is as narrow as the icons: 18 px, no frame margin (was 28 px with the icon 5 px off the left edge).
  The gear's menu arrow is hidden (it would not fit). Then "not symmetric": the image's pyqtgraph layout kept a 9 px
  left margin (plus the view box's 1 px), so `panel.py` sets `ci.setContentsMargins(0, 9, 9, 9)`. What remains left
  of the icons is the dock card's 3 px gap and the separator between docks, the same on every panel edge.

**2026-11-15, second round (maintainer): gestures, undo, no Max size.**
- **Right-drag on the image with the wand armed** sets the threshold (sideways, **right = a bigger region** for every
  method: for Contrast that lowers the number; a log scale, 80 px = x2) and Grow (up = more, 6 px per pixel), with a
  readout beside the cursor and **no preview** (maintainer: undo instead). `panels/image/wand_drag.py`
  (`WandDragAdjuster`, `dragged_threshold`, `dragged_grow`), `ImageViewBox.set_right_drag_handler`,
  `CanvasInteraction.on_right_drag`. It writes the settings form's own spin boxes, so the pop-up shows the same numbers
  and they are remembered. A right *click* without movement still opens the image menu (pyqtgraph tells click from
  drag; the view box ignored right drags before). Why these two: they are the only numbers that change the result of
  the same click and need no new "prepare" (smoothing and background width redo the 0.1-2 s step, so they cannot follow
  a mouse, and they are set once per dataset).
- **Wheel over the wand button** switches the method (Contrast / Brightness / Edges, wrapping round), a tool tip names
  it; the button's own tool tip always shows the method in use. A smooth wheel is summed to whole notches. The wand
  button's **icon shows the method**: the Tabler wand with a small glyph in place of its second sparkle - Contrast =
  `wand-contrast` (half-filled disc), Brightness = `wand-brightness` (sun), Edges = `wand-edges` (dotted outline). Drawn by
  hand in Tabler's style (the glyphs are not Tabler icons, so the names are ours), vendored into `lspr_ui/icon_assets/`
  (umbrella repo). A first try with plain Tabler `contrast` / `sun` / `circle-dotted` was dropped: without the wand the
  button no longer said "wand". A shorter baton with a bigger badge was tried too and looked like a stub. `wand.svg` stays
  vendored (the toolbar no longer uses it).
- **The area selection is undoable** (every change: rectangle, lasso, wand replace / add / subtract, invert,
  deselect; one step each, on the shared stack, so Ctrl+Z walks back through selection and mask edits in the order made).
  Reverses the old rule "selection is never undoable" for *this* selection only (the ROI / cube `SelectionModule` stays
  non-undoable). Steps hold zlib-packed snapshots (a few KB for a particle), and `reset()` (rotate / flip / crop) drops the
  selection without a step and voids older steps (skipped with a status-bar message): a restored pixel map of the old
  grid would not fit the new image. Record of the design: `selection/area_selection_module.py` docstring.
- **Max size removed** (it was only a leak guard: with undo a wand that ran into the background is one Ctrl+Z away). The
  Brightness / Contrast regions are just the connected component. **Edges** used Max size as the reach of its search
  box; it now looks at the whole image. A plain watershed on 1.2 MP took ~1.07 s per click (vs ~80 ms in the box);
  skipping the pixels deep inside the background marker (`mask=~deep_background`, they never decide a border) gives the
  **same labels** in 86 ms, so a click costs ~0.1 s (measured 2026-11-15, synthetic 1000 x 1200 plane). Before / after on
  47 clicks per method (random + disk / dust / scratch centres): every region the old code accepted is identical
  (0 differences, all three methods); the ones it refused as too big are now returned (Contrast 25, Brightness 37, Edges
  37 of 38). A click on the plain background of the Edges method now selects the connected open ground (it was
  refused). Known, unchanged: a one-pixel start exactly on the crest of a large ring's edge gives ~1 px (the background
  floods over the crest first); a click just inside the edge works. Enclosed islands still count as part of the region
  around them for Edges (by design), so Fill holes has no effect there.

## The three methods (`selection_wand.py` docstring has the details)

- **Contrast to background** (default): smoothed image minus a wide Gaussian background; pixels standing out by
  >= threshold x noise on the clicked pixel's side. Bigger threshold = smaller region.
- **Brightness**: smoothed value within tolerance x noise of the clicked value (classic wand). Bigger = bigger.
- **Edges**: grow through edge-free pixels, then a watershed on the gradient puts the border on the edge's ridge; a
  click on a slope (small rounded feature) starts from the click alone. "Outside" markers are only edge-free ground
  that reaches the side of the image (2026-11-15; it was the side of a search box sized by Max size), so disks enclosed
  by a dust shadow are not counted as background.

Thresholds are in noise sigmas of the image the wand looks at: pixel noise from the MAD of neighbour differences, times
the exact white-noise gain of the smoothing (and Sobel) filter. There is no size limit (see the 2026-11-15 round above;
until then a region over Max size was refused with a message). Feathering is not offered: masks are on/off per pixel (soft masks would be a schema and scan change); Grow is
the binary counterpart.

## Measurements

Synthetic picture (unit test `tests/unit/test_lspri_selection_wand.py`): dust radius 15 px -> Contrast 17.9 (catches
the soft rim), Edges 14.4; particle radius 6 px -> Contrast 9.6 (halo), Edges 5.6.

Real data (`04_Bulk_sensitivity_..._c80IZ_u16.ome.zarr`, cube 0, wavelength index 13, 1275 x 799, noise 419 counts at
median 45 600), smoothing 1 px, default settings:

| Click | Brightness | Contrast | Edges |
|---|---|---|---|
| Array disk (diameter ~40 px) | 723 px (inner part only) | 1378 px, eq. r 20.9 (whole disk) | 1249 px, eq. r 19.9 |
| Compact dust speck | 3 px | 41 px (good outline) | 23 px |
| Long faint scratch | 28 px or leak | leak -> refused (773 583 px) | 5-21 px |

Cost: first click on a new image 0.4 s (Brightness / Edges) or 1.5 s (Contrast: the background Gaussian), later clicks
on the same image 0.15-0.3 s (labelling a 1 MP plane), all off the GUI thread.

**Known limit (real data):** a long faint scratch that runs across the array touches the dark disks, so Contrast's
"darker than background" region joins the whole disk network and is refused; Edges finds only a few pixels (weak
edges). Nothing wrong is selected, but scratches cannot be picked yet. Ideas, not built: a band on the flattened image
(pixels whose contrast is *close to the clicked pixel's*, excluding the much darker disks); an Intersect modifier
(Shift+Alt) so a lasso can fence the wand in.

## Decided unasked (tell the maintainer)

- The wand looks at the image on screen (after rotation / crop / background flattening). Averaging wavelengths or cubes
  is not offered: without chromatic alignment the planes do not line up and the mean blurs edges.
- No hover preview (each preview would be a thread round trip per mouse move); the outline appears after the click.
- Rectangle / Lasso still replace the selection; only the wand combines with Shift / Alt.
- Invert stays in the right-click menu (no vendored invert icon; adding one touches `lspr_ui`).
- Clearing all masks and loading a PNG stay not undoable (the Clear dialog says so).

## Open decisions (maintainer, "decide later")

- **ROI from selection**: what the ROI's reference region is (ring around the shape? none? choose each time?), and
  whether a selection of several blobs makes one ROI per blob or one ROI for all. ROIs of geometry type `"mask"` exist
  in the model and the scan but no UI has made one before; check scan == reference implementation on them first.
- Faster Contrast preparation (a binned background, as the Background module does) - performance, priority 5.
