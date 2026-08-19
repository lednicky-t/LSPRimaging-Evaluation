# TODO - LSPRimaging Evaluation App

Priority levels: `[high]` `[medium]` `[low]`

---

## Medium Priority

### [medium] Visually verify the Metadata section and sensorgram time-axis live
Both the acquisition-metadata GUI (`Dataset > Metadata`: import/export/preview-edit,
status label, live per-image time/comment label) and the sensorgram's real-elapsed-time
x-axis (`gui/analysis_controller.py`'s `_sensorgram_x_values`, 2026-08-17) have only been
tested headlessly (`tests/integration/test_lspri_metadata_gui.py`,
`test_lspri_sensorgram_time_axis.py`) and via ad-hoc scripts against a real example
dataset - never actually run and clicked through in the live app.
- Launch `lspri-evaluation`, load a dataset with `measureing_times.csv`/`metaData.txt`
  nearby, confirm the Metadata section's layout/wording reads sensibly and the buttons are
  positioned/sized reasonably (not just functionally wired).
- Click through Import (multi-file dialog), Export, and Preview/edit - confirm the edit
  dialog's tables are usable at a glance, not just correct when driven programmatically.
- Calculate a sensorgram against a dataset with metadata loaded and confirm the x-axis
  genuinely reads as time (labels, cursor drag behavior) and looks right visually, not just
  numerically correct in tests.

### [medium] Read ROI/absorbance/sensorgram data back from a native v6.4 measurement file
LSPRimaging Acquisition's `ImagingMeasurementWriter` (schema v6.4) also writes
`processed/roi_definitions`, `processed/absorbance_spectra/{roi_id}`, and
`processed/sensorgram/{roi_id}` - none of which `lspr_io.read_imaging_acquisition_metadata`
(built 2026-08-16, covers only camera/illumination settings and cube timing) reads. Not
started - flagged as a known gap, not yet scoped.
- Would let eva restore ROI placements (and possibly a starting sensorgram/absorbance
  preview) from a native acquisition session file, not just camera/timing metadata.
- Needs its own reader function in `lspr_io` (parallel to `read_imaging_acquisition_metadata`,
  not folded into it - different consumer, different shape) and a decision on how it
  interacts with eva's own ROI system (`AreaRoi`/`AreaRoiGroup`) if the two ever disagree.

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

### [low] OME-Zarr export: validate adaptive worker tuning on real hardware
The original "export is slow, resources idle" problem (see git history around 2026-07-03,
`export_ome_zarr_dataset` in `io/dataset.py`) was fixed by bypassing zarr's own
`asyncio.to_thread`-per-chunk write API and writing shards directly from a
`ProcessPoolExecutor` (~119 MB/s vs ~21 MB/s in benchmarking at 64px chunks), plus zarr v3
sharding (`shard_mode`) to cut down on small-file count, plus a faster lz4+bitshuffle
compressor. A follow-up tuning idea (a static `1.5x cpu_count` worker-count guess,
2026-08-15) was replaced the same day by real adaptive measurement instead of a guess:
- `export_ome_zarr_dataset` now times each shard task's read/compress/write phases
  (`write_shard` returns a `ShardWriteResult`, `_zarr_export_worker.py`) and, every
  `adaptive_batch_mb` of data processed (default 1 GB), checks the measured I/O-wait
  fraction and adds or removes worker processes accordingly (bounded to
  `[cpu_count, 2x cpu_count]`) — see the coordinator loop around dataset.py:845-940.
- Togglable in `File > Preferences > OME-Zarr export: adaptive worker tuning` (default
  **on**), with the sample size adjustable there too. Off is a true baseline (plain
  `cpu_count`, no timing/decision logic runs at all) specifically so the feature itself can
  be A/B'd by running the same export twice.
- Still genuinely unverified: whether this actually helps on real hardware/drives, and
  whether the flip thresholds (`ADAPTIVE_IO_FLIP_UP`/`DOWN` = 0.5/0.2, step size
  `cpu_count // 4`) are well-tuned. That can only be answered by using it — compare
  wall-clock export time (reported in the finish message/workflow log) with the toggle on
  vs. off on the same dataset/destination, ideally including at least one slower drive
  (network share, HDD, or a drive with Windows AV scanning) where I/O-wait should actually
  show up.

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
  active painting simultaneously), but a real, localized lag when it applies. Root cause
  pinned down 2026-08-19: `overlay_manager.py`'s `_update_ignore_mask_overlay` calls the
  sibling `ignored_mask()` through a signature-checked cache (`plot_manager.py`), but the
  `apply_spatial_mask(...)` call right next to it is not cached at all - giving that call
  the same kind of signature cache should fix this without touching the rotation logic
  itself.
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
- **Background-image load/save also synchronous** (found 2026-08-19 while looking at the
  item above): `mask_controller.py`'s `load_background_from_file`, `_on_save_background_ready`,
  and `auto_load_mask_for_current_record` call `PIL.Image.open(...)`/`.save(...)` directly on
  the GUI thread - same class of gap as the processing-profile import/export item, just for a
  background reference image instead of the JSON profile. Likely low-impact unless someone
  loads an unusually large background file, but cheap to fix alongside that item if it's ever
  touched.

---

*Items marked with a priority can be re-ordered at any time. Add new items above the
relevant priority section.*
