# "Start analysis" RAM ballooning to several GB - root cause and fix (2026-09)

## Symptom

During a bulk "Start analysis" run, RAM climbed to several GB (reported:
~8GB) while CPU stayed low and disk throughput stayed modest (~22MB/s, well
under SSD capacity) - i.e. RAM was the only resource under real pressure.
The Analysis section's `[disk]`/`[RAM]` backup toggle (`_analysis_ram_only_
backup`) made no difference, which was itself a clue: that toggle only
controls whether *sensorgram metric* rows get buffered before an HDF5
write (a handful of floats per cube) - a completely different, much
smaller subsystem than what turned out to be the actual cause.

## Root cause: per-ROI masks cached at full-patch size

`_fast_roi_mask_cache_entry` (`gui/analysis_tasks.py`, a closure inside
`_scoped_formula_spectrum_task`) builds and caches, per unique
(patch shape/origin, ROI set, per-wavelength chromatic-affine) combination,
a full boolean sample-mask and reference-mask pair for **every selected
ROI individually** - not one combined mask, one full-size pair *per ROI*.

Before this fix, each pair was allocated at `(patch_h, patch_w)` - the size
of the whole *read region*, which for a TIFF dataset (or any dataset with
"flatten background" preprocessing on) is unconditionally the **entire
image plane** (`spectrum_read_region`, since TIFF can't be partially read).
So a ROI that's a 6px-radius circle got a full 800x1200-pixel (or whatever
the sensor is) boolean array, cached, even though well over 99.9% of it was
always `False`.

This cache (`_formula_spectrum_roi_mask_cache`, cap =
`FORMULA_SPECTRUM_ROI_MASK_CACHE_SIZE = 48`, `main_window.py`) is keyed by
the per-wavelength chromatic transform, so a bulk run typically fills one
slot per unique wavelength (reused across every cube at that wavelength),
up to 48 entries. For a realistic LSPRi multi-spot dataset - this app's own
existing comments already reference "a 170-spot array" / "a 160-ROI
selection" as normal usage (`analysis_tasks.py:701`,
`bulk_analysis_performance_investigation.md`) - that's easily many GB.
**Measured directly** (150 ROIs, 800x1200 plane, one wavelength): one cache
entry cost ~289MB before the fix; extrapolated to a full 48-entry cache,
~13.9GB - matching the ~8GB report's order of magnitude closely (their
exact ROI count/resolution/cache-fill-level would differ somewhat, but this
confirms the mechanism, not just its plausibility).

### Why the array was full-size in the first place

The interesting part: this *wasn't* necessary even before the fix - the
codebase already had the tools to avoid it, just not applied at the
allocation site. `_means_for` (the function that actually *reads* these
masks, same file) already reach-limits its own indexing to a small local
box around each ROI (`reach = max(sample_radius, reference_outer_radius) +
2px`, clipped to the patch) - a previous optimization
(`bulk_analysis_performance_investigation.md`'s Finding 2, an 11.3x
speedup) that cut the *compute* cost of scanning a full-plane mask per ROI.
But that fix only sliced the array down **after** it was already built and
cached in full - the allocation/caching side of the same function was never
updated to match, leaving the full-size array as what actually sits in RAM
between cubes.

## Fix applied

Added `_roi_reach_box(roi, sample_x, sample_y, patch_h, patch_w,
reference_inner_radius_px, reference_outer_radius_px)` - the exact same
reach-box formula `_means_for` already had inline, pulled out into one
shared function (`analysis_tasks.py`, right after `_effective_reference_
radii`) so the cache builder and the consumer can never disagree about
where a given ROI's window is. Returns `None` for "mask"-geometry ROIs
(an arbitrary bitmap unrelated to any radius - unchanged, still gets
full-patch treatment, exactly as before) or a degenerate/empty window.

- `_fast_roi_mask_cache_entry` now builds (and caches) each non-"mask"-
  geometry ROI's sample/reference mask pair at its own small reach-box
  size and origin, instead of the full `(patch_h, patch_w)` patch.
  `combined_roi_mask` (the union of every selected ROI's sample area, used
  only to exclude a neighboring ROI's sample pixels from *this* ROI's
  reference ring) still has to stay full-patch-sized - it can be sliced at
  any other ROI's window - so each small per-ROI mask is now OR'd into its
  own sub-region of it instead of OR'd directly (their shapes no longer
  match one another).
- `_means_for` now computes the reach box via the same shared
  `_roi_reach_box` call, and - when given a cached (`precomputed_masks`)
  pair - uses it directly with no further slicing, since the cache now
  supplies it already sized to exactly that window. The `extra_exclude_
  mask`/`ignored_patch`/`patch` slicing (still full-patch-sized objects)
  is unchanged.

**Not touched, deliberately**: the reach-box formula itself (margin,
floor/ceil rounding, clip bounds) is copied verbatim from what `_means_for`
already had inline - not reworked - to keep this a pure memory-layout
change with zero numeric difference. The one behavior difference is a
pre-existing degenerate case (a reach box that clips to empty on one axis,
possible only if a selected ROI's own reach extends past the patch
boundary entirely) - previously produced a silent NaN for that ROI, now
falls back to the full-patch window and computes a real value instead;
this can only make an already-degenerate case more correct, not less.

## Verification

- `tests/unit/test_lspri_roi_absorbance_multi_roi_isolation.py` (the
  existing dedicated regression test for this exact code path, cross-ROI
  reference-ring exclusion and the combined/per-ROI-average rule) - passes
  unchanged.
- `tests/unit -k lspri` (455 tests) and `tests/integration -k lspri` (99
  tests) - all pass.
- Direct before/after measurement (150 ROIs, 800x1200 TIFF-style plane,
  matching the reported TIFF/no-background-flatten case): one cache entry
  1.59MB after the fix vs. 289MB before (181x smaller); a full 48-entry
  cache extrapolates to ~77MB after vs. ~13.9GB before. All 150 ROIs still
  produced finite formula values in the same run.
