# Image panel layers (2026-11-16)

Maintainer request: Photoshop-style layers in the Image panel. Three fixed layers, bottom to top: **Image (frame)**, **ROI**, **Ignore mask**. A layer can be hidden. One layer is **active**, and the selection tools that read something work on the active layer only.

## Decisions (maintainer, 2026-11-16)

- The layer manager is one **Layers button** pinned at the top of the selection toolbar, under the View eye. Its face shows the **active layer's icon** with a small layer stack in the corner. The **wheel** over it changes the active layer; a **click** opens the pop-up: an eye and a name per layer, and clicking a name makes that layer active.
- At first the maintainer asked for "tools work on the visible layer", then changed to an **active layer** ("that's actually the better approach").
- Wand on the ROI layer: **whatever was clicked**: the sample disk, or the reference ring when the click is on the ring.
- Histogram range / Threshold / Local contrast: **greyed out unless Image is active**, with the reason in the tool tip. A value tool that is armed when another layer becomes active is put down, and the status line says so.

## Decisions I made unasked (open for the maintainer)

- The active layer starts as **Image**. **Hiding a layer never changes the active one** ("a control does exactly what it says").
- The wand on a **hidden active layer does nothing** and the status line says why (Photoshop also refuses to paint on a hidden layer). Value tools likewise refuse while the Image layer is hidden.
- Rectangle, Lasso, Draw (brush) and Modify are shapes: they act the same on every layer.
- A hidden image shows a **flat dark grey** (72/255), not the "no data" checker. The checker already means "this pixel has no value", so the two must not look alike.
- The ROI eye switches the sample circles and the reference rings **together**. The layer counts as shown while either is shown. With only the rings shown, the ROI wand finds rings only.
- **Stacking change:** the mask tint now sits above the ROI circles (z 0.5). The tool preview tint (0.6) and the lock's dimming (0.7) stay above the mask. The histogram highlight tint stays at z 0 and is now *under* the mask (it used to be above it). The ROI labels (screen layer, z 900) stay on top of everything.
- The Image layer's eye and the active layer are **remembered** (`image/layer_image_visible`, `image/active_layer`). The mask and ROI eyes are the existing toggles, which already remember their state.

## Where

| Piece | File |
|---|---|
| Layer state, `ImageCover`, z order | `panels/image/layers.py` |
| Built from the panel's toggles | `panels/image/layers_build.py` (`build_layers`, one call in `ImagePanel`) |
| Button + pop-up | `panels/image/layers_button.py` |
| Wand per layer, `mask_blob` | `panels/image/layer_wand.py` |
| ROI disk / ring under a point | `RoiQueries.region_at` (`roi_queries.py`) |
| Mask as drawn | `TintOverlays.displayed_mask` (`tint_overlays.py`) |
| Value-tool gating | `SelectionToolbar.set_value_tools_allowed`, `SelectionTools.run_value_tool` |
| Icon | `photo.svg` vendored into `lspr_ui` |
| Tests | `tests/integration/test_lspri_image_layers.py` (14) |

## How it works

- **Hiding the image:** the frame item and the no-data checker are set to opacity 0, so they keep their size and the view range does not change; Qt skips painting an item at opacity 0. A 1x1 grey `ImageItem` stretched to the image size is shown under everything. The frame keeps rendering, so the image wand still has it when the layer comes back.
- **Mask wand:** reads the mask exactly as the tint draws it (`resolve_external_mask` on the tint's cached inputs), shown or not. An 8-connected OpenCV flood fill that visits only the blob gives the same result as `raster_tools.connected_grain`; a test pins this.
- **ROI wand:** `roi_at` for the disk (nearest centre wins). Otherwise the nearest ROI whose effective ring contains the point. The pixels come from the scan's own rasterisers (`rasterize_sample` / `rasterize_reference` at the frame's chromatic affine).
- Every result is combined like any selection (mode button, Shift / Alt), cut to the lock, and is one undo step.

Unseen by eye.
