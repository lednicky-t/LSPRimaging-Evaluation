# Analysis provenance store design

Written 2026-09-22, during design discussion for `analysis/` (the rewrite's
still-unbuilt computational core). Elaborates and supersedes the file-shape
part of `rewrite_architecture_sketch_2026-09.md` §5 ("The analysis store:
one file, per-cell provenance") - that section's *rules* (one HDF5 file per
dataset, per-cell fingerprints, "any input change triggers recompute", the
ROI-adjacency exception) are unchanged and still authoritative; this doc is
specifically about how each cell's provenance is actually represented on
disk, which §5 had left as a placeholder ("`provenance_table.json`'s
dedup scheme is named but not designed" - sketch §9, item 1).

**Status: agreed in conversation, not yet implemented or reviewed against
real code.** Treat this as the design to build against, not as a completed
feature.

---

## Why file-based provenance, not an abstract in-memory table

The first draft of this idea (see the build log / chat history, 2026-09-22)
proposed hashing an in-memory settings blob to get a compact ID inside one
shared `provenance_table.json`. Rejected in favor of this doc's design for
a concrete reason: one of these inputs, the ignore mask, is naturally
*image-shaped* data rather than scalar settings - forcing it into a JSON blob
loses the ability to just open and look at what was actually used. (The first
draft counted the background estimate as a second such input; it is no longer
stored at all - see the background row below.) The design here instead keeps
each file-backed input as its
own real, independently-openable file, and a cell's provenance record is
just a set of short references (filenames) to them.

## The six provenance inputs and how each is stored

**Widened from five to six (2026-09-22)**: the original sketch §5 list
("this ROI's own geometry, the mask state..., the chromatic affine..., the
background model, the reduction method") omits `GeometryModule`'s own
crop/rotate/flip settings. That's a real gap, not a stylistic omission -
crop/rotate/flip changes the pixel grid every ROI's coordinates are already
defined against (`AGENTS.md`'s "ROI coordinates are in processed image
space" rule), so a crop/rotate change silently *not* triggering recompute
would be a real correctness bug, not just a missed optimization.

| Input | Format | Own version history? | Notes |
|---|---|---|---|
| Geometry (`GeometryModule`'s crop/rotate/flip settings) | JSON | via the settings snapshot (below) | small, dataset-wide, changes rarely - global-impact like chromatic/background |
| ROI geometry (this ROI's own shape/position) | JSON | via the settings snapshot (below) | small enough that no separate per-input versioning is needed |
| Ignore mask (`MaskModule`'s whole-image exclusion mask) | 8-bit grayscale PNG | yes, per (cube, wavelength, persi/indiv) | 8-bit leaves room for a future fractional/weighted mask, same idea as ROI's §6a work |
| Background (`BackgroundSettings` - the *method*, sigma/binning/exclusion flags) | JSON | via the settings snapshot (below) | small, dataset-wide, changes rarely - same treatment as Geometry above. The computed profile is **not** stored; see below |
| Chromatic model (affine matrix for one image key) | JSON | yes, per (cube, wavelength) - no persi/indiv tag, see below | every chromatic model is already frame-specific by nature |
| Reduction method (`"mean"`/`"median"`/...) | plain string, inline | no | too small to need its own file |

**What is deliberately *not* an input**: formula, fit method, fit window,
metric, and everything under Statistics. Those are the query layer
(`analysis/query.py`, `analysis/statistics.py`, built 2026-09-23) and they
re-derive from the stored pairs rather than changing them - sketch §6's
last bullet. Putting any of them in a snapshot would mean switching a combo
box invalidated the whole dataset, which is the outcome `compute_cell`'s
"store the raw pairs, stop there" shape exists to prevent. `reduction_method`
is the near-miss worth knowing: it *is* an input, because it decides how
pixels become the stored numbers in the first place.

Chromatic model's versioned-JSON treatment (same pattern as the mask image,
no persistent/local tag) confirmed 2026-09-22.

### The background row was rewritten 2026-09-23 - twice, and the second time reversed the first

The row originally asked for the *computed* background profile as a 16-bit
PNG versioned per `(cube, wavelength, persi/indiv)`, with a `MaskModule`-style
timeline on `BackgroundModule` named as a prerequisite. Both parts are gone.
The two corrections were separate decisions and are worth keeping apart,
because they were settled by different means:

**1. There is no persistent/individual timeline, settled by measurement.**
The `persi`/`indiv` tag presumed a "estimate the background once, carry it
forward" concept that exists nowhere in the code - `flatten_background()`
re-estimates from each frame's own pixels every time. Whether it *could*
instead be estimated once was a scientific question, and was measured on real
data rather than argued: it cannot. The illumination reshapes monotonically
over a run, and the part of that drift which does **not** cancel in a
sample/reference ratio crosses the single-frame shot-noise floor by about the
fifth cube, reaching ~2.9x it by the end of a 40-cube run. Full numbers and
caveats: `docs/background_drift_measurement_2026-09.md`.

**2. The computed profile is not stored at all - maintainer's decision,
2026-09-23.** Only the method (`BackgroundSettings`) is recorded, and because
that is flat and dataset-wide, it is identical for every cube and wavelength.
This supersedes that measurement doc's third conclusion, which had said
storing the profile was "still worth doing".

The reasoning, so it isn't re-litigated: the profile is a **deterministic
function of things this snapshot already fingerprints** - the raw frame
(fixed for a dataset), the geometry settings, the ignore mask, and
`BackgroundSettings`. Storing it therefore buys **zero** additional
invalidation power; nothing is cached more correctly because of it. Its only
value would be as an audit artifact, and that was measured too: at full
resolution a 16-bit PNG runs ~488 KiB per frame, which is ~4.0 GB for one
analysis of a 314-cube x 27-wavelength dataset, against a 30 GB dataset.
Downsampling to shrink it was tested and rejected - even 4x costs 107 ADU RMS
on the round trip, 55% of the single-frame shot-noise floor, because the
vignetting gradient at the frame edges is steeper than the 48 px blur radius
suggests. So the realistic choice was 4 GB or nothing, for an artifact that
improves no computation. Nothing.

**What this gives up, stated plainly**: if `estimate_background_profile()`'s
algorithm is ever changed, stored cells computed with the old algorithm will
**not** be invalidated - their recorded `BackgroundSettings` is unchanged, so
their fingerprints still match. Storing the profile would not have fixed that
either (it records what happened; it doesn't invalidate anything). The cheap
fix, if this ever matters, is a background-algorithm version constant carried
in the settings snapshot and bumped by hand when the estimator changes - not
yet built, deliberately, since the estimator is a verbatim port that nobody
is currently planning to change.

## Folder layout

```
analysis/
  data.h5                    # per-(ROI, cube) reduced values - unchanged from sketch §5
  settings/                  # "settings snapshots" - see Linking, below
  masks/
  chromatic/
  roi/
```

No `backgrounds/` folder - the background's method lives inside each settings
snapshot, and its computed profile is not stored (see the background row
above).

## Naming convention

Frame identity is always derived from `dataset/model.py`'s `ImageKey`
(`wavelength_nm`, `spectral_cube_index`) - already the app's own unified,
format-agnostic identity for "which frame," used the same way for both
TIFF-stack and OME-Zarr datasets (both get normalized into this same shape
on load). **Deliberately not** the legacy `WL500Frame1`-style filename
pattern `io/image_naming.py` parses - that's a device-specific convention
for reading one instrument's existing TIFF filenames, unrelated to what a
newly-designed file format should look like.

```
mask_cube{cube:0Nd}_wl{wavelength}_{tag}_v{version}.png
chromatic_cube{cube:0Nd}_wl{wavelength}_v{version}.json
```

- `cube` - zero-padded to `N` digits, where `N` is derived from the real
  dataset once loaded (`len(str(highest cube index))`), not a fixed guess.
- `wavelength` - decimal precision derived from the real dataset the same
  way, not fixed: round every wavelength in the dataset to 0 decimals
  (whole nm) and check whether any two distinct wavelengths collide (round
  to the same value). If none do, use 0 decimals everywhere (`wl500`). If
  some do, try 1 decimal, then 2, stopping at the first precision where
  every wavelength is distinguishable (`wl500.5` etc.) - confirmed
  2026-09-22: real acquisition hardware isn't expected to need this, but
  the naming stays correct if it ever does. Computed once per dataset,
  applied consistently to every wavelength-derived filename in that
  dataset's `analysis/` folder (mixing precisions within one dataset would
  break simple pattern-matching/sorting). Small illustrative sketch:

  ```python
  def wavelength_decimal_precision(wavelengths_nm: list[float], max_decimals: int = 3) -> int:
      """Smallest decimal precision at which every wavelength in the
      dataset stays distinguishable from every other - checked once per
      dataset, then used for every wavelength-derived filename in it."""
      for decimals in range(max_decimals + 1):
          rounded = [round(wl, decimals) for wl in wavelengths_nm]
          if len(set(rounded)) == len(wavelengths_nm):
              return decimals
      return max_decimals  # pathological collision even at max precision - not this naming scheme's problem to solve further
  ```
- `tag` - `persi` or `indiv` (both 5 characters, deliberately - keeps every
  filename in a folder listing aligned at the same column width). **Renamed
  from an earlier `persi`/`local` draft (2026-09-22)**: `MaskModule`
  (`image_tools/mask/module.py`) already implements exactly this
  persistent/individual timeline - built 2026-09-21, before this design
  conversation - using `scope: "individual" | "persistent"` as its real,
  shipped terminology. Matching it (`indiv` abbreviates "individual" the
  same way `persi` abbreviates "persistent") avoids a mismatch between what
  a file is tagged and what the code that produces it actually calls the
  same concept. `persi` = applies dataset-wide (from this cube onward,
  cube-granularity only - `MaskChange`'s own docstring: "no per-wavelength
  splitting of persistence") unless superseded by a later persistent
  change; `indiv` = applies to this exact frame only. Used **only** for mask
  filenames - the mask is the one input with a real timeline. A chromatic
  model is always frame-specific already, so no "dataset-wide" concept
  applies to it; background has no file of its own at all.
- `version` - see below.

## Versioning and dedup (no hashing)

Each (cube, wavelength, tag) group has its own independent sequential
version counter, starting at `v1`. When a new mask/background/chromatic
model would be saved: compare it directly against whatever versions already
exist for that *same* group (there are only ever a handful per group, so a
direct content comparison is cheap - no need for a hash). If it matches an
existing version, reuse that version's number - nothing new is written. If
not, it gets the next unused number.

This was a deliberate simplification from an earlier hash-based proposal
(see the earlier version of this design in chat history) - a hash gives the
same dedup correctness but produces meaningless-looking filenames; a small
per-group version count is exactly as correct here (the groups are small
enough that direct comparison is cheap) and is actually readable - `v1`,
`v2`, `v3` tells a human "this frame's mask has been edited twice," where a
hash tells them nothing without cross-referencing something else.

**Versions are written lazily, only when actually analyzed** - a file only
gets created at the moment `compute_cell` uses a given snapshot to produce
a real stored result, not on every intermediate UI edit while a mask is
still being dragged around. This falls out naturally from tying the write
to real computation rather than to settings changes, and directly avoids
the "don't store every edit" concern raised during design.

## Linking: the settings snapshot

A cell's provenance doesn't repeat the actual mask/background/chromatic
content - it references which specific versioned files were in effect. A
small JSON "settings snapshot" under `analysis/settings/` ties a
*combination* of these together (e.g. "ignore masks in effect: {persi v1},
background: {sigma 48, binning 2, exclude ROIs}, chromatic: {v3}, reduction:
mean") and is itself
deduplicated the same way: an unchanged combination (e.g. toggling a
per-frame mask off and getting back to exactly what was in effect before)
reuses the existing snapshot rather than creating a new one - worked
through concretely in chat via a mask1/mask2/mask3 example, confirmed
correct.

## Implementation status (2026-09-22)

Built: `analysis/provenance.py` (naming, adaptive precision, versioning/
dedup, file-backed mask/chromatic/settings-snapshot persistence,
`compute_fingerprint`), wired into `planner.py`/`tasks.py`/`engine.py`.
Full detail and what's still deferred: `docs/rewrite_build_log_2026-09.md`,
2026-09-22 entry.

**Resolved while implementing** (removed from "still open" below):
- Settings-snapshot JSON field shapes and its own ID scheme - a flat,
  dataset-wide sequential version counter (`settings_v1.json`,
  `settings_v2.json`, ...), same dedup-by-content-comparison rule as
  masks/chromatic models. `SettingsSnapshot.as_json()` is the real field
  shape (`geometry`, `mask`, `chromatic`, `background`, `reduction_method`).
- **`data.h5` itself, built 2026-09-22** (`analysis/store.py`) - maintainer
  confirmed compatibility with `packages/lspr_io`'s existing
  `lspr_measurement` schema is explicitly not a goal right now (that schema's
  `signature_hash` is one opaque combined hash, which is exactly the
  single-hash shape this whole design moved away from - see "Why
  file-based provenance" above); the store uses its own minimal,
  independent identity stamp instead. Avoids HDF5's lack of safe
  concurrent cross-thread read/write by construction, not locking:
  `AnalysisEngine` bulk-loads `data.h5` into `InMemoryProvenanceStore`
  once at construction and answers every query from memory afterward,
  never reading the file per-query; `run_analysis` writes each computed
  cell to both the in-memory store and the file together, from the single
  background thread that ever computes anything. Verified: a second,
  independent `AnalysisEngine` instance pointed at the same `data.h5` path
  (simulating an app restart) rehydrates prior results with zero
  recomputation.
- **Background provenance, 2026-09-23 - complete, with no code change.**
  `SettingsSnapshot.background` already carries `asdict(BackgroundSettings)`,
  and after the decision not to store the computed profile that *is* the
  finished design rather than the interim fallback the original row called
  it. The only work this took was correcting this document and the
  now-stale "interim scope" comments in `analysis/provenance.py`.

## Reference-ring exclusion modes (added 2026-09-23)

A seventh input, added after a correctness gap was found while
investigating sketch §6's "ROI-adjacency exception": the old app removes
pixels belonging to other ROIs' sample apertures from a reference ring
(otherwise a nearby, often much brighter sample spot inside the ring
biases the reference mean), and the rewrite's `compute_cell` had no way to
do that - it only ever saw one ROI.

Maintainer's decision, as a toggle rather than always-on:
- `"none"` - no cross-ROI exclusion; ROIs interact only through the ignore
  mask. Current default.
- `"exclude_all_sample_rois"` - a reference ring never counts a pixel
  inside *any* ROI's sample aperture. Overlapping *reference* rings are
  still counted normally; only sample pixels are removed.

**Computed from every ROI, never from the selected subset** - the old app
built this union from the selected ROIs, which made a stored value depend
on what else happened to be selected when it was computed. Making it
all-ROIs removes that dependency entirely and is what keeps the
fingerprint tractable.

**Fingerprint impact**: in `"exclude_all_sample_rois"` mode, moving ROI X
genuinely changes ROI Y's reference pixels, so Y's stored value must be
invalidated when X moves - `sample_exclusion_digest(all_rois)` is recorded
in `SettingsSnapshot` for exactly this. It's recorded *only* in that mode:
in `"none"` mode other ROIs aren't an input, and including it would
invalidate every cell on any ROI move for no reason. It lives in the
snapshot (not `ProvenanceRecord`) because snapshots are deduplicated and
referenced by a small integer - embedding an all-ROI digest in every
(ROI x cube) cell instead would be the difference between a few hundred KB
and well over a GB at realistic counts.

**Default resolved 2026-09-23**: `"exclude_all_sample_rois"`, not the
initial `"none"` (`provenance.DEFAULT_REFERENCE_EXCLUSION_MODE`). `"none"`
was the *safe* default - it changes nothing relative to code predating the
mode - but not the right one: the stable app effectively behaved like
`"exclude_all_sample_rois"` whenever more than one ROI was selected, which
is the normal case, so `"none"` matched only its single-ROI behaviour and
would have shipped a biased reference value for any two ROIs close enough
that one's sample circle falls inside the other's reference ring.

## Background exclusion (added 2026-09-23)

An eighth input, and the fingerprint half of a port gap found in
already-committed code: `compute_cell` called `apply_preprocessing` with
neither `rois` nor `mask_settings`, leaving *both* of `BackgroundSettings`'
exclusion toggles inert on the analysis path -
`flatten_background_exclude_area_rois` (default **on**) and
`flatten_background_exclude_mask`. Full finding, measured impact and the
display-path half: `docs/rewrite_build_log_2026-09.md`, the 2026-09-23
entry that closes it.

Wiring those two arguments through makes other ROIs an input to the
background estimate, which is the same invalidation problem the
reference-ring exclusion has: with ROI exclusion on, moving ROI X changes
the background estimate under ROI Y, so Y's stored cells must be
invalidated when X moves. `SettingsSnapshot.background_exclusion` records
it, via `background_exclusion_digest(...)`:

| Recorded | When | Why that and not more |
|---|---|---|
| `"rois"` - each ROI's id, centre, `sample_radius_px` | flattening on **and** `flatten_background_exclude_area_rois` | exactly the three fields `_roi_exclusion_mask` reads; it works off the radius field and never consults `sample_diameter_px` or a sample mask, so recording those would invalidate cells on edits the estimate cannot see |
| `"ignore_marked_pixels"` | flattening on **and** `flatten_background_exclude_mask` | the one `AreaRoiDetectionSettings` field that reaches the estimate (`ignored_pixel_mask` gates the whole external mask on it); flipping it changes the result while `BackgroundSettings` stays identical |

Two details worth keeping:

- **Absent, not `null`, when there is nothing to record.** Unlike every
  other snapshot field, `as_json()` omits this one when it is `None`. A
  snapshot is deduplicated by comparing it against already-written files,
  so an unconditional `"background_exclusion": null` would differ from
  every file written before the field existed - inflicting a full
  recompute on exactly those analyses (background flattening off) that this
  change cannot possibly affect.
- **The ROI exclusion is not chromatically warped**, matching both the old
  app and `_roi_exclusion_mask`'s own shape: it grows each ROI's radius by
  35% before excluding it, a margin far wider than a chromatic affine's
  shift. So the digest needs no affine either, and the chromatic model
  stays a separate input.

Also, like the reference-ring exclusion and for the same reason: the ROI
set is **every** ROI, never the selected subset the old app used.

## Still open

- No UI exposes the reference-exclusion toggle yet - it's an injected
  setting on `AnalysisEngine` with no panel behind it (the panel layer
  doesn't exist).
- **An algorithm-version constant is still not built** (see the background
  row): changing `estimate_background_profile()` will not invalidate stored
  cells. Deliberate, since the estimator is a verbatim port nobody plans to
  change - but note the background *exclusion* work above widened what feeds
  that estimator, so a future change there is now slightly more likely.
- TIFF→OME-Zarr "carry masks/backgrounds along" tooling - noted in chat as
  likely unnecessary work (frame identity via `ImageKey` doesn't change
  between formats, so no re-keying is needed); at most a "copy the
  `analysis/` folder alongside an exported dataset" convenience feature,
  not yet designed or committed to.
