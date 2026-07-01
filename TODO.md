# TODO — LSPRimaging Evaluation App

Priority levels: `[high]` `[medium]` `[low]`

---

## High Priority

### [high] OME-Zarr converter: performance / resource utilization
The export pipeline is slow despite low CPU and SSD utilization — machine resources are
largely idle during conversion. Investigate what is blocking full throughput:
- Is the bottleneck the Python GIL, Blosc thread count, small write granularity, zarr chunk
  file overhead (one file per chunk on Windows), or TIFF decode speed?
- Check whether the current worker-thread / Blosc-thread configuration is oversubscribed
  or undersubscribed.
- Consider exposing a faster compression preset (e.g. lz4 + byte-shuffle) alongside the
  current size-optimized zstd + bitshuffle default.
- Longer term: evaluate zarr v3 sharding to collapse many small chunk files into fewer
  physical files (especially beneficial on Windows with AV scanning and on network drives).
- See existing `TODO(perf)` comments in `io/dataset.py` → `export_ome_zarr_dataset()`.

### [high] Image tools: rotation not reverting when "not applied"
When Image tools are toggled off (not applied/linked), rotation is still visible in the
displayed image instead of reverting to the raw, unrotated data.
- Investigate `_apply_spatial_transform` in `processing/preprocess.py`: rotation and flip
  are currently applied unconditionally regardless of `image_tools_enabled`; only crop is
  gated on that flag.
- Decide whether rotation/flip should also respect `image_tools_enabled` (likely yes, to
  match user expectation), and update the live display pipeline accordingly.
- Make sure the cache-invalidation signatures are updated alongside any change.

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
  requires recalculation on pan/zoom — evaluate whether the jerkiness is acceptable or
  if a brief delay + async update is preferable).

### [medium] ROI value calculation: add variance / standard deviation
ROI metrics currently store only the mean pixel value per ROI. Extend to also track:
- Standard deviation and/or variance within each ROI's pixel set.
- This better characterizes pixel-value distribution and is more scientifically useful
  (a mean alone is insufficient when there is heterogeneity within a nanoparticle ROI).
- Update wherever ROI means are computed and displayed (table, sensorgram, spectrum).

---

## Low Priority

### [low] Image cursor: live pixel readout with histogram link
Implement a cursor overlay on the image view that shows:
- X, Y coordinates in the currently selected unit (px or µm, respecting the calibration
  setting).
- Grayscale (intensity) value of the pixel under the cursor.
- A configurable cursor "brush" width/diameter — the displayed intensity value will be
  the mean of all pixels within that radius.
- In the histogram panel: a vertical line marker at the grayscale value under the cursor;
  when a brush width > 1 px is active, highlight the full range of values covered by
  all pixels in the brush area.

### [low] Image filters (in Image tools)
Add an image filtering panel, ideally as a pop-out or collapsible section within Image
tools. Filters should be applied non-destructively (preview only, like rotation/crop):
- Design for extensibility so further filters can be added incrementally.
- First filter to implement: *(not yet specified — to be decided)*
- Consider Gaussian blur, sharpening, or background-subtraction style filters as
  candidates.

---

*Items marked with a priority can be re-ordered at any time. Add new items above the
relevant priority section.*
