# AGENTS.md — LSPRi Evaluation

## Status and scope

This file governs `apps/LSPRi/eva` specifically. It **supplements, not
replaces**, the umbrella `AGENTS.md` / `CLAUDE.md` at the LSPR-Suite root —
those still apply here (Engineering Priority Order, testing rules,
data-handling rules, error-handling rules, comment style, etc.). Where this
file gives a more specific rule for this app, this file wins for this app.

**This app is undergoing a from-scratch architectural rewrite**, on a
dedicated long-lived branch inside this same repository. This is a
deliberate, explicitly-approved exception to the root `AGENTS.md`'s "Do
not rewrite the whole application architecture without explicit approval"
rule. The approval and its reasoning are recorded in:

- `docs/rewrite_feature_inventory_2026-09.md` — why: quantified evidence of
  the entanglement problem being fixed (a god object, zero event system,
  a single method called from 40-70 sites across up to 13 files), plus a
  full catalog of what in the current app is worth keeping vs. rewriting.
- `docs/rewrite_architecture_sketch_2026-09.md` — what: the actual module
  boundaries, event design, and analysis-store design this file's rules
  are extracted from. **Read it before making any structural decision this
  file doesn't explicitly cover** — this file is the terse, actionable
  summary; that document is the reasoning and the detail.

**Scope of the rewrite is LSPRi Evaluation only.** No device/hardware
control, no shared package for a future acquisition app — explicitly
descoped by the maintainer. Do not design anything here as "reusable by
the future acquisition app" unless asked again.

---

## Non-negotiable invariants

These exist because of real, hard-to-diagnose incidents in the current
app, not style preference. Violating any of these will very likely
reproduce a bug that already cost real investigation time once — see the
feature inventory for the full incident writeups.

- **Never let a `QThreadPool` worker touch, even indirectly via a blocking
  wait, an OME-Zarr read.** Root-caused to a native
  `STATUS_HEAP_CORRUPTION` crash. Use plain `threading.Thread` (or a small
  pool built on it) for anything dataset/zarr-adjacent. The one safe
  exception: a single-thread `QThreadPool` for HDF5-only writes that need
  strict ordering.
- **Never run a curve fit, or any other nonlinear computation, on the GUI
  thread.** This includes anything framed as a "live preview" — display
  code reads already-computed values, it never computes.
- **Never pool pixels across ROIs before computing sample/reference
  ratios.** Every ROI's reduction stays independent through the whole
  pipeline.
- **Always average already-fitted per-ROI values, never average raw
  spectra and fit once.** Nonlinear fits don't commute with averaging.
- **Cache per-ROI analysis masks at that ROI's own small bounding box, not
  full-image-plane size.** Full-size caching measured 8-14GB RAM at
  realistic ROI counts.
- **Masks are authored and stored in raw pixel space**, forward-
  transformed into processed/wavelength space at each use site via
  Chromatic's `warp_mask()` — never the other way around.
- Rotation/flip/crop are **not display-only** — when applied, they
  resample the actual pixel grid every downstream calculation reads. This
  is deliberate (detection quality over spatial-transform purity), not an
  oversight.

---

## Module boundaries

Full detail: `docs/rewrite_architecture_sketch_2026-09.md` §2, §7, §10.

- `dataset/` — dataset load/convert, acquisition metadata.
- `image_tools/{geometry,mask,chromatic,background}/` — each an
  independent, self-documented module; only Chromatic's `affine_for()` /
  `warp_mask()` are ever called by other modules, never its internals.
- `roi/` — the ROI Toolbox: **one backend, several front-door UI
  surfaces** (Workflow panel's Finding/Editing/Groups sub-tabs, the Image
  panel, the ROI/Group table panel all call the same command API — that's
  intended duplication of *affordances*, not of logic). Follows
  `docs/roi_system_roadmap.md`'s `Pair` vocabulary and geometry-type
  dispatcher — adopt that roadmap, don't re-derive a new ROI model.
- `analysis/` — the store, the recompute planner, background workers, and
  the reduction math that turns masked pixel values into a scalar
  (`reduction.py`, moved here from `roi/` on 2026-09-21 — turning pixels
  into numbers is a calculation, not something the ROI Toolbox does).
  Never triggers computation on its own; only `run_analysis(scope)`,
  called explicitly by the user, computes anything.
- `selection/` — the one intentionally shared piece of state (current
  cube/wavelength/ROI selection). This is a deliberate, acknowledged
  exception to "nothing is shared" — not license to add more shared state
  elsewhere.
- `panels/` — display only. No panel computes anything or owns domain
  state; each reads its upstream modules' query interfaces and turns user
  gestures into command calls.

**Rule**: no module reads or writes another module's internal state
directly, ever — not even `panels/workflow`, which hosts navigation and
the status bar but owns no scientific state itself. If two modules need to
coordinate, that's a signal one of them should own a documented interface
method for the other to call, or a signal to emit — not a workaround for
one to reach in.

---

## Communication: typed signals, cosmetic vs. computational

Each module is a `QObject` exposing typed `pyqtSignal`s for what it owns —
not a shared string-keyed event bus. Every change a module can make is
wrapped as one of two distinct payload types, never left to a
naming-convention or a comment to disambiguate:

- `CosmeticChange` — redraw only (color, label, grouping, display mode).
  Never triggers analysis recompute.
- `ComputationalChange` — the underlying value may now be wrong (geometry,
  mask, chromatic model, background model, reduction method). This is what
  the Analysis Engine's recompute planner reacts to.

Every module's public mutating methods go through a shared `@instrumented`
helper (timing + structured DEBUG logging, always captured to the session
log file) so performance visibility is built in from the first commit, not
retrofitted after a slowdown report.

Every display panel owns its own short (~100ms) redraw-coalescing timer
between "an event arrived" and "actually redraw" — reacting to *every*
event immediately is not the same as reacting *promptly*, and skipping
this is exactly what made one real bug scale redraw cost with the square
of the cube count.

---

## Undo/redo

Added 2026-09-20 (not in the original architecture sketch - designed when
`RoiToolbox`'s command methods needed it and there was nowhere for it to
live yet). Full detail: `undo/manager.py`'s module docstring.

- **One shared `undo.undo_manager` instance**, not one stack per module.
  Modules import it directly (`from ...undo import undo_manager`), the same
  way every module already imports the shared `diagnostics_hub` - undo,
  like diagnostics, is cross-cutting infrastructure, not domain state any
  one module owns. A single global stack also matches ordinary user
  expectation: undoing steps back through every module's edits in the order
  they actually happened, not just one module's.
- **Every mutating command pushes one typed `undo.FunctionCommand`**
  (a label plus an `undo()`/`redo()` closure pair the command method writes
  inline, closing over its own module's private state) - not a generic
  before/after deep-clone of module state. This is deliberate: the old
  app's `_push_undo_point` deep-copied the *entire* app state on every
  single edit (every ROI, every mask array, every chromatic model), which
  is exactly the kind of cost this architecture exists to avoid.
- **`undo_manager.begin_batch(label)` / `end_batch()`** coalesce a burst of
  pushes (e.g. every intermediate sample of a mouse drag) into one undo-stack
  entry - mirrors the old app's prepare/commit-snapshot pattern for the same
  reason.
- **Selection is never undoable.** Matches the old app (selecting/
  deselecting was never wrapped in `_push_undo_point` there either) and this
  file's own "selecting/deselecting ROIs... must never implicitly trigger
  computation" spirit - undo history is for state that affects results, not
  where the cursor/selection happens to be.
- `RoiToolbox` is the first module wired to this (2026-09-20); Geometry/
  Mask/Chromatic/Background should adopt the identical pattern once their
  own command methods are built past the `NotImplementedError` stub stage -
  update this section with a second real example once that happens.

---

## The analysis store and recompute rules

Full detail: `docs/rewrite_architecture_sketch_2026-09.md` §5, §6, §6a; the
on-disk shape of a cell's provenance record (masks/backgrounds as real
image files, per-frame version numbers, settings-snapshot JSONs) is in
`docs/analysis_provenance_store_design_2026-09.md`, which supersedes §5's
original `provenance_table.json` sketch.

- **One ongoing HDF5 file per dataset.** Not a folder of settings-hashed
  version files — that design was proposed and explicitly rejected.
- Each stored cell (one ROI × one cube) carries its own **provenance
  record**: the narrow set of inputs that produced it (this ROI's
  geometry, the mask state within its own reach box, the chromatic affine
  for that image key, the background model, the reduction method).
- **Recompute rule is deliberately simple: any change to a cell's inputs
  triggers recompute for that cell.** No materiality/pixel-selection
  comparison — that was designed, then explicitly simplified away in favor
  of "no subtle rule to get wrong." Do not reintroduce it without
  discussing it again first.
- **Analysis only ever runs on explicit user action** (`run_analysis`),
  default scope all ROIs, with an option to scope to the current
  selection. Selecting/deselecting ROIs, or navigating between panels,
  must never implicitly trigger computation — display simply reports
  "not yet analyzed" when data isn't there.
- Formula, Fit method, and Metric settings are **never** written to the
  store or included in a cell's provenance — they're cheap reprojections,
  recomputed live from RAM against whatever's already stored.

---

## Fractional pixel weighting (§6a)

An optional, per-analysis-toggle enhancement: weight boundary pixels by
the fraction of their area inside the ROI shape, instead of a binary
center-in-shape test. Full detail: architecture sketch §6a.

**Split across two module boundaries (corrected 2026-09-21)**: producing
the fractional-weight *raster* from an ROI's shape is geometry and stays in
`roi/rasterize.py`. Consuming those weights to turn pixel values into a
scalar (`weighted_mean`/`weighted_median`/`weighted_trimmed_mean`/
`weighted_plane_fit`) is a calculation, not a ROI concern — those stubs (and
the rest of the unweighted reduction math they extend) live in
`analysis/reduction.py`, moved out of `roi/` entirely on 2026-09-21 (see the
rewrite build log). The ROI Toolbox produces masks/weights; Analysis turns
pixels into numbers.

**Both halves are now built (raster: 2026-09-22 morning; reduction:
2026-09-22 later the same day)**: `roi/rasterize.py`'s `rasterize_fractional`
is real, for all three geometry types (circle, annulus, mask) via one
shared engine (`_reach_box_coverage`) that only swaps out the per-geometry
point-test — not per-shape code paths. `analysis/reduction.py`'s
`weighted_mean`/`weighted_median`/`weighted_trimmed_mean`/`weighted_plane_fit`
are real too, each individually verified (500-3000 random trials each) to
reduce exactly (or to float noise, for `weighted_median`'s interpolation-
based formula) to its unweighted counterpart when every weight is equal —
the AGENTS.md rule below, actually checked, not just satisfied by
construction. **Not yet connected to each other**: `analysis/tasks.py`'s
`compute_cell` still calls the binary `rasterize_sample`/
`rasterize_reference`, not `rasterize_fractional` — wiring fractional
weighting into the real per-cell pipeline (a toggle, a mask-array plumb-
through, provenance implications) is separate, not-yet-started work.

- Implementation approach used: supersample-and-downsample — one shared
  engine for every geometry type (circle/rectangle/polygon/arbitrary
  mask), matching the ROI roadmap's one-dispatcher-not-per-shape-code
  direction, per-pixel inverse-affine-mapped (not "draw a circle at the
  transformed center", which is wrong whenever the chromatic fit has shear
  or anisotropic scale - the real fit, `fit_affine_matrix`, is an
  unconstrained 6-parameter affine, so this isn't a hypothetical edge
  case). Exact analytical per-shape formulas (e.g. `cv2.
  intersectConvexConvex`) were deliberately not used - supersampling
  proved sufficient (see the rewrite build log's 2026-09-22 entry for the
  verification numbers) and keeps one implementation instead of one per
  shape.
- Mask geometry gets the same fractional treatment as circle/annulus, via
  a nearest-neighbor lookup into the stored `RoiMask` array as its
  point-test (rather than a continuous formula, since a mask has no
  boundary information beyond its own stored pixels) - not exempted from
  §6a the way an earlier draft of this note implied.
- Not yet cached: `rasterize_fractional` recomputes on every call today.
  Since it depends only on ROI geometry + the chromatic affine (both fixed
  per spectral cube/wavelength, never per time-frame), it's a candidate for
  the same per-key caching `ChromaticModule` already does for its own
  affine models - deferred until a real caller (`analysis/tasks.py`) exists
  to need it, rather than building a cache with no caller to validate it
  against.
- This was real implementation work, not a free toggle, as predicted:
  `weighted_median` needed a genuine linear-interpolation-on-cumulative-
  weight algorithm (not "pass weights through" to `np.median`), and
  `weighted_trimmed_mean` needed its own explicit design choice
  (trim by element count, then weighted-mean the remainder - see that
  function's own docstring for why a weight-based trim wouldn't have
  provably matched the unweighted version). Both built and verified
  2026-09-22, see `analysis/reduction.py`.

---

## Testing rules specific to this rewrite

- Every pure-computation file (`*/fitting.py`, `*/detection.py`,
  `analysis/tasks.py`, `analysis/provenance.py`, `analysis/planner.py`,
  `analysis/reduction.py`, `roi/rasterize.py`) must be importable and testable
  with **zero Qt/GUI/dataset dependency** — this is structural, not a
  suggestion: if a test for one of these needs a `QApplication`, something
  is wired wrong.
- `plan_recompute()` needs a deterministic unit test per locality rule in
  the sketch (ROI-local geometry change, neighbor-exclusion adjacency,
  mask-local change, chromatic/background global change, reduction-method
  global change, cosmetic/range changes that touch nothing).
- Any weighted reduction method must be tested for numerical parity
  against its unweighted counterpart in the degenerate all-weights-equal
  case — they must produce identical results there.
- Follow the root `AGENTS.md`'s numerical-tolerance and simulated-hardware
  testing rules; nothing about this rewrite changes those.

---

## What NOT to do without checking in again first

- Don't add the materiality/pixel-selection-comparison recompute
  optimization back — it was designed, then deliberately dropped for
  simplicity. If unnecessary recompute becomes a real measured problem,
  bring it back as a proposal with a before/after measurement, not as an
  assumed improvement.
- Don't switch the analysis store back to per-settings-version files — one
  file with per-cell provenance was chosen specifically over that design.
- Don't make analysis run automatically on ROI selection, panel
  navigation, or any change other than an explicit user-invoked
  `run_analysis` call.
- Don't put ROI/group business logic inside a panel/view class — panels
  call the ROI Toolbox's command API; they never hold their own copy of
  ROI or group state.
- Don't use `QThreadPool` for anything that touches the dataset/zarr layer,
  even indirectly.
- Don't design anything here as shared infrastructure for a future
  acquisition app — that scope was explicitly cut for this rewrite.

---

## References

- `docs/rewrite_feature_inventory_2026-09.md` — the evidence base.
- `docs/rewrite_architecture_sketch_2026-09.md` — the full design this
  file summarizes.
- `docs/rewrite_build_log_2026-09.md` — dated, append-only record of what's
  actually been built on the `rewrite` branch so far, what was corrected
  along the way and why, and what's still open. Read this first when
  resuming the rewrite cold.
- `docs/roi_system_roadmap.md` — the adopted ROI model plan (`Pair`
  vocabulary, geometry-type dispatcher).
- `docs/qthreadpool_zarr_crash_investigation.md`,
  `docs/analysis_caching_architecture.md`,
  `docs/measurement_backup_performance_and_crash_recovery.md` — the
  incident writeups behind the non-negotiable invariants above.
- `undo/manager.py` — the cross-module undo/redo design (see "Undo/redo"
  above for the summary; that module's own docstring has the full reasoning).
