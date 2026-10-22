# Session carried along with the OME-Zarr export (2026-10-22)

Maintainer request: the Zarr export should be a **session export**. The exported dataset arrives with the ROIs, masks,
chromatic correction and mask pointers already in its (rotated, cropped, padded) coordinates, so the user loads it and
carries on, only with a faster, chunk-optimised dataset.

Code: `storage/session_carry.py` (pure, Qt-free), `storage/session_carry_task.py` (thread, progress, Cancel),
`panels/image/export_tab.py` ("Session" button), `app_rewrite.py` (`_wire_session_carry_prompt`).
Tests: `tests/unit/test_lspri_session_carry.py`, `tests/integration/test_lspri_session_carry_export.py`,
the five `session`-named tests in `tests/integration/test_lspri_rewrite_export_chunk_tools.py`.

## Decisions (maintainer, 2026-10-22)

- **Exception to "new sessions start blank" (2026-09-26).** A carried session is made by an explicit user action (the Session
  button of the Export tab) from a session the user built; it is not a clone command and there is still no "duplicate
  session". Everything else still starts blank.
- After the export the app **asks** whether to open the exported dataset. Opening it is a normal dataset load; the carried
  session is the active one there, so it is the same as choosing a session by hand. The source dataset and its session stay
  open and untouched until the user chooses to load the other.
- The new session has **no rotation, flip or crop** (they are in the pixels now). Calibration stays. What was done is recorded
  in `derived_from.json` next to the session (below).
- Background **settings** (the seven numbers; no pixel data) are carried. Mask settings, detection settings, metric and
  statistics settings, frame roles and the selection are copied.
- Mask pointers (formerly "transient events", renamed the same day) are carried, warped like masks.
- **No backward compatibility** for the session format (development stage). The rename of the session block
  (`transient_events` -> `mask_pointers`) means an older session file opens without its pointers (they are re-made by Scan).

## What moves, and why nothing is recalculated

| Part | Space it lives in | What the carry does |
|---|---|---|
| ROIs (centre, per-wavelength nudges, per-cube timeline, painted ROI masks), ROI arrays (anchor) | processed | shift by the frame origin |
| Chromatic models, landmarks, landmark search area | processed (the warp comes after the rotation) | shift; the affine is conjugated, `A'(q) = A(q + o) - o`; not refitted; fit quality untouched |
| Masks | raw | resampled once with the app's own nearest-neighbour warp (`apply_spatial_mask`), then cut to / padded into the frame |
| Mask pointers (centroid, box, contour) | raw | centroid through the raw -> stored affine; contour resampled like a mask, box re-tightened; dropped (and counted) if nothing is left in the frame |
| Geometry | | rotation 0, no flip, no crop; calibration and display choices stay |
| Background, mask, detection, metric, statistics settings; frame roles; selection | independent of position | copied |
| Analysis results (`analysis/data.h5`) | | **not carried**: a cell's input pixels changed by interpolation and by rounding onto the camera step, so every cell would be recomputed anyway; the rewrite does not produce results yet |
| Undo history | | not carried |

`o` is the export frame origin in processed space (negative = padding on the left / top). With the image tools off the export is
raw and the transform is the same shift only (`image_tools_enabled=False` makes the raw -> processed map the identity).

## Checks and measurements

- A bright feature in the source's processed image, in the exported plane, and the carried ROI agree
  (`test_the_carried_roi_sits_on_the_same_feature_in_the_exported_pixels`): rotation 1.4 deg, crop, frame padded 3 px left and
  2 px up, Lanczos4 + camera-step rounding. A ROI of radius 9 px straddling the feature's edge: mean differs by **0.5 counts**
  (of ~3800); the same ROI moved **1 px** differs by 11 counts; **not shifted at all** by 176.
- Masks: a block lands where the point map says (within 1 px); the whole stored mask equals `apply_spatial_mask` of the source
  mask, placed in the frame, with empty padding.
- The affine conjugation is pinned by a property check and a scaling example.
- Source objects are never changed (`carry_session` builds new ones); `snapshot_state` makes later in-place edits of the
  toolbox's ROIs harmless (the carried session is the one the export was started with).
- Cost, measured on 1300 x 900 masks with the 1.42 deg tilt, crop and a padded frame: **411 masks in 2.2 s (5.3 ms each:
  zlib decode, warp, compress)** on the carry's own thread. ROIs and chromatic models are instant (the whole carry of the
  small test session took 0.03 s).
- Export tab: the new "Session" group adds 69 px to the tab (495 -> 564 px). The Image panel's minimum width (1004 px in the
  offscreen test) is set by something else and did not change.

## How it runs

1. User presses Export. `ExportTab._prepare_session_carry` builds `ExportTransform(geometry, raw_shape, frame)` from exactly
   what it passes to the export, and `SessionCarry.prepare` snapshots the session on the GUI thread (copies ROIs, groups,
   arrays and settings; masks, pointers and chromatic models are immutable by convention).
2. The export runs. A failed or cancelled export calls `discard()`.
3. `export_finished` starts one plain `threading.Thread` (never `QThreadPool`; it is next to the zarr layer): carry, then
   `session_index.create_session(destination, "Carried from <name>")`, `SessionWriter.write`, `derived_from.json`.
   A failure or a cancel removes the half-written session folder and restores the index. Progress and Cancel are in the status
   bar's task indicator ("Carry session").
4. `carried` -> a non-blocking question box offers to open the exported dataset. It also says how the export went (time, images/s,
   GB/min of image data, bytes written: `DatasetModule.export_stats_ready` reaches `SessionCarry.set_export_stats` just before
   `export_finished`; see `export_speed_2026-10-22.md`) and what the session brought along. Tested by
   `tests/integration/test_lspri_session_carry_prompt.py`.

## `derived_from.json` (in `<exported>/sessions/<id>/`)

Not part of `session.json`, so the autosave (which rewrites `session.json` from the modules) can never drop it.

- `source`: dataset folder and session id.
- `transform`: image tools applied, rotation, flips, crop, raw / processed / stored shape, the frame (origin and size), whether
  the frame is lossless, and the 2x3 affine raw -> stored.
- `carried`: counts (and what was left out), plus the things deliberately not carried.
- `zarr`: the export's own `lspr` attributes (chunk shape, shard mode, compression, dtype, interpolation, value step,
  pixel size ...), read from `zarr.json` as plain JSON.

## Known limits / open

- A **lossy frame** (the grid trims the image) is carried, and `lossless: false` is recorded. ROIs that fall outside are counted
  (`rois_outside_image`) and kept, not deleted.
- The chromatic warp near the new image border samples differently from the source's (no data outside the stored frame), as it
  does at any crop. This is the same effect as cropping in the source.
- The exported dataset's session is found by its **home folder**. Loading the export by choosing the folder *above* it (the
  loader then searches one level down) makes the parent the home, and the carried session is not seen. The "Open" question
  loads the Zarr folder itself, so it works; loading by hand from the Dataset section should point at the Zarr folder.
- Not yet looked at by eye: the "Session" button's icon (vendored `copy`, violet when on) and its position next to Export.
