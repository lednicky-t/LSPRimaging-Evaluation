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

---

*Items marked with a priority can be re-ordered at any time. Add new items above the
relevant priority section.*
