# TODO - LSPRimaging Evaluation App

Priority levels: `[high]` `[medium]` `[low]`

---

## High Priority

### [high] OME-Zarr converter: performance / resource utilization
The export pipeline is slow despite low CPU and SSD utilization - machine resources are
largely idle during conversion. Remaining sub-tasks after thread-oversubscription fix:
- ~~Thread oversubscription fixed~~: Blosc now gets `cpu_count // 2` threads (not full
  `cpu_count`) so reader workers aren't starved during compression. Per-file tifffile
  `maxworkers` in `_load_image_array_native` reduced to 2 (from `cpu_count`) since the
  export worker pool already provides inter-file parallelism.
- Worker count (`worker_count`) is still capped at `cpu_count`; increasing to 1.5-2x
  may help on fast NVMe but needs benchmarking - see `TODO(perf)` in `io/dataset.py`.
- Consider exposing a faster compression preset (e.g. lz4 + byte-shuffle) alongside the
  current size-optimized zstd + bitshuffle default.
- Longer term: evaluate zarr v3 sharding to collapse many small chunk files into fewer
  physical files (especially beneficial on Windows with AV scanning and on network drives).

---

## Medium Priority

### [medium] Mask panel: control fields too wide
Controls in the Mask panel are excessively wide. Evaluate each control group and resize to
roughly half the current width where content allows:
- Review "Relative threshold", "Scale", and other controls for appropriate label/input
  proportions.
- Check each field's value range and label length before setting a fixed width.

### [medium] Masking tools: slow performance investigation
Some masking tools are noticeably slow even on images of only a few MB. Investigate:
- Profile the mask calculation pipeline to find the actual bottleneck (CPU, memory,
  display/overlay rendering, or Qt update path).
- Check whether overlay layers (ignore mask, figure mask, histogram mask) are composited
  efficiently or recalculated from scratch on every update.
- Consider whether mask preview should be computed only on the currently visible viewport
  region instead of the full image (trade-off: faster updates during static view, but
  requires recalculation on pan/zoom - evaluate whether the jerkiness is acceptable or
  if a brief delay + async update is preferable).
- Partially addressed 2026-08: relative/local-contrast/morphology mask Apply and preview
  no longer *freeze the GUI* while computing (backgrounded via `request_mask_candidate` in
  `mask_controller.py`, with caching so repeat/Apply-after-Preview is instant). The
  underlying computation cost itself (the actual bottleneck this item asks about) is
  unchanged - this item is still open for that part.

### [medium] ROI value calculation: add variance / standard deviation
ROI metrics currently store only the mean pixel value per ROI. Extend to also track:
- Standard deviation and/or variance within each ROI's pixel set.
- This better characterizes pixel-value distribution and is more scientifically useful
  (a mean alone is insufficient when there is heterogeneity within a nanoparticle ROI).
- Update wherever ROI means are computed and displayed (table, sensorgram, spectrum).

---

## Low Priority

### [low] Compare `plim` legacy repo against current imaging app
Create and keep a repo-local comparison note for `ondrejstranik/plim.git` so future work
can reuse the analysis without repeating the source review.
- Capture which `plim` functions are worth reusing, reimplementing, or ignoring.
- Focus on spot detection, spectrum extraction, peak fitting, kinetic fitting, and SPR
  transport/calibration helpers.
- Update the note if any of those ideas are ported into the imaging app.

### [low] Image cursor: live pixel readout with histogram link
Implement a cursor overlay on the image view that shows:
- X, Y coordinates in the currently selected unit (px or um, respecting the calibration
  setting).
- Grayscale (intensity) value of the pixel under the cursor.
- A configurable cursor "brush" width/diameter - the displayed intensity value will be
  the mean of all pixels within that radius.
- In the histogram panel: a vertical line marker at the grayscale value under the cursor;
  when a brush width > 1 px is active, highlight the full range of values covered by
  all pixels in the brush area.

### [low] Image filters (in Image tools)
Add an image filtering panel, ideally as a pop-out or collapsible section within Image
tools. Filters should be applied non-destructively (preview only, like rotation/crop):
- Design for extensibility so further filters can be added incrementally.
- First filter to implement: *(not yet specified - to be decided)*
- Consider Gaussian blur, sharpening, or background-subtraction style filters as
  candidates.

### [low] GUI freeze audit: remaining items
From the 2026-08 architecture review that fixed the top 5 GUI-freeze findings (auto
reference-image selection, mask apply/preview, background-profile create/save, and the
dataset-load + processing-profile read path - see git history around that date on
`dataset_controller.py`, `session_state_manager.py`, `background_profile_controller.py`,
`mask_controller.py`, `main_window.py`). Lower-priority items from the same review, not
yet addressed:

- **Undo/redo snapshot cost**: `UndoManager.make_snapshot` deep-copies the whole analysis
  state (all ROIs/groups/arrays, chromatic models/landmarks, exclusion rules, preprocessing
  settings) plus a full-resolution mask-array `.copy()`, inline on the GUI thread, on
  almost every mutating action (ROI drag-start, mask brush, crop, chromatic-landmark edit,
  "Detect ROIs", exclusion toggles, etc.). Individually cheap; a real concern only as a
  cumulative per-action stutter on sessions with large images/many ROIs/many landmarks.
- **`QApplication.processEvents()` re-entrancy smell**: `analysis_controller.py`
  (`_start_absorbance_spectrum_preparation`) and `image_tools_controller.py`
  (`on_image_tools_section_applied_changed`) call `processEvents()` right after
  `_begin_busy(...)` to force the busy cursor to paint before dispatching real work. Not
  currently causing a freeze (the heavy work in both cases is already backgrounded), but a
  stray `processEvents()` mid-handler re-enters the event loop and can let unrelated
  queued events (e.g. a second click on the same button) run somewhere the handler doesn't
  expect. Worth removing/replacing with a less re-entrant "force a repaint" mechanism if
  it's ever the source of a hard-to-reproduce bug.
- **Mask-brush painting + rotation resample**: `mask_controller.apply_mask_brush` (fires on
  every `MouseMove` while the mask pencil is active) calls `_update_ignore_mask_overlay()`,
  which - if a relative/local-contrast/morphology mask preview is toggled on *and* a
  non-zero rotation angle is configured - runs `ndimage.rotate` on the full image on every
  mouse-move during the stroke. Narrow combination (needs rotation + a mask preview +
  active painting simultaneously), but a real, localized lag when it applies.
- **`import_processing_profile()` / `export_processing_profile()` still synchronous**:
  these two explicit File-menu actions do `load_processing_profile()` /
  `save_processing_profile()` (JSON parse/serialize, potentially large with a big analysis
  cache) directly on the GUI thread - the same underlying cost that made the *dataset-load*
  read path worth backgrounding. Both already show a busy cursor via `_begin_busy`/
  `_end_busy`, so at least they're honest about being slow, but they still block. Should be
  lower-effort than the dataset-load fix was: the `_load_processing_state_task` dispatch
  pattern (`analysis_tasks.py`) can likely be reused directly for
  `import_processing_profile`; `export_processing_profile` would need an analogous
  background dispatch for `save_processing_profile`.

---

*Items marked with a priority can be re-ordered at any time. Add new items above the
relevant priority section.*
