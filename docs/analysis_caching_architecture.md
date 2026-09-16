# Analysis caching architecture: what exists and why

**Status: unified 2026-09-16.** Originally an audit (2026-09-15) of 8 caching
mechanisms found behind the Spectra and Sensogram panels, triggered by the
maintainer being surprised at "four caching tiers" mentioned in passing while
debugging the Sensogram disappearing-plot bug. The maintainer's read: this
many caches isn't what's expected of the app, and it was an active source of
bugs, not just hard to read - every Sensogram bug fixed that session (slow
reselection, a spurious "recalculate?" popup, the disappearing-plot bug)
traced back to the same root pattern: two independently-maintained code paths
answering "is this ROI's data available/fresh" differently. Rebuilt the
following day into the shape below: **5 mechanisms, one shared "is this
fresh" answer for both panels, not two.** Companion to
`analysis_pipeline_layers.md` (the three *computation* layers - ROI math ->
formula spectrum -> metric trace); this doc is the *caching* built around
those layers.

## The current shape

Both panels' displayed values derive from the same expensive step: read a
ROI's pixels out of the dataset and reduce them to sample/reference numbers.
Both panels now read that result from **the same cache**, atomic per (ROI,
cube, settings) - no separate "combined selection" tier for either panel
anymore, because combining several already-known per-ROI values is cheap
arithmetic, proven by the fact neither panel's render path has ever needed to
cache that combination step.

| Tier | What's cached | Used by |
|---|---|---|
| pixel masks | which pixels belong to a ROI's sample/reference regions | `_formula_spectrum_roi_mask_cache` - shared pixel-computation path (`_scoped_formula_spectrum_task`), both panels |
| pre-formula pixel reduction, bulk-sweep only | sample/reference reduced values, keyed to the WHOLE selected-ROI set | `_sensorgram_spectral_cube_result_cache` - only ever populated by a bulk "Start analysis" sweep |
| formula-applied spectrum, atomic per ROI | one ROI's own formula-independent spectrum (late-projectable onto any formula via `project_formula_spectrum`) | `_roi_formula_spectrum_cache` - **the one cache both panels' display paths read**; populated by a single-cube interactive preview, never directly by a bulk sweep |
| fitted metric, atomic per (ROI, cube) | Sensogram-only (Spectra doesn't fit anything): one fitted value+signal | `_sensorgram_metric_cache` |

Plus, underneath both:

- **Disk backup** - `measurement_backup.h5`, written incrementally during a
  run (`_write_measurement_backup_buffers`) so a crash mid-run loses at most
  one batch. Read back via `_sensorgram_result_from_disk_backup` (Sensogram
  display) and `_fill_roi_formula_spectrum_cache_from_disk` (Spectra display)
  whenever RAM misses.
- **Freshness-hash gate** - `_sensorgram_point_signature_hash_cube_context` +
  `_sensorgram_point_signature_hash_for_selection`/`_for_roi`
  (`analysis_worker_mixin.py`). Not a cache of values - a persisted
  fingerprint of every setting that produced a disk row, so a reopened
  session can tell a stale row from a valid one without storing the full
  signature tuple. Exists because RAM can use a tuple as a dict key for
  free; a persisted HDF5 column can't.

That's **4 RAM caches + 1 disk store + 1 hash-gate = 6 mechanisms** for the
per-cube compute path, plus one more RAM cache (`_sensorgram_metric_cache`)
serving the sweep-level "skip pixel-read+fit for this cube" shortcut
described below - **5 mechanisms in the sense that matters** (down from 8):
one shared per-ROI answer for "is this already computed" instead of each
panel keeping its own separate, sometimes-wrong opinion.

## Why `_sensorgram_spectral_cube_result_cache` and `_roi_formula_spectrum_cache` are still two caches, not one

This looked like the same redundancy the rest of this unification removed -
it isn't. They store the identical `FormulaSpectrumResult` type, but at
genuinely different granularity, by design:

- `_roi_formula_spectrum_cache` is atomic per ROI. It's what a single-cube
  interactive preview populates, and what every display path now reads.
- `_sensorgram_spectral_cube_result_cache` is keyed to the **whole selected-
  ROI set together**. `spectral_cube_formula_spectrum_cache_store` has an
  explicit `if len(spectral_cubes) > 1: return` guard - a bulk "Start
  analysis" sweep **never** writes into the per-ROI cache directly, on
  purpose (confirmed via git blame/comments, not inferred): populating a
  512-entry-budget interactive cache with, say, 160 ROIs x thousands of
  cubes worth of whole spectra during a single sweep would blow that budget
  and thrash it uselessly for a run that's already relying on the disk
  backup (schema 7, O(1) reads) as its own "was this already done" answer.

The bridge between them is explicit, not accidental:
`_cached_formula_spectrum_result_from_sensorgram_sweep`
(`analysis_worker_mixin.py`) lets the Spectra panel show an already-swept
cube instantly by reading the sweep's own cache directly - including while
the sweep is still mid-flight and hasn't flushed to the HDF5 disk backup yet,
the one case the disk fallback can't cover on its own.

## The sweep-level shortcut: per-ROI disk reuse across different selections

Before this pass, `_sensorgram_metric_task`'s only "skip pixel-read+fit for
this cube" shortcut (`metric_value_cache_get`) was keyed to the **exact**
selected-ROI combination's synthetic `combined_<ids>` backup row - so
stopping a sweep, reselecting a different ROI subset, and pressing Start
again re-read pixels for every selected ROI even when each one individually
was already done a moment ago (`_backup_per_roi_sensorgram_points` always
backs up every real ROI's own row, regardless of what combination it ran as
part of). `per_roi_metric_value_cache_get` checks those individual rows
instead - a strict superset of what the combined check can find, checked
first, with the combined check kept as a fallback out of caution rather than
replaced outright. New cost: builds one per-ROI disk index per selected ROI
at sweep setup (`writer.sensorgram_metric_index()` once per ROI instead of
once total) - stage-timed (`SG per-ROI disk index build`, DEBUG) since this
wasn't verified against schema 7's real cost at reference scale (160 ROIs)
in this session, per this repo's instrument-at-write-time convention.

## A real correctness bug fixed as part of this, not a side effect of naming

Comparing the two panels' ROI-geometry signatures field-by-field (needed to
know what "one shared cache" required) found they disagreed in two
*complementary*, both-live ways: Sensogram's tracked only the *global*
reference-diameter setting (missing a per-ROI "Edit reference ROI diameter"
edit); Spectra's tracked only a per-ROI *override* as `... or 0.0` (missing a
global-setting change for any ROI still using the default - the same
constant regardless of what the default actually was). Fixed with one shared
`roi_circular_geometry_signature()` (`analysis_tasks.py`, next to
`_effective_reference_radii` - reuses that exact resolution function, so the
signature can never drift from what the real pixel mask computes), wired
into every one of the three places that used to disagree, including the
disk-facing hash (arguably the most important of the three, since a stale
disk row persists across restarts). Diameters throughout, not radii, per the
maintainer's explicit preference. Non-circular ROI geometry (mask/geometry-
type ROIs) is deliberately still open - out of scope, not forgotten.

## What's simpler now, concretely

- One "is this ROI already computed" question per panel, not two answered
  differently. `_render_sensorgram_display`, `_calculate_sensorgram_for_
  range`, `preview_sensorgram_from_cache`, `_refresh_formula_spectrum`,
  `_refresh_visible_spectrum_from_cache`, and `_on_analysis_fit_settings_
  changed` all now call the same one or two functions
  (`_sensorgram_trace_for_roi` / `_cached_formula_spectrum_result_from_roi_
  cache`) instead of each keeping its own cache-check logic.
- Retired entirely: `_sensorgram_cache` (Sensogram's old combined-selection
  RAM cache), `_formula_spectrum_cache` and `_formula_spectral_cube_cache`
  (Spectra's old combined-selection and fast-index tiers), and every
  signature/covers-check helper that only existed to serve them.
- The one deliberately-remaining asymmetry (`_sensorgram_spectral_cube_
  result_cache` vs. `_roi_formula_spectrum_cache`) is now *documented* as
  intentional scoping, not a naming-obscured duplicate - see above.
