# Analysis pipeline layers: what's per-ROI, what's Statistics

**Status: describes the target architecture, confirmed against the code
2026-09-12; the one gap it names (see "The one gap this closes" below) was
closed by the same change that added this doc, which introduced the
Individual / Average all / Average by group Sensogram display modes in
`gui/analysis_controller.py`.**

This is the maintainer's own description of how analysis should be
layered, written down so it doesn't have to be re-derived from scratch or
re-explained in a future session. Read this before touching
`gui/analysis_tasks.py`, `gui/analysis_controller.py`,
`gui/analysis_worker_mixin.py`, or anything under "Statistics" in the
Analysis panel.

## The three per-ROI layers (Pillar I - the heavy work)

Every ROI goes through the same three layers, **independently of every
other selected ROI**. Nothing here ever mixes pixels, spectral values, or
fit inputs across ROIs:

1. **ROI math** - collapse one ROI's own sample/reference pixels to one
   number each (mean, median, trimmed mean, plane fit, ...). Chosen once at
   the start of analysis (`AreaRoiDetectionSettings.reduction_method`,
   `domain/models.py`) and persisted to the HDF5 measurement export so a
   later choice of a different method adds to what's stored rather than
   discarding it.
2. **Formula spectrum** - combine that ROI's own sample and reference
   numbers into one value per wavelength (absorbance / ratio / relative
   change / mOD), building that ROI's own spectrum.
3. **Metric trace** - collapse that ROI's own spectrum into one traceable
   number per spectral cube (centroid, a fitted peak position, a
   correlation-based shift, ...), building that ROI's own sensogram trace.

This is the expensive part: a real pixel read and fit, once per ROI per
spectral cube.

### Where this is enforced in code

`gui/analysis_tasks.py`'s `_scoped_formula_spectrum_task` is the one
spectrum-compute path, used for every dataset format
(`docs/bulk_analysis_performance_investigation.md`'s "Follow-up #6"). Even
when several ROIs are selected together, each one's own sample/reference
pixels are reduced independently, one ROI at a time:

```
per_roi = {
    roi_id: _means_for([roi], (roi_id,), ...)   # this ROI's own pixels only
    for roi in selected_rois
}
```

(`analysis_tasks.py:1029-1036`). The only thing shared across ROIs at this
layer is a defensive pixel mask - excluding a *neighboring* selected ROI's
sample circle from *this* ROI's reference ring, so a bright nearby spot
can't bias this ROI's own reference reading
(`extra_exclude_mask`, `analysis_tasks.py:1013-1019`). That mask is never
used to pool pixels into a shared measurement - the code's own comment is
explicit about this:

> "Sample and reference ROIs are always reduced to one absorbance value per
> ROI, independently - never by pooling pixels from multiple ROIs into one
> sample/reference mean first (pooling before the ratio is not the same
> calculation as averaging each ROI's own ratio afterward, and mixes pixels
> from different physical apertures)."
> - `analysis_tasks.py:1021-1027`

This was a real, fixed historical bug (pixels genuinely pooled across ROIs
before the ratio) - see the regression test
`tests/unit/test_lspri_roi_absorbance_multi_roi_isolation.py`, which pins
down both the reference-ring leak and the pixel-pooling fix so they can't
silently regress.

Each ROI's own metric-trace value per cube is likewise computed
independently and is always available as a byproduct of any run, combined
selection or not (`per_roi_metric_values`, `analysis_tasks.py:1539-1566` /
`gui/worker.py:140`).

## Statistics (Pillar II - purely visual)

Showing 170 raw sensogram traces at once doesn't mean anything to look at.
Statistics exists to make many ROIs' output legible - **and only that**:

- Statistics reads already-computed **sensogram trace values** (Pillar I's
  final output, layer 3 above) - one number per ROI per spectral cube.
- Statistics **never** reads spectra (layer 2) or pixels (layer 1). It has
  no business touching either.
- Statistics groups/averages/smooths what Pillar I already produced; it
  never changes how Pillar I computes anything.

A ROI can be grouped with others of similar behavior, left ungrouped, or
viewed solo - that grouping choice belongs to Statistics, not to the
per-ROI math above.

### The one gap this closes

Before this change, selecting several ROIs with no explicit Statistics mode
chosen showed a "combined" trace computed by averaging each ROI's
**absorbance value per wavelength**, then fitting the metric **once** on
that averaged spectrum - a layer-2 (spectrum) average, not a layer-3
(sensogram) one. Not pixel pooling (that bug was already fixed, see above),
but still the wrong layer per the rule above: for a nonlinear metric fit,
"average the spectra, then fit" and "fit each spectrum, then average the
results" are not guaranteed to produce the same number.

This was retired in favor of three explicit Statistics display modes -
Individual / Average all / Average by group - that only ever average
already-fitted per-ROI sensogram values. See
`gui/analysis_controller.py`'s sensogram trace-fetch/aggregation code for
the current implementation.
