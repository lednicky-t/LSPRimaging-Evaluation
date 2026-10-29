# Mask-tab edit preview, and Threshold / Local contrast Add / Subtract (2026-10-29)

Maintainer request: "mask editing tools are not showing the changes that would come with applying the tool (pixels of
selection). It is as for Threshold, Local contrast or Morphology." Built the same day, **uncommitted, not yet seen by eye**.

## What was found

| Tool | Before | Now |
|---|---|---|
| Histogram selection | live tint already (View -> Masks -> Histogram) | unchanged |
| Threshold | no preview; **Add / Subtract disabled** ("not wired yet - needs a background worker") | preview + Add / Subtract |
| Local contrast | same as Threshold | same as Threshold |
| Morphology | worked, applied at once, nothing shown before | hover a button = preview of that operation |

The maths for Threshold / Local contrast already existed (`raster_tools.create_relative_contrast_mask`,
`create_local_contrast_mask`) but nothing called it: it needs the **raw** plane and Gaussian filters, which is too slow for the GUI
thread. So the work was a worker, a preview layer, and (maintainer's choice) wiring the Add / Subtract buttons to the same pixels.

## Decisions (maintainer, 2026-10-29)

- Enable Add / Subtract together with the preview (the previewed pixels are exactly what the buttons merge).
- Morphology: the preview shows on **hover** over a button; a click still applies at once (no select-then-Apply step).

Decided without asking (cheap to change): the preview tints **all pixels the tool selects** (as the Histogram highlight does), not
only those that would differ from the mask; one fixed colour (theme `figure_mask_color`, the stable app's preview colour) at 0.55
opacity; shown only while the Mask tab is open, never while Play runs; the eye toggle is one shared state, remembered
(`image/mask_edit_preview`, default on).

## Where it lives

| Piece | File |
|---|---|
| pure: pixels an operation flips | `image_tools/mask/raster_tools.py` `morphology_changed_pixels` |
| worker: off-GUI-thread compute, cache, stale/bounded rules | `image_tools/mask_candidates.py` `MaskCandidates` |
| what to preview, when (display state + requests) | `panels/mask_edit_preview.py` `MaskEditPreview` |
| Add / Subtract | `panels/mask_candidate_editor.py` `MaskCandidateEditor` |
| the tint on the image | `panels/image/edit_preview_layer.py` `EditPreviewLayer` (created in `panel.py` `_build_ui`, refreshed in `_redraw`) |
| the eye button | `panels/image/edit_preview_toggle.py` |
| the three panels | `panels/image/mask_edit_panels.py`; owner `mask_tab.py`; remembered key `storage/ui_state_keys.py` `IMAGE_MASK_EDIT_PREVIEW` |
| tests | `tests/unit/test_lspri_mask_morphology_changed.py`, `tests/integration/test_lspri_mask_edit_preview.py` |

## Rules it follows (and why)

- **Plain `threading.Thread`**, never `QThreadPool` (dataset/zarr invariant); the dataset read uses `load_plane_uncached`, so the
  Image panel's plane cache is not evicted.
- **Measured before choosing a thread** (1.2 MP plane, dev laptop): Threshold sigma 48 px 0.9 s, sigma 200 px 3.7 s; Local contrast
  sigma 5 px 0.2 s, sigma 50 px 1.9 s; morphology 12-24 ms at any radius up to 100. While a worker ran, the longest stall of the main
  thread was 8 ms (scipy releases the interpreter lock), so a thread is safe here; no helper process needed.
- **Bounded work**: at most one preview computation runs; a newer waiting preview replaces the older waiting one; a number change
  is debounced 300 ms, a frame change 250 ms; a button press (not a preview) always starts at once and joins a running computation
  of the same key. Same candidate for the preview and the buttons (one cache, six entries of ~1.3 MB).
- **Stale results are dropped**: new numbers, another frame, a replaced dataset, Play, another tab, the eye off.
- `image_rendered` handler is O(1) (a compare and a timer start), as the slideshow rule requires.
- Area selection: the preview is cut to it on screen (cheap); Add / Subtract cut the raw-space pixels to it, as Morphology and the
  Histogram selection do. Under a rotation the two cuts can differ by a pixel along the selection's edge (nearest-pixel scatter).

## Known limits / open points

- **Threshold and Local contrast are slow at large sigma** (seconds). The tint is hidden while the new pixels are made and the
  status row says "Computing the ... preview...". One scipy call cannot be cancelled; a result nobody wants is dropped on arrival.
  A faster filter (OpenCV `GaussianBlur`, or a down-sampled estimate for large sigma) would help, but it would change the numbers
  Add / Subtract merge: that is a numeric change and needs its own before/after comparison. Not done.
- Colour and opacity are fixed (no swatch / slider like the mask and histogram tints); add a row to the View -> Masks menu if wanted.
- Morphology hover: the pixels shown are the ones that flip, so Open of a square shows its four corners (the app's structuring
  element is a cross) - that is what Open really does.
- The Threshold / Local contrast panels are 26 px wider (eye button): Manual edit group 208 -> 234 px, Mask tab size hint ~878 px.
- **Unseen by eye**: colours, the eye icon, the hover feel, the ribbon width. Checked by tests (pixel-exact against the candidate
  functions, the tint's alpha channel at the patch, the real Add / Subtract buttons, `Enter` / `Leave` events on the Morphology
  buttons) and by headless size hints only.
