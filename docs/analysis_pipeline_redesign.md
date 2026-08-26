# Analysis Pipeline & Caching Redesign

**Status: partially implemented.** Written after a maintainer-driven review of the whole analysis
pipeline (dataset load -> image tools -> ROIs -> preprocessing -> analysis -> caching). Goal:
reduce recomputation of unchanged data and make the app responsive on large datasets (reference
scale used throughout: 170 ROIs x up to 20,000 spectral cubes x ~50 wavelengths). \S2a, \S2b, and
\S3 are implemented and tested; \S2c's exclusion-signature fix and \S4c (schema + read-before-
recompute wiring for the sensorgram sweep) are also implemented and tested. \S2c's general bulk-
clear removal (item 4b) is deliberately deferred, and \S4b/\S4e (RAM cache as a Preferences
setting, removing `analysis_cache` from the JSON profile) are still proposals pending real-scale
proof and the sign-offs in \S6 - see \S7 for the current order/status of each piece.

This doc records what's already true in the code (so we don't re-debate settled ground), what's
actually broken, and what's proposed as new work - each item tagged so it's clear which parts
need sign-off before any code changes.

---

## 1. What already matches the target design (no change needed)

- **Image tools are already a "math layer," not eager pixel mutation.** Rotation/flip/crop and
  the ignore mask are pure parameters (`PreprocessingSettings`/`MaskSettings`,
  `domain/models.py:92-155`); the geometric remap (`processing/preprocess.py:501`) is computed
  lazily on every read, never cached as a transformed array. Chromatic correction is the same
  shape: an affine per `(cube, wavelength)` applied to ROI *coordinates* on demand
  (`processing/chromatic.py`), not a warped image.
- **Chromatic correction already varies per cube.** `ChromaticTransformModel`
  (`domain/models.py:306-318`) is keyed by `(spectral_cube_index, wavelength_nm)` - it's already
  the "one per-frame transform" pattern the rest of this doc wants to generalize.
- **Background removal is correctly identified as structurally different.** `flatten_background()`
  / `estimate_background_profile()` (`processing/preprocess.py:548,642`) do a real masked
  Gaussian-blur computation over actual pixel values - it can't be expressed as a coordinate
  recipe, and any change to ROI positions, exclusion mask, or sigma genuinely requires
  recomputing it. Any invalidation scheme must treat it (and chromatic, for the same reason) as
  "always a real recompute," never a cheap geometry-only pass.
- **Analysis is already selection-scoped, not all-ROIs-by-default.** Every entry point
  (`gui/analysis_controller.py:323,1273`) operates on `_selected_roi_ids`
  (`main_window.py:364`) and refuses to run with nothing selected. Selecting a subset of ROI-table
  rows already *is* "analyze just this subset" - no new mechanism needed here.
- **A cube/time toggle already exists** in the Metadata header and above the cube slider
  (`gui/layout_builder.py:479-519,534-571`), state in `main_window.py:312`. It's the right place
  for the new per-cube-timestamp-rule control proposed in \S3.
- **A persistent, signature-keyed result cache already exists and survives restarts** -
  `analysis_cache` in the session's processing-profile JSON (`storage/workspace.py:663-664`,
  `gui/analysis_controller.py:2827-2919`), covering `absorbance_spectrum_cache`,
  `absorbance_spectral_cube_cache`, `roi_absorbance_cache`, `sensorgram_cache`. Each entry is
  keyed by a composite signature (dataset, ROI geometry, per-`(cube, wavelength)`
  preprocessing/chromatic state, wavelength range, formula/reduction settings) - this is already
  most of the "container that fills in incrementally, as long as parameters match" mechanism
  described in the original request. The problem with it is scale, not concept (\S4).
- **A real HDF5 backup/export writer already exists and is wired into the GUI** -
  `storage/measurement_export.py`'s `ImagingMeasurementExportWriter`, writing into the shared
  `lspr_measurement` schema also used by sLSPR acq (`docs/imaging_measurement_export_format.md`).
  It already does resizable, one-row-at-a-time appends for absorbance spectra and sensorgram
  points, per ROI (`measurement_export.py:230-233,259-290`) - structurally the same "backup file
  IS the export file" pattern sLSPR acq uses (confirmed in \S5). **The doc it's built from is
  stamped "proposed, not implemented" - that status line is stale and should be corrected once
  this doc's plan is agreed, since the writer is real, tested-shaped code already in the GUI path.**

## 2. Confirmed gaps and one real bug

### 2a. Bug: measurement-export backup is destroyed on every dataset reload — FIXED

`dataset_controller.py:263` opened the backup file with `h5py.File(path, "w")` (write-truncate)
every time a dataset finished loading, including re-opening a dataset already analyzed in a
previous session - silently wiping the prior backup before the first new row was written.

**Fixed**: `ImagingMeasurementExportWriter.__init__` (`storage/measurement_export.py`) now opens
with `"a"` when the file already exists, preserving the original identity metadata
(`created_at_utc`/`started_at_utc`) instead of re-stamping it. Two new methods,
`existing_absorbance_keys()` and `existing_sensorgram_keys()`, scan just the small `cube_index`
columns (never the bulk float arrays) to rebuild the in-memory backed-up-already sets on reopen -
wired into `dataset_controller.py`'s `_open_measurement_export_writer_for_dataset()`. This
required adding a `cube_index` column to the sensorgram group (it previously stored only
`timestamp_utc_ms`/`metric_value`, which isn't reliably invertible back to a cube index) - a
small, additive, backward-compatible schema addition, independent of the larger `signature_hash`
question in \S6. `lspr_io.read_sensorgram()` was extended to expose this column when present.
Covered by `tests/unit/test_lspri_measurement_export.py::test_reopening_existing_backup_preserves_data_and_recovers_keys`.

### 2b. `rois_for_preprocessing()` is the one ROI-position call that isn't cached — FIXED

Display-time ROI positions are cached, signature-keyed (`gui/image_render_manager.py:484-511`).
The other consumer - `rois_for_preprocessing()`, which feeds background-exclusion math - was
recomputed from scratch on every call.

**Fixed**: same signature scheme as `display_rois()` (image key, ROI geometry, per-wavelength
overrides, chromatic signature - image shape omitted since this consumer never used it, unlike
the display cache), new single-slot cache fields `_preprocessing_roi_cache_signature`/`_value` on
the window, cleared alongside `_display_roi_cache_*` in the existing
`_invalidate_per_frame_display_caches()` (main_window.py) so it rides the same, already-correct
invalidation triggers (ROI edits, mask/chromatic/image-tools changes, dataset load) with no new
call sites needed.

### 2c. Fine-grained signature caching is undermined by coarse bulk-clears — exclusion gap fixed

Every mutation site (ROI edit, mask change, chromatic change, image-tools change) calls
`_invalidate_image_analysis_caches()` (`main_window.py`), which clears the **entire**
`absorbance_spectrum_cache`/`absorbance_spectral_cube_cache`/`roi_absorbance_cache` outright -
even for ROIs/cubes whose own signature didn't change. One specific, previously-documented
instance of this - the exclusion-rule set not being part of any signature - is now fixed (see
below). **The general bulk clear on ROI/mask/chromatic/image-tools changes is a separate, larger
question and was deliberately NOT touched by this fix** - investigating it surfaced that
`_absorbance_spectrum_cache`/`_roi_absorbance_cache` only fold in chromatic state today, not the
full rotation/crop/background/mask preprocessing signature the sensorgram caches use. Those two
caches still genuinely depend on `_invalidate_image_analysis_caches()`'s bulk clear for
correctness against everything except chromatic and (now) exclusion changes - removing that bulk
clear would need those two signatures extended with full preprocessing state first, which is a
separate, its-own-sign-off piece of work, not a trivial follow-on to the exclusion fix.

**Exclusion-rule fix, implemented**: a new `AnalysisController._exclusion_signature_for_cube()`
resolves each wavelength in a cube to a plain "is this frame currently excluded" bool via the
existing `is_excluded()` helper (which already collapses whole-cube/whole-wavelength wildcard
rules down to a per-frame answer, so no rule-list hashing is needed). This is now folded into all
four places that previously omitted it entirely: `_roi_absorbance_signature`,
`_absorbance_spectrum_signature_for_source_rois`, `_sensorgram_signature_for_selection`, and
`_sensorgram_spectral_cube_payload_signature`. With that in place, `_invalidate_caches_for_
exclusion_change()` (triggered from `image_exclusion_controller.py` when a rule is added/removed)
no longer needs to clear any cache at all - a rule change now produces a natural signature miss
only for the cube(s)/ROI(s) actually affected, while every other already-computed result stays a
valid hit. It still refreshes the ROI-table "already calculated" indicator snapshot (a cached set
that doesn't self-correct the way the caches themselves do) and marks the sensorgram stale, same
UX as any other analysis-affecting setting change.

### 2d. No first/last/midpoint option for deriving a cube's timestamp — FIXED

Only one rule existed: earliest frame in the cube (`_acquisition_timing_index()` kept the `min()`
of `acquired_at_unix_ms`). No last-frame or midpoint-of-first-and-last option.

## 3. Cube-timestamp rule toggle — implemented

- `_acquisition_timing_index()` (`analysis_controller.py`) now builds `per_cube_earliest` **and**
  `per_cube_latest` together in the same one-pass scan (previously only earliest). New
  `_cube_timestamp_ms_by_cube_index(metadata)` resolves each cube's representative timestamp per
  `window._cube_time_timestamp_rule` (`"first"` / `"last"` / `"midpoint"`, default `"first"` -
  unchanged default behavior).
- Both existing consumers now go through it: `_cube_time_display_text_for()` (the spinbox
  display) and `_sensorgram_x_values()` (the live plot's x-axis, which also redraws immediately
  when the rule is cycled if a sensorgram is already plotted, via `set_sensorgram_series()`).
- **Deliberate exception**: `_acquisition_timestamp_ms_for_cube()` - the timestamp written into
  the HDF5 measurement-export backup (\S1) - always uses the earliest frame regardless of this
  rule. A *display* preference shouldn't retroactively change what gets persisted as the record's
  timestamp; this keeps the toggle a pure viewing choice.
- UI: a second click-to-cycle control, `(First)`/`(Last)`/`(Mid)`, added next to the existing
  `[Cube]`/`[Time]` toggle in the Metadata section header row (both now live in one small
  container widget, `metadata_header_extra`, in `layout_builder.py`). It's hidden whenever Time
  mode isn't active or the toggle itself isn't available (no acquisition timing metadata loaded),
  so it never appears with nothing to control.

## 4. Proposed: unify the RAM cache and the HDF5 backup into one two-tier cache

### 4a. Why the current JSON-embedded cache doesn't scale

At the stated reference scale (170 ROIs x 20,000 cubes x 50 wavelengths = 170,000,000 floats):

| Representation | Size |
|---|---|
| Binary array, float64 | ~1.36 GB |
| Binary array, float32 | ~0.68 GB |
| Today's format: JSON text, ~3.4M separate `{signature: result}` entries | roughly 6-12 GB |

The raw numbers are small for a binary store (HDF5 handles this trivially, with chunked partial
I/O). The problem is that `analysis_cache` is embedded as one field in the session's
processing-profile **JSON**, and `_analysis_cache_payload()` / `_restore_analysis_caches()`
serialize/deserialize that **whole dict in one shot** on every save/load
(`storage/workspace.py:663-664`, `session_state_manager.py:123,374,478,553`). At scale that's a
multi-gigabyte JSON rewrite on every save - this is what actually breaks, not the data volume.

### 4b. Proposed shape: RAM hot-cache (bounded, user preference) + HDF5 disk-of-record (unbounded)

- **RAM tier**: today's LRU dicts (`roi_absorbance_cache`, `sensorgram_cache`, etc.,
  `main_window.py:234-235,422-427`), size becomes a **user preference**
  (`gui/preferences_dialog.py` already has the pattern for numeric settings) - e.g. "Analysis
  cache size (entries kept in memory)", defaulting near today's 512/48, with a live estimated-MB
  readout next to the spinbox so the number means something concrete. This tier's only job is
  avoiding recomputation *within* a session; it is not the thing responsible for durability.
- **Disk tier**: `ImagingMeasurementExportWriter`'s HDF5 file becomes the actual backing store for
  "has this ROI/cube/parameter combination already been computed, ever" - not just a passive
  backup written after the fact. `analysis_cache` is removed from the JSON processing profile
  entirely once this is in place; JSON keeps settings and ROI geometry only, which is what it's
  already good at.

### 4c. Required changes to make the HDF5 writer usable as a read-first cache, not just a backup

1. ~~**Fix the truncation bug (\S2a)**~~ - **done** (\S2a).
2. **Record the parameter signature per row - done, write-side only.** `signature_hash` (sha256 of
   a JSON-canonicalized signature tuple, `AnalysisController._signature_hash`) is now written
   alongside `cube_index`/`timestamp_utc_ms` in both the sensorgram and absorbance-spectra groups
   (schema 6.6 - additive, see `schema.py`'s changelog). New columns on a group that already has
   rows from an older writer version are backfilled row-aligned (`_ensure_column`), not left at
   length 0 next to longer siblings - a real bug caught while implementing this, covered by
   `test_reopening_legacy_group_backfills_new_columns_row_aligned`. Dedup-on-append is now
   signature-aware: `existing_absorbance_keys()`/`existing_sensorgram_keys()` return
   `(roi_id, cube_index, signature_hash)` triples, so a value recomputed under changed settings
   appends a fresh row instead of being mistaken for a duplicate of the (now-stale) old one.
   - **Sensorgram** uses `_sensorgram_point_signature_hash()`, which is the payload signature
     (\S2c's `_sensorgram_spectral_cube_payload_signature`, already covering preprocessing/
     chromatic/ROI-geometry/exclusion) **plus fit method/metric/poly order** - unlike the RAM
     result cache (which stores the full fit-independent spectrum and deliberately excludes these
     from its signature), the HDF5 row only stores the already-reduced final scalar, so a
     fit/metric change must count as a different value here.
   - **Absorbance spectra** uses the existing (narrower, \S2c/4b) `_roi_absorbance_signature` -
     written now for forward compatibility, but seen the same 4b gap the RAM cache has (missing
     full preprocessing state and ROI geometry). Recorded so no further schema bump is needed once
     4b is eventually done; not yet trustworthy enough to read back as a cache hit.
3. **Read-before-recompute wiring - implemented (option (a): a second, narrower cache-hit path).**
   Resolves the granularity mismatch described in the original version of this section: the RAM
   result cache the sweep worker normally consults (`_cached_sensorgram_spectral_cube_result`)
   returns a full pre-fit `AbsorbanceSpectrumResult`, which the worker then fits a metric from -
   that's *how* changing only the fit method avoids re-reading pixels. The HDF5 backup only ever
   stores the final, already-reduced scalar `metric_value`, which can't reconstruct that object.
   Rather than trying to make a disk hit repopulate the RAM spectrum cache, the sweep worker
   (`_sensorgram_metric_task`, `analysis_tasks.py`) got a second, independent cache-hit path,
   `metric_value_cache_get`, checked *before* `spectral_cube_result_cache_get`: on a hit it skips
   the pixel read AND the fit entirely for that cube, returning the disk-stored `metric_value`
   directly. It's deliberately scoped narrower than the RAM cache - it never populates
   `_cached_sensorgram_spectral_cube_result`, so the single-cube live-preview display (which reuses
   that RAM cache to show a full spectrum when browsing to an already-swept cube, see
   `analysis_controller.py:2839`) is untouched and still recomputes on browse exactly as before for
   any cube a disk hit covers - no regression, just no new benefit there (a live-preview disk
   fallback would need the RAM cache's contract to change, which is out of scope here).

   Concretely: `ImagingMeasurementExportWriter.sensorgram_metric_index(roi_id)`
   (`storage/measurement_export.py`) is the read-side counterpart of `existing_sensorgram_keys()` -
   it returns `cube_index -> (signature_hash, metric_value)` for the *latest* row per cube_index
   (later rows supersede earlier ones per §4d's append-only rule), read from the writer's
   already-open handle so it's safe to call mid-run. `_start_sensorgram_worker`
   (`analysis_controller.py`) builds this index once per sweep (keyed by the same backup roi_id/
   combined-id the write path uses - factored into a shared `_sensorgram_backup_roi_key` helper so
   both sides can never drift apart) and passes a `metric_value_cache_get` closure into the worker
   that only returns a value when the live `_sensorgram_point_signature_hash` for that cube exactly
   matches the stored hash. `metric_signal` (a secondary, only-used-for-a-single-cube-preview-plot
   value) isn't persisted on disk, so a disk hit reports it as unavailable (`None`) rather than a
   stale guess - confirmed nothing currently plots a full-sweep `metric_signal` series, so this
   loses no working UI. Covered by
   `tests/unit/test_lspri_sensorgram_disk_metric_shortcut.py` (worker-level: a disk hit must skip
   both the RAM-cache lookup and the fit task, a miss must compute normally) and
   `tests/unit/test_lspri_measurement_export.py::test_sensorgram_metric_index_keeps_latest_row_per_cube`
   (writer-level: index reflects the latest row per cube, not the first).

### 4d. Handling a stale row without rewriting the file - implemented (write-side)

HDF5 resizable datasets are cheap to *append* to, not to delete-and-reorder in place. When a
parameter change makes an existing on-disk row stale (e.g. an ROI moved), the writer **appends a
new row with the new `signature_hash` rather than overwriting the old one in place** (\S4c above).
The reader-side half of this - "build a small in-memory index of the highest row index whose hash
matches the live signature, per (roi_id, cube_index)" - is exactly what item 3's read-before-
recompute wiring would need to do; not built yet since nothing reads these rows back as cache hits
yet. This means a few superseded rows accumulate per recomputed entry (acceptable - recompute-
after-parameter-change should be rare relative to total entries) rather than ever risking
corrupting the file with an in-place rewrite, which fits this repo's "raw data is sacred" instinct
even though this is derived data, not raw acquisition.

### 4e. What this buys, concretely

- The scale problem in \S4a disappears: the JSON profile stays small (settings + ROI geometry
  only); the HDF5 file scales to the full 170-ROI x 20,000-cube case at ~0.7-1.4 GB, which is a
  non-issue for chunked, partial-read binary storage.
- "Compute 10% now, another batch later, in any order" already works today for the RAM-only cache
  within a session; this makes it also work **across app restarts**, which is the part that's
  currently missing (because of the \S2a bug and because nothing reads the backup file back).
- The backup file and the analysis result cache stop being two unrelated things (one passive/dead,
  one JSON-embedded and scale-limited) and become one system, matching how sLSPR acq already
  treats its own backup/export file as a single source of truth.

## 5. Confirmed: this mirrors sLSPR acq's existing pattern, not a new invention

Verified directly in `apps/sLSPR/acq`:

- Two writer *instances* of the identical `HDF5MeasurementWriter`/`AsyncHDF5MeasurementWriter`
  class - one auto-created "session backup" (`measurement_archive.py:41-131`,
  `session_backup_{timestamp}.h5`, always-on) and one user-initiated "measurement export"
  (`gui/acquisition_controller.py:1377-1436`) - same schema, same class, differing only in when/why
  they were created, not in structure.
- Both are scaffolded fully at creation (root/manifest metadata, all groups) and then **appended
  to incrementally** during acquisition via resizable-dataset `resize()` + slice-assignment
  (`storage/hdf5_export.py:310-394`), never rewritten wholesale.
- `save_session_copy_as_for()` (`gui/main_window_session_copy.py:12-52`) lets a user directly
  "Save session copy As..." the live backup file - it's already fully valid as an export, not a
  degraded internal format needing conversion.
- LSPRi eva's own `measurement_export.py` docstring already draws this exact analogy
  ("the same property that makes sLSPR acq's HDF5 format usable as a live backup, not just a
  final export") - the intent was already there; \S4 is what's needed to make it real for the
  analysis-result-caching use case specifically (sLSPR acq doesn't have this problem in the same
  way - it acquires each measurement once, it doesn't repeatedly reanalyze the same cube under
  changing parameters the way LSPRi eva's ROI/mask/chromatic workflow does).

## 6. Open decisions needing explicit sign-off before implementation

- **Schema change**: adding a `signature_hash` column to `/rois/<roi_id>/processed/absorbance`
  and the sensorgram group is a layout change to the LSPRi-eva-authored part of the
  `lspr_measurement` schema (schema major 7 per `imaging_measurement_export_format.md`, itself
  still unimplemented pending its own open questions about `metric_name` enum vs. open string and
  dark/reference correction - those are unrelated to this doc and should stay separately scoped).
  Confirm whether `signature_hash` should be added to that same major-7 proposal before it's
  implemented, rather than bolted on afterward as a major 8.
- **Removing `analysis_cache` from the JSON processing profile** is a session-file-format change -
  needs confirmation that nothing currently depends on reading that JSON field directly (e.g. an
  external script, or a support workflow) before it's dropped.
- **Correcting the stale "proposed, not implemented" status** in
  `imaging_measurement_export_format.md` now that the writer is real code in the GUI path.

## 7. Suggested implementation order

1. ~~Fix the truncation bug (\S2a / \S4c-1)~~ - **done**.
2. ~~Cube-timestamp rule toggle (\S3)~~ - **done**.
3. ~~Cache `rois_for_preprocessing()` (\S2b)~~ - **done**.
4. ~~Close the exclusion-rule signature gap and remove its now-redundant bulk clear (\S2c)~~ -
   **done**. Does NOT yet deliver "moving one ROI only recomputes that ROI" in general - that
   needs item 4b below first.
4b. **Deliberately deferred, not started.** Extending `_absorbance_spectrum_signature_for_source_
    rois`/`_roi_absorbance_signature` to make their bulk clear removable turned out bigger than
    scoped: those two caches are missing not just full preprocessing state but the selected ROIs'
    own geometry (only the ROI *id* is in the signature today - the actual computation reads
    `_rois_for_preprocessing()`, which reflects center/radius/mask). Getting this right needs a
    real audit against everything `_prepare_absorbance_spectrum_payload_for_spectral_cube`/
    `_prepare_fast_spectrum_payload_for_spectral_cube` actually read, not a small follow-on to the
    exclusion fix. It's also lower-payoff than it looked: this pair serves the single-cube live
    preview (recomputed on nearly every settings tweak, for one displayed cube), not the sensorgram
    sweep that matters at the 170x20,000-cube scale this doc exists for - the sensorgram caches
    already carry full preprocessing state correctly. Decision (maintainer, this session): skip 4b
    for now, keep its bulk clear as a deliberate, documented correctness net, and prioritize item 5
    instead, which targets the actual stated scale problem. Revisit 4b only if the single-cube
    preview's recompute cost is ever actually observed to matter.
5. ~~Add `signature_hash` to the HDF5 writer (§4c-2, §4d)~~ - **done** (schema bump to 6.6,
   sign-off obtained: hash not full-JSON signature). ~~Read-before-recompute wiring (§4c-3)~~ -
   **done** (option (a): a second, fit-skipping cache-hit path scoped to the sweep worker only, see
   §4c item 3 above). This is now proven at the unit-test level (disk hit skips read+fit, miss
   computes normally, index reflects latest row per cube); it hasn't yet been exercised against a
   real large dataset at the 170×20,000-cube reference scale - that's the natural next check before
   fully trusting item 6 below.
6. Remove `analysis_cache` from the JSON profile once (5) is fully proven (including the read
   side), and make the RAM cache size a Preferences setting (§4b) - last, since it depends on (5)
   actually working end-to-end.

## Where this document lives

Inside the `apps/LSPRi/eva` submodule (its own git repository) - committing it requires a commit
inside `apps/LSPRi/eva` first, then a submodule-pointer bump commit in the umbrella repo, per
`CLAUDE.md`'s Submodule Workflow section.
