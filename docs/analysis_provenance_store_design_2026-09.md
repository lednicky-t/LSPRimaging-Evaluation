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
a concrete reason: several of these inputs (the ignore mask, the background
estimate) are naturally *image-shaped* data, not scalar settings - forcing
them into a JSON blob loses the ability to just open and look at what was
actually used. The design here instead keeps each of these inputs as its
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
| Background (the *computed* profile, not just settings) | 16-bit grayscale PNG | yes, per (cube, wavelength, persi/indiv) | stores what was actually subtracted, not just the sigma/binning that produced it - survives an algorithm change later. **Interim scope note**: `BackgroundModule` has no timeline mechanism yet (unlike `MaskModule` - it's flat, dataset-wide `BackgroundSettings`, no persistent/individual concept exists to hang a per-frame image on). Implementing this as designed needs `BackgroundModule` extended with a `MaskModule`-style timeline first - not yet built, flagged as a real prerequisite, not assumed away. Until then, background provenance falls back to a small settings JSON (sigma/binning/exclusion flags), the same shape as Geometry above. |
| Chromatic model (affine matrix for one image key) | JSON | yes, per (cube, wavelength) - no persi/indiv tag, see below | every chromatic model is already frame-specific by nature |
| Reduction method (`"mean"`/`"median"`/...) | plain string, inline | no | too small to need its own file |

Chromatic model's versioned-JSON treatment (same pattern as mask/
background, no persistent/local tag) confirmed 2026-09-22.

## Folder layout

```
analysis/
  data.h5                    # per-(ROI, cube) reduced values - unchanged from sketch §5
  settings/                  # "settings snapshots" - see Linking, below
  masks/
  backgrounds/
  chromatic/
  roi/
```

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
bg_cube{cube:0Nd}_wl{wavelength}_{tag}_v{version}.png
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
  change; `indiv` = applies to this exact frame only. Not used for
  chromatic model filenames (always frame-specific already, no
  "dataset-wide" concept applies).
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
background: {persi v1}, chromatic: {v3}, reduction: mean") and is itself
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

## Still open

- **Which mode should be the default.** Currently `"none"` (preserves
  "everything as it is"). Worth noting the stable app effectively behaved
  like `"exclude_all_sample_rois"` whenever more than one ROI was selected,
  so `"none"` is not strictly "what the old app did" - it matches the
  old app's *single-ROI* behavior. Flagged for the maintainer rather than
  silently picking the scientifically-preferable one.
- No UI exposes the toggle yet - it's an injected setting on
  `AnalysisEngine` with no panel behind it (the panel layer doesn't exist).
- TIFF→OME-Zarr "carry masks/backgrounds along" tooling - noted in chat as
  likely unnecessary work (frame identity via `ImageKey` doesn't change
  between formats, so no re-keying is needed); at most a "copy the
  `analysis/` folder alongside an exported dataset" convenience feature,
  not yet designed or committed to.
