# LSPRi Evaluation rewrite — Build Log

Dated, append-only record of what has actually been done on the from-scratch
rewrite (see `AGENTS.md`, `docs/rewrite_feature_inventory_2026-09.md`,
`docs/rewrite_architecture_sketch_2026-09.md`), on the `rewrite` branch.
This is a history/decision record, not a task list.

**Why this file exists**: this project spans multiple sessions and will
very likely be picked up by a different AI agent instance (with no memory
of prior sessions) partway through. A future reader — human or agent —
should be able to read this file top to bottom and understand what exists,
what was tried, what was corrected and why, without needing to reconstruct
it from chat history. Follows the same convention as
`apps/LSPRi/acq/../lspri_acq_build_log.md` (the sibling acquisition
project's build log) and `apps/sLSPR/acq/docs/device-layer/
DEVICE_LAYER_AUDIT_2026.md`.

**Rule for future entries**: append, don't rewrite history. If something
described below turns out to be wrong or gets superseded, add a new dated
entry saying so and pointing back at the old one — don't silently edit the
old entry. Update the sketch/AGENTS.md's own content (the target design)
freely; this file is the log of how we got there.

**Working method established this session, worth continuing**: before
porting any file the sketch names, read the real file first and check its
actual imports/dependencies against what the sketch assumed. Two files so
far (`io/dataset.py`, `roi/detection.py`↔`chromatic.py`'s coupling) turned
out to have real scope the sketch didn't call out; one (`processing/
chromatic.py`) matched the sketch's assumption exactly. Don't skip the
check just because a prior file matched.

---

## 2026-09-20: Planning landed — feature inventory, architecture sketch, AGENTS.md

Committed on `develop` as `3b2ef97`. See `docs/rewrite_feature_inventory_
2026-09.md` (the evidence: god object, zero event system, a single method
called from 40-70 sites) and `docs/rewrite_architecture_sketch_2026-09.md`
(the design: typed cosmetic/computational signals, one HDF5 store with
per-cell provenance, explicit-only analysis triggering, the §10 package
layout). `AGENTS.md` distills both into terse, enforceable rules for this
app specifically.

## 2026-09-20: Scaffold — `rewrite` branch created, §10's package layout built

No `rewrite` branch existed before this (AGENTS.md's "dedicated long-lived
branch" reference was aspirational until now). Created it off `develop`.
Scaffolded 30 files across `diagnostics/`, `dataset/`, `image_tools/
{geometry,mask,chromatic,background}/`, `roi/`, `analysis/`, `selection/`,
`panels/{image,histogram,roi_table,spectra,sensorgram,workflow}/`, plus
`change_events.py` and `storage/session.py` — real `QObject` classes with
the signals/command-API signatures named in sketch §7, stub methods raising
`NotImplementedError` with docstrings pointing at the relevant section, not
empty files. Deliberately left `app.py` and `storage/measurement_export.py`
untouched — both already exist as the current app's working code, and a
same-named stub would have silently shadowed it. Verified: every package
imports cleanly, pyflakes-clean. Committed as `7a36217`.

## 2026-09-20: Dataset stage ported — real model.py + io.py, §10's file split corrected

`dataset/model.py` got the real `ImageKey`/`ImageRecord`/
`CompactImageTimings`/`ImageDataset` dataclasses, ported verbatim from
`domain/models.py` (including the `CompactImageTimings` docstring — a
documented PyQt6-sip crash-correlation mitigation, not incidental).

**Real finding**: sketch §10 assumed `io/dataset.py` (1794 lines) splits
cleanly into `io_tiff.py`/`io_ome_zarr.py`. It doesn't — format-agnostic
dispatch/caching/discovery (`dataset_load_plane_roi`, `dataset_get_record`,
`discover_dataset_candidates`, `load_image_array`, ...) is mixed
throughout, and `dataset_load_plane_roi` itself branches internally
between a scoped zarr-chunk read and a full-TIFF-load-then-crop for a real
performance reason — not separable per format. Presented three options to
the maintainer; chose **one `io.py`, format-specific internals stay
private**. Corrected the sketch doc and `dataset/__init__.py`'s own
docstring to match rather than silently deviating from the documented
design.

**Mechanics**: physically copied (`cp`, not retyped) `io/dataset.py` into
`dataset/io.py`, then diffed after — confirmed only the import block
changed, all 1794 lines of actual logic byte-for-byte identical to the
still-working original. `ImageDataset`/`ImageKey`/`ImageRecord` now import
from the new local `dataset/model.py`; everything else not yet ported
(`domain/exclusions.py`, `io/image_naming.py`, `io/legacy_metadata.py`,
`storage/workspace.py`, `processing/preprocess.py`, `PreprocessingSettings`)
intentionally still imports from its current `develop`/`main`-branch
location — safe interim scaffolding (this module doesn't wire into the old
app anywhere), not a shim. `image_naming`/`legacy_metadata` have no
assigned new home in §10 yet — flag this when next revisiting the sketch.

Verified: imports cleanly, pyflakes-clean, old `io/dataset.py` and
`domain/models.py` completely untouched. Committed as `ab30214`.

## 2026-09-20: Rewrite-preview launcher entry point added

Maintainer asked for a second Suite Launcher card so the in-progress
skeleton is actually launchable alongside the still-fully-functional
stable app — no second git worktree needed, since the scaffold is additive
(old app files untouched): both `app.py` and a new dev-preview entry point
coexist in the same `apps/LSPRi/eva` checkout once it's on `rewrite`.

Built `app_rewrite.py` + `main_rewrite.py` + the `lspri-evaluation-rewrite`
console script: constructs every module built so far (`DatasetModule`,
`GeometryModule`/`MaskModule`/`ChromaticModule`/`BackgroundModule`,
`RoiToolbox`, `SelectionModule`, `AnalysisEngine`) and wires all five
display panels to them inside a `WorkflowPanel` tab strip, with a
"nothing here works yet" banner. Deliberately skips everything `app.py`
does beyond that (splash, dataset restore, layout persistence) — none of
it exists yet in the new architecture. `version_rewrite.py` (0.1.0) tracks
the rewrite's own progress separately from `version.py`'s stable 0.2.1, so
the launcher card doesn't show a misleading version number.

Added a second `AppTarget` ("lspri_eva_rewrite") in `apps/suite_launcher/
src/suite_launcher/targets.py`, pointing at the same root but
`src/main_rewrite.py` — `is_available()`'s existing "does the script file
exist" check already greys the card out when the submodule isn't on
`rewrite`, no new branch-detection logic needed.

**Real bug caught by smoke-testing before shipping**: `WorkflowPanel.
__init__` wires Qt's own `currentChanged` signal to `_on_tab_changed`,
which unconditionally raised `NotImplementedError` — harmless while
nothing used the panel, but Qt fires that signal itself the instant any
tab exists, so the very first click in the new window would have crashed
it immediately. Fixed to a safe no-op (logs the index) — this one stub
couldn't follow the "raise NotImplementedError" convention every other
stub in the scaffold uses, since Qt's own wiring calls it automatically
rather than waiting for real calling code to exist.

Verified via `QT_QPA_PLATFORM=offscreen`: window builds, all 5 tabs
clickable without crashing. Committed as `69930d9` (submodule, `rewrite`
branch) and `1fcaaba` (umbrella `main`, `suite_launcher`) —
**deliberately did not bump the umbrella's tracked `apps/LSPRi/eva`
submodule pointer** (maintainer's explicit choice), so umbrella `main`
keeps referencing the stable `develop` commit; the rewrite branch stays
opt-in locally.

## 2026-09-20: ROI stage ported — real model.py + detection.py, group_id type fixed

Checked `processing/chromatic.py` (1695 lines) before porting it next per
the sketch's ordering — found it imports `detect_rois` plus two private
helpers (`_masked_gaussian_filter`, `_refine_roi_center`) from
`processing/roi_detection.py`, a real dependency the sketch didn't call
out. Maintainer chose to port ROI detection first (dependency order)
rather than bridge chromatic to the old location.

`roi/model.py` got the real `RoiMask`/`AreaRoi`/`AreaRoiGroup`/
`RoiArrayGroup`/`AreaRoiDetectionSettings` dataclasses (replacing the
scaffold's placeholder shapes), ported verbatim from `domain/models.py`.
`roi/detection.py` is a verbatim copy of `processing/roi_detection.py`
(501 lines, diffed after — only the import line changed).

**Real placeholder bug caught**: the scaffold's guessed
`AreaRoiGroup.group_id: int` didn't match the real dataclass's
`group_id: str`. Fixed through everywhere it flows: `RoiToolbox`'s
group-command signatures, its `_groups`/`_array_groups` dict key types,
and `RoiTablePanel`'s two group-editing handlers.

Verified: imports cleanly, pyflakes-clean, rewrite-preview window still
builds and is clickable after the change. Committed as `6bb700e`.

## 2026-09-20: Chromatic sub-module ported — real model.py + fitting.py, real module.py

`image_tools/chromatic/model.py` got the real `ChromaticTransformModel`/
`ChromaticLandmarkObservation` dataclasses, verbatim from `domain/
models.py`. `fitting.py` is a verbatim port of `processing/chromatic.py`
(1695 lines) — checked first and confirmed genuinely pure numpy/scipy/
skimage math with zero Qt, so unlike `io/dataset.py`, the sketch's
assumption held here. Only the import block changed (diff-confirmed): now
depends on this session's `roi/detection.py`/`roi/model.py` instead of the
old `processing.roi_detection`/`domain.models` locations.

**`ChromaticModule.affine_for()`/`warp_mask()` are now real, working
implementations, not stubs.** Traced `gui/chromatic_controller.py` first
rather than guessing at the shape: `ChromaticTransformModel` is one model
per `(spectral_cube_index, wavelength_nm)`, stored as a flat list/dict —
not one global model, which is what the scaffold's original placeholder
(`self._model: ChromaticModel | None`) had assumed. Rewrote the module's
internal state to `dict[tuple[int, float], ChromaticTransformModel]` to
match. `affine_for()` returns the stored affine, or identity if no model
exists yet for that key (matching the old app's `affine_for_image_key_any`
shape); the old app's separate reference-key/
`chromatic_correction_enabled`-gated variant isn't wired here yet since
`GeometryModule`'s own settings don't exist yet — documented inline as a
TODO, not silently dropped. `add_landmark`/`refit` stay
`NotImplementedError` — fitting a real model from landmarks
(`fitting.estimate_affine_chromatic_transform`) is genuine implementation
work, not a port.

Verified with actual function calls, not just import-checking: identity
fallback for an unfitted key, a manually-stored model's real affine
matrix, and an actual boolean-mask warp all produced correct output.
Rewrite-preview window still builds. Committed as `4ae80f5`.

**Not done / still open, as of this entry**:
- `image_tools/preprocess.py` (966 lines) — not yet scope-checked against
  the sketch's "ports mostly as-is" assumption.
- `roi/reduction.py`/`roi/rasterize.py` — still the scaffold's placeholder
  stubs; real sources are `processing/roi_math.py`/`processing/
  roi_rasterize.py`, not yet scope-checked.
- `analysis/tasks.py`, `storage/session.py` — not yet started.
- The genuinely-new design pieces (`analysis/provenance.py`+`planner.py`,
  the §6a weighted-reduction/supersampling math) — not yet started, no
  existing file to port from.
- `image_tools/geometry.py`, `image_tools/mask.py`,
  `image_tools/background/*` — still the scaffold's `NotImplementedError`
  stubs; their real sources (`domain/models.py`'s `CropDefinition`/
  `GridBoundsDefinition`/`PreprocessingSettings`/`MaskSettings`, plus
  whatever background-estimation code exists in `processing/`) haven't
  been located/scope-checked yet.
- Nothing on `rewrite` has been pushed to `origin` yet. The umbrella
  repo's tracked `apps/LSPRi/eva` submodule pointer still points at the
  stable `develop` commit, deliberately not bumped to track this branch.

## 2026-09-20: Image Tools preprocess.py ported — geometry/mask/background split, settings decomposed

Scope-checked `processing/preprocess.py` (966 lines) per this session's
working method before porting it. Two real findings, both surfaced to the
maintainer before writing any code (mirroring how the `io.py` finding was
handled):

**Finding 1**: sketch §10 implies `preprocess.py`'s logic splits cleanly
across `geometry.py`/`mask.py`/`background/{estimate,apply}.py`. It
doesn't — the real file mixes all three: ~410 lines of self-contained
spatial-transform math (only reads `PreprocessingSettings`), ~350 lines of
background flattening (reads `PreprocessingSettings` *and* ROI's
`AreaRoi`/`AreaRoiDetectionSettings` for exclusion — not `MaskSettings`),
and ~50 lines of self-contained mask-creation math (only reads
`MaskSettings`), composed by one ~130-line `apply_preprocessing()`.
Presented three options (split now / port as one file first / defer);
maintainer chose **split by concern now**.

**Finding 2**: `PreprocessingSettings` itself (the dataclass these
functions consume) is a 40-field grab-bag mixing geometry (rotation/flip/
crop/display/calibration), background-flatten, and chromatic-correction
(`chromatic_*`/`reference_*`) fields in one dataclass — the sketch only
ever said Geometry owns its "spatial fields," never specifying the actual
boundary, and this shapes the not-yet-designed `storage/session.py`
persistence format. Presented two options (decompose now / keep unsplit
for later); maintainer chose **decompose now**.

**Mechanics**: traced each ambiguous field's real GUI consumer on
`develop`/`main` before placing it, rather than guessing — e.g.
`local_reference_normalization_enabled` moved to `BackgroundSettings`
because its checkbox (`background_local_reference_check`) lives in the
Background GUI section; `histogram_highlight_min/max_value` moved to
`MaskSettings` because it's the persisted state behind
`mask_controller.py`'s `current_histogram_highlight_mask_raw` (an actual
mask-candidate input, not just a cosmetic readout); `image_tools_enabled`
confirmed Geometry-only via `io/dataset.py`'s own comment ("Image tools
(rotation/flip/crop) ... Calibration ... rides along with the same flag").
`GridBoundsDefinition`/`ChromaticSettings` went to `chromatic/model.py`
since chromatic's own pure math never actually reads them today (they're
for the not-yet-implemented `add_landmark`/`refit`).

Built: `image_tools/geometry/` (new package — `model.py`'s
`GeometrySettings`/`CropDefinition`, `transform.py`'s 13 spatial-transform
functions, `module.py`'s relocated `GeometryModule` stub),
`image_tools/mask/` (new package, same shape — `creation.py`'s 3
mask-math functions), `image_tools/background/model.py` (new —
`BackgroundSettings`; `estimate.py`'s stub replaced with the 8 real
background functions), `image_tools/chromatic/model.py` (added
`ChromaticSettings`/`GridBoundsDefinition`), and `image_tools/preprocess.py`
(the real `apply_preprocessing()` composer, replacing the scaffold's
guessed and completely unused `preprocess_image()` stub — another
placeholder-shape mismatch in the same family as the `group_id: int` bug,
caught the same way: check the real call sites before trusting a guessed
signature).

**`apply.py`'s stub is unchanged, deliberately**: today's `flatten_background`
still does estimate-and-apply in one call, exactly as ported — splitting
that into a real reusable model object `apply.py` could consume is the
already-flagged genuine new work (§7 "Background"), not something this
port invents an answer for.

Verified every ported function byte-for-byte identical to its source
(diffed programmatically, not by eye) except type annotations
(`PreprocessingSettings` → `GeometrySettings`/`BackgroundSettings`/
`MaskSettings`) and one cosmetic constant reordering. Confirmed
pyflakes-clean, `apply_preprocessing()` runs correctly end-to-end (plain
call and a rotate+crop call), and the rewrite-preview window still builds.
Committed as `82146f5`.

**Not done / still open, as of this entry**:
- `roi/reduction.py`/`roi/rasterize.py` — still placeholder stubs; real
  sources are `processing/roi_math.py`/`processing/roi_rasterize.py`, not
  yet scope-checked.
- `analysis/tasks.py`, `storage/session.py` — not yet started.
- The genuinely-new design pieces (`analysis/provenance.py`+`planner.py`,
  the §6a weighted-reduction/supersampling math, the real background
  estimate/apply split) — not yet started.
- `GeometryModule`/`MaskModule`/`BackgroundModule`/`ChromaticModule`'s own
  command methods (`set_crop`, `set_rotation`, ...) are still
  `NotImplementedError` stubs — only their settings dataclasses and the
  pure math consuming them are real so far.
- Nothing on `rewrite` has been pushed to `origin` yet; the umbrella
  repo's submodule pointer is still deliberately not bumped.

## 2026-09-20: ROI stage finished — reduction.py + rasterize.py ported, circle/annulus rasterization unified into `roi/`

Scope-checked both remaining ROI-stage stubs before porting, per this
session's working method. Two real findings, both differing from the
sketch's assumption.

**`roi/reduction.py` — placeholder shape was simply wrong (fixed directly,
no check-in needed).** The stub guessed single-array functions (`mean
(values)`, `plane_fit(values)`). The real `processing/roi_math.py` (175
lines) is built around **sample+reference pairs**, not lone arrays —
`reduce_sample_and_reference_all_methods()` computes every
`REDUCTION_METHODS` entry from one already-extracted pixel pair in a single
call, which is what lets switching "Reduction method" in the GUI be instant
instead of re-reading pixels. `plane_fit` isn't a per-array function at all —
it fits a plane to the *reference* ROI's pixels and evaluates it at the
*sample* ROI's center, needing the reference region's pixel coordinates and
the sample center as extra arguments. Same class of bug as the
`AreaRoiGroup.group_id: int` and guessed `preprocess_image()` mismatches
already caught on this branch. Ported verbatim (diffed programmatically via
`ast.unparse` per function body, ignoring docstrings — confirmed
byte-identical logic; only cross-references to old-app-only files were
trimmed from two docstrings). `weighted_*` variants stay `NotImplementedError`
— genuine §6a implementation work, not a port.

**`roi/rasterize.py` — genuine scope mismatch, presented to the maintainer
before porting.** The stub's guessed `rasterize_binary(roi, bounding_box)`
implied this module rasterizes every ROI shape. The real
`processing/roi_rasterize.py` (80 lines) only handles the "mask" geometry
escape hatch (`crop_mask`/`expand_mask`/`expand_mask_to_patch`) — its own
docstring is explicit that circle/annulus rasterization already lives in
`processing/chromatic.py` (`transformed_disk_mask`/`transformed_annulus_mask`),
already ported to `image_tools/chromatic/fitting.py` earlier this session.
Presented two options: keep today's split (mask-geometry math in `roi/`,
circle/annulus math stays in Chromatic) vs. unify into one dispatcher in
`roi/` per AGENTS.md §6a's "one dispatcher, not per-shape code" preference.
**Maintainer chose: unify into `roi/`.**

Traced the real dependency shape before moving anything: `transformed_disk_
mask`/`transformed_annulus_mask`/`transformed_circle_points` and their
`_for_patch` variants take `affine_matrix` as a plain parameter — they don't
reach into Chromatic's internal state — so moving them to `roi/rasterize.py`
and having callers obtain the matrix via `ChromaticModule.affine_for()`
(the module's only public surface per AGENTS.md) keeps the module boundary
intact; `roi/rasterize.py` never imports or calls `ChromaticModule` itself.
`apply_affine_to_points`/`invert_affine_matrix` stayed in `fitting.py` since
other functions there (`fit_similarity_matrix`'s neighbors) still use them.
Moved 7 functions total (`transformed_circle_points`, `transformed_disk_
mask`, `_annulus_mask_in_box`, `annulus_reach_box`, `transformed_annulus_
mask`, `transformed_annulus_mask_for_patch`, `transformed_disk_mask_for_
patch`) verbatim — grep-confirmed no other in-repo new-code references to
the old location, one stale docstring cross-reference in `fitting.py`'s
`warp_boolean_mask_affine` updated to point at the new module.

**New real dispatcher, not just a move**: also ported `_effective_reference_
radii` and built `rasterize_sample`/`rasterize_reference` (+ `_for_patch`
variants) from the inline `if roi.sample_geometry_type == "mask" ... else
transformed_disk_mask(...)` dispatch that previously only existed inline
inside `gui/analysis_tasks.py`'s `_selected_roi_masks_for_spectrum` —
consolidated into one reusable per-ROI, per-side function in `roi/`, per the
maintainer's "unify" choice. Deliberately **did not** port that function's
own multi-ROI OR-accumulation loop (iterate every selected ROI, combine into
one mask, patch/reach-window caching) — that's real analysis-layer looping
logic (selection scope, exclusion, caching) that belongs to the not-yet-
built `analysis/tasks.py`, not to ROI's own rasterization.

**Real behavior preserved, not silently "fixed"**: mask-geometry ROIs are
**not** re-warped by the chromatic affine in `rasterize_sample`/
`rasterize_reference`, matching the current app's own documented limitation
(`analysis_tasks.py`'s inline comment: "mask-geometry ROIs are not re-warped
... revisit if chromatic-corrected arbitrary masks are needed"). This
appears to conflict with AGENTS.md's "masks are forward-transformed via
Chromatic's `warp_mask()`" invariant — resolved by reading that invariant as
describing a *future* chromatic-corrected-mask feature, not something
already true today; documented inline in `rasterize_sample`'s docstring
rather than silently building a fix nobody asked for.

Verified: pyflakes-clean, `ast.unparse`-diffed circle/annulus functions
identical pre/post move. Exercised with real calls (not just import-
checking) — disk/annulus mask geometry at an identity affine, `crop_mask`/
`expand_mask` roundtrip, `rasterize_sample`/`rasterize_reference` against
both circle+annulus and mask+"none" geometry ROIs, `_for_patch` variant
agreement with the full-image variant at a (0,0) patch origin. Rewrite-
preview window still builds, all 5 tabs present.

**ROI stage is now fully ported**: `roi/model.py`, `roi/detection.py`,
`roi/reduction.py`, `roi/rasterize.py` all real. `RoiToolbox`'s own command
methods (`add_roi`, `move_roi`, ...) are still `NotImplementedError` stubs —
wiring the toolbox's commands to this now-real math is separate work.

**Not done / still open, as of this entry**:
- `RoiToolbox`'s own command methods — still stubs; the math they'll call
  (`roi/reduction.py`, `roi/rasterize.py`, `roi/detection.py`) is now real.
- `analysis/tasks.py`, `storage/session.py` — not yet started. `analysis/
  tasks.py` will own the multi-ROI OR-accumulation loop this entry
  deliberately left out of `roi/rasterize.py`.
- The genuinely-new design pieces (`analysis/provenance.py`+`planner.py`,
  the §6a weighted-reduction/supersampling math — `roi/reduction.py`'s
  `weighted_*` and `roi/rasterize.py`'s `rasterize_fractional`, the real
  background estimate/apply split) — not yet started.
- `GeometryModule`/`MaskModule`/`BackgroundModule`/`ChromaticModule`'s own
  command methods are still `NotImplementedError` stubs.
- Nothing on `rewrite` has been pushed to `origin` yet; the umbrella
  repo's submodule pointer is still deliberately not bumped.

## 2026-09-20: Cross-module undo/redo added; RoiToolbox's real command methods built

Started as "port RoiToolbox's stub command methods" (the next item after
the ROI stage's math finished), but scope-checking the source first showed
this was the biggest task on this branch so far: the real logic is scattered
across `gui/roi_geometry_mixin.py` (553 lines) and `gui/roi_table_controller.py`
(732 lines), tangled with dialogs, undo snapshots, status-bar text, and
table/overlay redraw calls - plus ~250 more lines of delegate methods living
directly on `MainWindow` (`_rename_roi_group_from_table`, `_edit_roi_color_
from_table`, ...). A clean, concrete instance of the god-object pattern
`docs/rewrite_feature_inventory_2026-09.md` diagnosed.

**Undo/redo gap surfaced and resolved before porting anything.** Nearly
every one of those old-app mutations wraps a `_push_undo_point()`/
`_prepare_undo_snapshot()` call - a deepcopy-based undo stack on
`MainWindow`. Neither the architecture sketch nor `AGENTS.md` designs
undo/redo anywhere for the rewrite. Asked the maintainer how to handle it
rather than guessing at a cross-cutting design silently; they asked for a
**proper (not minimal) design, scoped to handle every module**, not just
ROI. Built `undo/manager.py`: one shared `undo_manager` instance (matches
`diagnostics_hub`'s existing process-wide singleton pattern exactly -
modules import it directly, no constructor injection), typed
`FunctionCommand` objects (a label plus `undo()`/`redo()` closures a command
method writes inline over its own state - not a generic before/after
deep-clone, which is what made the old app's version expensive: a deepcopy
of the *entire* app state, every ROI/mask/chromatic model, on every single
edit), `begin_batch()`/`end_batch()` for coalescing a drag gesture's many
intermediate pushes into one undo-stack entry, redo-stack invalidation on a
fresh push, and a `max_depth`-bounded stack. Verified with real push/undo/
redo/batch/nested-batch-raises/empty-batch-no-op scenarios, not just import-
checking. `AGENTS.md` gets a new "Undo/redo" section for the next module
that needs it.

**RoiToolbox's real command methods**, consolidated from the scattered
source above with dialogs/status-text/table-rendering deliberately left
out (those belong to the not-yet-built panel layer, which will prompt for
input then call these command methods with resolved values): `add_roi`
(a real gap in the original stub list - there was no command for placing a
single manual ROI at all), `move_roi`, `resize_roi`, `delete_roi`/
`delete_rois` (bulk primitive, prunes and correctly restores empty groups/
arrays on undo), `detect_rois` (bulk replace, takes already-detected ROIs
rather than running detection itself - that stays off the GUI thread per
AGENTS.md, the same threading boundary the old app's async worker already
enforced), `create_group`/`rename_group`/`recolor_group`/`reorder_group`/
`add_to_group` (enforces "at most one group per ROI" by evicting from any
prior group first)/`remove_from_group`, and `request_move` (forwards to
`move_roi`; image-bounds clamping is the Image panel's job now, since this
module has no image to clamp against).

**Two scaffold bugs found and fixed, not guessed past**:
- `RoiToolbox` had a duplicated `set_selection`/`selection_changed` pair
  alongside `SelectionModule`'s own - AGENTS.md is explicit that
  `SelectionModule` alone owns ROI selection ("the one intentionally shared
  piece of state"). Removed the duplicate; grep-confirmed nothing in the
  panel scaffolds referenced it.
- `RoiComputationalChange`'s documented `reason` values
  (`change_events.py`) didn't include `"added"`/`"deleted"`/`"detected"` -
  extended the comment to match the real reasons these new methods emit.

**Deliberate behavior change, flagged prominently (not buried)**: ROI IDs
are now stable and never reused (an incrementing counter), instead of the
old app's `_reindex_detected_rois` renumbering every ID to stay contiguous
after each delete. Reasoning: reindexing-on-delete is what makes undo of a
delete hard to get right (it would have to reverse the renumbering cascade
into every group/array reference too); it only existed because the old
list-based representation conflated ID with position, and the scaffold's
`dict[int, AreaRoi]` (already chosen before this session) doesn't need
that. User-visible effect: the ROI table's numbering can show gaps after a
delete (e.g. "1, 2, 4, 5") instead of always staying contiguous - documented
in `toolbox.py`'s module docstring for the maintainer to react to if this
isn't wanted.

**Deliberately not done this pass** (documented in `toolbox.py`'s own
docstring, not silently skipped): `display_position()` stays a stub - needs
a decision on how `RoiToolbox` obtains a Chromatic affine (hold a
`ChromaticModule` reference vs. take `affine_matrix` as an explicit
parameter, matching `roi/rasterize.py`'s precedent) that nothing in this
pass forced. The old app's spatial array-reordering feature
(`_reorder_rois_by_position`/`_order_rois_as_array`/`_group_rois_by_column`)
isn't ported - separate, UI-heavy feature, distinct from `reorder_group`
(group *display* order, which this pass does implement). Geometry-type
switching (circle/annulus <-> mask) isn't built - no mask-editing UI exists
yet to drive it.

Verified: pyflakes-clean. Exercised with real calls (not just import-
checking) - full CRUD + group lifecycle + undo/redo round-trips for every
command, including the "at most one group per ROI" invariant and undo
correctly restoring a group that `delete_rois` had pruned for being empty.
Rewrite-preview window still builds, all 5 tabs present. Panel scaffolds
(`panels/image`, `panels/roi_table`) already called `request_move`/
`rename_group`/`reorder_group` with signatures matching what was actually
built - no panel-side changes needed.

**Not done / still open, as of this entry**:
- `RoiToolbox.display_position()` — stub; needs the Chromatic-affine
  decision noted above.
- `analysis/tasks.py`, `storage/session.py` — not yet started.
- The genuinely-new design pieces (`analysis/provenance.py`+`planner.py`,
  the §6a weighted-reduction/supersampling math, the real background
  estimate/apply split) — not yet started.
- `GeometryModule`/`MaskModule`/`BackgroundModule`/`ChromaticModule`'s own
  command methods are still `NotImplementedError` stubs — first candidates
  to adopt the new `undo_manager` pattern once built.
- The panel layer (dialogs, table rendering, overlay drawing) that will
  actually call `RoiToolbox`'s now-real command methods isn't built beyond
  the scaffold stubs.
- Nothing on `rewrite` has been pushed to `origin` yet; the umbrella
  repo's submodule pointer is still deliberately not bumped.

## 2026-09-20: Renumber-on-delete restored (supersedes the entry above's ID-scheme decision); undo/redo signal gap fixed

The previous entry's "IDs are now stable/never-reused" decision was
presented to the maintainer as a flagged, reconsiderable choice - they
reconsidered it: **ROI ids must renumber contiguously on delete**, matching
the old app's `_reindex_detected_rois`, but designed so undo/redo handles
the renumbering correctly rather than avoiding it. Per this file's own
append-only rule, the previous entry's reasoning isn't edited - this
supersedes it.

**How the renumbering is made undo/redo-safe**: `delete_rois()` computes one
`old_id -> new_id` map up front, from the sorted surviving ids at the moment
of the call, and both `apply()` and `revert()` reuse that same fixed map
(`revert()` uses its reverse) rather than recomputing anything from
whatever the live state looks like when undo/redo actually runs later. This
is safe specifically because `undo_manager`'s stack is linear (see
`undo/manager.py`): by the time this command's `revert()` runs, every
command pushed after it has already been undone in reverse order, so the
toolbox is guaranteed to be in exactly the post-`apply()` state the map was
computed against. Group/array member-id references are remapped through
the same map; a group/array `delete_rois` pruned for becoming empty is
still correctly restored on undo (already true before this change, verified
again after).

**New cross-module concern surfaced, not yet resolved**: renumbering means
any *other* module holding a roi_id across a delete (`SelectionModule`'s
current selection; the future analysis store's per-ROI provenance) goes
stale unless it also remaps. Added `RoiToolbox.roi_ids_renumbered`
(`{old_id: new_id}`, survivors only, emitted on both `apply()` and
`revert()`) specifically so those modules *can* subscribe without
`RoiToolbox` reaching into their internals. Nothing subscribes yet -
`SelectionModule`'s own command methods and `analysis/tasks.py` are both
still stubs. Flagged in `toolbox.py`'s module docstring so this isn't
forgotten once either is built: without a `roi_ids_renumbered` handler,
they'll ship a real, silent "stale selection/provenance after a delete" bug.

**Separate correctness gap found and fixed while doing this**: every
command method's change signal (`geometry_changed`/`cosmetic_changed`) was
being emitted once, right after the initial `apply()` call at the bottom of
each method - never from inside `apply()`/`revert()` themselves. That meant
calling `undo_manager.undo()` or `.redo()` later would correctly mutate
state but emit nothing, so any panel or module reacting to those signals
would never learn a Ctrl+Z happened. Moved every `emit()` inside its
`apply`/`revert` closure, across every command method in the file (not just
`delete_rois`), so undo/redo now notifies exactly like a fresh call would.
This wasn't part of the maintainer's ask but was directly exposed by
actually exercising undo/redo with signal listeners attached while
verifying the renumbering fix, so it was fixed in the same pass rather than
left for a future session to rediscover.

Verified with real calls, not just import-checking: delete a middle ROI out
of five, confirm survivors renumber contiguously and the reported
`roi_ids_renumbered` map is exactly right; confirm a group holding the
deleted ROI *and* a survivor keeps only the survivor's new id; confirm
`add_roi` right after a delete continues the contiguous sequence; undo
restores the original five ids and the original group membership, with the
correct reverse map on `roi_ids_renumbered`; redo reproduces the exact same
renumbering. Separately verified a plain `move_roi`'s signal now fires on
`undo()` and `redo()`, not just the initial call. Re-ran the full prior
CRUD/group regression suite from the previous entry - still passes
unchanged. Rewrite-preview window still builds, all 5 tabs present.

**Not done / still open, as of this entry** (unchanged from the previous
entry, still open):
- `RoiToolbox.display_position()` — stub; needs the Chromatic-affine
  decision noted in `toolbox.py`'s module docstring.
- `analysis/tasks.py`, `storage/session.py` — not yet started.
- `SelectionModule`'s own command methods — still stubs; first real
  consumer needed for `roi_ids_renumbered` to actually matter.
- `GeometryModule`/`MaskModule`/`BackgroundModule`/`ChromaticModule`'s own
  command methods are still `NotImplementedError` stubs.
- The panel layer isn't built beyond the scaffold stubs.
- Nothing on `rewrite` has been pushed to `origin` yet; the umbrella
  repo's submodule pointer is still deliberately not bumped.

## 2026-09-20: `SelectionModule`'s command methods built — closes the `roi_ids_renumbered` gap

`set_cube`/`set_wavelength`/`set_roi_selection` are now real (`selection/
module.py`), replacing the scaffold stubs. Each is a plain mutate + emit,
no-op-skipped when the new value equals the current one (same convention
`RoiToolbox`'s command methods already use) - `set_cube` also rejects a
negative index. **Deliberately not wired through `undo.undo_manager`**:
selection was never part of `_push_undo_point` in the old app either, and
the sketch (§9) settles that selection can never trigger recompute, which
extends naturally to "selection isn't undo history" - undo is for state
that affects results, not where the cursor happens to be.

**Closes the cross-module gap `RoiToolbox.delete_rois()` flagged** (see
previous two entries, and `roi/toolbox.py`'s module docstring before this
edit): added `SelectionModule.remap_roi_ids(id_map)`, connected to
`RoiToolbox.roi_ids_renumbered` in `app_rewrite.build_main_window()` (the
one place that wires cross-module signals - `SelectionModule` itself holds
no `RoiToolbox` reference, per AGENTS.md's "no module reaches into another
module's internals"). A selected id absent from the map (i.e. it was the
one deleted, not renumbered) is dropped, not treated as an error - deleting
a currently-selected ROI is an ordinary event.

**Verified with real calls** (scripted, not pytest - no test harness exists
yet for the rewrite modules): no-op skip confirmed for repeated identical
`set_cube`/`set_wavelength`/`set_roi_selection` calls (no duplicate signal
emission); negative `set_cube` raises; and the full delete/undo/redo cycle
against `RoiToolbox` was exercised directly - select ROIs `{2, 3, 5}` out of
five, delete ROI 3 (survivors `{1,2,4,5}` renumber to `{1,2,3,4}`),
selection correctly becomes `{2, 4}` (id 3 dropped since it was the one
deleted, id 5→4 remapped) with exactly one `roi_selection_changed`
emission; `undo_manager.undo()` restores the original five ROIs and remaps the still-live part of
the selection back through the reverse map to `{2, 5}` (id 3 does **not**
reappear in the selection - correct, since selection isn't undo-tracked,
only the surviving members' ids get un-renumbered); `undo_manager.redo()`
reproduces the original post-delete selection `{2, 4}` exactly. Confirmed
the rewrite-preview window (`app_rewrite.build_main_window()`) still builds
with the new cross-module `connect()` in place. `roi/toolbox.py`'s module
docstring updated to record the gap as closed rather than open.

**Not done / still open, as of this entry**:
- `RoiToolbox.display_position()` — stub; needs the Chromatic-affine
  decision noted in `toolbox.py`'s module docstring.
- `analysis/tasks.py`, `storage/session.py` — not yet started.
- `GeometryModule`/`MaskModule`/`BackgroundModule`/`ChromaticModule`'s own
  command methods are still `NotImplementedError` stubs — next candidates
  to adopt the `undo_manager` pattern, now proven out on two modules
  (`RoiToolbox`, and by deliberate contrast `SelectionModule`'s decision
  *not* to use it).
- The panel layer isn't built beyond the scaffold stubs.
- Nothing on `rewrite` has been pushed to `origin` yet; the umbrella
  repo's submodule pointer is still deliberately not bumped.

## 2026-09-20: `GeometryModule`'s command methods built

`set_image_tools_enabled`/`set_rotation`/`set_rotation_fill_dark`/
`set_flip`/`set_crop`/`clear_crop` replace the scaffold stubs
(`image_tools/geometry/module.py`), the first Image Tools sub-module past
`NotImplementedError`. Ported the real state-mutation logic out of
`gui/image_tools_controller.py` on `develop` - that file's tool-activation
state, pyqtgraph `RectROI` sync, and overlay redraw calls stay in the
not-yet-built panel layer, same split as `RoiToolbox`'s own port. Two
methods weren't in the original stub list, matching `RoiToolbox.add_roi`
being a real gap found the same way: `set_image_tools_enabled` (the
link/unlink toggle) and `set_rotation_fill_dark` (edge-stretch vs. 0-fill
for rotation padding) both had real old-app actions with no scaffold
counterpart.

**Adopts the `undo_manager` pattern** exactly like `RoiToolbox` (the
pattern's second real user, as flagged in the previous entry) - every
setter pushes one `FunctionCommand` with the mutation re-run from inside
`apply()`/`revert()`, same as `RoiToolbox`, and is a no-op (no undo entry)
when the new value already matches, same no-op-skip convention.
**By deliberate contrast with `SelectionModule`**: Geometry's changes are
computational (they resample the pixel grid), so unlike Selection they
*do* belong in undo history - this is the concrete case the
`SelectionModule` entry's "selection isn't undo history" reasoning was
drawing the line against.

**New payload type**: `GeometryComputationalChange` (`reason: str`,
colocated in `geometry/model.py` per `change_events.py`'s own instruction
for each Image Tools module to define its own analogous type) - no
`roi_ids` field, since Geometry's scope is the whole processed image, not
a subset of ROIs, unlike `RoiComputationalChange`. Replaces
`geometry_changed`'s old bare `pyqtSignal()` placeholder.

**`GeometryModule.settings()` returns a defensive copy** (`dataclasses.
replace()` on both the settings object and its nested `CropDefinition`) -
verified a caller mutating the returned object (`s.rotation_angle_deg =
999`, `s.crop.x = 999`) has zero effect on the module's real state. Used
`dataclasses.replace`, not the newer `copy.replace` (Python 3.13+ only;
this repo's floor is 3.12 per `pyproject.toml`).

**Scope boundary, deliberate**: `GeometrySettings`' display-only
calibration/scale-bar/measurement-anchor fields (`display_units` through
`measurement_anchor2_y_px`) get no command methods this pass - nothing in
`transform.py`'s pure math reads them (per `model.py`'s own docstring), so
nothing downstream is gated on them, unlike crop/rotate/flip. The real
logic to port for those lives in `gui/measurement_calibration_mixin.py`'s
`_apply_measurement_calibration` on `develop` - left for a future pass,
flagged in `geometry/module.py`'s own docstring rather than silently
skipped. No `GeometryCosmeticChange` type defined yet for the same reason
(would be dead code until those commands exist).

**Verified with real calls** (scripted, no pytest harness yet): every
setter's no-op-skip (a repeated identical call emits nothing twice); the
defensive-copy guarantee above; `set_crop` implies `enabled=True` matching
the old app's `crop_roi_changed`; a full undo of all 6 pushed commands
(rotation, flip, rotation-fill, crop, image-tools-link, crop-set again)
returns every field to `GeometrySettings()`'s defaults, and redo reproduces
the exact final state; `clear_crop` correctly restores the prior crop
rectangle on undo (not an empty one). Confirmed the rewrite-preview window
still builds.

**Correction to the previous entry**: "nothing on `rewrite` has been
pushed to `origin` yet" is now stale - the `SelectionModule` commit was
pushed and `origin/rewrite` exists as of this session.

**Not done / still open, as of this entry**:
- `RoiToolbox.display_position()` — stub; needs the Chromatic-affine
  decision noted in `toolbox.py`'s module docstring.
- `analysis/tasks.py`, `storage/session.py` — not yet started.
- `GeometryModule`'s calibration/scale-bar command methods — deliberately
  out of scope this pass (see above); `gui/measurement_calibration_mixin.py`
  on `develop` has the real logic to port.
- `MaskModule`/`BackgroundModule`/`ChromaticModule`'s own command methods
  are still `NotImplementedError` stubs — next candidates for the
  `undo_manager` pattern, now proven out on two real modules.
- The panel layer isn't built beyond the scaffold stubs.

## 2026-09-21: `GeometryModule`'s calibration/scale-bar commands built — closes the scope gap the previous entry flagged

Folded in the deliberately-deferred piece from the previous entry, at the
maintainer's request: `set_measurement_anchors`/`apply_measurement_
calibration`/`set_display_units`/`set_scale_bar_visible`, ported from
`gui/measurement_calibration_mixin.py` and the relevant state-mutation
lines of `gui/main_window.py`/`gui/overlay_manager.py` on `develop` (ruler
overlay drawing, scale-bar rendering, and spinbox/status-label wiring stay
in the not-yet-built panel layer, same split as every other command port
so far). Also added `can_display_micrometers()`/`microns_per_pixel_scalar()`
to the query interface - pure derived reads ported from `_can_display_
micrometers`/`_microns_per_pixel_scalar`, which only ever read settings
fields, unlike the actual px<->um label-formatting helpers
(`gui/ui_helpers.py`'s `length_px_to_display` et al.), which are trivial
one-liners left for whichever panel needs them rather than duplicated here.

**New payload type**: `GeometryCosmeticChange` (`reason: str`, colocated in
`geometry/model.py` next to `GeometryComputationalChange`) - confirmed
never analysis-invalidating by `transform.py` never reading any of these
fields (the same fact the previous entry's scope-boundary decision rested
on). New `GeometryModule.cosmetic_changed` signal.

**Important nuance found while porting, worth flagging explicitly**:
"cosmetic" (never triggers recompute) and "undo-tracked" turned out to be
two independent axes, not the same distinction - checked the old app's
actual behavior method-by-method rather than assuming every action in
`measurement_calibration_mixin.py` pushed an undo point the way
`RoiToolbox.rename_group`/`recolor_group` do (cosmetic *and*
undo-tracked). It doesn't: only `_apply_measurement_calibration` calls
`_push_undo_point`; dragging the ruler anchors, toggling display units,
and toggling the scale bar never did there. Matched that exactly rather
than "regularizing" it - `set_measurement_anchors`/`set_display_units`/
`set_scale_bar_visible` are **not** wired through `undo_manager`, while
`apply_measurement_calibration` is, pushed as `"Measurement calibration"`
(the old app's exact label).

**Validation ported as `ValueError`, not a silent status-bar refusal**:
`apply_measurement_calibration` raises for the same three preconditions
`_apply_measurement_calibration` guarded with an early `return` + status
text (both dx/dy µm are non-positive; a requested axis's ruler delta is
effectively zero), and `set_display_units("um")` raises if not yet
calibrated (old app: `_toggle_display_units`'s "Calibrate the ruler
first..." status message) - this module has no status bar, so the panel
layer is responsible for catching these, the same convention established
by `SelectionModule.set_cube`'s negative-index guard. The asymmetric-axis
fallback (only Δx given → µm/px-y follows µm/px-x, and vice versa) was
kept exactly as the old app computed it.

**Deliberately not ported**: `_normalize_display_units`'s defensive
"silently fall back to px if calibration was lost" repair - there is
currently no command on this module that can *revoke* calibration once
applied (matching the old app: no "uncalibrate" action exists either), so
the invariant it protects can't actually be broken through this module's
own command surface yet. Flagged in the module docstring for
`storage/session.py` (not started) to revisit if a loaded session file
ever needs that repair.

**Verified with real calls** (scripted, no pytest harness yet):
`set_display_units("um")` correctly refused before calibration;
`set_measurement_anchors`'s no-op-skip and *not* pushing an undo entry;
`apply_measurement_calibration(dx_um=50, dy_um=0)` against a 100px ruler
producing `microns_per_pixel_x == microns_per_pixel_y == 0.5` (the
symmetric-fallback case), `calibration_enabled=True`, `display_units=
"um"`, and exactly one undo entry pushed; the zero-ruler-delta and
both-axes-non-positive guards both raising `ValueError`;
`set_scale_bar_visible` mutating state and emitting `cosmetic_changed`
without touching the undo stack; undoing the calibration restoring the
pre-calibration defaults exactly. Confirmed the rewrite-preview window
still builds.

**Not done / still open, as of this entry**:
- `RoiToolbox.display_position()` — stub; needs the Chromatic-affine
  decision noted in `toolbox.py`'s module docstring.
- `analysis/tasks.py`, `storage/session.py` — not yet started.
- `MaskModule`/`BackgroundModule`/`ChromaticModule`'s own command methods
  are still `NotImplementedError` stubs — next candidates for the
  `undo_manager` pattern, now proven out on two real modules. `GeometryModule`
  itself is now fully built (computational + cosmetic commands both done).
- The panel layer isn't built beyond the scaffold stubs.

## 2026-09-21: `BackgroundModule` built; `MaskModule` built (scoped narrower - see below)

**`BackgroundModule`**: `set_flatten_background_settings()` replaces the
scaffold stub - and fixes a bug found by actually running it, not just
reading it: the old stub called `estimate.estimate_background(image)`,
which raised `AttributeError` - that function doesn't exist in
`estimate.py` (only `flatten_background()` does, estimate+apply combined).
Deeper problem underneath the crash: checking the real caller
(`processing/preprocess.py`) shows `flatten_background()` runs live,
inline, per rendered frame from `BackgroundSettings` fields directly -
there's no persisted "fitted model" object anywhere in the current app for
this stage (`BackgroundSettings` itself confirms this: every field is a
parameter/toggle, none is a result), unlike Chromatic's genuinely-stored
per-image affine models the sketch's "owns the fitted background model"
phrase was evidently modeled on. So this module's honest job is settings
ownership, same shape as `GeometryModule`, not fit-and-cache - the old
`fit_from_image`/`model()` pair is removed rather than fixed in place.
One combined command (not six granular setters, deliberately unlike
Geometry): checked the real UI wiring
(`gui/main_window.py`'s `_update_image_processing_settings`) rather than
assuming, and it's a settings-panel-with-an-Apply-button pushing exactly
one `_push_undo_point("Image processing")` for the whole group - matched
that shape and that exact undo label.

**`MaskModule`**: scoped narrower than Geometry/Background after reading
all 1216 lines of `gui/mask_controller.py` - Mask's real "apply" flow is
async-worker-backed (`request_mask_candidate` backgrounds two of its four
tool kinds) and file-I/O-heavy (`QFileDialog` load/save), not plain
in-memory settings mutation. Built the two things that are:
`set_tool_settings()` (every tuning parameter at once, same combined-Apply
shape as Background) and `set_file_mask()`/`raw_mask()` (the committed
raster mask, replacing those two stubs). `set_histogram_highlight_range()`
handles the histogram widget's live drag-selection separately, matching
`GeometryModule.set_measurement_anchors`'s split.

Two findings from reading the real file, worth recording since they
contradict what the sketch/model.py's ported field list would suggest:
1. `window._state.mask` (`domain.models.MaskSettings`) is essentially
   unused for its own tunable fields in the old app - grepped every write
   to it; the *only* writes are `clear_preview_overlays()` resetting the
   "New mask system state" fields (`histogram_enabled`/`histogram_mask`/
   `figure_enabled`/`figure_mask`) to their defaults. Every real
   tool-tuning value is read straight from its Qt spinbox each time
   (`mask_settings_from_controls()`), never persisted through this
   dataclass - so `MaskModule.set_tool_settings()` isn't a port of an
   existing setter, it's these settings' first real owner. Deliberately
   left the four "New mask system state" fields alone - no command
   methods added for them, carried over exactly as inert as they were.
2. `create_histogram_mask()` (`creation.py`) is dead code in the old app -
   never called anywhere. The "histogram" mask tool's real candidate comes
   from `current_histogram_highlight_mask_raw()` in `mask_controller.py`
   instead, a completely different algorithm (selects by *displayed*
   value range, then maps through processed<->raw coordinate maps) never
   ported into `creation.py` as a pure function. Its fields
   (`histogram_min_value`/`histogram_max_value`) are still included in
   `set_tool_settings()` as real dataclass fields regardless of current
   dead-code status - flagged rather than silently dropped.

**Confirmed no undo-tracking for either module's new commands**: grepped
`mask_controller.py` for `_push_undo_point` - zero matches, for any mask
action, ever. Neither `BackgroundModule`'s nor `MaskModule`'s tool-tuning
settings are undo-tracked either (`BackgroundModule`'s combined command
*is* undo-tracked, matching the real `_push_undo_point("Image
processing")` call the old app does make for that one).

**New payload types**: `BackgroundComputationalChange` (one type, no
cosmetic half - every `BackgroundSettings` field feeds
`flatten_background()` directly). `MaskComputationalChange` +
`MaskCosmeticChange` (two types, like Geometry) - only the committed
`file_mask` is computational; tool-tuning settings and the histogram-
highlight selection are cosmetic, on the same reasoning as Geometry's
measurement anchors: unlike crop/rotate/flip (which apply continuously,
every render), a Mask tool's settings only affect anything once a
not-yet-built "apply" command merges a computed candidate into
`file_mask` - until then, changing a threshold slider invalidates nothing
already computed.

**Real bug caught during implementation, not just porting**: a dataclass
`==` compare across `MaskSettings` (which has two `np.ndarray | None`
fields) returns an array, not a bool, once either field actually holds an
array - `ndarray == ndarray` doesn't reduce to a scalar. `set_tool_settings`'s
no-op check compares an explicit tuple of just the 8 scalar tunables
instead of `new_settings == self._settings`, sidestepping this rather than
relying on the fact that `histogram_mask`/`figure_mask` happen to always
be `None` today (per finding 1 above - true now, but not a safe thing to
build a no-op check on).

**Verified with real calls** (scripted, no pytest harness yet): both
modules' defensive-copy guarantees; `BackgroundModule`'s no-op-skip and
clamping (`binning` floors at 1, `exclusion_dilation_px` floors at 0) and
full undo-back-to-defaults; `MaskModule`'s `set_tool_settings`/
`set_histogram_highlight_range` no-op-skip with zero undo-stack growth
(confirming they're genuinely not undo-tracked); `set_file_mask`'s
defensive copy (mutating the returned array doesn't touch internal state)
and content-based no-op skip (a different array object with identical
content is a no-op, matching the old app's `np.array_equal` check).
Confirmed the rewrite-preview window still builds.

**Not built this pass, explicitly flagged rather than guessed at** (the
`MaskModule` remainder, all bigger/async/file-I/O-shaped, unlike anything
built so far): computing an actual mask *candidate* from these settings
and merging it into `file_mask` (`apply_histogram_mask`/`apply_relative_
mask`/`apply_local_contrast_mask`/`apply_morphology_mask` and their
`reset_*` counterparts, the `request_mask_candidate` worker/cache
machinery behind them); brush painting (`apply_mask_brush`); mask file
load/save; per-wavelength mask diffs (needed once off-reference painting
under chromatic correction matters); porting `current_histogram_highlight_
mask_raw()`'s real coordinate-map algorithm into `creation.py`.

**Not done / still open, as of this entry**:
- `RoiToolbox.display_position()` — stub; needs the Chromatic-affine
  decision noted in `toolbox.py`'s module docstring.
- `analysis/tasks.py`, `storage/session.py` — not yet started.
- `MaskModule`'s async/file-I/O-shaped remainder — see above, deliberately
  out of scope this pass.
- `ChromaticModule`'s own command methods (`add_landmark`/`refit`) are
  still `NotImplementedError` stubs, untouched this pass - `fitting.py` is
  1539 lines and genuinely the biggest remaining chunk of the four Image
  Tools sub-modules; deferred rather than rushed. `GeometryModule`/
  `BackgroundModule`/`MaskModule` (within the scope noted above) are now
  built.
- The panel layer isn't built beyond the scaffold stubs.

## 2026-09-21: `chromatic/fitting.py` split into four files; dense tile-matching mode dropped

Maintainer's request, after reviewing the previous entry's chromatic-fitting
scope assessment: the single 1539-line `fitting.py` bundles three genuinely
independent things, not one algorithm that grew large - split each into its
own file rather than deferring the whole thing as one chunk.

**`affine.py`** (210 lines) - the actual "points in, matrix out" math:
`fit_affine_matrix`/`fit_similarity_matrix`, `apply_affine_to_points`,
`affine_residuals`, `invert_affine_matrix`, `compose_affine_matrices`,
`decompose_similarity_matrix`/`compose_similarity_matrix`,
`identity_affine_matrix`, `transform_rois_affine`. Zero dependency on
anything registration/detection-related - this is the part the maintainer's
own mental model of the feature matches, and it was already this simple;
splitting it out just makes that visible instead of buried in a 1539-line
file.

**`warp.py`** (83 lines) - apply a matrix to pixels instead of points:
`warp_image_affine`, `warp_boolean_mask_affine`, `apply_mask_wavelength_diff`.
Depends only on `affine.invert_affine_matrix`.

**`landmark_autotrack.py`** (1059 lines) - the automatic landmark detection
+ tracking feature (Harris-corner or particle-centroid finding, then
phase-correlation-predicted patch/centroid tracking wavelength-to-
wavelength with trend-consistency drift correction). Confirmed still
actively used (the previous entry's finding: it's the backend of a real
worker task in `gui/analysis_tasks.py`) - kept deliberately independent per
the maintainer's explicit request: zero dependency on `ChromaticModule` or
any other Image Tools sub-module (only `roi.detection`/`roi.model`, for the
optional "match against a real detected particle" refinement), and a
narrow public surface (`auto_track_landmarks_over_wavelengths` -
mirroring the "combined"/`kind="both"` mode confirmed as the one actually
used - `default_landmark_anchors`, plus the four detect/track building
blocks it's assembled from) so the algorithm can be retuned or rewritten
later by touching only this one file, never `ChromaticModule` itself.

**Two functions dropped, not carried into the split**:
`_traceable_landmark_candidates`/`_select_spread_landmarks`, an alternate
landmark-selection strategy - grepped every call site across the *old* app
too (not just this rewrite) and found neither is ever called anywhere;
dead code inherited from `fitting.py`'s own source, not something this
split newly orphaned. Left out to keep `landmark_autotrack.py`'s surface
matching what's genuinely used; recoverable from git history if ever
needed.

**Dense whole-image tile-matching mode dropped entirely** (maintainer's
explicit go-ahead, after the previous entry's explanation): removed
`estimate_affine_chromatic_transform` and `ChromaticRegistrationResult`
(only ever used by that one function). Confirmed dead in the *old* app
before removing anything - `chromatic_registration_mode` is hardcoded to
`"landmark_radial"` at all three places that ever write it
(`gui/chromatic_controller.py` x2, `gui/session_state_manager.py`); no UI
control ever sets it to anything else, so `_estimate_chromatic_models_task`'s
`else` branch (the only caller) never actually runs. `phase_correlation_
shift`/`multiscale_phase_correlation_shift`/`_match_patch`/the subpixel-
refinement helpers stayed - they're still needed by the confirmed-live
auto-tracking path (`track_landmarks`/`track_spot_landmarks`), just not by
the dense mode's own outer tiling loop, which is what's gone. Recoverable
from `develop`'s git history if ever wanted back; not deleted there, only
left unported here.

**One duplication fixed as a direct consequence, not scope creep**: the old
app's `landmark_radial` branch computed a fit's RMSE with an inline
`np.sqrt(np.sum((apply_affine_to_points(...) - target_points) ** 2, axis=1))`
- the exact same formula `affine_residuals()` already provides as a named
function, just never called from there. Left as a note for whoever builds
the not-yet-extracted wavelength-interpolation step (see below): call
`affine.affine_residuals()` there instead of re-deriving the formula a
third time.

**Also fixed while touching these files**: `chromatic/module.py`'s
`refit()` docstring previously pointed at
`fitting.estimate_affine_chromatic_transform` as "the" thing to call - both
wrong now (that function is gone) and wrong before (the landmark_radial
path, the only live one, never called it either - it uses
`affine.fit_similarity_matrix`/`fit_affine_matrix` plus a wavelength-
interpolation step, not the dense-tile function). Corrected to describe
the real source to port from
(`gui/analysis_tasks.py`'s `_estimate_chromatic_models_task`).

**Not done this pass**: the fourth piece - "fit at a few sampled
wavelengths, interpolate the rest across the whole cube" - still lives
inline in `gui/analysis_tasks.py`, not extracted into its own file. Not
done speculatively; left for whoever actually builds `ChromaticModule.
refit()`, since extracting it now with no real caller would be exactly the
kind of premature module the maintainer didn't ask for. `ChromaticModule.
add_landmark()`/`refit()` themselves are still `NotImplementedError` stubs
- this pass only reorganized/pruned the math they'll eventually call.

**Verified with real calls** (scripted, no pytest harness yet): a
similarity-fit round-trip (`fit_similarity_matrix` recovers a known
scale/rotation/shift transform to `1e-6`, `affine_residuals` near zero on
the same data, `invert_affine_matrix` round-trips points back to their
original positions); `warp_boolean_mask_affine` with an identity matrix
leaves a mask unchanged; `apply_mask_wavelength_diff` is non-mutating; a
full synthetic two-wavelength `auto_track_landmarks_over_wavelengths` run
(6 synthetic Gaussian "particles", a known integer pixel shift between the
two frames) correctly detects and tracks all 5 requested landmarks across
both wavelengths; `default_landmark_anchors` still importable and callable
directly (matching `gui/chromatic_controller.py`'s own direct call);
`ChromaticModule.affine_for()`/`warp_mask()` still work correctly through
the new `affine`/`warp` imports. Confirmed `pyflakes` clean across the
entire `src/lspr_imaging_app` tree (not just the touched files) and that
the rewrite-preview window still builds.

**Not done / still open, as of this entry**:
- `RoiToolbox.display_position()` — stub; needs the Chromatic-affine
  decision noted in `toolbox.py`'s module docstring.
- `analysis/tasks.py`, `storage/session.py` — not yet started.
- `ChromaticModule.add_landmark()`/`refit()` — still `NotImplementedError`
  stubs; the math they'll call is now split/pruned (`affine.py`/`warp.py`/
  `landmark_autotrack.py`), but the wavelength-interpolation step and the
  command methods themselves aren't built.
- `MaskModule`'s async/file-I/O-shaped remainder (mask candidate
  computation, brush painting, file load/save) - deliberately out of scope,
  see the previous entry. Mask/ROI tool-sharing design (maintainer raised
  this, not yet discussed in depth) is a separate open conversation.
- The panel layer isn't built beyond the scaffold stubs.

## 2026-09-21: Mask/ROI raster-tools design conversation - `creation.py` renamed to `raster_tools.py`, made shared-ready

Design discussion with the maintainer, resolved before any code changed
(their explicit request - "we can discuss after"). Two findings worth
recording since they correct/sharpen earlier statements in this log and in
`roi/rasterize.py`'s own docstring:

**The ignore mask already undergoes the same chromatic-correction warp as
ROIs - a prior message in this conversation described it as if it
didn't, which was wrong.** Checked `gui/mask_controller.py`'s
`current_external_mask()` (the real read-time resolution path): it warps
the canonical reference-frame mask through `warp_boolean_mask_affine`
(the same `ChromaticModule.affine_for()`-driven mechanism that moves
circle/annulus ROI positions) *first*, and only *then* layers a sparse
manual per-wavelength diff (`apply_mask_wavelength_diff`) on top as an
optional touch-up. The earlier description conflated this always-on
warp step with `apply_mask_brush`'s *write-time* branching (whether a new
stroke goes straight into the canonical array or into the diff dict,
depending on reference/off-reference + chromatic-correction-on) - a real,
Mask-specific mechanism, but unrelated to whether CC-warping happens at
all (it always does).

**ROI arbitrary-mask geometry (`AreaRoi.sample_mask`/`reference_mask`, a
`RoiMask`) does *not* yet get this same warp - a known, already-documented
gap, not a design disagreement.** `roi/rasterize.py`'s own docstring
(written during the earlier ROI-stage session) already states AGENTS.md's
"masks are forward-transformed via Chromatic's `warp_mask()`" invariant as
the *target* state, "not something this port silently adds." Scoped the
fix concretely in that same docstring: warp the expanded mask through
`image_tools.chromatic.warp.warp_boolean_mask_affine(expanded_mask,
affine_matrix)` before returning it, using the same `affine_matrix`
parameter the circle/annulus branch already takes - the identical pattern
already in use, not a new one. Not built this pass (no mask-geometry ROI
UI exists yet to need it), but now has a concrete "how," not just a "this
should happen eventually."

**Agreed design for shared raster tools**: `image_tools/mask/creation.py`
already had two functions with zero Mask-specific coupling
(`apply_morphology_to_mask`, the relative/local-contrast candidate
generator) - renamed the file to `raster_tools.py` and decoupled the
threshold/contrast generators from `MaskSettings` (now take plain scalar
parameters, split `create_figure_mask(mode=...)` into
`create_relative_contrast_mask`/`create_local_contrast_mask`) specifically
so a caller with no `MaskSettings` instance - i.e. a future ROI
mask-drawing command - can call them too. Zero existing callers anywhere
in the rewrite (verified by grep before renaming), so this was a
zero-migration-cost change.

**Two new functions added**, the pieces that didn't already exist:
- `brush_stamp_bounds(canvas_shape, center_xy, radius_px)` - the circular
  brush footprint for one stroke, clamped to the canvas, returning
  `(x0, x1, y0, y1, local_mask)` or `None` if fully off-edge. Deliberately
  just the footprint, not a paint operation - it doesn't know or care
  whether the caller writes directly into a canonical array or (Mask's own
  wrinkle) accumulates into a sparse per-wavelength diff dict instead; see
  the function's own docstring for why that branching stays out of this
  shared toolbox rather than being generalized into it.
- `apply_brush_stamp(canvas, center_xy, radius_px, value=...)` - the
  direct-write convenience built on `brush_stamp_bounds`, for the common
  case (both Mask's on-reference/CC-disabled case and, later, every ROI
  mask-drawing edit, which has no per-wavelength-diff complication at all
  per `roi/model.py`'s own "a mask sits at the same absolute pixel
  location for every wavelength" note).
- `merge_mask_candidate(current, candidate, subtract=...)` - named the
  add/subtract-a-candidate operation that was previously just inlined in
  the old app's apply-delta code (`np.logical_or`/`np.logical_and(~...)`),
  so both future consumers call one function instead of re-deriving it.

**Grayscale/weighted masks** (maintainer's stated future direction - the
mask as intensity-weighted, not just binary include/exclude) deliberately
**not** built into this pass: it would touch how `flatten_background`'s
exclusion mask and `AreaRoiDetectionSettings.ignored_pixel_mask` consume
the array (both expect boolean today) and any persisted weighting is an
HDF5-schema decision - flagged as a real direction, not assumed or
half-built.

**Caching the per-wavelength chromatic warp** (maintainer's question) -
agreed to defer until it's actually measured slow, per this repo's own
performance-work rule (instrument, then optimize, don't guess); noted a
cache would most naturally key the same way `ChromaticModule`'s own
fitted models are keyed, wherever the analysis-store recompute planner
ends up living (`analysis/tasks.py`, not started).

**Verified with real calls** (scripted, no pytest harness yet): threshold
mask correctness on a hand-computed 2x3 example; relative/local-contrast
masks correctly flag a synthetic bright blob; a dilate-then-erode
morphology round trip; `brush_stamp_bounds`/`apply_brush_stamp` on a
centered stroke, an off-canvas stroke (`None`, no-op paint), and an
edge-straddling stroke (paints only the in-bounds part); `apply_brush_stamp`
confirmed non-mutating (original canvas untouched); `merge_mask_candidate`
add/subtract correctness on overlapping regions. Confirmed `pyflakes`
clean across the whole `src/lspr_imaging_app` tree and that the
rewrite-preview window still builds.

**Not done / still open, as of this entry**:
- `RoiToolbox.display_position()` — stub; needs the Chromatic-affine
  decision noted in `toolbox.py`'s module docstring.
- `analysis/tasks.py`, `storage/session.py` — not yet started.
- `ChromaticModule.add_landmark()`/`refit()` — still stubs.
- `MaskModule`'s async/file-I/O-shaped remainder (candidate computation,
  brush painting, file load/save) - the command methods that will
  actually call `raster_tools.py` aren't built yet, only the toolbox
  itself.
- ROI mask-geometry chromatic warp (`roi/rasterize.py`'s scoped-but-
  unbuilt task above) and ROI mask-drawing commands/UI - neither started;
  no mask-geometry ROI editing exists yet at all.
- The panel layer isn't built beyond the scaffold stubs.

## 2026-09-21: `MaskModule`'s "apply" command methods built - `apply_candidate`/`apply_morphology`/`paint_brush`

Closes most of the previous entry's remaining gap: real commands that
call into `raster_tools.py`, replacing the "toolbox built, nothing calls
it yet" state.

**Read the real button wiring before designing the morphology command,
rather than guessing from the earlier `apply_mask_delta`/`candidate_mask_
for_tool` code alone** - `gui/main_window.py`'s morphology buttons are
tooltipped "Add the current morphology preview to the current mask" /
"Subtract the current morphology preview from the current mask." That
settles an ambiguity flagged internally while designing this: morphology
is **not** a direct mask replacement (`mask = erode(mask)`) - like the
threshold/contrast tools, it produces a *candidate* (the current mask run
through erode/dilate/open/close) that the user then merges in additively
or subtractively, via the exact same OR/AND-NOT merge every other tool
uses. This is why `apply_candidate(candidate, *, subtract=False)` is one
generic command shared by all four old-app tools, not a per-tool method -
they only differ in how the candidate gets computed. `apply_morphology`
is the one convenience wrapper (candidate = `raster_tools.apply_
morphology_to_mask(current_mask, operation, radius_px)`, then
`apply_candidate`), since morphology is the only one of the four that
needs no external image - histogram/relative/local-contrast all need a
raw image this module doesn't own (`Dataset`'s job), so their candidates
have to be computed by the caller (a future panel) via `raster_tools`
directly, using this module's own `settings()`.

**`paint_brush(center_xy, radius_px, value=...)`** implements only the
old app's on-reference/chromatic-correction-disabled branch of
`apply_mask_brush` (direct write via `raster_tools.apply_brush_stamp`).
The off-reference/CC-enabled branch (accumulate into a sparse per-
wavelength diff instead of touching the canonical mask) is deliberately
not built - it needs this module to know cross-module facts (is CC on,
is the displayed wavelength the reference) it doesn't own and hasn't been
designed to receive, most likely as caller-supplied parameters, not
decided yet. Requires an existing `file_mask` and raises rather than
defaulting one into existence, since (unlike the old app's `manual_mask_
required(create_if_missing=True)`) this module has no image-shape
knowledge to create a blank one from - the caller creates one first.

**None of the three wired through `undo_manager`** - consistent with
every other Mask command and the confirmed-by-grep finding that no mask
action was ever undo-tracked in the old app.

**Verified with real calls** (scripted, no pytest harness yet):
`apply_candidate` against no existing mask (treated as all-unmasked,
matching the old app's `_finish_apply_mask_delta`) and a shape-mismatch
`ValueError`; `apply_morphology` correctly growing a mask via dilate+add
and shrinking it via erode+subtract, and a no-op when no mask exists yet;
`paint_brush` raising without an existing mask, then painting/erasing
correctly once one exists; a direct cross-check that `apply_morphology`'s
result matches calling `raster_tools.apply_morphology_to_mask` +
`merge_mask_candidate` by hand; confirmed zero undo-stack growth across
all of the above. Confirmed `pyflakes` clean across the whole
`src/lspr_imaging_app` tree and that the rewrite-preview window still
builds.

**Not done / still open, as of this entry**:
- `RoiToolbox.display_position()` — stub; needs the Chromatic-affine
  decision noted in `toolbox.py`'s module docstring.
- `analysis/tasks.py`, `storage/session.py` — not yet started.
- `ChromaticModule.add_landmark()`/`refit()` — still stubs.
- `MaskModule`'s remaining async/file-I/O-shaped pieces: worker/cache
  machinery for the two genuinely-slow image-based tools (relative/
  local-contrast), mask file load/save, and the per-wavelength-diff
  branch of `paint_brush` (needs the cross-module design decision noted
  above).
- ROI mask-geometry chromatic warp and ROI mask-drawing commands/UI -
  neither started.
- The panel layer isn't built beyond the scaffold stubs.

## 2026-09-21: Time-varying ignore mask - `ChromaticModule` frame-to-frame warp + `MaskModule` timeline storage

Followed a multi-turn design conversation with the maintainer (see this
file's earlier 2026-09-21 mask/ROI-sharing entry for where it started) to
its conclusion via a written, approved plan
(`C:\Users\Admin\.claude\plans\recursive-coalescing-kurzweil.md`) before
touching code - this was flagged mid-conversation as a data-format-
adjacent decision needing explicit sign-off, not something to build
incrementally from chat alone, per `CLAUDE.md`'s hard-rule list.

**The agreed model**: the ignore mask can now vary by cube (cubes are
sequential time points), not just by wavelength within one fixed mask.
Every edit is tagged with the exact `(cube_index, wavelength_nm)` frame it
was authored at (never normalized back to the reference frame - "it is not
important that everything is coming from one wavelength, it's important
where the change happened") and a scope: `"individual"` (applies to that
one frame only) or `"persistent"` (applies to that whole cube and every
cube after, until superseded - cube granularity only). Persistent changes
are stored as full replacements, not diffs, the maintainer's explicit call
for independent verifiability and to avoid a diff-chain where one lost
link corrupts everything downstream.

**`ChromaticModule.affine_between()`/`warp_mask_between()`** (`image_tools/
chromatic/module.py`) generalize `affine_for()`/`warp_mask()` from
"reference -> any wavelength" to "any frame -> any frame" - needed because
a mask can now be authored at an arbitrary wavelength, not only the
reference. No new math: pivots through the reference using `affine.py`'s
existing `invert_affine_matrix`/`compose_affine_matrices`, the identical
composition the old app's wavelength-interpolation code already uses to
re-anchor a landmark-fitted transform onto a different wavelength. `affine_
between(K, K)` short-circuits to exact identity rather than relying on
`M @ invert(M)` to land there by luck. `affine_for`/`warp_mask` are kept
unchanged as the simpler call for the common reference-authored case.

**`MaskModule`'s storage redesign** (`image_tools/mask/model.py`+
`module.py`): the single `self._file_mask: np.ndarray | None` is replaced
by `MaskChange` records (`frame`, `scope`, `mask`) in two dicts -
`_individual_changes` keyed by exact frame, `_persistent_changes` keyed by
starting cube index. `resolve_mask_source(frame)` is the query primitive:
an exact individual match wins outright; otherwise the latest persistent
change at or before `frame`'s cube applies; `None` if nothing applies yet.
It deliberately returns the mask **as authored**, not warped into the
queried frame's geometry - this module holds no `ChromaticModule`
reference (AGENTS.md boundary rule) and leaves the warp to the caller,
the identical one-directional pattern `roi/rasterize.py` already uses for
`affine_matrix`.

`set_file_mask`/`raw_mask` are replaced by `set_mask_change`/
`resolve_mask_source`. `apply_candidate`/`apply_morphology`/`paint_brush`
now take an explicit `base_mask` parameter instead of reading an implicit
single mask - there's no longer one canonical mask to read, and this
module can't resolve the CC-correct starting canvas for a target frame
without the warp step it doesn't own. This also fixes what would
otherwise have been a real bug: editing a *new* cube needs to start from
whatever's already in effect there (inherited from the last persistent
change), not a blank canvas - only the caller (who *can* call
`ChromaticModule`) can resolve that correctly. `MaskComputationalChange`/
`MaskCosmeticChange` gained `frame`/`scope` fields so a future subscriber
knows what's affected (a persistent change means "cube N onward may be
stale", individual means "just this frame") - free information at emit
time, same reasoning as `RoiToolbox.roi_ids_renumbered`.

The old app's `apply_mask_brush` per-wavelength-diff mechanism (flagged as
not-built in the previous entry, pending a cross-module design decision)
is superseded rather than separately built: its "just this one wavelength"
case is now exactly `scope="individual"`, no separate diff-dict mechanism
needed.

**Why no explicit opt-in toggle was built**: falls out of the design for
free. If only one persistent change ever exists (at the dataset's first
cube), every cube resolves to it identically - same behavior and cost as
today's single mask, no branch anywhere for "is this feature on." Whether
a panel *offers* per-frame editing controls is a UI decision, not
something this module needs to gate.

**Verified with real calls** (scripted, no pytest harness yet), covering
every scenario in the approved plan: `affine_between`/`warp_mask_between`
cross-checked against manually composing two synthetic per-wavelength
similarity transforms by hand, a round-trip (A -> B -> A) returning the
original points to `1e-8`, and `affine_between(K, K)` exact identity; the
full timeline scenario - a baseline persistent change at cube 0, a new
persistent change introduced at cube 3 authored at a *non-reference*
wavelength (540nm, not the dataset's 500nm reference), an individual
override at one exact frame inside cube 4 - confirmed cubes 0-2 resolve to
the baseline, cubes 3+ resolve to the cube-3 change, cube 4's individual
frame resolves to its own override while cube 4's *other* wavelengths and
cube 5 are unaffected, and removing the cube-3 persistent change falls
back to the baseline for cubes 3+ with zero effect on the individual
override; `apply_candidate`/`apply_morphology`/`paint_brush` each
correctly writing a new change reflected immediately by `resolve_mask_
source`; the shape-mismatch `ValueError` guard; zero undo-stack growth
throughout. Confirmed `pyflakes` clean across `src/lspr_imaging_app` and
that the rewrite-preview window still builds.

**Not done / still open, as of this entry** (per the plan's explicit
"deferred" list):
- `RoiToolbox.display_position()` — stub; needs the Chromatic-affine
  decision noted in `toolbox.py`'s module docstring.
- `analysis/tasks.py`, `storage/session.py` — not yet started; `MaskChange`
  HDF5 persistence has no schema yet.
- `ChromaticModule` owning `ChromaticSettings`/`add_landmark`/`refit` -
  separate, already-tracked open item (and its `affine_for` docstring's
  stale "toggle lives in GeometryModule" line is now flagged twice, worth
  fixing whenever that item gets picked up).
- UI: per-frame mask editing controls, "which frames have edits"
  indicators, dataset slider markers - maintainer's own words, "that's
  UI," explicitly out of scope.
- Time-varying treatment for chromatic transforms or background removal -
  raised as later, lower-priority extensions of the same idea, not this
  pass.
- `MaskModule`'s remaining async/file-I/O-shaped pieces (worker/cache
  machinery for relative/local-contrast, mask file load/save).
- ROI mask-geometry chromatic warp and ROI mask-drawing commands/UI -
  neither started (the warp side is now trivial given `warp_mask_between`
  - `roi/rasterize.py`'s mask-geometry branch just needs to call it).
- The panel layer isn't built beyond the scaffold stubs.

## 2026-09-21: ROI mask-geometry chromatic warp - half of it, not all of it

Followed up on the previous entry's "now trivial" claim - turned out to be
correct for half of `roi/rasterize.py`'s four mask-geometry call sites and
wrong for the other half, caught before writing the wrong fix rather than
after.

**`rasterize_sample`/`rasterize_reference` now warp mask geometry**
(`roi/rasterize.py`) - `expand_mask(roi.sample_mask, image_shape)` then
`chromatic.warp.warp_boolean_mask_affine(expanded, affine_matrix,
output_shape=image_shape)`, the exact same `affine_matrix` parameter the
circle/annulus branch already takes. Genuinely the trivial case: both
functions already returned a full-image-sized array before this change
(that's what `expand_mask` always did), so there's no new memory cost.
Verified with a hand-computed translation affine (a mask block shifted by
a known offset lands exactly where expected) and confirmed identity-affine
warp exactly reproduces the old unwarped output.

**`rasterize_sample_for_patch`/`rasterize_reference_for_patch` are
deliberately left unwarped** - this is the half that wasn't actually
trivial. These two exist specifically to avoid materializing a full-image-
sized array per ROI (AGENTS.md's non-negotiable invariant, with a measured
8-14GB RAM cost at realistic ROI counts if violated). Naively warping the
full expanded mask and then cropping to the patch would do exactly that -
defeating the function's whole purpose. A correct fix needs its own small
reach-box calculation (transform the stored `RoiMask`'s bounding-box
corners through `affine_matrix` to bound how far the warped result can
reach in target space, mirroring `annulus_reach_box`'s existing circle-
radius version of the same idea) before warping only within that box -
real, scoped design work, not a drop-in call. Flagged in both the module
docstring and each function's own docstring rather than either skipped
silently or built in a way that risks quietly reintroducing the memory
blowup this function exists to prevent. Verified the `_for_patch` mask
branch is unchanged (still matches plain `expand_mask_to_patch`).

**Not done / still open, as of this entry**:
- `RoiToolbox.display_position()` — stub; needs the Chromatic-affine
  decision noted in `toolbox.py`'s module docstring.
- `analysis/tasks.py`, `storage/session.py` — not yet started.
- `ChromaticModule` owning `ChromaticSettings`/`add_landmark`/`refit` -
  still open.
- `rasterize_sample_for_patch`/`rasterize_reference_for_patch`'s mask-
  geometry reach-box warp - scoped above, not built.
- ROI mask-drawing commands/UI - not started (the warp math they'd need is
  now half-built, per above).
- `MaskModule`'s remaining async/file-I/O-shaped pieces and UI layer -
  still open, see prior entries.
- The panel layer isn't built beyond the scaffold stubs.

## 2026-09-21: `RoiToolbox.display_position()` built; a real circular import caught and fixed

**`display_position()`** - the one remaining ROI-stage stub, settled by
precedent rather than a fresh decision: every cross-module boundary built
this session (`roi/rasterize.py`'s dispatchers, `MaskModule.resolve_mask_
source()`) takes an already-resolved matrix/result as a plain parameter
instead of holding a reference to the module that produced it, so this
does too - takes `affine_matrix` (the caller's job to get from
`ChromaticModule.affine_for(image_key)`), never reaches into Chromatic.
A manual per-wavelength nudge (`AreaRoi.per_wavelength`) wins outright
when one exists for the queried `image_key`, already being expressed in
that wavelength's own display space.

**Real bug caught while wiring this up, not just ported**: importing
`apply_affine_to_points` from `image_tools.chromatic.affine` directly in
`roi/toolbox.py` created an actual circular import -
`roi/__init__.py` imports `toolbox.py`, which now imports
`chromatic/affine.py`, which itself imports `roi.model.AreaRoi` (for
`transform_rois_affine`'s type hint) - and since Python has to import the
`roi` *package* (running `__init__.py`, which is mid-way through importing
`toolbox.py`) before it can reach `roi.model` as a submodule, this failed
with `ImportError: cannot import name 'apply_affine_to_points' from
partially initialized module`. Fixed at the root rather than routed
around: `chromatic/affine.py`'s `AreaRoi` import moved under `TYPE_CHECKING`
- safe because `from __future__ import annotations` (already present)
means the annotation is never evaluated at runtime, and nothing in that
file uses `AreaRoi` as an actual runtime value, only as
`transform_rois_affine`'s parameter type. Confirmed this was latent
(the import existed before today, just never triggered from this specific
direction until `toolbox.py` started importing `chromatic.affine` too).

**Verified with real calls** (scripted): identity affine leaves a
position unchanged; a translation affine shifts it by the expected
offset; a per-wavelength nudge overrides the affine-computed position
entirely for its exact `image_key` while a different `image_key` on the
same ROI still uses the affine. Re-ran every verification script from
this session's earlier chromatic/mask/ROI entries after the circular-
import fix to confirm nothing else broke. Confirmed `pyflakes` clean
across `src/lspr_imaging_app` and that the rewrite-preview window builds.

**Not done / still open, as of this entry**:
- `analysis/tasks.py`, `storage/session.py` — not yet started.
- `ChromaticModule` owning `ChromaticSettings`/`add_landmark`/`refit` -
  still open.
- `rasterize_sample_for_patch`/`rasterize_reference_for_patch`'s mask-
  geometry reach-box warp - scoped in the previous entry, not built.
- ROI mask-drawing commands/UI - not started.
- `MaskModule`'s remaining async/file-I/O-shaped pieces and UI layer -
  still open.
- The panel layer isn't built beyond the scaffold stubs. Every ROI-stage
  stub named in the sketch is now built.

## 2026-09-21: `ChromaticModule` settings ownership + `set_grid_bounds`/`clear_grid_bounds`

Closes the "doesn't hold a `ChromaticSettings` instance" gap flagged twice
in earlier entries. `__init__` now sets `self._settings = ChromaticSettings()`;
`settings()` returns a defensive copy (deep-ish, like `GeometryModule`'s -
`chromatic_grid_bounds` is a nested dataclass needing its own copy, same
reason `GeometrySettings.crop` does).

**Scoped to what's genuinely independent of the landmark workflow, after
checking rather than assuming**: `set_grid_bounds()`/`clear_grid_bounds()`
(the reference-point search-area rectangle) are undo-tracked - confirmed
by reading `gui/chromatic_controller.py`'s `grid_roi_changed`/`reset_
grid_bounds`, which do push undo points there (`"Chromatic search area"`/
`"Reset chromatic search area"`), unlike every Mask command. Worth calling
out: this session's rule has been "check each module's own undo-tracking
by reading its real code, never assume from another module's precedent" -
this is the first case this session where that check came back "yes,
actually undo-tracked," after several modules in a row where it came back
no.

**Every other `ChromaticSettings` field deliberately not exposed by a
command yet**: read the old app's actual writers for `chromatic_
correction_enabled`/`chromatic_sample_image_count`/`chromatic_feature_
count`/`reference_mode`/`reference_wavelength_nm`/`reference_spectral_
cube_index` and found they're all set as part of bigger workflow actions
(`update_settings`, `start_workflow` - which also clears landmarks/models
and computes which wavelengths to sample) rather than a standalone
settings-apply form the way Background's fields are. Building a generic
setter for them now would mean guessing at a shape that `add_landmark`/
`refit`/a `start_workflow` equivalent should actually define once built -
left for that pass instead. `chromatic_registration_mode`/`chromatic_
tile_size_px`/`chromatic_search_radius_px` are vestigial (only the removed
dense tile-matching mode ever read them) - kept on the dataclass, exposed
by no command, matching this app's existing "carry dead fields, don't
invent meaning" discipline.

**`affine_for()`'s docstring corrected**, not just left stale: it claimed
the CC-enabled toggle "lives in GeometryModule's settings, not built yet"
- wrong even when written (it's always been `ChromaticSettings.chromatic_
correction_enabled`). `affine_for()` still doesn't gate on it, but now
explicitly *because* the old app has two different functions for this
(`affine_for_image_key`, gated; `affine_for_image_key_any`, not - this
module was built matching the ungated one) and picking one for the gated
case needs a real caller to motivate it, not a guess.

**Verified with real calls** (scripted): defensive-copy guarantee on both
the settings object and its nested grid-bounds; `set_grid_bounds` implying
`enabled=True`; no-op-skip; a full undo/redo round trip through set ->
clear -> undo -> undo back to defaults. Re-ran every prior chromatic/ROI
verification script from this session to confirm no regression. Confirmed
`pyflakes` clean and the rewrite-preview window still builds.

**Not done / still open, as of this entry**:
- `analysis/tasks.py`, `storage/session.py` — not yet started.
- `ChromaticModule.add_landmark()`/`refit()` (and by extension a
  `start_workflow`-equivalent command, and the rest of `ChromaticSettings`'
  fields) - still stubs; the math they'll call is fully ready
  (`affine.py`, the wavelength-interpolation pattern already documented in
  `refit()`'s own docstring), settings ownership is now ready too, but the
  workflow logic itself isn't built.
- `rasterize_sample_for_patch`/`rasterize_reference_for_patch`'s mask-
  geometry reach-box warp - scoped, not built.
- `MaskModule`'s remaining async/file-I/O-shaped pieces and UI layer.
- The panel layer isn't built beyond the scaffold stubs.

## 2026-09-21: `ChromaticModule.add_landmark()`/`remove_landmark()`/`clear_landmarks()` built

Turned out to be more tractable than `refit()` on its own - split out and
built separately rather than treating "the landmark workflow" as one
inseparable chunk. Ported from `gui/chromatic_controller.py`'s
`upsert_current_landmark`/`clear_landmark`/`clear_landmarks`.

**Landmarks are now a dict, not the old app's list** - keyed by
`(landmark_id, spectral_cube_index, wavelength_nm)`, matching the exact
uniqueness the old app's own linear-scan upsert already enforced by hand.
`add_landmark()` upserts by that key: placing the same `landmark_id` again
at the same `(cube, wavelength)` moves it in place, never duplicates -
verified this distinction explicitly (a same-key resubmission updates,
a different `landmark_id` *or* different wavelength creates a new entry).

**Every one of the three commands invalidates every fitted model and
disables `chromatic_correction_enabled`** - confirmed by reading the old
app's `finalize_landmark_edit`, called unconditionally from every
landmark-edit path there: a landmark's position changing invalidates the
*whole* fit it fed into (every wavelength's model derives from the same
landmark set via `refit()`'s wavelength-interpolation step), not just one
wavelength's. A small real bug caught and fixed before verification, not
after: an early draft of `add_landmark()` mutated `self._models`/
`chromatic_correction_enabled` directly *before* the no-op check, outside
any `apply()`/`revert()` closure - breaking the "only mutate inside the
closures, so undo/redo actually works" convention every other command in
this codebase follows. Replaced with a read-only `_model_snapshot()`
helper shared by all three commands, fixed before it was ever run.

**New typed payload**: `ChromaticModelChange` (`reason: str`) - one type,
not a cosmetic/computational pair like `Roi`/`Geometry`/`Mask`, since every
landmark edit is whole-app-scope by nature (invalidates every model at
once, no per-item identifier makes sense). `chromatic_model_changed`'s
signal is now typed with it, replacing the old bare `pyqtSignal()`
placeholder.

`refit()` itself is still a stub - genuinely separate, bigger work (the
wavelength-interpolation extraction its own docstring already describes),
not bundled in just because landmark editing is now real.

**Verified with real calls** (scripted): upsert-vs-duplicate distinction
confirmed for same-key/different-landmark_id/different-wavelength cases;
model + correction-enabled invalidation confirmed on every one of the
three commands; `remove_landmark`'s no-op-when-absent; a full undo/redo
round trip through 7 pushed commands returning to the exact same empty
landmark state on both ends. Re-ran every prior chromatic/ROI verification
script from this session - no regressions. Confirmed `pyflakes` clean and
the rewrite-preview window still builds.

**Not done / still open, as of this entry**:
- `analysis/tasks.py`, `storage/session.py` — not yet started.
- `ChromaticModule.refit()` (and the wavelength-interpolation extraction,
  and a `start_workflow`-equivalent bundling the settings fields flagged
  in the previous entry) - still the one real stub left in Chromatic.
- `rasterize_sample_for_patch`/`rasterize_reference_for_patch`'s mask-
  geometry reach-box warp - scoped, not built.
- `MaskModule`'s remaining async/file-I/O-shaped pieces and UI layer.
- The panel layer isn't built beyond the scaffold stubs.
