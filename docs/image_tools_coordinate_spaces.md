# Image Tools: Coordinate Spaces and Where They Apply

This document explains what "image tools" (rotation, flip, crop) actually touch in
the app, why that scope was chosen, and what to watch out for as a result. It was
written after an audit that traced every place `apply_spatial_preprocessing` /
`apply_preprocessing` / `_apply_spatial_transform` (`processing/preprocess.py`) is
used, to check actual behavior against intent.

## What image tools are

Rotation, flip, and crop are controlled by a single flag,
`PreprocessingSettings.image_tools_enabled` (`domain/models.py`), toggled by an
explicit "link/apply" action in the GUI (`main_window.py:_on_image_tools_section_applied_changed`).
When linked, `apply_spatial_preprocessing` resamples the image (rotate via
`scipy.ndimage`/`cv2.warpAffine`, crop via slicing) into a new pixel grid.

## Design decision: image tools are NOT display-only

It would be simpler to reason about if rotation/crop only affected on-screen
rendering and never touched a calculated number. **That is not how this app
works, and it is intentional, not a bug.** When image tools are linked, the
rotated/cropped pixel grid is what feeds:

- **Spectrum / absorbance / sensorgram calculation** — ROI pixel sampling in
  `gui/analysis_tasks.py` (`_load_and_preprocess_measurement`,
  `_absorbance_spectrum_task`) runs on `apply_preprocessing()`'s output, i.e. the
  processed (rotated/cropped) array, not the raw one.
- **Background flattening** — `flatten_background`/`estimate_background_profile`
  run on the already-rotated array (`apply_preprocessing` applies spatial
  preprocessing before background flattening).
- **ROI / spot detection** — `detect_spots` (`processing/spot_detection.py`) runs
  on the processed image; detected `AreaRoi.center_x/center_y` are stored in
  that same rotated/cropped pixel grid, not raw-image coordinates.
- **Chromatic aberration calibration** — landmark detection and registration
  (`processing/chromatic.py`, called from `gui/analysis_tasks.py`) also run on
  the processed image, so the resulting chromatic model lives in
  rotated/cropped coordinate space.

**Why this is kept this way:** rotation and cropping meaningfully help automatic
ROI finding — an axis-aligned sample lattice (after rotation) and a tight crop
around the region of interest both make automatic spot/ROI detection more
reliable than running it on a raw, tilted, wide-field frame. Decoupling image
tools from these calculations would mean re-deriving a raw-space anchor for every
detected ROI and every chromatic landmark, and forward-transforming it back at
every point of use (the way mask authoring already does — see below) — a much
larger change for a benefit (numeric independence from a display setting) that
doesn't offset the loss of detection quality. The trade-off was made deliberately
in favor of detection quality over spatial-transform purity.

## What IS guaranteed regardless of image tools

- **Source TIFF files on disk are never modified.** Every read path
  (`load_image_array`, `dataset_load_plane`, `_load_image_array_native`) is
  read-only; no write path in the codebase targets a source image path.
- **OME-Zarr export is the only place a spatial transform is permanently baked
  into a saved dataset.** `export_ome_zarr_dataset` (`io/dataset.py`) applies
  `apply_spatial_preprocessing_export` (a performance-optimized, numerically
  validated equivalent of `apply_spatial_preprocessing`, used only on this
  export path) when, and only when, `image_tools_enabled` is set — and it always
  writes to a new destination directory, never the source dataset folder. This
  export intentionally reduces the exported spatial footprint (cropping unused
  borders) and aligns the pixel lattice for OME-Zarr chunking performance.
- **Mask authoring is coordinate-space-correct.** Unlike ROIs and chromatic
  landmarks, masks are created and stored in **raw** pixel space
  (`gui/mask_controller.py`) and forward-transformed into processed space at
  each point of use via `apply_spatial_mask`/`spatial_coordinate_maps`. This is
  the pattern the rest of the codebase would need to follow if ROI/chromatic
  data were ever made raw-space-independent.

## Known gap: stale coordinates after changing image tools

Changing `rotation_angle_deg` or the crop rectangle *after* detecting ROIs or
calibrating chromatic correction does not invalidate the ROI positions or the
chromatic model — only downstream *result caches* (spectrum/sensorgram values)
are invalidated and recomputed. The recompute then silently samples the old
ROI/landmark coordinates against the new processed geometry, producing a
confidently-displayed but physically wrong number, with no warning.

Practical guidance until this is addressed: **re-run ROI detection and chromatic
calibration after changing rotation angle, flip, or crop**, rather than assuming
existing ROIs/landmarks remain valid. See the fix tracked for this (staleness
signature on `AreaRoi` / `ChromaticTransformModel` / `ChromaticLandmarkObservation`)
in `TODO.md` / recent changelog entries.
