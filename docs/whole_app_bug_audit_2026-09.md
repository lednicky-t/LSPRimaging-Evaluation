# LSPRi Evaluation — whole-app bug/data-flow audit (2026-09-08)

Full-app correctness audit requested by the maintainer ("check LSPRi eva app as whole... possible
bugs or logical hiccups, and data flow. Also check analysis once more if it can be improved").
Baseline: all 554 LSPRi-tagged tests in `tests/unit` + `tests/integration` passed before and after
this audit — every finding below is a gap current tests don't cover, not a regression.

Method: five parallel deep-read agents, each covering one subsystem (full file reads, not
sampling), cross-checked against this app's `docs/*.md` history and existing tests so
already-fixed/already-decided issues wouldn't be re-flagged. Findings are ranked by confidence and
impact within each section. `file:line` refs are relative to
`apps/LSPRi/eva/src/lspr_imaging_app/` unless stated otherwise.

---

## 1. Data-integrity bugs (highest priority — can destroy or silently corrupt real data)

### 1.1 OME-Zarr "Replace export" destroys the old export before the new one is confirmed complete
`gui/dataset_controller.py:454,459-460` (collision dialog offers "Replace") →
`io/dataset.py:1313` (`zarr.open_group(..., mode="w", ...)` wipes destination immediately) →
`gui/dataset_controller.py:876-882` (`_finish_ome_zarr_export` `shutil.rmtree`s destination on
failure).

If a user replaces an existing, valid OME-Zarr export (a supported, expected workflow — e.g.
re-exporting with different chunk settings), the old data is deleted the instant the new export
opens the destination in write mode, before a single shard of the new export is written. Any
interruption after that point (cancel, exception, crash, power loss) leaves neither the old nor a
complete new export — `shutil.rmtree` on failure removes what's left entirely. `test_lspri_ome_zarr_incomplete_export.py`
only tests that an interrupted *fresh* export isn't loaded as complete; it doesn't cover this
overwrite-then-fail path.

**Fix direction**: write to a temp sibling directory (`<destination>.tmp-<uuid>`) and only replace
the real destination after `export_ome_zarr_dataset` returns successfully; delete the temp dir on
failure, never the pre-existing destination.

### 1.2 Non-atomic file swap in the measurement-backup `compact()` step
`storage/measurement_export.py:826-839` (`ImagingMeasurementExportWriter.compact()`).

`compact()` does `self._handle.close(); self.path.unlink(); temp_path.rename(self.path)` — a
manual delete-then-rename with a comment explaining it's worked around because "Windows can't
rename onto an existing path." Between `unlink()` and `rename()`, **neither** the old nor the new
backup file exists on disk. A crash exactly then destroys the entire measurement backup (every
sensorgram + absorbance-spectrum row recorded so far) — the opposite of what this class exists for,
and it's specifically recommended for use during long write-heavy sessions
(`measurement_backup_performance_and_crash_recovery.md`), i.e. exactly the sessions most likely to
crash.

This repo already has the correct pattern one file away: `storage/workspace.py:31-53`'s
`write_json_file` uses `os.replace()` (genuinely atomic on both POSIX and Windows via
`MOVEFILE_REPLACE_EXISTING`, no prior unlink needed).

**Fix direction**: replace lines 837-839 with `self._handle.close(); temp_path.replace(self.path)`
— drop the `unlink()` call entirely.

### 1.3 Stale per-wavelength mask edits can bleed from one dataset into another
`gui/session_state_manager.py:177-215` (`_reset_processing_state_to_defaults`) and
`gui/dataset_controller.py:536-572` (`clear_dataset`).

`window._current_file_mask_wavelength_diffs` (manual mask edits drawn on a non-reference wavelength
while chromatic correction is on) is correctly cleared in every other place the base file mask is
reset (`MaskController.set_current_file_mask`, both branches of
`SessionStateManager._on_processing_state_loaded`) — **except** these two functions, which reset
`_current_file_mask`/`_current_file_mask_path`/`_current_file_mask_session_source_path` but never
touch `_current_file_mask_wavelength_diffs`.

Concrete failure path: draw manual per-wavelength mask edits on dataset A (chromatic correction on,
off-reference image) → switch to / start a new session on dataset B with no saved processing
profile → the reset above runs but leaves A's diffs in memory → `MaskController.current_external_mask()`
(`mask_controller.py:769-788`) always layers `apply_wavelength_diff()` on top of whatever mask it
loads for B, keyed only by `(cube_index, wavelength_nm)` — a pair very plausibly shared between two
datasets on the same standard wavelength grid. Result: A's stale pixel edits silently reapply to
B's mask and everything downstream (ROI stats, sensorgram) that reads it.

`tests/unit/test_lspri_mask_wavelength_diffs.py` only covers the low-level encode/decode/persist
round-trip, not this window-level reset path — not caught by the existing suite.

**Fix direction**: add `window._current_file_mask_wavelength_diffs = {}` to both functions, matching
the pattern used everywhere else the base mask is cleared.

### 1.4 Undo/redo doesn't capture or restore the same field (same root cause as 1.3)
`gui/undo_manager.py:26-57` (`make_snapshot`) and `:132-239` (`restore`).

Neither function touches `_current_file_mask_wavelength_diffs`, so an undo/redo that crosses a mask
change can leave the diff dict out of sync with whichever base mask was just restored. Likely fixed
together with 1.3 by giving this field the same snapshot/restore lifecycle as the other file-mask
fields.

---

## 2. Logic / correctness bugs (wrong or stale results shown, not data loss)

### 2.1 Analysis settings changed mid-run are silently dropped — finished run shows a stale result with no warning
`gui/analysis_worker_mixin.py:1362-1371` (`mark_stale`), `:1096-1131`,
`gui/analysis_controller.py:260-349`.

`mark_stale()` unconditionally no-ops while a "Start analysis" run is in flight
(`if self.window._sensorgram_running: return`). Every settings-changed handler that isn't the
"Start analysis" button itself (Fit method, Metric, Poly order, Reduction, Formula, spectral-cube
range, wavelength range) routes through `mark_stale`/`_mark_sensorgram_stale`, **not** through the
one method that actually knows how to queue a follow-up run for an in-progress computation
(`_calculate_sensorgram_for_range`'s `_pending_sensorgram_payload` mechanism).

Concrete scenario: start a long bulk sweep, then change Metric or Fit method or Formula while it's
running. No warning appears anywhere. `on_sensorgram_ready` applies the completed result
unconditionally and prints a normal `"SG | done"` status — indistinguishable from a fully current
result. The controls show the new setting; the plotted trace and HDF5 backup rows reflect the old
one.

**Fix direction**: mirror the existing pending-payload mechanism — when a settings-change handler
fires while `_sensorgram_running` is true, either queue a follow-up run the same way, or set a
"settings changed since this run started" flag that `on_sensorgram_ready` checks before trusting its
own result, then immediately re-mark stale / auto-refresh.

### 2.2 Sensorgram group "SEM" error band is statistically too narrow
`processing/trace_statistics.py:180,184` (`aggregate_group_traces`).

The `"sem"` band uses `np.nanstd(stacked, axis=0)` with the numpy default `ddof=0` (population
standard deviation) before dividing by `sqrt(n_valid)`. Standard SEM should use the *unbiased
sample* standard deviation (`ddof=1`). This makes the displayed band too narrow by a factor of
`sqrt((n-1)/n)` — about 29% too narrow for a 2-ROI group, ~11% for 5. A user comparing two groups'
sensorgrams with SEM bands on will see more visual separation than the data statistically supports,
worst for small group sizes (the GUI only requires ≥2 member traces to show the band —
`analysis_controller.py:817`). The existing test (`test_lspri_trace_statistics.py`) only asserts
`sem band < sd band`, not the exact formula, so this wasn't caught.

**Fix direction**: pass `ddof=1` to the `np.nanstd` call in the SEM branch (with `n_valid <= 1` →
NaN, arguably more honest than today's silent 0-width band at n=1).

### 2.3 Rare `KeyError` race between an unlocked GUI-thread cache read and a locked background eviction
`gui/analysis_worker_mixin.py:1573-1577, 2017-2030, 2285-2288, 2943` (unlocked reads) vs.
`:2660-2684` (`_store_in_lru_cache`, lock-protected writes/evictions).

Background-thread cache writes correctly take `window._analysis_cache_lock` before possibly evicting
the oldest entry. Several GUI-thread read sites instead do an unlocked `.get(signature)` followed by
a separate, non-atomic `.move_to_end(signature)` call. A background-thread eviction landing between
those two GUI-thread calls removes the key the GUI thread just read, and `move_to_end` on a missing
key raises. Narrow window (cache capped at 512 entries), most likely while a long bulk run is
actively evicting and the user is simultaneously browsing a cached cube.

**Fix direction**: wrap these get+move_to_end pairs in `window._analysis_cache_lock`, or guard
`move_to_end` with `if signature in cache:` at each unlocked site.

### 2.4 Importing a sidecar JSON + native HDF5 metadata file together silently drops one
`io/metadata_import.py:101-120` (`import_metadata_files`).

The reader dict is checked in a fixed order (`SIDECAR_JSON`, then `NATIVE_HDF5`); whichever kind is
found first wins and the function returns immediately. The "skipped/ignored" notes only cover the
*other* file kinds and *extra* files of the winning kind — if a user selects one sidecar JSON and
one native HDF5 file together, the HDF5 file is silently used for nothing with no note explaining
why, contradicting the function's own docstring.

**Fix direction**: after the reader loop picks a winner, also emit a note for any non-empty
`by_kind` entry of the other winner-eligible kind that wasn't chosen.

---

## 3. Analysis-quality / scientific-validity notes (not necessarily bugs — worth your judgment call)

### 3.1 Background-flattening exclusion radius may leave part of the reference ring in the "background" pool
`processing/preprocess.py:865-885` (`_roi_exclusion_mask`).

When "exclude area ROIs from background flattening" is on, the exclusion radius is
`max(sample_radius_px*1.35, sample_radius_px+2)`. With this app's defaults (`sample_radius_px=10`,
`reference_outer_radius_px=18` — `domain/models.py:299-301`), that's 13.5px, leaving the outer
~4.5px of the reference ring (14–18px) inside the pool `flatten_background` averages over as "valid
background," right next to the ROI it's estimating for. This might be intentional (the reference
ring is itself a local-background estimate, so including it could improve the fit right where it
matters), or it might be a leftover from before the ROI carried separate reference geometry — no
test exercises this specifically either way. **Recommend a quick judgment call from you rather than
a blind fix**, since it depends on the intended meaning of "background" here.

### 3.2 `formula_value` clamps near-zero sample/reference to 1e-9 with no upper bound on the result
`processing/analysis.py:21-22,40-41`.

A real sample-ROI mean landing at/near 0 (from the flattening clip, or an ROI mostly over
masked/rotation-fill pixels) produces `mod_absorbance` values in the thousands instead of NaN (e.g.
sample≈0→clamped 1e-9, reference=60000 → ≈+13780). This passes every `isfinite` filter downstream,
so it isn't dropped — it can dominate a "Maximum" metric search or blow out a plot's y-axis with no
indication anything unusual happened.

**Fix direction**: either cap the output magnitude, or emit NaN when the clamp actually engaged
(distinguish "genuinely computed as 1e-9" from "was clamped from a non-positive input").

### 3.3 Gaussian-fit centroid can drift from the actual fitted peak on an asymmetric window
`processing/analysis.py:344-350` (`fit_gaussian_curve`).

The centroid is the intensity-weighted mean of the *full fitted curve* (including its constant
baseline/offset) over `[wl_min, wl_max]`. This only equals the fitted peak center when the window is
symmetric about it — true by default for the full spectral range or a symmetric crop, but a
non-symmetric crop with a non-trivial baseline pulls "Centroid" toward whichever side has more
window area, independent of the true peak location. General property of intensity-weighted
centroids (also present in the polynomial-fit and generic metric paths), not a coding error.
**Idea, not a required fix**: for the Gaussian case specifically, compute the centroid on the
baseline-subtracted curve (`amplitude*exp(...)` only) rather than the full fitted curve, since the
fitted center is already known exactly.

### 3.4 Other improvement ideas (no action needed, for awareness)
- `roi_detection.py`'s `_refine_roi_center`/`_roi_circular_contrast_score` re-scan a fresh
  `np.mgrid` per candidate pixel; vectorizing or using an integral-image approach would speed up
  detection with no change in results.
- The `"sd"` band in `aggregate_group_traces` also uses `ddof=0`; worth deciding (and documenting)
  deliberately alongside the SEM fix in 2.2, since "spread among these specific ROIs" (population)
  and "estimate of underlying variability" (sample) are both defensible readings.
- `formula_value` could log when its 1e-9 floor actually engages, making the near-zero condition
  visible in diagnostics instead of only inferable from an outlier trace value.

---

## 4. Minor / cosmetic / cleanup (no analysis impact)

- **`gui/overlay_manager.py:141-155` + `gui/main_window.py:7264-7279`** — under a non-similarity
  ("affine") chromatic correction model on a non-reference wavelength, the reference-ring *outline*
  correctly renders as a warped ellipse, but the translucent *fill* between the rings is still drawn
  as an untransformed circle (`_create_reference_fill_path` uses `QPainterPath.addEllipse` with
  un-warped radii). Cosmetic mismatch only — the actual pixel sampling
  (`transformed_annulus_mask`) is unaffected, so no computed value is wrong. Fix: build the fill from
  the same warped boundary points `_roi_curve_points` already computes.
- **`gui/histogram_mask_mixin.py:18-76`** — `HistogramMaskMixin._roi_area_masks`/`_roi_intensity_values`
  are a dead, unoptimized duplicate of `PlotManager.roi_area_masks`/`roi_intensity_values` (the
  version actually wired into the live histogram path, and the one carrying the documented
  bounding-box performance fix from `bulk_analysis_performance_investigation.md`). Currently
  unreachable (zero external callers), but a natural name for a future edit to reach for on
  `MainWindow` — would silently reintroduce the pre-optimization full-image-scan cost per ROI if
  ever called. Recommend deleting the dead mixin methods (and the mixin from `MainWindow`'s bases if
  nothing else in it is used).
- **`io/dataset.py:64-82`** — a full, dead, duplicate `_crc32c` implementation; the only live call
  site (`_fast_read_zarr_plane`) imports a separate copy from `io/_zarr_export_worker.py` instead.
  Byte-identical today so no live bug, but a correctness trap if either copy is ever edited alone.
  Delete the unused module-level copy.
- **`gui/worker.py:224-226`** — `FunctionWorker.run` discards the traceback at the except site
  (`self.signals.error.emit(str(exc))`), only the message string survives. Add
  `logger.exception(...)` before emitting so the traceback reaches the log file.
- **`gui/image_interaction_controller.py:303-305` vs. `gui/main_window.py:5893-5895`** — ROI-move
  eligibility (`on_reference or chromatic_correction_enabled`) is computed independently in two
  places instead of one reading the other's already-maintained `roi_move_action.isEnabled()`. Agree
  today; could silently desync on a future edit to one side only.
- **`gui/ui_helpers.py:37-50`** — `settings_bool` and `read_bool_setting` are near-duplicate
  QSettings-boolean parsers; harmless today, worth consolidating if either is touched again.
- **`storage/measurement_export.py:702-761`** — `reduced_values_start_row` can't distinguish
  "predates this method" NaN from "this row's batch didn't compute this method" NaN if
  `compute_all_reduction_methods` is toggled mid-run. Extension of an already-accepted, documented
  ambiguity (empty-mask NaN) rather than a fresh bug; low real-world likelihood.

---

## 5. What was checked and found solid (representative, not exhaustive — see full agent reports for complete lists)

- Affine/warp geometry (`fit_affine_matrix`, `invert_affine_matrix`, `compose_affine_matrices`,
  `warp_image_affine`), phase-correlation sign convention, ROI reduction methods including the
  rank-deficient plane-fit fallback, ROI array-geometry outlier rejection — all verified correct by
  hand.
- Coordinate-space discipline for ROI drag/select/hit-testing and mask editing — consistently
  matches the documented reference-space vs. display-space contract
  (`image_tools_coordinate_spaces.md`).
- Stale-background-result rejection (request-id/signature checks) across image refresh, formula
  spectrum, and cube-slider cache paths — all correctly reject superseded results.
- "Start analysis" reentrancy fix, mid-run settings-snapshot consistency, and cache-signature
  completeness — all previously-documented fixes verified still intact.
- OME-Zarr shard write/read byte-layout agreement between writer and fast-path reader; safe fallback
  to the standard zarr API on any mismatch.
- Atomic JSON writes (`storage/workspace.py:write_json_file`) used consistently for ROI table,
  processing profile, and acquisition-metadata saves.
- No parentless-widget flash pattern, no popup-during-construction pattern, no GUI-thread-blocking
  long-running call, and no duplicate signal-connection pattern found anywhere in `main_window.py` or
  its supporting controllers.
- Startup/shutdown ordering, including the documented PyQt6-sip crash-on-close fix, correctly
  sequenced.

---

## Suggested priority order for fixes

1. **1.2** (non-atomic backup swap) — one-line fix, closes a real crash-data-loss window in the
   feature whose entire job is crash safety.
2. **1.3 + 1.4** (stale mask diffs) — same root cause, two small additions, closes a silent
   cross-dataset data-corruption path.
3. **1.1** (OME-Zarr replace) — slightly larger change (temp-dir + swap), but protects against
   losing a good export.
4. **2.1** (stale analysis results after mid-run settings change) — most user-visible logic bug;
   worth a UX decision (queue vs. warn) as well as the code fix.
5. **2.2** (SEM band) — one-line fix, but changes a displayed number; flag prominently in the
   changelog since existing recorded sessions' *displayed* SEM (not stored raw data) would look
   different after the fix.
6. Everything else — lower urgency, tackle opportunistically.
