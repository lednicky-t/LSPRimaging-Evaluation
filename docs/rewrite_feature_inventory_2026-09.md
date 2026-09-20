# LSPRi Evaluation rewrite: feature inventory (2026-09-19)

Context: the maintainer decided to rewrite LSPRi Evaluation from scratch after
diagnosing severe architectural entanglement in the sensorgram/display code
(see the "entanglement diagnosis" discussion earlier the same day - main
takeaway: `main_window.py` is a ~8,600-line god object, zero custom
Qt-signal-based pub/sub exists anywhere in the package, and standard
"refresh tail" methods are called from 40-70 sites across 6-13 files each).
Scope was later narrowed to LSPRi Evaluation only - no device/hardware
abstraction, no shared-package work for a future acquisition app.

This document is the feature inventory that followed: six research passes
(one per major subsystem) cataloging what the current app does, file:line
references, coupling evidence, and known pain points - gathered specifically
to decide what to **keep**, **redesign**, or **drop** in the rewrite. Each
pass read the actual code and cross-referenced this repo's own `docs/*.md`
history rather than guessing.

---

## Cross-cutting findings (read this first)

### 1. The same "clean core, entangled shell" split shows up in every single area

This is the most important, most consistently-repeated finding across all
six independent investigations:

| Area | The clean, Qt-free numeric/IO core | The entangled GUI/orchestration shell |
|---|---|---|
| Dataset & persistence | `io/dataset.py`, `io/legacy_metadata.py`, `storage/workspace.py`, `storage/measurement_export*.py` | `dataset_controller.py`, `session_state_manager.py`, `undo_manager.py` |
| Image display & masking | `processing/preprocess.py` | `image_render_manager.py`, `image_interaction_controller.py`, `mask_controller.py` |
| ROI system | `processing/roi_detection.py`, `roi_array_geometry.py`, `roi_math.py`, `roi_histogram.py`, `roi_rasterize.py` | `roi_geometry_mixin.py`, `roi_table_controller.py`, `group_table_controller.py` |
| Chromatic correction | `processing/chromatic.py`'s fitting/warping/tracking math | `chromatic_controller.py` |
| Sensorgram computation | `analysis_tasks.py` (verified: **zero** `window.*` references) | `analysis_worker_mixin.py` (425 `window.*` reads touching 113 distinct attributes - the highest coupling number found anywhere in this inventory) |
| App shell | window-geometry & panel-layout persistence (`layout_state_controller.py`'s geometry methods, explicitly "touches no scientific state" per its own docstring) | `layout_builder.py` (shell mechanism and feature-specific panel content are intertwined in one 1,772-line file) |

**Implication for the rewrite**: the domain/algorithm layer already follows
this repo's own "don't mix scientific code with GUI code" rule well, is
generally well-tested, and is where the real hard-won correctness and
performance knowledge lives. It is a strong **keep-as-is** candidate almost
everywhere. The GUI/orchestration layer - not the science - is where nearly
all of the entanglement, the god-object pattern, and the recurring bugs
live. The rewrite's effort belongs almost entirely there.

### 2. The sensorgram's own bug history already proves the fix

The single strongest piece of evidence in this whole inventory: the
sensorgram caching layer grew to **8 separate caching mechanisms**,
consolidated down to 5-6 only after the maintainer found that *"two
independently-maintained code paths answering 'is this ROI's data
fresh' differently" was the root cause of every recent Sensogram bug*
(disappearing plot, spurious recalc popup, slow reselection - all of it).
This directly validates the event-driven/single-source-of-truth direction
discussed earlier: build **one canonical "is this fresh / who needs to
know" mechanism per concern from day one**, not one per consumer, because
the two-caches-disagreeing pattern is empirically what caused the bugs,
not a hypothetical risk.

### 3. Hard invariants that must survive regardless of new architecture

These are not style preferences - they're lessons paid for with real,
hard-to-diagnose incidents. A rewrite that doesn't know about them will
likely rediscover them the same expensive way:

- **Never let a `QThreadPool` worker thread touch (even indirectly, via a
  blocking wait) an OME-Zarr read.** Root-caused via a 10-row isolation
  matrix to a native `STATUS_HEAP_CORRUPTION`/`STATUS_STACK_BUFFER_OVERRUN`
  crash (zarr-python's internal asyncio bridge colliding with Qt-native
  threads). Current fix: `FunctionWorker` dispatches via plain
  `threading.Thread`. One deliberate, safe exception: an HDF5-only,
  single-thread `QThreadPool` for the measurement-backup flush (needs
  write-ordering, never touches zarr).
- **Never pool pixels across ROIs before computing a ratio** (sample/
  reference must stay per-ROI through the whole pipeline) - a real,
  regression-tested historical bug.
- **Average already-fitted per-ROI metric values, never average spectra
  then fit once** - nonlinear fits don't commute with averaging; this was
  a real, fixed 2026-09-12 bug.
- **Per-ROI analysis masks must be cached at their own small bounding-box
  size, not full-image-plane size** - the full-size version measured
  ~289MB/entry, ballooning to ~8-14GB RAM at realistic (150+) ROI counts.
  Fixed via a shared `_roi_reach_box` helper used by both the cache
  builder and the pixel reader.
- **Chromatic/annulus mask computation must respect its own reach-limit
  box, not the whole read patch** - an unfixed version cost 322 seconds
  for one spectral cube at 160-ROI scale.
- **The `cv2` chromatic-resample fast path's tiny numeric deviation from
  the `scipy` reference path is deliberate and measured** (~70x faster,
  ~30,000x below the sensor's own shot-noise floor) - an existing test's
  tolerance was loosened with a documented measurement to match. Don't
  silently retighten it without redoing that measurement.
- **`flatten_background=True` bypasses every OME-Zarr scoped-read
  optimization** (14x measured slowdown) - a known, accepted, currently
  unfixed gap, not a surprise to "fix" reflexively.
- Rotation/flip/crop are **not display-only** - when linked, they resample
  the actual pixel grid that feeds every downstream calculation (spectrum,
  sensorgram, background flattening, ROI/chromatic detection). This is a
  deliberate trade-off documented in `image_tools_coordinate_spaces.md`,
  not an accident.
- Masks are authored in **raw** pixel space and forward-transformed into
  processed space at each use site - the one subsystem that already does
  this correctly. If ROI/chromatic data is ever made robust to image-tools
  changes (see open bug below), this is the pattern to copy.

### 4. Currently-open correctness/design bugs found (decide, don't silently inherit)

These exist in the current app right now and were surfaced independently
by more than one research pass in some cases - they're real, not
theoretical:

- **Per-wavelength mask-diff cross-dataset leak** (found independently by
  both the image-display and chromatic-correction investigations):
  `window._current_file_mask_wavelength_diffs` is correctly cleared in
  `MaskController.set_current_file_mask` and session-load code, but *not*
  in `SessionStateManager._reset_processing_state_to_defaults`,
  `DatasetController.clear_dataset`, or undo/redo restore - stale
  per-wavelength mask edits from dataset A can silently corrupt ROI
  stats/sensorgram on dataset B if they share a `(cube_index,
  wavelength_nm)` key, with no warning.
- **Stale ROI/chromatic positions after recropping**: changing
  rotation/crop *after* ROI detection or chromatic calibration doesn't
  invalidate ROI positions or the chromatic model - only downstream result
  caches are invalidated, so a recompute silently samples old coordinates
  against new geometry. Documented as a "known gap," mitigation is
  procedural only ("re-run detection/calibration after changing image
  tools"), not enforced in code.
- **Live-preview curve fit runs on the GUI thread every 80ms during a
  running bulk sweep**, contending for the GIL with the background sweep
  and slowing the whole run down. Diagnosed, not fixed - and the code's
  own docstring *incorrectly* claims this path is fit-free. A rewrite
  should treat "live preview must never fit on the GUI thread" as a hard
  requirement.
- **Non-atomic backup-file swap**: `measurement_export.py`'s `compact()`
  does `unlink()` then `rename()` (neither file exists between the two
  calls - a crash there destroys the whole backup), even though the
  correct atomic `os.replace()` pattern already exists one file away in
  `storage/workspace.py`.
- **Changing Fit method/Metric/Formula/Reduction while a bulk sweep is
  running is silently dropped** - the finished run applies unconditionally
  with no warning that displayed settings don't match computed data.
- **SEM error band uses `ddof=0` instead of `ddof=1`** in
  `processing/trace_statistics.py` - displayed band is ~11-29% too narrow.
  One-line fix, not yet applied.
- **`formula_value`'s near-zero clamp has no upper bound** (floors at
  1e-9, never caps) - can produce absorbance values in the thousands that
  pass every `isfinite` filter and silently dominate fits/plots instead of
  becoming NaN.
- **An unlocked GUI-thread cache read racing a locked background-thread
  eviction** - narrow window, documented, not yet fixed.
- Two places compute the same "can this ROI be moved right now" condition
  independently (`image_interaction_controller.py` vs `main_window.py`) -
  currently in agreement, a desync risk if only one is ever edited.
- Shortcut *behavior* (`shortcut_manager.py`) and shortcut *documentation*
  (`shortcut_registry.py`'s `SHORTCUT_SECTIONS`, shown in the F1 help
  panel) are two independent, hand-maintained sources of truth with
  nothing enforcing they match.
- 18 settings sections each need their save/restore code added by hand in
  two separate places in `layout_state_controller.py` with no central
  registry/schema - the same "no central schema" anti-pattern already
  known from acq settings persistence elsewhere in this repo.

### 5. Existing forward-looking design docs to adopt, not re-derive

- **`roi_system_roadmap.md` (Aug 2026) is authoritative** and directly
  relevant: it already evaluated and *explicitly rejected* a fully generic
  many-to-many ROI model in favor of incrementally extending the current
  `AreaRoi` (a `Pair` vocabulary, a geometry-type-to-mask dispatcher,
  phased rectangle/freeform support - rectangle explicitly flagged as
  needing maintainer sign-off since it touches the shared model). It also
  records a cautionary precedent: **a previous parallel ROI editor was
  built, never wired to analysis, and deleted as dead scaffolding in
  2026-08-21** - worth not repeating that "second editor nobody finishes
  wiring" pattern. `roi_table_direction.md` and (mostly-superseded)
  `roi_implementation_direction.md` are worth a skim but the roadmap
  supersedes them by its own explicit statement.
- `roi_system_roadmap.md` also identifies the existing
  `transformed_disk_mask`/`transformed_annulus_mask` pattern (chromatic
  correction applying to ROI pixel extraction) as **already the right
  shape to reuse** for any future ROI geometry types - "does NOT need to be
  rebuilt."
- `docs/analysis_caching_architecture.md` and
  `docs/analysis_pipeline_layers.md` already describe the target shape for
  the sensorgram's 3-layer compute pipeline and its (consolidated) caching
  tiers - read before redesigning that area, not after.

### 6. Debt/dead code found along the way

- An abandoned "v2" sensorgram completion-handler set (~90 lines) never
  wired to any signal.
- A previously-built, never-wired parallel ROI editor, deleted 2026-08-21
  (see above) - historical, already gone, just a precedent to remember.
- Two independent, unreused distance-tolerance clustering implementations
  solving nearly the same problem (`roi_array_geometry.py`'s `_cluster_1d`
  vs `roi_geometry_mixin.py`'s `_cluster_rois_into_columns`).
- Group-mutation logic (create/merge/destroy) is split across at least two
  files (`roi_geometry_mixin.py` and `group_table_controller.py`) rather
  than centralized in one owner.
- `chromatic.py` still carries a full alternate "tile-based/robust"
  registration path (`estimate_affine_chromatic_transform`) that the GUI
  never actually selects (`mode="landmark_radial"` is hardcoded) - dead
  from the UI's perspective.
- Two fully-built-but-practically-unreachable features, needing an
  explicit keep-and-finish-or-drop decision: **arbitrary-mask ROI
  geometry** (fully implemented and tested end-to-end, but nothing in the
  current GUI can create one - only reachable by hand-editing a saved ROI
  JSON file), and **`RoiArrayGroup`'s "grid recipe"** (persisted on
  detection, but no UI currently regenerates/nudges an array using it -
  forward-looking infrastructure, not a used feature).
- `io/legacy_metadata.py` (~420 lines) imports the old acquisition tool's
  `meta_data.txt`/`measuring_times.csv` format - a candidate to drop or
  simplify if datasets in that legacy format are no longer in active use
  (maintainer call, not inferrable from code).

### 7. Features that look mis-homed structurally (naming/module boundary, not a bug)

- The **histogram panel** (intensity distribution, sample/reference/mask
  sub-curves) lives in `plot_manager.py` under what's nominally
  "sensorgram/spectra plotting," but it's really an image/ROI/mask
  feature that happens to share a file.
- **`DataAxisSlider`'s cache-state tick coloring** straddles the
  widget/shell layer (the slider widget itself, in `widgets.py`) and the
  sensorgram cache-freshness state that drives its colors (in
  `analysis_controller.py`/`analysis_worker_mixin.py`).

---

## Per-area classification starting point

This is a first-pass suggestion, not a decision - the whole point of the
next brainstorming step is to go through these together.

| Area | Numeric/IO core | GUI/orchestration shell | Notes |
|---|---|---|---|
| Dataset & persistence | **Keep** (HDF5 schema 6/7 writers, OME-Zarr I/O, JSON encode/decode) | **Redesign** (`DatasetController`, `SessionStateManager`, `UndoManager` - replace the "hand-list every attribute" pattern) | Schema 7 migration is itself incomplete (sLSPR acq was never migrated to match) - decide whether the rewrite finishes that unification or leaves it. |
| Image display, masking, tools | **Keep** (`preprocess.py`'s pixel math, including the validated cv2 fast paths) | **Redesign** (render manager, the single-`eventFilter`-does-everything interaction controller, mask controller) | Fix the two open staleness bugs (mask-diff leak, stale-ROI-after-recrop) as part of the redesign, not as a patch to the old app. |
| ROI system | **Keep** (detection, array-geometry estimation, pixel-reduction math, histogram range estimation, mask rasterization) | **Redesign**, informed by `roi_system_roadmap.md` | Decide explicitly on arbitrary-mask ROIs and `RoiArrayGroup` (finish or drop). |
| Chromatic correction | **Keep** (affine/similarity fitting, phase correlation, landmark tracking/trend-filtering) | **Redesign, with a real interface boundary** (`affine_for(image_key)`, `warp_mask(...)`) so ROI/mask code stops reaching into chromatic internals directly | This area's coupling is partly *inherent*, not just accidental - the boundary needs deliberate design, not just "make it less messy." |
| Sensorgram computation | **Keep** (all pipeline-layer and threading/caching invariants listed above) | **Full redesign** - this is the area that motivated the rewrite | Build the "is this (ROI, cube, settings) already computed" answer as one function from day one, per finding #2 above. |
| Sensorgram/spectra display & plotting | Rendering logic (draw-from-already-computed-values) is conceptually clean | **Redesign**, and decide up front whether display and cache-staleness-tracking are one concern or two (they're welded together today) | Move the histogram panel out of "sensorgram" conceptually per finding #7. |
| App shell (panels, layout, preferences, shortcuts, workflow log, icons) | Window-geometry and panel-layout persistence are clean, keep-as-is | **Light redesign**: settings-section persistence needs a registry instead of 18 hand-duplicated pairs; shortcut behavior and documentation need one source of truth | `main_window_icons.py` (1885 lines, ~101 near-identical methods) is a good candidate for wholesale regeneration from a data-driven icon spec rather than porting line by line. |

---

## Open questions for the maintainer

1. Is `io/legacy_metadata.py`'s old-format import still needed, or can it
   be dropped/simplified?
2. Arbitrary-mask ROI geometry: finish wiring a GUI for it (freehand paint,
   per the roadmap's Phase 4), or drop it since nothing currently creates
   one?
3. `RoiArrayGroup`'s grid recipe: build the "regenerate/nudge the whole
   array" UI that would actually use it, or drop the persistence since
   it's currently write-only?
4. Adopt `roi_system_roadmap.md`'s phased plan as the rewrite's ROI-section
   plan as-is, or use it only as a reference while designing independently?
5. Schema 7 (measurement backup) unification with sLSPR acquisition's
   writer was explicitly out of scope when introduced - does the rewrite
   pick that up, or leave it deferred again?

---

## Appendix: full per-area research reports

The six full reports (dataset & persistence; image display & processing;
ROI system; chromatic correction; sensorgram computation engine; sensorgram
UI/plotting & app shell) are preserved in the session that produced this
document. This summary captures every decision-relevant fact from them;
re-run a similar research pass if line-level detail is needed again rather
than assuming this file is exhaustive down to that level.
