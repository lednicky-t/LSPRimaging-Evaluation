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

## 2026-09-21: `ChromaticModule.refit()` built - `wavelength_interpolation.py` extracted

Turned out more tractable than expected once `add_landmark`/`remove_
landmark`/`clear_landmarks` were already done - the remaining piece was
"extract the math, wire it to this module's own settings," not a new
design. Ported from the old app's `gui/analysis_tasks.py`
(`_estimate_chromatic_models_task`'s `landmark_radial` branch, its only
live branch, plus `_sampled_wavelengths`/`_normalized_odd_count`) into a
new pure-math file, `chromatic/wavelength_interpolation.py` - no Qt/
worker/dataset dependency, matching every other pure-math file in this
package.

**The algorithm, faithfully ported**: fit a transform only at a handful of
evenly-spaced *sampled* wavelengths that have every expected landmark
marked (never every wavelength - the whole point of sampling), anchored on
whichever sampled wavelength is closest to the true reference (the
reference itself need not be landmark-marked). Every other wavelength's
transform is obtained by linearly interpolating the fitted matrices'
coefficients across the sample axis. Every result - sampled or
interpolated - is then re-expressed relative to the *true* reference by
composing with a reference<->anchor transform, the same "translate a
measurement between two arbitrary basepoints" trick already used
elsewhere in this codebase (`affine.compose_affine_matrices`).

**`ChromaticModule.refit(image_keys, reference_key)`** wires this to the
module's own state: groups `self._landmarks` by wavelength (reference
cube only, matching the old app's own filter), reads `chromatic_sample_
image_count`/`chromatic_feature_count`/`chromatic_landmark_model` from
`self._settings`, and matches the old app's exact cube-broadcasting
behavior - one fit per unique *wavelength*, applied identically to every
cube in `image_keys` at that wavelength (chromatic models don't vary by
cube today; per the earlier mask/ROI design conversation, that's a later,
lower-priority extension). Does **not** enable `chromatic_correction_
enabled` - confirmed by reading the old app's `_on_models_ready`, which
explicitly turns the toggle off after every (re)fit. Undo-tracked
(`"Chromatic correction"`, the old app's own label, pushed before its
worker dispatch there).

**`sample_wavelengths_for_cube()`** added as a companion query method -
mirrors `refit()`'s own internal sampling exactly, so a future caller
knows which wavelengths to prompt the user to mark landmarks on *before*
attempting a fit, rather than discovering it only from a raised
`ValueError`.

**Verified with real calls** (scripted, no pytest harness yet) - not just
"doesn't crash", checked against a known ground truth: built a synthetic
scenario with a deliberately wavelength-varying similarity transform,
placed landmark points consistent with that transform at every sampled
wavelength, ran `refit()`, and confirmed (a) the reference wavelength's
own resolved model is identity to `1e-6`, (b) every sampled wavelength's
fitted model matches the true relative-to-reference transform to `1e-6`,
(c) both error paths raise with the old app's exact messages (no
landmarks at all; an incomplete sample wavelength) and push no undo entry;
`chromatic_correction_enabled` confirmed to stay `False` after a
successful refit; a full undo confirmed every model reverts to identity/
unfitted. Re-ran every prior chromatic/ROI verification script from this
session - no regressions. Confirmed `pyflakes` clean and the
rewrite-preview window still builds.

**Every Chromatic scaffold stub named in the original sketch is now
built.** What's left there is UI-adjacent orchestration (a `start_
workflow`-equivalent bundling the remaining `ChromaticSettings` fields,
flagged in the settings-ownership entry), not a missing command.

**Not done / still open, as of this entry**:
- `analysis/tasks.py`, `storage/session.py` — not yet started.
- A `start_workflow`-equivalent command bundling `chromatic_correction_
  enabled`/`chromatic_sample_image_count`/`chromatic_feature_count`/
  `reference_mode`/`reference_wavelength_nm`/`reference_spectral_cube_
  index` - scoped in the settings-ownership entry, not built.
- `rasterize_sample_for_patch`/`rasterize_reference_for_patch`'s mask-
  geometry reach-box warp - scoped, not built.
- `MaskModule`'s remaining async/file-I/O-shaped pieces and UI layer.
- The panel layer isn't built beyond the scaffold stubs.

## 2026-09-21: `rasterize_sample_for_patch`/`rasterize_reference_for_patch`'s mask-geometry reach-box warp built - a real scipy boundary bug found and fixed before it shipped

Closes the last piece flagged from the earlier "ROI mask-geometry chromatic
warp" entry. Built `_mask_reach_box` (the arbitrary-mask analogue of
`annulus_reach_box`, transforming the stored `RoiMask`'s bounding-box
corners through `affine_matrix` instead of a circle's radius) and
`_warp_roi_mask_into_box`/`expand_mask_to_patch_warped`, which warp only
within that bound, reading directly from `roi_mask.mask`'s own small array
- the full source/target canvases are never materialized, preserving the
reason these `_for_patch` functions exist (AGENTS.md's non-negotiable
invariant).

**A real bug caught by testing against the already-verified ground truth,
not assumed correct from the math alone**: verification against
`rasterize_sample`'s full-image path (patch == whole image should give
identical output) failed for rotated/sheared affines - 20 pixels silently
missing, always near the mask's own edge. Traced to a genuine
`scipy.ndimage.affine_transform` behavior that didn't match the "rounds to
nearest, so anything within 0.5px of the boundary is in-bounds" mental
model this function's design assumed: verified directly with a minimal
repro (a 2x2 array, `order=0`, `mode="constant"`) that an offset of just
`-0.1` already returns the constant-fill value, not index 0 - `order=0`'s
boundary handling is stricter than symmetric rounding. This only showed up
because the fix samples directly into `roi_mask.mask`'s own tiny array,
where real content can legitimately sit right at index `(0, 0)`; the
already-correct full-canvas path (`expand_mask` + `warp_boolean_mask_
affine`) never hit this, since the same physical location is always deep
in a large array's interior there, nowhere near its own edge. Fixed by
padding `roi_mask.mask` with a small margin (4px) of `False` before
warping, keeping every real sample comfortably away from the array
boundary - confirmed this resolves every previously-failing case across
four different affine matrices (identity, translation, rotation+scale,
shear-like) and four different patch windows (full-image, a sub-window, a
corner that misses the mask entirely, and a far corner that should stay
empty).

**Verified with real calls** (scripted): the fix above; direct pixel-for-
pixel equality between the patch-scoped result and the already-verified
full-image result, cropped to the same window, across all sixteen
(matrix × patch-window) combinations; the reference-side dispatcher
confirmed to use the identical warp. Confirmed `pyflakes` clean and the
rewrite-preview window still builds.

**Not done / still open, as of this entry**:
- `analysis/tasks.py`, `storage/session.py` — not yet started.
- A `start_workflow`-equivalent command bundling the remaining
  `ChromaticSettings` fields - scoped, not built.
- `MaskModule`'s remaining async/file-I/O-shaped pieces and UI layer.
- ROI mask-drawing commands/UI - not started (the warp math is now fully
  built on both the full-image and patch-scoped paths; only the
  authoring/drawing side is missing).
- The panel layer isn't built beyond the scaffold stubs.

## 2026-09-21: `MaskModule`'s pure file I/O built; scoped its async remainder to the panel layer

Picked up "MaskModule's remaining async/file-I/O-shaped pieces" from the
previous entry - splitting it into two genuinely different kinds of work
before touching code, rather than assuming the whole thing is this
module's job.

**Real architecture finding, checked against precedent before building
anything**: `RoiToolbox.detect_rois()` already established (see its own
docstring) that a potentially-slow, dataset-touching computation is run
by the *caller*, off the GUI thread, with the module only ever accepting
an already-computed result - the module itself never dispatches async
work. Applying that same precedent to Mask's `request_mask_candidate`
(background dispatch + an LRU cache for the "relative"/"local_contrast"
tools, which reload the raw image and run scipy filtering) means it isn't
MaskModule work at all - it's panel-layer orchestration, deferred until
panels get built, exactly like ROI detection's own dispatch. Presented
this split to the maintainer before building either half; chosen scope:
pure file I/O now, defer the worker/cache question entirely.

**`image_tools/mask/io.py` added** - `read_mask_image`/`write_mask_image`,
a verbatim port of the read/threshold and write/encode logic from
`gui/mask_controller.py`'s `read_mask_image`/the PNG-writing half of
`save_mask_to_file` (`develop`/`main`), minus the `QFileDialog` picker
that chooses the path - that stays panel work, matching the file's own
docstring. `>= 128` read threshold and `0/255` write encoding kept
consistent with each other explicitly (documented in both docstrings,
since getting them out of sync would silently corrupt a round trip).
Exported from `image_tools/mask/__init__.py` alongside the existing
`MaskSettings`/`MaskModule` exports.

**Verified with real calls** (scripted): a round trip (write a small
boolean array, read it back, confirm exact equality and `dtype=bool`)
through a not-yet-existing nested directory (confirms the parent-`mkdir`
behavior); the shape-mismatch `ValueError` path, checked against both the
old and new dimensions appearing in the message text. Confirmed
`pyflakes` clean and the rewrite-preview window still builds.

## 2026-09-21: `ChromaticModule.start_workflow()` built - the last flagged Chromatic gap closed

Picked up the "flagged loose end" named in several previous entries: a
`start_workflow`-equivalent bundling `chromatic_correction_enabled`/
`chromatic_sample_image_count`/`chromatic_feature_count`/`reference_mode`/
`reference_wavelength_nm`/`reference_spectral_cube_index`, deferred
because building a setter for these needed `add_landmark`/`refit` to
exist first to know the right shape - both are now built (see the two
entries above this one from earlier today).

Ported from `gui/chromatic_controller.py`'s `start_workflow`: sets
`chromatic_registration_mode="landmark_radial"`, `reference_mode=
"manual"`, the four caller-given values, and forces `chromatic_
correction_enabled=False`, then wipes every landmark and fitted model -
starting a fresh workflow invalidates whatever was fit under a possibly
different reference/sample count, the same reasoning `add_landmark`/
`refit` already apply per-edit, just at workflow-reset granularity.

**Scoped narrower than the old app's method, on purpose, following this
session's now-consistent worker-dispatch precedent** (see the MaskModule
entry above): the old `start_workflow` immediately called `auto_detect_
landmarks()`, dispatching an async background computation. That's panel
work, not this module's - the panel should call `start_workflow()`, then
separately run detection off-thread and call `add_landmark()` per result,
mirroring `RoiToolbox.detect_rois()`'s own caller contract. Also doesn't
touch current cube/wavelength selection (`SelectionModule`'s job) or any
UI widget - every value comes in pre-resolved. `sample_image_count` is
stored as given, not pre-normalized to an odd count here - `sampled_
wavelengths()`/`refit()` already do that normalization downstream against
whatever candidate wavelength list is current at call time, so redoing it
in `start_workflow()` would just be a second, possibly-stale copy of the
same logic.

**No-op rule matches this module's existing commands**: skipped only when
every given value already matches current settings *and* there's nothing
to wipe (no landmarks, no models) - unlike a plain setter, "start a
workflow" is a real action whenever it actually clears something, even if
the target settings happen to already match. Undo-tracked as one combined
entry ("Chromatic workflow", the old app's own label) - settings change
and landmark/model wipe happen together as a single user-visible action.

**Every Chromatic scaffold stub named in the sketch, plus every gap this
session's own build-log entries flagged along the way, is now built.**
What's left in Chromatic is UI/orchestration - a panel to call this
method and drive `auto_detect_landmarks`/`add_landmark` off-thread - not
a missing module command.

**Verified with real calls** (scripted): baseline defaults confirmed
before any call; every one of the four given values lands correctly in
`settings()`, with `chromatic_registration_mode`/`reference_mode`/
`chromatic_correction_enabled` forced as documented; a call with
unchanged settings but an existing landmark still wipes it and pushes a
new undo entry (confirmed via undo-stack depth); a true no-op (identical
settings, nothing to clear) pushes no entry; a full undo/redo round trip
back to and from factory-default settings. Confirmed `pyflakes` clean and
the rewrite-preview window still builds.

**Not done / still open, as of this entry** (checked with a fresh
`grep -rn "raise NotImplementedError"` across every built package before
writing this, rather than assuming from memory - caught the correction
below):
- `analysis/tasks.py`, `storage/session.py` — not yet started.
- `MaskModule`'s worker/cache dispatch for the two slow candidate tools,
  and its `QFileDialog` file-picker wiring - deferred to the panel layer
  (see this entry's own finding above); the pure file I/O half is done.
- ROI mask-drawing commands/UI - not started.
- **`DatasetModule` itself (`dataset/module.py`) is still the original
  scaffold stub, all six methods raising `NotImplementedError`** - only
  its IO/model layer (`dataset/io.py`, `dataset/model.py`) was ever
  ported (2026-09-20). Every other module's command surface (ROI,
  Chromatic, Geometry, Background, Mask, Selection) is real; Dataset's
  own `QObject` state-owner is the one exception, not yet caught by any
  prior entry's "what's left" list - flagging it now so it isn't
  silently assumed done.
- §6a fractional pixel weighting (`roi/reduction.py`'s `weighted_*`
  functions, `roi/rasterize.py`'s `rasterize_fractional`) and the
  background estimate/apply split (`image_tools/background/apply.py`) -
  both pre-existing, already-documented deferrals (AGENTS.md, the
  2026-09-20 preprocess.py scope-check entry), not new findings, called
  out here only so this entry's grep-based check is complete.
- The panel layer isn't built beyond the scaffold stubs.

## 2026-09-21: `DatasetModule` built - the Dataset stage's own gap, closed

Picked up the gap flagged in the previous entry: `dataset/module.py`'s
`QObject` state-owner was still the original scaffold stub (all six
methods raising `NotImplementedError`), unlike `dataset/io.py`/`model.py`
(the pure IO/dataclass layer it wraps), ported back on 2026-09-20.

**Confirmed the sketch's narrow four-method query surface
(`current_image`/`wavelengths`/`spectral_cubes`/`acquisition_metadata`)
is actually sufficient, rather than assuming it from the sketch's prose**:
checked every `dataset/io.py` function a future caller would need pixel
data from. `dataset_load_plane_roi` already accepts an optional
pre-looked-up `record` parameter specifically so a caller doesn't need
the full `ImageDataset` to avoid an O(N) scan; `dataset_load_plane`/
`dataset_plane_shape` need nothing from `ImageDataset` beyond the one
`ImageRecord` a lookup already resolves to. So `current_image()` handing
back an `ImageRecord` is enough for a caller to reach every one of those
functions without this module ever exposing the raw dataset object -
matching its own "no other module may read dataset state any other way"
rule instead of quietly working around it for convenience.

**`current_image()` raises two different errors on purpose**: `RuntimeError`
if no dataset is loaded at all (a caller asking before any load happened
is a caller bug), `KeyError` - same message shape as `dataset_load_plane`'s
own miss - if a dataset is loaded but has no record at that exact key. The
two other query methods (`wavelengths()`/`spectral_cubes()`) return an
empty tuple rather than raising when nothing is loaded, since unlike
`current_image` there's no specific key being asked for that could be
"missing."

**`clear_dataset()` built deliberately minimal, not a full port of the old
app's method of the same name**: `gui/dataset_controller.py`'s
`clear_dataset` resets a dozen *other* pieces of window state in the same
method (record maps, mask state, sensorgram caches, image caches, UI
widgets) - exactly the "one method touches everything" entanglement
pattern this whole rewrite exists to undo (see the feature inventory).
This module's version clears only its own `_dataset` reference and emits
`dataset_cleared`; every other module that holds dataset-derived state is
expected to subscribe and reset itself. No subscriber exists yet (no
panel layer), so this contract is unverified end-to-end - flagged for
whoever wires the first subscriber, not assumed correct just because it
matches the intended design on paper.

**No no-op check on `load_dataset()`** (unlike `clear_dataset()`, which
skips emitting when already empty, matching this codebase's usual
convention): a reload is a deliberate user action even when the freshly
re-scanned dataset happens to be structurally identical to what was
already loaded, so it always replaces and always emits.

**Verified with real calls** (scripted): every query method's before-load
empty/`None`/`RuntimeError` behavior; `clear_dataset()` on an
already-empty module emits nothing (signal-spy checked); a real load with
three records across two cubes/two wavelengths resolves `wavelengths()`/
`spectral_cubes()`/`current_image()` correctly, including the `KeyError`
path for a wavelength that isn't in the dataset; `clear_dataset()` after a
real load does emit and does reset every query method back to its
before-load state. Confirmed `pyflakes` clean and the rewrite-preview
window still builds.

**Not done / still open, as of this entry**:
- `analysis/tasks.py`, `storage/session.py` — not yet started.
- `MaskModule`'s worker/cache dispatch for the two slow candidate tools,
  and its `QFileDialog` file-picker wiring - deferred to the panel layer.
- ROI mask-drawing commands/UI - not started.
- §6a fractional pixel weighting and the background estimate/apply split -
  both pre-existing, already-documented deferrals, unchanged by this entry.
- Every module's own command/query surface named in the sketch is now
  built (Dataset, ROI, Chromatic, Geometry, Background, Mask, Selection).
  What remains across the board is the panel layer and the two
  not-yet-started subsystems above - not a missing module-layer piece.

## 2026-09-21: `roi/reduction.py` moved to `analysis/reduction.py`; background estimate/apply split built

Two small corrections, both maintainer decisions, not new findings from
code:

**§6a rescoped from ROI to Analysis.** Discussing what was left, maintainer
clarified §6a fractional pixel weighting belongs to the analysis stage, not
ROI - "ROI should [not] do any calculations." Asked a follow-up since
`roi/reduction.py` already held *real, already-ported* (unweighted)
reduction math (`reduce_mean`/`reduce_median`/`reduce_trimmed_mean`/
`reduce_plane_fit_reference`/`reduce_sample_and_reference[_all_methods]`),
not just the not-yet-built `weighted_*` stubs the original §6a conversation
was about: should that existing code move too, or just future weighted
work? Maintainer's answer: **move all of it** - any "masked pixels →
scalar" computation is an analysis concern, regardless of whether it's
weighted.

`roi/reduction.py` → `analysis/reduction.py` via `git mv` (history
preserved). Checked for callers first: nothing on `rewrite` imported it yet
(`roi/toolbox.py` never referenced it) - a zero-fixup file move, not a
rewire. `roi/rasterize.py` (including its still-stubbed `rasterize_fractional`
for §6a) stays in `roi/` - it turns a shape into a raster/weight mask and
never reads pixel values, so it's geometry, not a "calculation" by the same
principle; the maintainer's question and decision were specifically about
`reduction.py`, and this boundary was inferred from their stated
reasoning, not separately confirmed - flag if that's wrong. Updated
`AGENTS.md` (module boundaries, the §6a section, the testing-rules file
list) and `docs/rewrite_architecture_sketch_2026-09.md` (§10 file tree,
the "what's new work" paragraph) to point at the new location; updated
`roi/model.py`'s two comments referencing the old path. Verified
pyflakes-clean and the rewrite-preview window still builds.

**Background estimate/apply split built.** `image_tools/background/
apply.py`'s `apply_background(image, background, baseline)` is now real -
the cheap subtract-recenter-clip formula that used to be inlined at the end
of `estimate.py`'s `flatten_background`. `flatten_background` now composes
`estimate_background_profile()`/`_background_baseline()` (unchanged) with
`apply_background()` instead of inlining that arithmetic itself, across all
three of its paths (plain, region-scoped, binned+region).

**Deliberately not a cached "model" object**: `BackgroundModule`'s own
2026-09-21-earlier docstring already established there's no persisted
background model anywhere in the app to cache - `flatten_background` runs
live, inline, per rendered frame, straight from `BackgroundSettings`, unlike
Chromatic's genuinely-stored per-(cube, wavelength) affine models. Building
a `BackgroundModel` dataclass for `apply_background` to consume would be
machinery for a caller that doesn't exist - `apply_background` instead just
takes whatever background array/baseline the caller already computed and
applies it, matching what `apply.py`'s docstring called for ("cheap
formula/lookup") without inventing unneeded state.

Verified with a standalone script (not committed) comparing the refactored
`flatten_background` against an independent reimplementation of the
original inline arithmetic, across 6 cases (plain; with ROI exclusion;
region-scoped; region-scoped with ROI; binned+region; binned+region with
ROI and exclusion dilation) - `np.array_equal` (bit-for-bit, not just
`allclose`) true in every case. Updated `estimate.py`/`apply.py`/
`BackgroundModule`'s docstrings to match (the `BackgroundModule` one keeps
its original historical narrative about the bug that was fixed earlier
that day, with a note appended rather than rewritten, since the split
hadn't happened yet at the point that narrative describes). Confirmed
pyflakes-clean and the rewrite-preview window still builds.

**Not done / still open, as of this entry**:
- `analysis/tasks.py`, `storage/session.py` — not yet started.
- `MaskModule`'s worker/cache dispatch for the two slow candidate tools,
  and its `QFileDialog` file-picker wiring - deferred to the panel layer.
- ROI mask-drawing commands/UI - not started.
- §6a fractional pixel weighting itself is still unimplemented (only its
  target location moved) - `analysis/reduction.py`'s `weighted_*` stubs
  and `roi/rasterize.py`'s `rasterize_fractional` stub are both still
  `NotImplementedError`, deferred until the analysis stage is actually
  built.
- Nothing from this entry has been committed yet.

## 2026-09-22: `roi/rasterize.py`'s `rasterize_fractional` built (§6a's raster half)

Maintainer wanted to focus specifically on §6a next and get a working,
tested implementation, not just discuss it further. Scoped to the raster
half only (`roi/rasterize.py`) - `analysis/reduction.py`'s `weighted_*`
stubs stay deferred, unchanged by this entry, per the maintainer's own
framing ("this would be mostly issue of CC" - the raster/coverage problem,
not the reduction-math problem).

**Real correctness constraint that shaped the design**: checked
`fit_affine_matrix` (`processing/chromatic.py:927`, what
`estimate_affine_chromatic_transform` - the real production CC fit -
actually calls) before designing anything. It's an unconstrained
6-parameter affine (independent x/y scale *and* shear allowed, fit by
OLS through matched landmarks), not a similarity transform - so a circle
in native space can genuinely warp into an ellipse in target space. That
ruled out the simpler-looking "draw a same-radius circle at the
transformed center" approach (wrong under any shear/anisotropic scale) in
favor of extending the existing, already-correct-for-any-affine pattern
`_annulus_mask_in_box` already uses: map target-space points *backward*
through the inverse affine to native space and test them there.

**One shared engine, not per-geometry-type code** (matching AGENTS.md's
"don't reach for shape-specific exact-intersection formulas" and the
one-dispatcher-not-per-shape-code direction): `_reach_box_coverage`
generalizes `_annulus_mask_in_box`'s single-point-per-pixel test to
`supersample_factor ** 2` evenly-spaced sub-pixel samples per pixel,
averaged into a `[0, 1]` coverage fraction - reach-box-bounded exactly like
every other function in this file, so the returned array is
`image_shape`-sized but the actual computation never touches more than the
shape's own bounding box. The only thing that differs per geometry type is
a `point_test(source_x, source_y) -> bool array` callable plugged into it:
`_circle_annulus_point_test` (a distance-from-center formula) or
`_mask_point_test` (nearest-neighbor lookup into the stored `RoiMask`
array - a mask has no continuous boundary beyond its own pixels, unlike
circle/annulus, so "coverage" there reflects how many back-projected
sub-samples land on a `True` source pixel, not sub-pixel precision the
mask never had). Both `rasterize_sample`/`rasterize_reference`'s existing
dispatch-by-geometry-type logic and `_effective_reference_radii`/
`annulus_reach_box`/`_mask_reach_box` are reused as-is, not reimplemented.

**Not cached** - `rasterize_fractional` recomputes on every call. Flagged
in `AGENTS.md` as a real candidate for the same per-key caching
`ChromaticModule` already does (depends only on ROI geometry + the affine,
both fixed per cube/wavelength) but deliberately not built now: no caller
exists yet (`analysis/tasks.py` isn't built), and building a cache with
nothing to validate it against would be speculative. A patch-scoped
(`_for_patch`) variant, mirroring `rasterize_sample_for_patch`, was
likewise not built for the same reason - flagged, not silently skipped.

**Verified with a standalone script** (not committed - no established
location for rewrite-branch unit tests exists yet; the umbrella's
`tests/unit/test_lspri_*.py` files test the stable `develop`-branch app by
import path and would break for anyone whose submodule isn't on
`rewrite` if a rewrite-only-module test were added there. Flagging this as
an open question for whoever builds real committed test coverage for this
branch, not deciding it here). 17 checks, all passing:
- Interior pixels match `transformed_disk_mask`'s existing boolean result
  exactly (coverage `1.0`); exterior pixels match `0.0`; the boundary band
  contains genuinely fractional values (feature isn't a no-op).
- **Area conservation**: `sum(coverage)` in target space matches
  `source_area * |det(affine linear part)|` to `<0.01%` relative error,
  across identity, sub-pixel-translation, and shear+anisotropic-scale
  affines - catches the "circle becomes ellipse" case directly, not just
  by inspection. (First run of this check failed at 5.5% error under a
  stronger shear/scale combination - turned out to be the test's own bug,
  not the implementation's: that affine pushed the transformed disk
  partly outside the 200px test canvas, so part of its true area was
  legitimately clipped by `image_shape`'s edge, which the test's expected-
  area formula didn't account for. Fixed by keeping the transformed shape
  inside the canvas rather than by loosening the tolerance - see the
  Performance Work section's rule on this.)
- **Convergence**: max per-pixel error vs. a `supersample_factor=64`
  reference shrinks monotonically at `2 < 4 < 8 < 16`; the default (`8`)
  stays under `0.05` of a full pixel's weight.
- **Independent oracle**: a plain nested-`for`-loop Python reimplementation
  (no shared code with `_reach_box_coverage`) matches the vectorized
  implementation exactly (not `allclose` - bit-for-bit) at 8 sample points
  under a shear+anisotropic-scale affine - guards against a
  vectorization/broadcasting bug the module's own internal consistency
  can't catch.
- **Translation sanity check**: `translate_affine(+10, 0)` shifts the
  coverage pattern exactly +10 columns (not rows) - catches x/y transpose
  bugs, a real recurring bug class in this codebase's image code.
- **Mask geometry**: binary (all `1.0`/`0.0`, no partial values) at an
  identity affine; genuinely fractional edge values under a sub-pixel
  shift. (Second failed run, same session: a *0.5*-pixel shift produced
  zero partial values - not a bug either. Nearest-neighbor rounding
  buckets are exactly one pixel wide, so a translation of *exactly* 0.5
  happens to realign every target pixel's sub-samples onto a single source
  pixel's rounding bucket - a genuine degenerate case of nearest-neighbor
  sampling specifically at that value, not representative of a real
  chromatic shift. Fixed by testing a non-half-integer shift instead.)
- Reference side with `geometry_type == "none"` returns all zeros; output
  dtype/shape check; strictly-zero-outside-the-reach-box check.

Confirmed pyflakes-clean and the rewrite-preview window still builds. Not
committed yet.

**Not done / still open, as of this entry**:
- `analysis/tasks.py`, `storage/session.py` — not yet started.
- `MaskModule`'s worker/cache dispatch for the two slow candidate tools,
  and its `QFileDialog` file-picker wiring - deferred to the panel layer.
- ROI mask-drawing commands/UI - not started.
- `analysis/reduction.py`'s `weighted_*` functions (the reduction-math half
  of §6a) - still `NotImplementedError`, deferred until the analysis stage
  is built, per this entry's own scoping.
- `rasterize_fractional` has no `_for_patch` variant and no caching yet -
  both deliberately deferred until a real caller exists (see above).
- No committed, permanent test coverage for anything on `rewrite` yet -
  every verification so far (this entry included) has been a standalone,
  uncommitted script. Where rewrite-branch tests should permanently live
  is an open question, not yet decided.
- Nothing from this entry has been committed yet.

## 2026-09-22: `analysis/` built - provenance, planner, worker, tasks,
## engine all real; store still in-memory (`data.h5` not designed yet)

Maintainer's direction for this session, explicitly: build `analysis/`
"correctly from the start," don't be limited by the old app's design, and
flag anything deferred rather than let it get lost. What follows is a
large batch - five files went from `NotImplementedError` scaffolds to real,
individually-verified implementations - preceded by an extensive design
conversation (see `docs/analysis_provenance_store_design_2026-09.md`,
built the same session) that settled the provenance file scheme before any
of this was written.

### `provenance.py` - naming, versioning, dedup, fingerprinting

Real implementation of the whole design doc: `FrameNamingScheme` (adaptive
cube-digit-padding and wavelength-decimal-precision, both derived from the
real dataset, not guessed); `next_version` (the shared sequential-
version-with-content-comparison dedup rule, no hashing - the same
mechanism for masks, chromatic models, and settings snapshots);
`persist_mask_snapshot`/`persist_chromatic_snapshot`/
`persist_settings_snapshot` (lazy, file-backed, dedup'd); `ProvenanceRecord`
(real fields) and `compute_fingerprint`. `ProvenanceStore` stays
`NotImplementedError` - genuinely blocked on `data.h5`'s schema, not
unstarted (see "Not done" below) - but a new `InMemoryProvenanceStore`
implements the identical `fingerprint_for` interface as a working,
non-persistent stand-in, which is what everything below actually runs
against today.

**Two real findings, both fixed before writing any provenance code**:
1. `MaskModule` (`image_tools/mask/module.py`) already implements exactly
   the persistent/individual timeline this session's design conversation
   spent a long time re-deriving from scratch - built 2026-09-21, a prior
   session, using `scope: "individual" | "persistent"` as its real,
   shipped terminology. The design doc's filename tag was `persi`/`local`
   at that point - renamed to `persi`/`indiv` to match the real code
   instead of inventing a second word for the same concept.
2. Geometry (`GeometryModule`'s crop/rotate/flip) was missing from the
   sketch's original five-input provenance list - a real gap, not stylistic:
   crop/rotate changes the pixel grid every ROI's coordinates are already
   defined against, so a geometry change silently not triggering recompute
   would be a correctness bug. Widened to six inputs; design doc updated.

Verified with a standalone script (26 checks): naming/padding edge cases,
dedup reusing identical content vs. assigning new versions for different
content, floating-point noise below the rounding threshold still dedups,
8-bit mask and 16-bit background PNG round-trip exactly through `cv2`
(confirming the format choice from the design conversation actually works,
not just "should work"), and - the property `plan_recompute` actually
depends on - recomputing an unchanged live fingerprint produces an
*exactly equal* `ProvenanceRecord`, while a real input change produces a
different one.

### `planner.py` - `plan_recompute`, correctness-first

Real `plan_recompute`/`CurrentInputs`/`RecomputePlan`/`AnalysisScope`.
Scoped deliberately: recomputes every in-scope cell's live fingerprint
fresh and compares to stored - always correct, not yet optimized to skip
fingerprint recomputation for cells a locality rule could already rule out
unaffected. **The one locality rule that's a correctness requirement, not
an optimization - the ROI-adjacency exception (sketch §6: a nearby ROI's
reference-ring exclusion can affect this ROI's own fingerprint too) - is
NOT handled**, flagged explicitly in the module docstring as a named
follow-up needing its own investigation into the old app's exact mechanism,
not guessed at.

**Real tension found and documented, not silently resolved**:
`compute_fingerprint` (needed to get a *comparable* live fingerprint) calls
`persist_settings_snapshot` as a side effect, which can write a new
settings-snapshot JSON even during a pure preview/plan that never actually
computes anything. This is *not* the "don't store every edit" problem the
design doc's lazy-write rule was protecting against - that rule is about
mask/background/chromatic image files, none of which `compute_fingerprint`
ever touches (only `compute_cell` persists those) - so the actual cost is
a handful of small orphaned JSON files at worst, not the thousands-of-images
problem. Flagged as a known simplification in `planner.py`'s module
docstring rather than solved: a fully side-effect-free preview needs
comparing live state against stored file *content* directly, real
follow-up work.

Verified with a standalone script (7 checks) using a hand-written
`FakeStore` (`ProvenanceStore`'s real implementation doesn't exist yet, but
`plan_recompute` only needs its interface) - matching-fingerprint cells
skip, stale/missing ones recompute, `ALL_ROIS`/`SELECTED_ROIS` scope
filtering, and re-running `plan_recompute` with unchanged live state is
idempotent (creates no new settings files on the second call).

### `worker.py` - minimal threading primitive, not a `FunctionWorker` port

Deliberately smaller than the old app's `FunctionWorker` (a much larger
`QRunnable` with its own progress/partial-result Qt plumbing) - this class
is just "run one callable on a fresh `threading.Thread`, expose a stable,
cooperatively-checked cancel flag." Progress/result reporting is the
caller's job: the task closure can safely `.emit()` a Qt signal directly
from this background thread (Qt queues a cross-thread `.emit()`
automatically - the same guarantee `FunctionWorker`'s own docstring already
documents), no extra machinery needed here. Cancellation is checked
**between cells, never mid-cell** - the "beyond sketch" idea from this
session's earlier design conversation - so a cancelled run always leaves a
valid, if partial, result set.

Verified with a standalone script (7 checks: not-running state, submit-
while-running raises, cancellation actually stops a running task early,
`cancel_event` is cleared - not left set - on a fresh `submit()`). One
check was flaky on a bare-`time.sleep`-based test (a real thread-scheduling
race in the *test*, not the implementation - `cancel_event.clear()` happens
synchronously on the main thread before the new thread starts, so there's
no race in the actual code); passed on a second run. Noted for whenever
this becomes a real committed test: synchronize on an event/queue, not a
sleep duration.

### `tasks.py` - `compute_cell`, a genuine rewrite of the per-cell arithmetic, not a port

**"Ports `analysis_tasks.py` largely as-is" did not survive contact with
the real code** (same family as the `dataset/io.py` §10 correction): the
old app's closest equivalent, `_sensorgram_metric_task` (~400 lines), is
deeply entangled with *bulk multi-ROI* concerns this file's job doesn't
own - GC toggling, a `ThreadPoolExecutor` prefetch stage, a `roi_mask_cache`
shared across an entire multi-ROI run, worker-count calibration, and a
cancellation story spread across several paragraphs of comment explaining
edge cases. `compute_cell` is a correct rewrite of the per-cell arithmetic
that function performs internally, deliberately without its batch-level
optimizations - those belong in a different layer (batching/caching around
repeated `compute_cell` calls), to be built once there's a real dataset to
measure against, not guessed at now (AGENTS.md's Performance Work rules).

**Real correction to this file's own stub**: the scaffold's
`compute_cell` returned a bare `float`. Sketch §6 is explicit - "Formula,
Fit method, Metric choice - never touch the stored cells at all". Baking
`formula_value` into what gets stored would mean a Formula change (e.g.
absorbance → ratio) silently requiring a full recompute purely because a
*display*-math choice changed the shape of what's on disk. `compute_cell`
now stops at the raw reduced (sample, reference) pair per wavelength
(`CellResult`); formula math becomes a query-time concern for whichever
future code reads `get_spectrum`/`get_metric` - `formula_value` itself
(`processing/analysis.py`) isn't even imported here.

**Persists mask/chromatic/settings snapshots as a real side effect of
`compute_cell` itself** - deliberately here, not in `compute_fingerprint`
(see `planner.py`'s section above): this is the actual "written lazily,
only when analyzed" moment.

**Two flagged, unverified assumptions**, documented in the module
docstring rather than guessed at confidently:
1. `MaskModule`'s resolved mask is passed to `apply_preprocessing` as
   `external_mask` with `external_mask_processed=False` (assumed authored
   in raw image space) - not confirmed against the real mask-drawing GUI
   code. Wrong would silently misalign the mask, not crash.
2. Uses `roi/rasterize.py`'s binary `rasterize_sample`/`rasterize_reference`,
   not `rasterize_fractional` (§6a) - deliberate: `analysis/reduction.py`'s
   `weighted_*` functions that would consume fractional weights are still
   `NotImplementedError`, so there's nothing yet to plug a fractional mask
   into.

Verified with a standalone script (14 checks) using a known step-function
synthetic image (uniform fill regions, so expected sample/reference means
are exactly computable by hand). **Two of the first-run failures were
themselves informative test bugs, not implementation bugs**: (a) the test's
hand-built annulus mask used strict `>` at the inner radius while
`rasterize_reference` correctly uses `>=` (12 boundary pixels disagreed -
confirmed by direct mask comparison, not assumed); (b) a wrong test
expectation that two wavelengths sharing an *identical-valued* chromatic
affine should share one settings snapshot - they don't, by design, since
each wavelength's chromatic reference is genuinely independent even when
its content happens to coincide. Final passing checks include: correct
values for both wavelengths, reduction-method choice actually changing the
provenance (mean vs. median → different settings-snapshot ids) while
producing identical *values* over a uniform region (a mean-equals-median
degenerate-case sanity check), cancellation before the first wavelength
returns `None` with nothing persisted, and a mask supplied without
raising, with its snapshot file actually written.

### `engine.py` - real orchestration, built against injected callables

**Real, load-bearing gap found while wiring this up**: `DatasetModule`
(`dataset/module.py`) deliberately exposes only 4 narrow query methods -
none of them load actual pixels (`dataset_load_plane` needs the full
`ImageDataset`, which `DatasetModule` never exposes, per its own "no other
module may read dataset state any other way" rule, confirmed in this same
build log's 2026-09-21 `DatasetModule` entry). `DatasetModule` needs a 5th
method (e.g. `load_plane(cube_index, wavelength_nm) -> np.ndarray`) before
this engine can be wired to the real module - not guessed at or worked
around here. Built `AnalysisEngine`'s constructor against injected
callables (`load_plane`, `rois`, `chromatic_affine`, `resolve_mask`, ...)
instead, so this file's own orchestration logic is real, complete, and
testable today with fake callables standing in for the real modules -
wiring it up later is a small change at construction time, not a logic
change.

**Real fix, caught by checking the scaffold's own documented contract**:
the first version of this constructor required every callable as a
mandatory keyword argument, which broke `app_rewrite.py`'s existing
`AnalysisEngine()` no-args call - violating this scaffold's own stated
rule ("every module constructs without error, only action methods raise
`NotImplementedError`", from `app_rewrite.py`'s own module docstring).
Fixed: every callable now defaults to `None`, resolved through a small
`_unwired()` helper that raises only when actually *called* -
`AnalysisEngine()` constructs fine again; `run_analysis()`/
`preview_recompute()` raise until real callables are supplied. Confirmed
the rewrite-preview window still builds after the fix.

`get_metric`/`get_spectrum`/`status_summary` are real, but read from the
in-memory `_results`/`InMemoryProvenanceStore`, not `data.h5` - same
blocker as `ProvenanceStore`. `get_metric`'s own return shape is flagged
as a real gap against the sketch (`-> float | None`): since formula math
isn't stored (see `tasks.py` section above) and no Formula/Fit/Metric
query layer exists yet either, it currently returns the raw
`(*sample_values, *reference_values)` tuple, not a single derived scalar -
documented in its own docstring rather than faked with a wrong number.
`preview_recompute()` (the third "beyond sketch" idea from this session's
earlier design conversation) is real and side-effect-light per the
`planner.py` caveat above.

Verified with two standalone scripts. First (14 checks, unwired-callable
version before the scaffold-compatibility fix was found necessary):
`preview_recompute` before any run correctly shows every cell needing
recompute; `run_analysis` dispatches through the real `AnalysisWorker`,
completes (`analysis_complete` fires), computes distinct values per
distinct (cube, wavelength) input (not a fluke of a single fixed test
image), `load_plane` called exactly once per (cube, wavelength) per cell;
**re-running `run_analysis`/`preview_recompute` with nothing changed
loads zero planes and shows zero cells needing recompute** - the actual
end-to-end dedup property, verified through the whole stack (provenance →
planner → worker → tasks → engine), not just at one layer in isolation;
`SELECTED_ROIS` scope filtering. Second script (2 checks, after the
constructor fix): the real wired-callable path still completes and
computes correctly, confirming the scaffold-compatibility fix didn't
silently break the real path while fixing the scaffold one.

### Not done / still open, as of this entry

- **`data.h5` itself - the single biggest remaining gap.** Blocks
  `ProvenanceStore`'s real implementation and `get_metric`/`get_spectrum`/
  `status_summary`'s real persistence (all currently backed by
  `InMemoryProvenanceStore` + an in-memory dict - working, but lost on
  restart). Also blocks the suite-wide HDF5 identity-field contract
  question (`packages/lspr_io` reuse) flagged earlier this session.
- **`DatasetModule.load_plane`** (or equivalent) - the real gap found
  while building `engine.py`. Needed before `AnalysisEngine` can be
  constructed with real callables instead of test fakes.
- **The ROI-adjacency exception** in `plan_recompute` - a correctness
  requirement, not yet handled, needs its own investigation into the old
  app's exact mechanism (see `planner.py` section above).
- **`preview_recompute`'s settings-snapshot side effect** - a known,
  low-cost simplification (small JSON files only, never mask/background/
  chromatic images), not solved - see `planner.py` section above.
- **Two unverified assumptions in `tasks.py`** (mask coordinate space;
  binary vs. fractional rasterization) - see that section above.
- `analysis/reduction.py`'s `weighted_*` functions (§6a's reduction half)
  - still not built, unrelated to this entry's scope.
- `BackgroundModule`'s image-file provenance treatment - still blocked on
  it needing a `MaskModule`-style timeline, per the design doc; background
  provenance currently persists as a settings dict, not an image, inside
  `SettingsSnapshot`.
- `storage/session.py` - untouched, separate piece.
- No committed, permanent test coverage - every verification in this
  entry (five scripts) was standalone and uncommitted, same open question
  as every prior entry.
- Nothing from this entry has been committed yet.

## 2026-09-22 (same day, continued): `DatasetModule.load_plane` built - closes the gap the previous entry flagged

Added a fifth query method, `load_plane(cube_index, wavelength) -> np.ndarray`,
closing the real gap flagged while building `engine.py` above: the original
four-method surface was confirmed sufficient to *reach* every
`dataset/io.py` loading function, but none of those four actually return
pixel data, and the loading functions all need the full `ImageDataset`
this module deliberately never exposes. `load_plane()` does the loading
internally (reusing `current_image()`'s own record resolution rather than
re-deriving it via `dataset.io.dataset_load_plane`, avoiding a redundant
second lookup) - the "no other module reads dataset state any other way"
rule is unchanged, this is one more narrow read, not a loosening of it.

Verified with a standalone script (4 checks) against a **real TIFF file
written to a temp directory** (via `tifffile.imwrite`), not a mock -
`RuntimeError` before any dataset is loaded, correct shape and exact pixel
values after loading a real dataset pointed at the real file, `KeyError`
for a (cube, wavelength) combination that doesn't exist. Confirmed
pyflakes-clean.

This unblocks constructing `AnalysisEngine` with `load_plane=dataset_
module.load_plane` for real pixel access - **not done in this entry**:
wiring the *rest* of `AnalysisEngine`'s real callables
(`resolve_mask`/`chromatic_affine`/`rois`/etc. into `app_rewrite.py`)
surfaces further real gaps on inspection (e.g. `DatasetModule.wavelengths()`
is dataset-*global*, not per-cube - using it for every cube would silently
mis-handle a dataset where a cube is missing a wavelength another cube
has; `resolve_mask` needs `ChromaticModule.warp_mask_between` when a
mask's authored frame differs from the queried one, not yet checked
against that method's real signature) - flagging these now rather than
guessing through them, since each is its own small investigation, not
assumed-safe scope creep of "wire up the engine."

Not committed yet, alongside this entry.

## 2026-09-22 (same day, continued): `analysis/reduction.py`'s `weighted_*` functions built - §6a's reduction half

Maintainer picked this as the first of four follow-up items ("1-4" from
the prior status update) - self-contained, and its prerequisite ("wait
until the analysis stage exists") was satisfied by this same day's earlier
`analysis/` work.

**Prototyped and numerically verified each formula in a scratch script
before writing the real implementation** - not derived by hand and trusted:
- `weighted_mean` - the standard `sum(values*weights)/sum(weights)`.
- `weighted_median` - a genuine concern, not a formality: a naive "smallest
  value where cumulative weight crosses 50%" formula does *not* reduce to
  `np.median`'s "average the two middle values" convention for even-length
  arrays at equal weights, which would have silently broken AGENTS.md's
  degenerate-case parity rule. Used linear interpolation on the weighted
  cumulative distribution instead (`np.interp` on `(cumsum(weights) - 0.5*
  weights) / total`) - verified against `np.median` over 2000 random
  trials (odd and even n both), max diff ~1e-13 (float noise, not a real
  difference).
- `weighted_trimmed_mean` - trims by **element count** (matching
  `reduce_trimmed_mean`'s exact `int(n*fraction)` slicing), then takes the
  weighted mean of the surviving middle elements - deliberately not a
  weight-based trim, which would only *coincidentally* match the unweighted
  version when weights happen to be equal rather than matching *by
  construction*. Verified exact (0.0 max diff, not just within tolerance)
  over 3000 random trials.
- `weighted_plane_fit` - **signature corrected while implementing**, same
  family as this branch's other guessed-placeholder-shape bugs (`AreaRoiGroup.
  group_id: int`, the `preprocess_image()` stub): the scaffold's
  `(values, weights) -> float` couldn't have actually fit a plane - real
  callers need pixel coordinates and the sample-side evaluation point too.
  Fixed to `(reference_pixels, reference_xx, reference_yy, weights,
  sample_x, sample_y)`, matching `reduce_plane_fit_reference`'s real shape
  plus weights. Implemented via the standard sqrt(weight)-scaling trick
  (scale every design-matrix row and target by `sqrt(weight)`, same
  `np.linalg.lstsq` call the unweighted version already uses) rather than a
  different algorithm - verified exact (0.0 max diff) over 1000 random
  trials at equal weights, correct fallback behavior for the degenerate
  <4-point case, and a down-weighted-outlier sanity check pulling a fitted
  value much closer to the true underlying plane than the unweighted fit.

Every function falls back to (weighted, not plain) `reduce_mean`-equivalent
behavior for its own degenerate case (zero total weight, insufficient
points for a plane fit, non-finite result) rather than raising - matching
this file's existing fallback conventions.

Verified with a standalone script (12 checks, all passing): the four
equal-weights-parity checks above, plus a "genuinely down-weights an
outlier" sanity check for each function (confirms the weighting isn't a
no-op pass-through), the even-n `np.median` convention check specifically,
and a return-type check. Confirmed pyflakes-clean.

**Not done in this entry**: wiring `rasterize_fractional`'s weight arrays
into `analysis/tasks.py`'s `compute_cell` (still uses the binary
`rasterize_sample`/`rasterize_reference`) - a toggle, mask-array plumbing,
and provenance implications, separate work not started. `AGENTS.md`'s §6a
section updated to reflect both halves now being built.

Not committed yet, alongside this entry.

## 2026-09-22 (same day, continued): `data.h5` built (`analysis/store.py`) - the biggest remaining gap from the "1-4" list, closed

Maintainer's second item from the earlier "1-4" priority list. Flagged in
the prior `engine.py` entry as needing a check-in before design (touches
the suite-wide HDF5 contract and `packages/lspr_io`) - checked in before
writing anything, per that flag.

**Real finding surfaced during that check-in, not assumed**: `packages/
lspr_io` isn't a generic HDF5 helper library - it's `lspr_measurement`,
an already-shipped, versioned schema (currently 6.7) the *stable* LSPRi
Evaluation app already writes to, and it already does something
structurally similar to this session's `analysis/` work: per-ROI
absorbance spectra and sensorgram points (schema 6.4), every reduction
method stored rather than just the active one (schema 6.7 - the same
"don't bake Formula into what's stored" principle `tasks.py` was built
around, independently arrived at), and a `signature_hash` (schema 6.6) -
a sha256 of the combined preprocessing/chromatic/ROI/exclusion cache
signature, serving the same "is this row still valid" role
`ProvenanceRecord` does.

**Maintainer's decision, presented with the real trade-off**: don't reuse
`lspr_measurement`'s mechanism. Its `signature_hash` is one opaque
combined hash - structurally the same shape as this session's *first*,
rejected provenance draft (before the maintainer pushed back twice, first
on the dedup table, then on filenames, in favor of separate,
individually-versioned, human-readable files). Reusing it would have
quietly reintroduced exactly what was steered away from. Confirmed:
compatibility with the stable app's export format is not a goal right
now; the store also doesn't need to follow the suite-wide HDF5
identity-stamping convention if it's simpler not to - built with a light,
independent identity stamp instead (`schema_name`/`schema_version`/
`app_name`/`app_version`/`created_at_utc`, stamped once via `_ensure_
identity`), not `lspr_io`'s registered schema.

**Design decision to avoid HDF5's lack of safe concurrent cross-thread
read/write, by construction rather than locking**: `compute_cell` runs on
`AnalysisWorker`'s single background thread; adding a per-query file read
to `get_metric`/`get_spectrum` (the originally-sketched `ProvenanceStore`
shape) would have meant the GUI thread reading the file while the worker
thread might be mid-write. Instead: `AnalysisEngine` bulk-loads `data.h5`
into `InMemoryProvenanceStore` **once, at construction**
(`store.read_all_cells`) and answers every query from memory afterward,
exactly as it already did before this entry; `run_analysis` writes each
computed cell to both the in-memory store and the file, from the same
single thread that computes it. This is a real design decision, not a
placeholder - `ProvenanceStore` (the originally-sketched per-query-read
class) is documented as **superseded, not blocked**: no HDF5-reading
implementation of it is planned, `InMemoryProvenanceStore` is the real,
permanent store.

**Layout**: `/cells/roi_<id>/cube_<index>/` groups, each holding
`wavelengths_nm`/`sample_values`/`reference_values` datasets plus
`roi_geometry_json`/`reduction_method`/`per_wavelength_settings_json`
attrs (JSON-serialized `ProvenanceRecord` fields). A recompute deletes and
recreates its cell's group - always replaces, never duplicates, matching
sketch §5's "one ongoing file, not a version-hashed folder."

**Real trap caught by testing, not assumed safe**: `ProvenanceRecord.
per_wavelength_settings` is `tuple[tuple[float, int], ...]` - JSON
round-trips a tuple-of-tuples as a list-of-lists, which would silently
break the dataclass `==` comparison `plan_recompute` depends on
(`(500.0, 1) != [500.0, 1]` in Python, even with identical logical
content). `read_all_cells` explicitly reconstructs nested tuples; a
dedicated test asserts the roundtripped `per_wavelength_settings` is a
tuple of tuples, not lists, specifically to guard against this regressing
silently.

Verified with two standalone scripts. First (14 checks): `write_cell`/
`read_all_cells` round-trip fidelity, including the exact property
`plan_recompute` actually depends on (`CellResult == CellResult` and
`ProvenanceRecord == ProvenanceRecord` after a full write-then-read
cycle, not just "the numbers look right"), the tuple-vs-list trap above,
overwrite-not-duplicate semantics on recompute, and identity-stamp
presence. Second (9 checks) - **the real end-to-end restart test**: a
completely independent second `AnalysisEngine` instance, constructed
fresh and pointed at the same `data.h5` path (simulating an app restart),
rehydrates the prior session's result with **zero** `load_plane` calls -
proving persistence actually works across a restart, not just that the
file format round-trips in isolation. Confirmed pyflakes-clean across
`analysis/` and the rewrite-preview window still builds.

### Not done / still open, as of this entry

- The ROI-adjacency exception in `plan_recompute` - unchanged, still not
  handled (see the earlier `analysis/` entry).
- `preview_recompute`'s settings-snapshot side effect - unchanged, still a
  known simplification (see the earlier `analysis/` entry).
- `BackgroundModule`'s timeline extension (item 4 of the "1-4" list) - not
  started.
- `storage/session.py` - untouched, separate piece.
- No committed, permanent test coverage - two more standalone,
  uncommitted scripts this entry, same open question as every prior entry.
- Nothing from this entry has been committed yet.

## 2026-09-23: reference-ring exclusion modes - a real correctness gap in already-committed `compute_cell`, found and closed

Item 3 of the maintainer's "1-4" list was "the ROI-adjacency exception in
`plan_recompute`" - investigating it (as flagged: it needed its own look
at the old app's mechanism rather than a guess) turned up something
bigger than a planner rule.

**The gap**: `gui/analysis_tasks.py`'s `_means_for` (lines 994-999)
subtracts `all_selected_sample_mask` - the union of every *selected* ROI's
sample circle - from each ROI's reference ring, because "a nearby selected
ROI's (often much brighter) sample spot can fall inside this ROI's
reference ring and bias its reference mean." The rewrite's `compute_cell`
takes a single `roi: AreaRoi` and had no way to do this - it was never
given the information. So for any two ROIs close enough that one's sample
circle overlaps the other's reference ring, it computed a **biased
reference value**: a genuine correctness bug in code committed earlier the
same session, not a missing optimization. Flagged to the maintainer before
touching anything, per this repo's "flag any change that measurably
alters computed values" rule (and because the fix changes an
already-tested signature).

**Maintainer's design decision, which simplified the hard part away**:
make it a toggle with two modes rather than always-on, and - crucially -
compute the exclusion from **all ROIs, never the selected subset**:
- `"none"` (current default) - no cross-ROI exclusion at all.
- `"exclude_all_sample_rois"` - a reference ring never counts a pixel
  inside any ROI's sample aperture; overlapping *reference* rings are
  still counted normally.

The all-ROIs part is what makes this tractable: the old app's
selection-scoped union meant a cell's correct value depended on what else
happened to be selected when it ran - a genuinely unpleasant thing to
fingerprint. As a deterministic function of ROI geometry alone, it's just
another recorded input.

**One deliberate difference from the old app**, documented at
`_sample_exclusion_union`: the union includes the ROI's *own* sample
aperture, unconditionally. The old app skipped the union entirely for a
single selected ROI but included self once two were selected, so the same
geometry behaved differently depending on how many ROIs were selected.
Excluding every sample aperture always is both simpler and consistent.

**Fingerprint handling** - the maintainer was unsure this was needed; it
is, and cheaply: in exclusion mode, moving ROI X really does change ROI
Y's reference pixels, so Y's stored value has to be invalidated when X
moves or a reopened session shows a stale, biased number with nothing
indicating it. `sample_exclusion_digest(all_rois)` is recorded in
`SettingsSnapshot` - **only in that mode** (in `"none"` mode other ROIs
genuinely aren't an input, and recording it would invalidate every cell on
any ROI move for nothing). It lives in the snapshot rather than
`ProvenanceRecord` because snapshots are already deduplicated behind a
small integer version: embedding an all-ROI digest in every (ROI x cube)
cell would be the difference between a few hundred KB and well over a GB
at realistic counts.

**Performance**: the union is identical for every cell at a given (cube,
wavelength), so building it per cell would be O(ROIs x cells)
rasterizations. `compute_cell` takes a `sample_exclusion_cache` dict and
memoizes into it, making it O(ROIs); the cache is created per
`run_analysis` call in `engine.py` (scoped to the run so it can't go stale
against a later ROI edit). Passed as an explicit parameter rather than
held internally, following the same convention the old app's
`_scoped_formula_spectrum_task` already used for `roi_mask_cache` - minus
its lock, since only one `AnalysisWorker` task runs at a time and it
processes cells sequentially.

Verified with a standalone script (16 checks) using geometry where ROI B's
sample circle genuinely sits inside ROI A's reference ring, with B's
sample filled far brighter than background so the bias is unmistakable
rather than hypothetical:
- `"none"` mode: A's reference is measurably biased upward by B.
- `"exclude_all_sample_rois"`: A's reference is *exactly* the background
  fill - the bias is gone, not merely reduced.
- A's own sample value is byte-identical between modes (only the reference
  ring is filtered), and overlapping reference rings are not over-excluded.
- **The staleness property both ways**: in exclusion mode, moving a
  different ROI changes this ROI's fingerprint; in `"none"` mode it
  deliberately does not.
- Cache correctness: populated once per (cube, wavelength), cached results
  identical to uncached, and a second ROI through the same cache still
  computes its own distinct value.
- End-to-end through `AnalysisEngine`, including the planner agreeing with
  what `compute_cell` recorded - without that, every cell would look
  permanently stale.

Confirmed pyflakes-clean, the rewrite-preview window still builds, and
`AnalysisEngine()` with no arguments still constructs (the scaffold
contract): `reference_exclusion_mode` is the one constructor callable with
a real default rather than an `_unwired` raiser, since unlike the others
it's a plain setting, not module state that must be read from somewhere.

### Not done / still open, as of this entry

- **Which mode should be the default** - currently `"none"`. Worth the
  maintainer's explicit call: the stable app effectively behaved like
  `"exclude_all_sample_rois"` whenever more than one ROI was selected, so
  `"none"` matches its *single-ROI* behavior, not "what the old app did"
  generally.
- No UI exposes the toggle - injected setting only, no panel behind it.
- `BackgroundModule`'s timeline extension (item 4 of the "1-4" list) - not
  started.
- `storage/session.py` - untouched, separate piece.
- Nothing from this entry has been committed yet.

## 2026-09-23: `AnalysisEngine` wired to the real modules - four gaps closed, two real bugs found by running it

`app_rewrite.py` constructed `AnalysisEngine()` with no arguments, i.e.
every action method raised. It is now built by `_build_analysis_engine()`
with real callables into every module. Doing that surfaced the gaps the
previous entries flagged as "needs its own small investigation", plus two
bugs that only a real run could expose - which is the point of this entry:
neither was visible from reading the code.

### The four gaps, closed

**1. `DatasetModule.wavelengths()` is dataset-global.** Confirmed, and it
matters: `ImageDataset.records` is a flat list, so a cube missing one
wavelength (an aborted acquisition) is an ordinary state, and driving a
per-cube loop off the global union would ask `load_plane` for a plane that
does not exist. Added `ImageDataset.wavelengths_for_cube()` (marked in its
own docstring as new on this branch, **not** part of `domain/models.py`'s
verbatim port) and `DatasetModule.wavelengths_for_cube()` as a sixth query.

**2. `resolve_mask` needed `warp_mask_between` - and the wiring the engine's
own docstring described would have been wrong.** See "real bug 1" below.
`MaskModule.resolve_mask_source()` now returns a triple
(`(authored_frame, mask, scope)`), because the analysis store records which
timeline a mask came from and the only other way to learn that would be for
the caller to read `MaskModule`'s private dicts. No callers existed yet, so
the signature was free to change. Scope stays in `MaskModule`'s own
`"persistent"`/`"individual"` vocabulary; `provenance.mask_scope_tag()` is
the single place it becomes the `persi`/`indiv` filename tag.

**3. Nothing owned `AreaRoiDetectionSettings`.** Every module took it as a
function *parameter* (`roi/detection.py`, `image_tools/background/
estimate.py`, `image_tools/preprocess.py`, `chromatic/landmark_autotrack.py`)
- so when the engine needed `reference_inner/outer_radius_px` and
`reduction_method`, there was no module to read them from. `RoiToolbox` now
owns it: `detection_settings()` (defensive copy) + `set_detection_settings()`,
one combined command in `BackgroundModule`'s shape, undo-tracked with the
old app's exact label `"Detection settings"` (`gui/main_window.py:7427`).
New `RoiComputationalChange` reason `"detection_settings"`, carrying an
empty `roi_ids` - the shared inputs changed, no individual ROI did.
**Flagged, not decided**: `reduction_method`/`formula_key` are arguably
analysis-stage, not ROI-stage; they live here because the old app's
dataclass put them here, and splitting a ported dataclass is its own design
change.

**4. The store's path depends on a dataset that isn't loaded at construction
time.** `masks_dir`/`chromatic_dir`/`settings_dir`/`data_h5_path` were
constructor arguments defaulting to *relative* paths, so a real run would
have written `data.h5` into whatever the process's working directory
happened to be. Replaced with one `set_storage_root(root)` that re-points a
live engine and rehydrates from the new root's `data.h5`, with the four
paths derived as properties; `app_rewrite` connects it to `dataset_loaded`
(using `ImageDataset.home`, not `folder`, so an analysis never lands inside
a raw TIFF/OME-Zarr folder it doesn't own) and `dataset_cleared`. Rebuilding
the engine per dataset was the alternative and is worse - every panel's
`connect()` would have to be torn down and rebuilt. `run_analysis` now
raises without a root rather than writing somewhere arbitrary.

### Real bug 1: the ignore mask was going to be applied in the wrong coordinate space

`tasks.py`'s flagged "assumption 1" (mask authored in raw space, passed with
`external_mask_processed=False`) was **half right**, and the wrong half
mattered. Traced the old app rather than guessing again
(`gui/mask_controller.py`'s `external_mask_for_record`, plus its callers in
`gui/analysis_tasks.py`/`gui/analysis_worker_mixin.py`):

- The authored mask genuinely is in **raw** image space - it is read from a
  file sized to `load_image_shape(record.path)`. That half held.
- But the **chromatic affine is expressed in processed space** (the same
  space `rasterize_sample`/`rasterize_reference` work in, off
  `processed.shape`). The engine's documented plan was for the caller to
  `warp_mask_between` the mask before handing it over - i.e. warp a
  raw-space mask with a processed-space matrix. With any crop or rotation
  active that puts the ignore mask somewhere meaningless. Exactly the
  CLAUDE.md pitfall ("mixing coordinate spaces produces silently wrong
  results"), and it would not have crashed - just quietly excluded the
  wrong pixels.

The old app's analysis path does `apply_spatial_mask(...)` then
`warp_boolean_mask_affine(...)` then `external_mask_processed=True`. New
`tasks.py:_mask_for_compute` does exactly that, unconditionally (one path,
one behavior - masking in raw space first is only *nearly* equivalent even
with no warp, since the image transform interpolates and would bleed zeroed
pixels into their neighbours at mask edges). `WavelengthComputeInput` gains
`mask_warp_affine`, supplied by the engine from
`ChromaticModule.affine_between` and only when the authored frame differs
from the queried one.

`resolved_mask` now explicitly carries the mask **as authored**, unwarped,
and that is still what gets persisted to provenance - a per-wavelength
warped variant written under the authored frame's filename would mint a new
version per wavelength for what is really one mask.

### Real bug 2: with any mask set, every cell was permanently stale

Found by the end-to-end script, not by reading: `preview_recompute` kept
reporting every cell as needing recompute even immediately after a
successful full run, and a restart re-ran everything.

Cause: planning may not write mask files (the design doc's "written lazily"
rule), so `_gather_current_inputs` substituted `MaskSnapshotRef(version=0)`
as a placeholder. But that placeholder goes into the `SettingsSnapshot` the
planner fingerprints, while `compute_cell` records the **real** version. The
two could therefore never match. Effect: with any ignore mask present,
"N cells will be recomputed" always said *all* of them, every run recomputed
everything from scratch forever, and the whole provenance/dedup design was
silently inert. The settings-snapshot files gave it away in passing -
`settings_v13.json` after a handful of previews.

Fix: `provenance.resolve_mask_snapshot_ref()` splits version-*resolution*
from the write. Planning now asks "which version would this exact content
be?", reading existing PNGs but writing nothing - so an already-persisted
mask resolves to its existing version and the fingerprints match, while a
genuinely new mask resolves to the version it will get and correctly fails
to match. `persist_mask_snapshot` is now that function plus the write.
Costs planning the mask-PNG readback it used to skip; buys a
`preview_recompute` that tells the truth.

This also means the previous entry's end-to-end verification passed only
because it never set a mask. Worth recording as a lesson for the eventual
test suite: the dedup property needs a case *with* a mask, which is the one
where it breaks.

### Also in this pass

- `DEFAULT_REFERENCE_EXCLUSION_MODE` is now `"exclude_all_sample_rois"`
  (maintainer's call, 2026-09-23, closing the open question the previous
  entry left). `"none"` was the safe default, not the right one: the stable
  app effectively behaved like exclusion whenever more than one ROI was
  selected, which is the normal case.
- `trimmed_mean_fraction` defaults to `reduction.DEFAULT_TRIMMED_MEAN_FRACTION`
  instead of a duplicated literal `0.10`.
- `selection.roi_selection_changed` to `engine.set_selected_rois` wired
  (sorted, since the signal carries a set whose order isn't meaningful).

### Verified

One standalone script, 34 checks, all passing - against **real TIFF files
written to a temp directory** and a real `data.h5`, with a deliberately
uneven dataset (two cubes, one of them missing a wavelength). Notable ones:
the uneven cube producing a 2-wavelength cell next to a 3-wavelength one end
to end; `run_analysis` refusing without a storage root; `data.h5` landing
beside the dataset; `_mask_for_compute` equalling `apply_spatial_mask`
exactly in the no-warp case and coming out in processed shape (40x48) rather
than raw (64x80); the dedup property **with a mask set** in all three forms
(second preview plans zero, a fresh engine rehydrates identical values, and
that fresh engine also plans zero); a detection-settings change correctly
invalidating everything; and clearing the storage root dropping results. The
script also caught a wrong property name in the wiring itself (`data_root`,
which doesn't exist - it is `ImageDataset.home`).

Confirmed pyflakes-clean and the rewrite-preview window still builds with
all five tabs.

### Not done / still open, as of this entry

- **`AnalysisWorker` swallows a task exception.** If the run callable
  raises, the thread dies, `analysis_complete` never fires, and the UI would
  wait forever with nothing reported. Noticed while debugging the harness
  (the failure looked identical to "still running"). Not fixed here - it
  wants a deliberate error-signal design, not a bare try/except.
- `preview_recompute` still writes a settings-snapshot JSON as a side
  effect - unchanged, and now the *only* remaining planning side effect.
- `rasterize_fractional`/`weighted_*` still not wired into `compute_cell` -
  no longer blocked (the weighted functions exist), but it changes computed
  values and wants its own toggle and a check-in.
- No UI exposes the exclusion-mode toggle, reduction method, or detection
  settings - all command-only, no panel behind them.
- `BackgroundModule`'s timeline extension - not started.
- `storage/session.py` - untouched.
- Still no committed test coverage. This entry's script is the strongest
  candidate yet to become one, since it exercises every module together
  against real files.

## 2026-09-23 (same day, continued): `ImagePanel` built - the first real panel

The panel layer was six files of scaffolding totalling ~400 lines, with
every `_redraw` raising `NotImplementedError`. `panels/image/` is now real:
it renders the processed image for the current frame, draws ROI overlays,
and turns clicks and drags into module commands.

### What it does

**Renders off the GUI thread** (`panels/image/render.py`, new). A plain
`threading.Thread`, never `QThreadPool` (AGENTS.md's zarr rule - the same
choice `analysis/worker.py` makes, for the same
`STATUS_HEAP_CORRUPTION` reason). **Latest request wins**: dragging a
wavelength control can queue dozens of renders a second and only the last
is ever seen, so pending-but-unstarted requests are dropped. This is the
lossy-UI half of the acquisition app's lossless-acquisition/lossy-UI rule
applied to display - deliberately the opposite of `AnalysisWorker`, which
drops nothing, because there every requested cell is one the user asked to
keep. A request that is superseded *after* the worker already picked it up
is caught a second time on arrival, by serial number.

`RenderRequest` carries settings **by value**, snapshotted on the GUI
thread, rather than letting the worker call back into the modules - reading
module state from a background thread while the GUI thread may be mutating
it is exactly the race this architecture avoids by convention. The modules'
`settings()` methods already return defensive copies, so this is free.

**Owns no state another module owns.** No ROI list, no selection set, no
settings copy; every draw re-reads. That is the single thing the old app
broke hardest, and it is cheap here because every query is an in-memory
read.

**Every gesture is a command call, never a mutation.** A drag calls
`RoiToolbox.request_move`; a click calls `SelectionModule.set_roi_selection`;
the navigation controls call `set_cube`/`set_wavelength`. The panel then
redraws *because a module emitted a change* - which is what makes undo work
without the panel knowing undo exists: Ctrl+Z emits the same signals a
fresh edit does, and the panel reacts identically. Verified directly rather
than assumed (see below).

**Overlays** are three `PlotDataItem`s (sample, reference, selected) for the
whole scene rather than items per ROI - NaN separators with
`connect="finite"` let one item draw any number of disjoint circles, so
adding an ROI costs an array append rather than a new `QGraphicsItem`. That
matters because the overlay is rebuilt on every selection change, not just
on an ROI edit.

**Overlays are drawn synchronously** while only pixels go off-thread: a
coalesced 100 ms round trip to a worker would make dragging an ROI feel
broken, and the overlay is a few thousand points of pure numpy.

### Two correctness details worth recording

**The drawn ring must be the measured ring.** `roi/rasterize.py`'s
`_effective_reference_radii` is now public `effective_reference_radii`, and
the panel uses it rather than re-deriving the per-ROI
`reference_inner/outer_diameter_px` override rule. Re-deriving it would mean
the ring drawn and the ring measured could silently disagree - the exact
class of error an overlay exists to rule out. Same reasoning for the
sample-diameter override.

**The displayed image must be the analyzed image.** `_mask_for_compute` (new
that morning, in `analysis/tasks.py`) moved to
`image_tools/preprocess.resolve_external_mask`, and both the panel and
`compute_cell` call it. The transform order it encodes - spatial transform
first, chromatic warp second, `external_mask_processed=True` - is subtle
enough (see the earlier entry today) that having two call sites re-derive it
independently would be a matter of time.

**A cube short a wavelength is handled, not errored.** `_current_wavelength`
snaps the selected wavelength to the nearest one the *current cube* actually
has. Switching to a cube missing 550 nm shows 500 or 600 rather than an
error - the same uneven-dataset case `wavelengths_for_cube` was added for.

### Verified

One standalone script, 33 checks, all passing - a real `QApplication` built
in-process without ever calling `.exec()`, real widgets, real TIFF files,
driven entirely by direct method and signal calls, never screen coordinates
(AGENTS.md's testability rule, matching
`tests/integration/test_lspri_preferences_dialog.py`'s existing pattern).

Notable: an image genuinely rendering after a dataset load and the worker
confirmed to be on its own thread; a crop changing the displayed shape
64x80 -> 40x48 and disabling image tools restoring it; the exact overlay
point counts for 2 ROIs (2 sample circles, 4 reference circles); hit-testing
at centre, inside the radius, and on empty image; selection moving an ROI
between the normal and highlight curves; a drag reaching `RoiToolbox` as a
`"moved"` change, **and `undo_manager.undo()` moving the ROI back and
redrawing the panel - with the panel containing no undo code at all**;
switching to the short cube snapping the wavelength; a missing frame
surfacing as a status message rather than a crash; a stale result being
dropped rather than drawn; and `dataset_cleared` resetting the panel (the
first real subscriber to that signal, which `DatasetModule`'s docstring had
flagged as unverified end to end).

`pyflakes`-clean; the rewrite-preview window still builds with all five
tabs; the previous entry's 34-check engine script still passes unchanged
after the `resolve_external_mask` move.

### Deliberately not built here

Each is its own piece of work, not something this panel should invent an
answer for:

- Crop/rotate tool interaction (the draggable rectangle, the rotation
  handle). `GeometryModule`'s commands are real; the old app's tool UI
  (`gui/image_tools_controller.py`) is tangled with pyqtgraph `RectROI`
  sync that needs its own port.
- Mask painting and preview overlays, the intensity-highlight overlay, the
  histogram-driven highlight - `MaskModule`'s async candidate machinery
  isn't built either.
- Cursor readout, scale bar, ruler overlay - `GeometryModule` already owns
  the calibration state they would draw from.
- ROI creation by click and resize by handle. `add_roi`/`resize_roi` are
  real commands; this panel only moves and selects so far.
- Splitting "redraw overlay only" out of "re-render pixels". Currently every
  change schedules the same redraw. That is a real optimization once there
  is a dataset big enough to measure against - guessing at it now would be
  the premature kind AGENTS.md's performance rules warn about.

### Not done / still open, as of this entry

- The other five panels are still scaffolding.
- `AnalysisWorker` still swallows a task exception (flagged in the previous
  entry, unchanged).
- `BackgroundModule`'s timeline extension - not started.
- `storage/session.py` - untouched.
- Still no committed test coverage. Two strong candidate scripts now.

## 2026-09-23 (same day, continued): committed test coverage - the gap every prior entry flagged, closed

Every entry on this branch has ended with "no committed, permanent test
coverage - every verification was a standalone, uncommitted script". Those
scripts have now been turned into real tests, while the two they came from
were still fresh.

**Location decided by the maintainer** (asked, because it is the first thing
on this branch to land in a *different repo*): the umbrella repo's
`tests/`, alongside every other Suite test, each file guarded by a
module-level `unittest.SkipTest` when the rewrite modules aren't importable.
So on a stable checkout the whole file skips cleanly; on a `rewrite`
checkout it runs. The alternative considered was a new `apps/LSPRi/eva/tests/`
inside the submodule - rejected because it would give the Suite a second
test location and the files would have to move anyway once the rewrite
replaces the stable app. Verified the guard really does register as a skip
rather than a collection error, rather than assuming pytest's behavior.

**Three files, 35 tests:**

- `tests/unit/test_lspri_rewrite_analysis_core.py` (14) - genuinely pure,
  no Qt and no files, which is what keeps it in `tests/unit` per the
  documented split: per-cube wavelengths on the dataclass, the
  persistent/individual to persi/indiv translation and its loud failure,
  the exclusion-mode default, `effective_reference_radii`'s override rules,
  and `resolve_external_mask`'s coordinate-space contract.
- `tests/integration/test_lspri_rewrite_analysis_engine.py` (13) - the real
  module graph `_build_analysis_engine` wires, against real TIFFs and a
  real `data.h5`.
- `tests/integration/test_lspri_rewrite_image_panel.py` (12) - a real
  `QApplication` built in-process without `.exec()`, driven by direct
  method and signal calls, never screen coordinates.

**What was chosen to pin, and why** - these are regression guards for
things that fail *silently*, not coverage for its own sake:

- **The dedup tests set an ignore mask on purpose**, and the test file says
  so in its own docstring. That is the exact condition under which the
  placeholder-mask-version bug appeared; without a mask it is invisible,
  which is how it survived the previous session's own end-to-end
  verification. A future edit that reintroduces a planning-side placeholder
  now fails a test instead of quietly making every run a full recompute.
- **`resolve_external_mask`'s transform order** is pinned by asserting the
  *shape* of the result (40x48 processed, not 64x80 raw) after a warp - if
  the warp ran first, in raw space, the shape would give it away. Getting
  this wrong misaligns the ignore mask rather than raising.
- **Off-thread rendering** is asserted directly (the render thread is alive
  and is not the calling thread). It regresses invisibly: the panel keeps
  working, it just freezes under load.
- **A drag being undoable** pins the one-way flow - the panel has no undo
  code, so if a future handler "just" mutates ROI state directly, the
  feature still appears to work and only undo breaks.
- **`effective_reference_radii`** is pinned because the Image panel draws
  the ring analysis measures; a re-derivation drifting apart would show a
  ring that isn't the one being measured.

**Not pinned, deliberately**: numerical values of computed
sample/reference pairs. Those need a real dataset with known physics to be
meaningful, and asserting on synthetic-noise values would only pin the
random seed. The tests assert structure (which wavelengths, finite values,
which cells recompute) and leave numerical correctness to the standalone
before/after comparisons AGENTS.md already requires for compute changes.

### Not done / still open, as of this entry

- The other five panels are still scaffolding.
- `AnalysisWorker` still swallows a task exception.
- `BackgroundModule`'s timeline extension - not started.
- `storage/session.py` - untouched.
- Coverage is deliberately shallow in places: nothing exercises
  `ChromaticModule.refit`, the background/mask modules' own commands, or
  `compute_cell`'s reference-exclusion mode end to end (that one has a
  standalone 16-check script from 2026-09-23 that could be converted the
  same way).

## 2026-09-23 (same day, continued): `storage/session.py` built - session save/load, and the `restore_*` surface it needed

### Scope-check first: "ports `storage/workspace.py` mostly as-is" holds for one third of it

- **The ROI half ports directly.** `_encode_area_roi` and the
  `RoiMask`/`per_wavelength` encoders came over with their logic intact,
  including the documented performance reason for enumerating fields by
  hand instead of `asdict()` (the recursive deep-copy of `per_wavelength`
  and the mask arrays measured ~2 s of ~3.6 s per 160 ROIs on a real
  dataset, paid on every close and every autosave's unchanged-check - and
  then thrown away, since all three fields get their own encoding
  immediately afterwards).
- **The settings half cannot port.** The old `processing_profile.json` has
  one flat `"preprocessing"` block mirroring `PreprocessingSettings`'s 40
  fields, and that dataclass no longer exists - the 2026-09-20
  decomposition split it four ways.
- **The mask half has no old format at all.** The old app stored one
  `session_mask` (a single boolean array inline in JSON) plus per-frame
  pixel diffs; `MaskModule` holds a *timeline* of `MaskChange` records.

### Maintainer's two decisions, presented with trade-offs before any code

1. **A new per-module format, and no importer for old
   `processing_profile.json` files.** Each top-level block is one module's
   own state. The rejected alternative was keeping the old flat shape with
   a translation layer, which would have to track two different
   decompositions forever and re-couple the new modules to exactly the
   grab-bag boundary the September split removed.
2. **Mask pixels to versioned PNGs**, through the same
   `persist_mask_snapshot` mechanism `analysis/provenance.py` uses, rather
   than inline in the JSON or in a third HDF5 file.

**A wrong claim caught by testing, worth recording**: the first draft of
this file's docstring said identical masks across frames "collapse to one
file for free". They don't - the dedup is per `(frame, scope)` group,
because the frame is part of the filename. What it actually buys is that
re-saving an *unchanged* session rewrites no mask file at all, which is the
property an autosave needs. Content-addressing across frames would mean
hash-named files, which the provenance design doc deliberately rejected in
favour of readable names. The test asserted the wrong number, which is how
the wrong docstring surfaced.

Session masks live in `session/masks/`, not `analysis/masks/`: same kind of
thing, different question ("what is the mask now" vs "what mask produced
this stored cell"), and sharing a folder would make deleting a stale
analysis quietly destroy current session state.

### The `restore_*` surface, which loading turned out to require

Loading a session cannot go through the command API. `RoiToolbox` has no
way to set explicit ROI ids (`add_roi` mints them from a counter),
`ChromaticModule` had no model setter at all, and every `GeometryModule`
setter pushes an undo entry - so a restore would have arrived as half a
dozen undoable edits. Added one method per module:
`GeometryModule.restore_settings`, `BackgroundModule.restore_settings`,
`MaskModule.restore_state`, `ChromaticModule.restore_state`,
`RoiToolbox.restore_state`.

Each replaces state wholesale, pushes **nothing** onto the undo stack, and
still **emits**, so panels redraw. `apply_session` then calls
`undo_manager.clear()`. That is the point of not tracking a restore: Ctrl+Z
straight after opening a dataset should do nothing, not rewind past the
file that was just opened into a half-restored state that never existed.

Two query methods were missing and got added alongside:
`MaskModule.mask_changes()` and `ChromaticModule.models()`, both returning
a deterministically ordered tuple - so saving unchanged state twice
produces an identical file, which is what lets an autosave's "did anything
change" check be a plain comparison.

**The id-counter trap, found while writing `RoiToolbox.restore_state`**:
restoring N ROIs has to resume the id counter past the highest restored id,
and the group counter past the highest `"group_<n>"`. Resetting either to 1
would make the first ROI or group created after opening a session silently
*replace* an existing one rather than be added. Pinned by a test.

**New change reasons**: `"session_restored"` on Roi/Geometry/Background/
Mask/Chromatic payloads, all carrying empty or `None` narrowing fields,
documented as "no narrowing possible, redo everything" - which is correct,
since after a session load nothing computed against the previous state is
still trustworthy. `MaskComputationalChange.frame`/`scope` became
`| None` for exactly this.

`capture_session`/`apply_session` live in `app_rewrite.py`, next to
`_build_analysis_engine`, for the same reason: that is the one place that
knows about every module, and keeping it there leaves `storage/session.py`
a pure, Qt-free data layer a test can drive with plain dataclasses.

### Not persisted, deliberately

The old profile's `statistics_settings` (no counterpart exists anywhere in
the rewrite) and `image_exclusions` (`ImageExclusionRule` is referenced only
inside `dataset/io.py` and owned by no module). Both want a real owner
first; a session format is the wrong place to decide that.

### Verified

A standalone script (32 checks) plus a committed test file,
`tests/integration/test_lspri_rewrite_session.py` (17 tests) - total
rewrite coverage is now 49 tests. Covers: the file layout and schema stamp;
mask pixels going to PNGs with only references in the JSON; no ndarray
leaking into the mask-settings block; a full round trip into a *completely
fresh* set of modules for every block; `RoiMask` and `per_wavelength`
surviving (JSON has no tuple keys - the field a naive encoder loses
silently); an individual mask change still winning over the persistent one
after restore; the undo stack cleared; both id counters resuming; re-saving
unchanged state being byte-identical and writing no new PNGs; a missing
session returning `None`; a foreign schema and a future major version both
rejected rather than silently defaulted; and an orphaned mask reference
dropping just that change while keeping the ROIs and settings.

pyflakes-clean across the whole package; the rewrite-preview window still
builds.

### Not done / still open, as of this entry

- **Nothing calls `save_session`/`load_session` yet.** No autosave, no
  save-on-close, no load-on-dataset-open - `app_rewrite.py` has the
  capture/apply pair but no trigger. Wiring that needs a debounce policy
  (the old app's `SessionStateManager` has one worth reading first), so it
  is its own piece rather than tacked on here.
- The other five panels are still scaffolding.
- `AnalysisWorker` still swallows a task exception.
- `BackgroundModule`'s timeline extension - not started.
- `statistics_settings`/`image_exclusions` have no owner, so nothing
  persists them.

## 2026-09-23 (same day, continued): background provenance closed out - the computed profile is not stored, and `BackgroundModule`'s timeline is dropped

Two decisions, and the second one cancelled the piece of work that was
queued. No behavioural code change landed: the only edits are this document,
`docs/analysis_provenance_store_design_2026-09.md`,
`docs/background_drift_measurement_2026-09.md`, and two now-wrong comments in
`analysis/provenance.py`.

### Decision 1: no persistent/individual timeline (settled by measurement)

Covered in full by `docs/background_drift_measurement_2026-09.md`, written
earlier the same day. Short version: the provenance design doc's `persi`/
`indiv` tag on background presumed an "estimate once, carry it forward"
concept that exists nowhere in the code, and whether it *should* exist was a
scientific question. Measured rather than argued, on
`04_Bulk_sensitivity_...`: the illumination reshapes monotonically, and the
non-uniform part of the drift - the part that does **not** cancel in a
sample/reference ratio - crosses the single-frame shot-noise floor by about
cube 5 and reaches ~2.9x it by cube 39. Per-frame re-estimation stays.

**This removes "`BackgroundModule`'s timeline extension - not started" from
the previous entry's still-open list.** It was never a gap; it was a
prerequisite for a design that turned out to be wrong.

### Decision 2: the computed profile is not stored at all (maintainer's call)

The design doc asked for the profile as a 16-bit PNG per frame. Dropped. Only
the method (`BackgroundSettings`) is recorded, and being flat and dataset-wide
that is identical for every cube and wavelength - which is exactly what
`SettingsSnapshot.background` has carried since 2026-09-22. So the feature is
complete as already built, and what the original row called an "interim
fallback" is the finished design.

The reasoning, so nobody rebuilds it: the profile is a **deterministic
function of inputs the snapshot already fingerprints** - the raw frame,
`geometry`, `mask`, `background`. Storing it buys **zero** invalidation
power. Its only value would be as an audit artifact, and the cost of that
artifact was measured, not estimated:

| | per frame | full 314-cube x 27-wl analysis |
|---|---|---|
| 16-bit PNG, full res, level 3 | 488 KiB, 116 ms | ~4.0 GB, ~16 min |
| raw frame, for scale | 2340 KiB | 30 GB (the dataset) |

float32 -> uint16 quantization costs 0.5 ADU max against a ~193 ADU
single-frame shot-noise floor, so 16-bit was never the problem. **Downsampling
to shrink it was tested and rejected**: sigma=48 px suggests there is no fine
detail worth keeping, but the vignetting gradient at the frame edges is
steeper than that implies - 4x downsampling round-trips at 107 ADU RMS (55% of
shot noise), 8x at 499, 16x at 771. So the real choice was 4 GB or nothing,
for something that improves no computation.

**What this gives up, recorded deliberately**: changing
`estimate_background_profile()`'s algorithm will **not** invalidate stored
cells - their recorded `BackgroundSettings` is unchanged, so fingerprints
still match. Storing the profile would not have fixed that either (it records;
it does not invalidate). The cheap fix if it ever matters is an algorithm
version constant in the settings snapshot, bumped by hand. Not built - the
estimator is a verbatim port nobody plans to change.

### Design trap found while planning the implementation, worth keeping

Had the profile been stored as a content-deduplicated versioned file the way
masks are, it would have re-introduced the exact bug
`resolve_mask_snapshot_ref` was written to fix. `_gather_current_inputs()`
fingerprints every frame with **no pixel access** - that is what makes
`preview_recompute` cheap. A background ref versioned by comparing profile
*pixels* would have forced planning to estimate the background for all 8478
frames just to answer "how many cells will recompute": ~38 minutes at
binning=2. The workable scheme was to version it by the settings that
*determine* the profile (geometry + mask ref + `BackgroundSettings`), keeping
plan-time resolution to JSON comparisons. Recorded because the same trap
applies to any future file-backed provenance input whose identity is
expensive to compute.

Also noted at the time: a PNG cannot carry the scalar baseline, and
`apply_background()` is `image - background + baseline`, so a stored profile
alone would not have been enough to reconstruct the subtraction.

### Separate regression found while reading this, NOT fixed here

`analysis/tasks.py`'s `compute_cell` calls `apply_preprocessing` without
`rois` or `mask_settings`, so both background-exclusion toggles are inert on
the rewrite's analysis path: `flatten_background_exclude_area_rois` (defaults
**on**) and `flatten_background_exclude_mask`. The old app's equivalent bulk
path wires both explicitly (`gui/analysis_tasks.py:907-913`), so this is a
port gap, not inherited behaviour.

The mask half compounds: `preprocess.py` zeroes masked pixels into the
processed image, and inside `_combined_exclusion_mask` the ignore mask only
reaches the exclusion set *through* `mask_settings` - so those zeros are left
to drag the local background average down, which is the exact failure the
comment above that line says was fixed. The fix landed; it is inert on this
path. Rotation-fill exclusion is unaffected (passed separately,
unconditionally).

Dormant for now: `flatten_background_enabled` is `False` in
`04_Bulk_sensitivity_...`'s saved profile, and nothing is wrong when
flattening is off. When it is fixed, the exclusion ROI set should be **all**
ROIs, not the selected subset the old app used - same reasoning as the
2026-09-23 reference-ring exclusion entry.

**How much it actually costs, measured before deciding urgency** (real frame,
real 160-ROI table, identity geometry, sigma=48/binning=2; current `rois=None`
behaviour vs. the `rois=all` the saved profile asks for). Scripts
`bg_exclusion_impact.py`/`bg_exclusion_drift.py`, scratch, reproducible from
this entry:

| | median | worst ROI |
|---|---|---|
| shift in sample mean | 794 ADU (4.1x shot noise) | 4883 ADU |
| shift in **sample/reference ratio** | 0.0012 = 0.13% (0.3x shot noise) | 0.0155 = 1.6% (3.8x) |
| **change in that ratio bias over 300 cubes** | 0.0001 (**0.02x** shot noise) | 0.0068 (1.7x) |

The absolute shift is large but ~99% of it is uniform and cancels in the
ratio. What survives is a **near-constant per-ROI offset**: only 2 of 160 ROIs
move by more than one shot-noise floor across the entire run. So a sensorgram,
which reads *changes* in that ratio, barely sees this - which is why the
verdict is "real, fix it, not urgent" rather than "results are wrong".

**What that measurement does not cover**: the mask half. This profile has
`ignore_marked_pixels=False` and `flatten_background_exclude_mask=False`, so
there was no real mask to test against, and the numbers above are the ROI half
only. The mask half is plausibly worse, not better - a masked region is set to
literal 0, a far bigger perturbation of a local average than a nanoparticle
spot is. Measure it separately, on a dataset that actually uses an ignore
mask, before assuming these numbers transfer.

## 2026-09-23 (same day, continued): the background-exclusion regression fixed, and session autosave wired

Three pieces, applied from what the previous entries had written down but
left undone. The first changes computed values; the other two don't.

### 1. `compute_cell` now passes `rois` and `mask_settings`

The regression the previous entry found and deliberately did not fix.
`apply_preprocessing` gates each of these on its own `BackgroundSettings`
toggle internally, so passing them unconditionally is a no-op whenever
flattening is off - which is why the fix is two arguments rather than a
branch.

The ROI set is **all** ROIs, per that entry's own instruction; the old app
used the selected subset, which made a stored value depend on what happened
to be selected when it was computed.

**The display path had the same gap**, which the entry didn't mention:
`panels/image/render.py` also called `apply_preprocessing` without either
argument, under a comment promising that what is displayed and what is
measured "cannot drift apart". Fixing only analysis would have made that
comment false in exactly the case it was written for. `RenderRequest` now
carries `rois`/`detection`, and **neither has a default**: the obvious
default for both is "nothing to exclude", which is precisely the inert case
that caused this bug, so a new call site has to say what it means.

No extra re-render comes with it - `ImagePanel._redraw` was already
connected to `roi_toolbox.geometry_changed` and re-submits a full render on
every ROI edit regardless.

**Numeric impact**: as measured in the previous entry - median 0.13% shift
in the sample/reference ratio, 4.1x shot noise in the *sample* mean but
~99% of that cancelling in the ratio, and only 2 of 160 ROIs drifting by
more than one shot-noise floor across a 300-cube run. Any stored analysis
computed with flattening on is now invalidated and will recompute; one with
flattening off is untouched (see the `as_json()` note below, which is what
makes that true).

### 2. The fingerprint half, which the flagged fix did not mention

Wiring ROIs into the background estimate makes *other ROIs an input to this
ROI's value* - the same invalidation problem `sample_exclusion_digest` was
written for on the reference-ring side, arrived at from a different
direction. Without it the fix would be a half-fix of the worst kind: the
background would correctly exclude a moved ROI while every other ROI's
stored cell kept a value computed against a background that no longer
exists, with nothing on screen to say so.

`background_exclusion_digest(rois, background_settings, detection_settings)`
records it into `SettingsSnapshot.background_exclusion`, computed by the
identical call on both the compute side (`tasks.py`) and the planning side
(`engine.py`) - if those two ever disagree, every cell looks permanently
stale, which is exactly the bug the mask-version placeholder caused earlier
the same day and the reason that test file sets a mask.

Three deliberate narrowings:

- **Only the three fields `_roi_exclusion_mask` actually reads** (id,
  centre, `sample_radius_px`). It works off the radius field directly and
  never consults `sample_diameter_px` or a sample mask, so recording those
  would invalidate cells on edits the background estimate cannot see.
- **Only `ignore_marked_pixels`** from `AreaRoiDetectionSettings` - the one
  field that reaches the estimate, via `ignored_pixel_mask` gating the whole
  external mask on it. Flipping it changes the result while
  `BackgroundSettings` stays identical, so nothing else in the snapshot
  would notice.
- **Omitted from `as_json()` entirely when `None`**, not written as `null`.
  Snapshots are deduplicated by comparing the payload against
  already-written files, so an unconditional null key would differ from
  every pre-existing file and force a full recompute of precisely those
  analyses this change cannot affect.

The digest is pure geometry and settings - no pixel is read to build it,
which keeps `preview_recompute` cheap and avoids the design trap the
previous entry recorded (a background ref versioned by profile *pixels*
would have made "how many cells will recompute" a 38-minute question).

**Not chromatically warped**, matching the old app and the shape of
`_roi_exclusion_mask` itself: it grows each ROI's radius by 35% before
excluding it, a margin far wider than the shift a chromatic affine applies.

**Still redundant, and left that way on purpose**: `apply_preprocessing` is
called per (cell x wavelength), so the same frame's background is estimated
once per ROI - and the ROI exclusion now adds an O(ROIs) rasterization to
each of those. That redundancy predates this change and belongs to the
batch-caching layer `tasks.py`'s docstring defers to `worker.py`/`engine.py`;
adding a second cache here would optimize the inner half of something whose
outer half is the real waste.

### 3. `AnalysisWorker` no longer loses a task exception

`threading.Thread(target=task)` sends an uncaught exception to
`threading.excepthook`, i.e. to a stderr a packaged GUI build has nowhere to
show - so an analysis that died on its third cell was indistinguishable from
one still running. The worker now catches, logs with a traceback, and keeps
it on `last_error`; `run_analysis`'s task body emits `analysis_complete` from
a `finally`, so the UI stops waiting for a run that is over. Cells computed
before the failure stay in the store: each is written whole, so a partial run
is valid, just incomplete.

Re-raising is deliberately not done - there is no caller left on that thread
to catch it, so it would land straight back on the excepthook this exists to
avoid.

### 4. Session autosave - `save_session`/`load_session` finally have triggers

The previous entry's first still-open item. `storage/session_autosave.py`
(new) holds the debounce; `app_rewrite._build_session_autosave` holds the
knowledge of what counts as a change, next to `_build_analysis_engine` for
the same reason - it is the one place that knows every module, and keeping
it there leaves the autosave itself a pure, module-free timer.

**Policy ported from the old app**, interval included: 2500 ms single-shot,
restarted per change, with checkpoints (dataset switch, session load,
application quit) bypassing it via `flush()`. Quit is hooked on
`aboutToQuit`, not `closeEvent`, the same correction `ImagePanel` documents
for its render thread.

Every computational **and** cosmetic signal schedules a save. The
cosmetic/computational split says what may be *stale*; it says nothing about
what is worth *keeping*, and a relabelled ROI is exactly as worth keeping as
a moved one.

Two deliberate departures from the old app:

- **A plain dirty flag, not a content signature.** The old app rebuilds the
  whole payload to compare it - it has to, because its triggers fire whether
  or not anything really changed, and that rebuild is itself measured in
  seconds on a real dataset. Here a save is only ever scheduled by a
  module's own change signal, so "was anything scheduled since the last
  successful write" answers the same question for free.
- **The write is synchronous on the GUI thread.** The old app moved it to a
  worker after finding it froze the UI for a beat. That may well need doing
  here too, but it needs a real measurement first, and doing it safely means
  serializing concurrent writes to one file. `capture_session` already
  returns defensive copies, so it stays a small change when there is a
  number to justify it.

**The destructive case, handled explicitly**: `load_session` raises on a
file it cannot parse, by design. The modules are then sitting at defaults,
which is *not* what that file describes - so autosaving would replace a
recoverable session with blank state and destroy the only copy. A failed
load therefore accepts the root with `enabled=False`: the app runs, nothing
is written to that dataset until it is opened successfully. A failed *write*
likewise leaves the change pending rather than marking it saved.

`set_root` flushes against the **previous** root before switching, because
the pending timer carries no root of its own - a pending edit written after
the switch would land in the new dataset's folder.

### Verified

`tests/integration/test_lspri_rewrite_session_autosave.py` (15 tests, new)
and four added to `test_lspri_rewrite_analysis_engine.py`; 763 LSPRi tests
pass, pyflakes clean across the package.

The two background-exclusion tests fail for deliberately different reasons
if the fix is reverted - one pins the computed value against an independent
reference computation (with a guard asserting the two references actually
differ, so it cannot pass vacuously), the other pins the invalidation. A
third asserts the converse: with the exclusion off, moving one ROI must not
drag every other cell into a recompute.

### Not done / still open, as of this entry

- The other five panels are still scaffolding.
- **The mask half of the exclusion impact is still unmeasured** - the
  previous entry's numbers cover the ROI half only, on a profile with no
  ignore mask. A masked region is set to literal 0, plausibly a bigger
  perturbation of a local average than a nanoparticle spot; worth measuring
  on a dataset that uses one before assuming those numbers transfer.
- `AnalysisEngine` now reads `RoiToolbox.detection_settings()` through three
  separate callables (`reduction_method`, `default_reference_radii`,
  `detection_settings`). Worth collapsing into one the next time that
  constructor is touched; not done here to avoid reshaping a just-tested
  surface as a side effect of a bug fix.
- `_frame_naming` in `app_rewrite.py` duplicates `AnalysisEngine._naming`.
  Two copies is tolerable; a third means it belongs on `DatasetModule`.
- Session autosave has no "unsaved changes" indicator and no manual save -
  it is invisible until it fails.
- `statistics_settings`/`image_exclusions` still have no owner, so nothing
  persists them.

## 2026-09-23 (same day, continued): the query layer - formula, fit, metric, statistics

The last big non-GUI piece. `compute_cell` has stored raw reduced (sample,
reference) pairs since 2026-09-22 specifically so that everything here is a
*re-derivation* rather than a recompute; until now nothing consumed them, so
`get_metric` returned the raw pairs and its own docstring called that a gap.
Both display panels were blocked behind it.

Four new files, all in `analysis/`: `query.py` (layers 2-3), `statistics.py`
(Pillar II), `settings.py` + `settings_module.py` (the owner).

### What maps onto what

`docs/analysis_pipeline_layers.md` already specified the layering - this is
that document built, not a new design:

| Layer | Where it now lives |
|---|---|
| 1. ROI math (reduction) | `analysis/reduction.py` - already built, stored in `data.h5` |
| 2. Formula spectrum | `query.formula_spectrum` - 4 formulas |
| 3. Metric trace (fit -> peak/centroid) | `query.fit_spectrum` / `metric_value` |
| Pillar II: Statistics | `statistics.py` - spikes, smoothing, baseline, group aggregation |

The ports are genuinely verbatim this time, unlike `tasks.py`'s was: these
were already pure functions over two arrays in `processing/analysis.py` and
`processing/trace_statistics.py`, with no batch-dispatch or caching
entanglement to strip out. The clamps and fallbacks came over with their
reasoning intact, including the polynomial order cap that exists because an
over-fitted curve's edge oscillation once got reported as the peak.

Two small consolidations while porting:

- **One `formula_values`, not a scalar and a vector version.** The old app
  keeps both and has to pin them together with a test so they cannot drift.
  Nothing in the rewrite needs the scalar form - a single wavelength is a
  length-1 array - so there is one implementation and nothing to drift.
- **`apply_statistics` is new.** The stable app applies spike rejection,
  then smoothing, then baseline in that order, but inline in the sensorgram
  controller, so the ordering rule lives in a comment rather than in a
  function. It is now one callable with the reasoning attached: spikes
  first, because smoothing first spreads one bad frame across a window,
  after which the spike filter no longer sees an outlier to reject. There
  is a test that asserts exactly that, by comparing against the wrong
  order.

### Where the settings live - maintainer's decision

A new `AnalysisSettingsModule`, chosen over two alternatives that were put
up with their trade-offs:

- Extending `AreaRoiDetectionSettings` would have been less code
  (`reduction_method`/`formula_key` are already there) but would have put
  sensorgram smoothing and baseline windows inside an ROI-*detection*
  dataclass - regrowing the grab-bag the September decomposition split.
- Panel ownership would have matched the "self-sufficient panels" idea, but
  the Sensorgram's metric is read off the *same fit* the Spectra panel
  configures, so one pipeline would have had two owners that must agree.

**This takes `statistics_settings` off the "no owner, so nothing persists
them" list** it has been on since the session format was built. Session
schema 1.0 -> 1.1, additive: a 1.0 file still loads and both settings groups
fall back to their defaults, which is the correct reading of a session
written before they could be configured at all. There is a test that
downgrades a real file to 1.0 and checks exactly that.

`formula_key` stays where it is, in `AreaRoiDetectionSettings` next to
`reduction_method` - moving it would have been a second change riding along
with this one. Recorded as the one split seam in an otherwise clean
separation.

**Nothing here is undo-tracked**, unlike every other settings module. The
undo stack records changes to the *experiment* - where a ROI is, what the
crop is. These change only how already-measured numbers are displayed, are
one click to put back, and interleaving them would mean Ctrl+Z after
nudging a smoothing window silently rewinds a ROI edit instead. Same
treatment `SelectionModule` already gets.

### A third change category, and why

`AnalysisSettingsChange` is neither cosmetic nor computational, and saying
so explicitly seemed better than forcing it into one:

- *Computational* means stored cells may be stale. These never can - nothing
  they touch is on disk.
- *Cosmetic* means "redraw, the numbers are unchanged". These are not that
  either - switching maximum to centroid changes every plotted value.

So: nothing on disk invalidated, every derived value in memory invalidated.

### The sketch's "computed live" does not survive real scale

Sketch Â§6 says Formula/Fit/Metric are "computed live from whatever's already
in the store". True in principle, unusable as written: 160 ROIs x 300 cubes
is 48,000 fits, and a gaussian `curve_fit` is ~1 ms - ~48 s per redraw, on
the GUI thread, which AGENTS.md forbids outright anyway. So the layer is
pure functions *plus*:

- **A memo cache keyed by a settings fingerprint.** Deliberately not a
  subscription: a changed setting simply misses the cache. A subscription
  would be a second thing to keep in step with the settings that actually
  matter, and forgetting to update it would show a stale plot with no
  error. The one case a fingerprint cannot see is a *recompute* under
  unchanged settings, so `run_analysis` pops that cell's entry as it writes
  it - a single-key pop, never an iteration, since the derived worker may
  be reading the same dict from its own thread.
- **A second `AnalysisWorker`.** `run_analysis` holds the first one for the
  length of a run; a panel asking for a sensorgram trace must not wait for
  an analysis to finish, or get a `RuntimeError` from `submit`.
  `request_metric_traces` drops a request while one is in flight - unlike
  an analysis run, nothing is stored from a display query, so the newest
  answer is the only one that matters.

This follows `docs/rewrite_feature_inventory_2026-09.md`'s own instruction
for this area: "Build the 'is this (ROI, cube, settings) already computed'
answer as one function from day one."

**Pillar II deliberately stays out of the engine.** Smoothing 300 points is
microseconds and the result is purely visual, so it belongs to whichever
panel is displaying - which also keeps group aggregation next to the group
membership it needs from `RoiToolbox`.

**A trace is NaN-padded, never shortened.** Every ROI's trace has to stay
aligned to one x axis for `aggregate_traces` to stack them, and a missing
cube is ordinary (not analyzed, or the fit didn't converge) - dropping the
point would silently shift every later point left.

### Verified

`tests/unit/test_lspri_rewrite_query.py` (24 tests, new, no Qt) and a
`RewriteQueryLayerTest` class in the engine's integration file; 797 LSPRi
tests pass, pyflakes clean.

The formula tests use values worked out by hand from the definitions rather
than copied out of the implementation - otherwise they would pin whatever
the code happens to do, sign error included.

**A test that passed for the wrong reason, caught and fixed**: the first
version of the cache test switched fit method and asserted the metric
changed. It failed, and the failure was the test's fault, not the code's -
this dataset's spectrum rises monotonically to 600 nm, so every fit method
agrees on the peak and a method change cannot distinguish a live cache from
a stale one. Narrowing the fit *window* instead moves the answer whatever
the fit does. Worth recording because the fix is not "loosen the assertion":
the test was measuring nothing, and would have passed just as happily with
the cache permanently stale.

### Not done / still open, as of this entry

- **The panels themselves.** `SpectraPanel` and `SensorgramPanel` are still
  49-85 line stubs whose `_redraw` raises - they now have everything they
  need, which was the point of this piece. The other three panels are
  untouched.
- **No UI exposes any of these settings**, same as the reference-exclusion
  toggle - `AnalysisSettingsModule` is real and persisted, with no panel
  behind it yet.
- The metric cache is unbounded. Fine at a few hundred ROIs x a few hundred
  cubes (one float per cell), worth revisiting only if a dataset makes it
  measurably large - not a guess worth acting on now.
- `image_exclusions` still has no owner (`statistics_settings` now does).
- A metric that needs more than one cube - the correlation-based shift
  `analysis_pipeline_layers.md` mentions as a possible layer-3 method -
  does not fit `metric_value`'s one-spectrum-in signature. Not needed yet;
  flagged because the signature would have to widen, not just gain a key.

## 2026-09-24: GUI shell - real menu bar/status bar, all six panels dock-wrapped

First implementation pass on `docs/rewrite_gui_shell_design_2026-09.md`
(agreed with the maintainer the same day). Previously `WorkflowPanel` was a
bare `QTabWidget` that itself hosted Image/Histogram/ROI-table/Spectra/
Sensorgram as its own tabs - placeholder wiring from the original scaffold,
not the design: per the design doc, those five are each their own dock
widget, and Workflow is docked alongside them (left), not their container.

**Ported `PanelContainer`** (`panels/dock_container.py`, new) from the
*stable* app's `gui/widgets.py` - a `QDockWidget` with a custom title bar
(float/maximize/close, optional help button, optional subtitle) already
proven in production there: real undock/float/resize, a fix for Qt's own
bug where a restored floating panel can land off-screen, and "docking
disabled while floating" so dragging a floating panel over the main window
doesn't trigger an accidental snap-back. Two deliberate trims, not a
verbatim copy this time (both documented in the new file's own docstring):

1. The source's help-button icon falls back to the `lucide` icon library
   through `MainWindowIcons` - a dependency this app's icon policy
   (`packages/lspr_ui/ICONS.md`) avoids. Replaced with the vendored
   `info-circle` tabler icon, rendered through the same inline SVG-to-QIcon
   pattern the class already uses for close/float/maximize.
2. The source's `title_options` clickable-segment title and its
   `_make_chevron_icon`/`_make_apply_icon` helpers were left out - nothing
   in the rewrite's six fixed panels needs a toggleable title yet.
   `_make_pin_icon` was kept despite being unused today, specifically for
   the design doc §4 auto-hide-to-strip work still to come.

**`WorkflowPanel` rebuilt** (`panels/workflow/panel.py`) to match the
design: four stage tabs (Dataset/Image Tools/ROI Selection/Analysis, each a
"not built yet" placeholder for now - real per-stage settings forms are
separate future work, out of scope for this shell-only pass), a real
`stage_changed` signal driven by tab index instead of the scaffold's
logging-only stub, and `set_status()` now actually emits (`status_requested`)
instead of raising `NotImplementedError`.

**`app_rewrite.build_main_window()` rewired**: real `QMenuBar`
(File/Edit/View/Options/Help - only File->Exit is real so far, the rest are
empty placeholders the design doc's later pieces fill in) and `QStatusBar`
(`WorkflowPanel.status_requested` feeds transient messages on the left; the
old "scaffold preview" banner moved from a central widget to a permanent
status-bar label on the right, since the window's central area is dock
widgets now and there's no fixed banner slot left). All six panels wrapped
in `PanelContainer` and docked: Workflow left (sized to 320px via
`resizeDocks`, not a hard clamp - the user can still drag it wider, per the
design doc's "give the user real freedom" principle), Image the main area
with Histogram split below it, ROI table on the right, Spectra/Sensorgram
tabbed together along the bottom. **Explicitly a default arrangement, not a
preset** - the design doc's named, user-editable presets (§5) aren't built
yet; closing a panel via its title bar today has no way back short of
restarting, since the View-menu "show panel" toggle that would fix that is
also §5/§6 territory, not yet built.

**Not done this pass, deliberately** (the rest of the design doc):
presets (apply/save-current/reset, `Ctrl+Shift+1-4`, the auto-apply-on-
stage-change Preferences toggle), theme switching wired into the View menu
(the `apply_app_theme`/`GRAY_DARK_THEME`/`BRIGHT_THEME` infrastructure is
already real, just not reachable from this window yet), pyqtgraph canvas
theming (Image/Histogram/Spectra/Sensorgram backgrounds don't follow the
active theme yet), and the Workflow panel's auto-hide-to-strip collapse
(flagged in the design doc itself as new engineering with no existing
pattern to port).

Verified: pyflakes-clean on every changed/new file. Exercised with real
calls, not just import-checking (`QT_QPA_PLATFORM=offscreen`): all 6 panels
present as docked (non-floating) `QDockWidget`s; the menu bar has the five
expected top-level menus; `WorkflowPanel.setCurrentIndex(2)` emits
`stage_changed(WorkflowStage.ROI_SELECTION)`; `set_status()` reaches the
real status bar's `currentMessage()`. 797 LSPRi tests pass (full LSPRi-
scoped subset, `pytest tests/ -k lspri` - this is a shell/wiring change
touching no scientific-compute code, so the app's own subset was run
rather than the full suite, per the maintainer's usual test-scope
guidance).

## 2026-09-24 (same day, continued): theme switching wired; named panel presets built

Second implementation pass on `docs/rewrite_gui_shell_design_2026-09.md`,
same day as the shell restructure above.

**Theme switching (design doc §6)**: View -> Theme -> Dark/Bright, exclusive
-checkable, in `app_rewrite._wire_theme_menu`. The `GRAY_DARK_THEME`/
`BRIGHT_THEME`/`apply_app_theme` infrastructure was already real
(`packages/lspr_ui`) - what didn't exist was anything in this window
calling it after startup. Switching now does the same three things the
stable app's `MainWindow._apply_theme_styles` already does: the
QApplication-level palette/QSS (`apply_app_theme`), every `PanelContainer`'s
title bar (`refresh_theme()` - baked-in per-widget stylesheets, doesn't
follow QSS), and pyqtgraph canvases (`refresh_theme()` doesn't follow QSS
either). Only `ImagePanel` has a real pyqtgraph canvas today
(`GraphicsLayoutWidget`) - added its `refresh_theme()` and an initial call
from `_build_ui` so it's themed correctly even before any live switch.
Histogram/Spectra/Sensorgram will need the same one-line `refresh_theme()`
once their real plot widgets exist (still `NotImplementedError` stubs) -
`_wire_theme_menu`'s docstring flags this so it isn't rediscovered as a bug
later. **Not persisted across restarts** - no settings/`QSettings` layer in
the rewrite yet, unlike the stable app's `ui/theme` value; flagged rather
than silently limited.

**Named panel presets (design doc §5)**: new `panels/layout_presets.py` -
`LayoutPresetManager` (apply/save-current/reset-to-default against one
`QMainWindow`'s docks) plus `wire_view_menu`, which builds View -> Panel
Presets (the four presets from the design doc's table, `Ctrl+Shift+1-4`,
exclusive-checkable, synced to whichever preset is actually active
including when auto-apply - not a menu click - is what applied it) and the
Options menu's "Automatically Apply Layout Preset on Stage Change" toggle
(off by default, per the design doc's reasoning - see the doc's own record
of that decision). Options menu stands in for a not-yet-built Preferences
dialog - flagged in `wire_view_menu`'s docstring, not silently treated as
the toggle's permanent home.

**Built-in presets are visibility-only, not a hand-authored geometry blob**
- a deliberate scope call made while writing this, not something the design
doc specified either way. Authoring realistic default dock sizes/split
orientation directly in code would mean guessing at numbers nobody has
looked at; instead, applying a never-customized preset just shows/hides
the right docks (per the design doc's table) and leaves whatever geometry
is already on screen alone. The first `Save Current Layout to Active
Preset` upgrades that slot to a real `QMainWindow.saveState()` blob, which
then restores exact geometry, not just visibility - `LayoutPresetManager`
was verified doing exactly this (see below), so the two tiers are already
proven to compose correctly, not just designed to.

**Not persisted across restarts either** - same reason as theme: no
settings layer yet. A saved preset blob only lives for the current run.

**Startup deliberately applies no preset** - all six docks stay visible at
launch, same as before this pass. Auto-applying one at startup while the
auto-apply toggle defaults to off would contradict "manual application
always available, auto-apply is opt-in" (design doc §5) - the first preset
effect only happens when the user acts (a menu click, a shortcut, or
turning the toggle on and then changing a stage tab).

**`Dataset` stage and `Results` preset, both confirmed by construction**:
`STAGE_TO_PRESET` maps `WorkflowStage.DATASET` to the `"Image Tools"`
preset (per the design doc's decision not to give Dataset its own preset)
and has no entry for `"Results"` at all, since no workflow stage
corresponds to it (Spectra/Sensorgram are pure downstream consumers, per
sketch §1) - auto-apply can never reach for Results, matching the design
doc exactly rather than needing a special-case guard.

Verified: pyflakes-clean. Exercised with real calls
(`QT_QPA_PLATFORM=offscreen`), not just import-checking: theme switch
changes `ImagePanel`'s pyqtgraph background color and every `PanelContainer`
survives `refresh_theme()`; each of the four presets shows exactly its
documented panel set; save-current-then-switch-away-then-back restores the
customized (not default) visibility; reset-to-default reverts it; the
auto-apply toggle is confirmed off by default (a stage change does nothing
while off) and, once turned on, a Dataset-stage tab switch applies the
Image Tools preset exactly. 797 LSPRi tests pass (full LSPRi-scoped subset,
`pytest tests/ -k lspri`), same as the previous entry's baseline - nothing
regressed.

**Not done this pass, deliberately** - the last remaining design doc piece:
the Workflow panel's auto-hide-to-strip collapse (§4), explicitly the
riskiest one (the design doc itself leaves "hover-to-peek vs.
click-to-expand" as an open interaction question) - worth a quick check-in
on the exact interaction shape before building it, rather than guessing and
risking a rebuild once the maintainer actually sees it.

## 2026-09-24 (same day, continued): Workflow panel collapse - click-to-expand, not hover-to-peek

Third and last implementation pass on `docs/rewrite_gui_shell_design_
2026-09.md`, same day as the two entries above. The design doc's own open
question (hover-to-peek flyout vs. plain click-to-expand) was put to the
maintainer before building anything - **click-to-expand chosen**,
explicitly for its lower cost: no new overlay/mouse-tracking/z-order
machinery, reuses the dock mechanics already in place from the shell
restructure.

**`PanelContainer` gained an optional `collapsible` flag** (default
`False`, so every panel but Workflow is unaffected). When collapsible, the
title bar gets a chevron-left "collapse" button; `_set_collapsed(True)`
swaps the dock's content for a full-height chevron-right button (the whole
36px-wide strip is clickable, not just a small icon glued to the top -
nothing to miss), swaps the title bar for a bare 1px divider (no room for a
label plus buttons at that width, and the point of collapsing is giving the
click target the full height), and fixes the dock's width via
`setFixedWidth`. Expanding reverses all three and releases the width
clamp - `setMinimumWidth(0)` + `setMaximumWidth(16777215)`, since Qt has no
single call to "unfix" a width once fixed (confirmed by the smoke test
below: Qt's own layout pass then recalculates a real minimum from the
restored content, not literally 0 - the point was only that it's no longer
stuck at 36px, which it wasn't).

**Two correctness traps found and fixed while building this, both from
reasoning about what already exists rather than from a bug report**:

1. **The exact "phantom top-level window" pattern this repo's own
   `CLAUDE.md` documents (Common Pitfalls) - triggered on *swap-out*, not
   construction, so it wasn't obviously the same bug at first glance.**
   `QDockWidget.setWidget(new)` doesn't destroy the widget it replaces - it
   leaves the old one parentless. A parentless widget that is still
   `visible` (the content *was* on screen the instant before collapsing) is
   a real top-level OS window for however long it stays that way, same as
   the documented `LaunchCard` bug, just approached from the opposite
   direction (losing a parent instead of never having one). Fixed by
   explicitly `.hide()`-ing the outgoing widget in the same call that
   displaces it, before the event loop gets a chance to paint anything.
2. **`refresh_theme()` and `_on_top_level_changed` both assumed the normal
   title bar's buttons (`_float_button`, `_maximize_button`) always exist.**
   They don't, while collapsed - the collapsed title bar is a bare divider
   with no buttons at all. `refresh_theme()` now rebuilds whichever variant
   (collapsed strip vs. normal bar) is actually active instead of always
   rebuilding the normal one. `_on_top_level_changed` now returns early
   while collapsed, found by reasoning through a real path that reaches it
   even when collapsed: Qt's native drag-the-title-bar-to-float gesture
   still works on the 1px collapsed title bar (it's a real title-bar area
   as far as Qt's dock machinery is concerned, however thin), so a user
   could float a collapsed panel without ever touching a button - which
   would otherwise hit an `AttributeError` reaching for buttons that were
   never built. Confirmed by exercising exactly that path in the smoke
   test, not just reasoning about it.

Verified: pyflakes-clean. Exercised with real calls
(`QT_QPA_PLATFORM=offscreen`): collapsing sets a 36px fixed width and hides
(not destroys) the content, which survives and is restored correctly on
expand; clicking the collapsed strip's button expands it; a theme switch
while collapsed rebuilds the collapsed strip/divider without crashing or
silently reverting to the normal title bar; floating and re-docking a
*collapsed* panel both work without the `AttributeError` finding 2 above
would otherwise cause; a non-collapsible panel (`Image`) is confirmed
unaffected (`_collapsible=False`, normal title bar, normal float/maximize
behavior throughout). 797 LSPRi tests pass (same subset as the previous two
entries).

**This closes out a first implementation pass on all four pieces of
`docs/rewrite_gui_shell_design_2026-09.md`** (window shell, icon-placement
principle, panel presets, theming). Still open, all already flagged in
their own entries above: exact icon-by-icon placement on Image/ROI-table
panels still needs a real pass once those panels' own toolbars exist;
preset/theme choices still don't persist across restarts (no app-level
settings layer in the rewrite yet); Histogram/Spectra/Sensorgram still need
their own `refresh_theme()` once their real plot widgets are built; a
closed panel has no menu-driven way back yet.

## 2026-09-24 (same day, continued): Workflow panel corrected to match the
## source's real structure (no tabs); the panel's first real content -
## Dataset stage's "Summary" section - built end to end

Fourth pass the same day, following a maintainer request to make the
Workflow panel's visualization match the stable app "as much as possible."

**Tabs removed - the source never had them.** The GUI shell pass above
built `WorkflowPanel` as a `QTabWidget`, one page per `WorkflowStage`. Trying
to actually match the stable app's look found this wasn't the source's
design at all: `gui/layout_builder.py:1411-1429` builds a `QTabWidget` with
exactly *one* tab and calls `tabBar().hide()` - the real UI is one
continuously scrollable page holding all 5 top-level `CollapsibleSection`s
stacked vertically. `panels/workflow/panel.py` now matches that: plain
`QWidget` + one `QScrollArea`, no tab bar.

**Real single-open accordion - the maintainer's explicit deviation from the
source.** The source lets several top-level sections sit expanded at once
(no real exclusivity, despite the accordion look - see the earlier pin-
button finding: no real mutual-exclusion logic exists in the source at
all). Asked directly, the maintainer chose real exclusivity instead:
expanding one top-level section now collapses every other, and collapsing
the only open one snaps it back open rather than leaving nothing expanded
(`WorkflowPanel._on_section_toggled`). This is also what makes `stage_changed`
well-defined now that there's no tab-click to hang it on.

**Dataset stage's "Summary" section built for real** (`dataset_summary.py`,
new) - folder browse/load plus the size/count summary, the first section
to move past a placeholder. Ported from the stable app's
`DatasetController` (`browse_folder`/`load_dataset_from_folder`/
`_on_dataset_loaded`/`_prompt_dataset_candidate_choice`), deliberately
scoped down to just what a "Summary" section owns - not the ~25 other
pieces of window state the source's version also resets in the same method
(record maps, caches, session restore, ROI/mask state, ...), each of which
belongs to whichever *other* rewrite module owns that state and is expected
to subscribe to `DatasetModule.dataset_loaded`/`dataset_cleared` itself.

**`DatasetModule` gained `load_dataset_from_folder()`** (plus
`dataset_load_failed`/`dataset_choice_needed` signals) - the module now owns
the async load command itself, matching how `AnalysisEngine.run_analysis()`
already owns its own `AnalysisWorker`, rather than the UI widget reaching
around it. **One deliberate threading deviation from the source**: the
source runs this on a `QThreadPool`-based `FunctionWorker`
(`gui/worker.py`); this uses `AnalysisWorker` (plain `threading.Thread`)
instead, because `load_dataset` probes for OME-Zarr candidates and
`analysis/worker.py` documents a since-learned, non-negotiable invariant in
this rewrite: never let a `QThreadPool` worker touch, even indirectly, an
OME-Zarr read (root-caused to a native `STATUS_HEAP_CORRUPTION` crash) -
worth copying the source's *behavior*, not its threading primitive.

Verified: pyflakes-clean. Exercised with real calls
(`QT_QPA_PLATFORM=offscreen`), not just import-checking: a real synthetic
TIFF-stack folder loaded end to end through a background thread, into
`DatasetModule`, with `DatasetSummarySection`'s label showing the correct
image/cube/wavelength/size summary; the accordion's exactly-one-open
invariant holds under direct expand/collapse calls; a nonexistent folder
correctly emits `dataset_load_failed` (verified at the `DatasetModule`
level - the widget's own failure path shows a real modal `QMessageBox`,
which headless testing can't dismiss, so that leg was verified by code
inspection instead of execution); the full rewrite window still builds.
797 LSPRi tests pass (same subset as prior entries - unaffected, since
nothing outside `panels/workflow/` and `dataset/module.py` changed).

**Not done this pass, deliberately**: Reference/Export/Metadata (Dataset's
other three nested sections) are still placeholders - next in line, per the
maintainer's explicit "one by one" instruction. The multi-candidate
chooser dialog (`_prompt_dataset_candidate_choice`) is ported and wired but
not exercised end-to-end in this pass (needs a real ambiguous-folder
fixture and a way to click a `QMessageBox` button programmatically -
deferred, not skipped for a substantive reason).

## 2026-09-24 (same day, continued): Dataset stage's "Export" section built;
## "Reference" skipped - it needs state no rewrite module owns yet

Fifth pass the same day, continuing "one by one" through the Dataset
stage's nested sections.

**Export built for real** (`dataset_export.py`, new; `DatasetModule.
export_to_ome_zarr` added alongside it) - destination folder + Export/
Cancel + a live progress bar, backed by `dataset.io.export_ome_zarr_dataset`
exactly as the stable app's own OME-Zarr export uses, again via
`AnalysisWorker` (plain `threading.Thread`) rather than the source's
`QThreadPool`-based `FunctionWorker`, same reasoning as `load_dataset_from_
folder` (zarr writes, not just reads, are exactly the thing the documented
`STATUS_HEAP_CORRUPTION` invariant exists to keep off a `QThreadPool`).
`DatasetModule` gained a *second*, independent `AnalysisWorker`
(`_export_worker`) rather than reusing the load one - a load and an export
are unrelated operations with no reason to serialize against each other,
mirroring `AnalysisEngine`'s own two-worker (`_worker`/`_derived_worker`)
precedent.

**Scoped down from the source on purpose, flagged in the new file's own
docstring rather than silently dropped**: no chunk-size/compression/
shard-mode widgets yet (the module method already accepts them as keyword
options - just not surfaced), no auto-generated descriptive folder name
(`build_ome_zarr_export_folder_name` needs the dataset's shape/dtype
probed first - skipped for this pass), no destination-collision prompt
("this will replace an existing export" - `export_ome_zarr_dataset` itself
still writes safely to a temp sibling and swaps in only on success either
way, so this is a missing confirmation step, not a missing safety net).
**No modal dialog on export failure**, unlike `DatasetSummarySection`'s
load-failure path - a cancelled export reaches `export_failed` through the
same path as a real error (`export_ome_zarr_dataset` raises
`RuntimeError("... cancelled.")` when the cancel event fires, deliberately
not given its own signal - see `DatasetModule.export_to_ome_zarr`'s
docstring), and popping a "failed" dialog in response to the user's own
Cancel click would be bad UX; a later pass could distinguish the two and
only dialog on a real error.

**"Reference" skipped, not just deferred.** Investigated what the source's
Reference section actually does (`gui/main_window.py`: `_set_reference_
mode`, `_auto_reference_image_key_for_spectral_cube`,
`_reference_contrast_score`, ...) before starting to port it: Auto/Manual
selection of one *reference image* (a specific cube+wavelength) - Auto
re-picks the best-contrast wavelength in the current spectral cube live,
Manual locks to whatever's currently being viewed. This needs new state
(the mode, plus the manual key) that **no existing rewrite module owns** -
confirmed by checking, not assumed: `image_tools/chromatic/module.py` has a
same-named `reference_mode`/`reference_wavelength_nm`, but that's a
different concept entirely (chromatic correction's own registration-target
wavelength), and neither `SelectionModule` (current cube/wavelength) nor
`DatasetModule` (candidate records) claims this state today. Building it
properly means deciding *where this state lives* first - a real design
question, not a straightforward port - so it was skipped rather than
guessed at. Metadata (the fourth Dataset child) remains a plain "not
started yet" placeholder, no investigation blocker, just next in the queue.

Verified: pyflakes-clean. Exercised with real calls
(`QT_QPA_PLATFORM=offscreen`): a real synthetic TIFF dataset exported to a
real `.ome.zarr` folder end to end (progress bar updates, destination path
in the finished message matches the actual written folder including the
source's own `.ome.zarr` suffix normalization); exporting with no dataset
loaded fails immediately with `RuntimeError`, surfaced as status text, not
a crash; the full rewrite window still builds. 797 LSPRi tests pass (same
subset as every prior entry this branch - unaffected, since nothing outside
`panels/workflow/` and `dataset/module.py` changed).

**Not done this pass, deliberately**: Reference (see above - blocked on a
state-ownership decision, not effort) and Metadata (Dataset's remaining two
nested sections) are still placeholders.

## 2026-09-24 (same day, continued): Dataset stage's "Metadata" section
## built, read-only - closes out this pass on Dataset's four nested sections

Sixth pass the same day. **Dataset's nested-section tree is now
Summary/Export/Metadata real, Reference deliberately skipped** (see the
previous entry) - closing out this pass on the Dataset stage before moving
to the next one, per the maintainer's "one by one" instruction.

**Metadata built read-only, on purpose** (`dataset_metadata.py`, new) -
shows whatever `DatasetModule.acquisition_metadata()` already has
(source format, operator, start time, wavelength/timing/comment counts,
notes), live on `dataset_loaded`/`dataset_cleared`. **Not** the source's
import/export buttons or Cube/Time display toggle - investigated first,
same as Reference: `DatasetModule` has no command to *attach* metadata
after a dataset is already loaded (only `dataset.io.load_dataset` reads it,
automatically, at load time), so a real "Import" button needs that command
added first, not just a widget; the Cube/Time toggle is view-wide display
state relabeling a slider several *other* panels share, with no obvious
owner among the rewrite's modules yet, same unresolved-ownership shape as
Reference. Export (of metadata specifically, not OME-Zarr - Dataset's
separate Export section already covers that) wasn't investigated this pass
at all. All three flagged in the new file's own docstring rather than
silently dropped. What *is* real: this needed no new module state at all,
since the data was already there - showing it live is legitimate progress
even without the write-side actions.

Verified: pyflakes-clean. Exercised with real calls
(`QT_QPA_PLATFORM=offscreen`): a real synthetic dataset with real legacy
`measureing_times.csv`/`metaData.txt` sidecar files loaded end to end,
correctly parsed into operator/start-time/wavelength-count/timing-count/
comment-count and shown in the section; clearing the dataset resets the
label; the full rewrite window still builds. 797 LSPRi tests pass (same
subset as every prior entry this branch).

**Not done this pass**: Reference remains skipped (state-ownership
question, see above). Image Tools/ROI editor/Analysis/Outputs are still
entirely placeholders - next stage in the "one by one" queue.

## 2026-09-24 (same day, continued): Image Tools stage's "Background
## removal" section built - the first section using the apply toggle for real

Seventh pass the same day, moving to the Image Tools stage.

**Background removal chosen first among Image Tools' four children**,
deliberately, not just next-in-list: Transforms (crop/rotate/flip) and
Chromatic correction both need either a canvas/toolbar this rewrite
doesn't have yet (Image panel's own toolbar - design doc §3's icon-
placement split puts the actual crop/rotate/landmark-placement controls
there, not in Workflow) or live interaction Mask also partly needs
(painting/drawing). Background removal's whole settings surface -
sigma/binning/exclusion/local-reference - is plain form controls with no
canvas dependency, and `BackgroundModule` (real since 2026-09-21) exposes
it as one command, `set_flatten_background_settings`. Checked this before
starting, not assumed.

**`background_removal.py`, new** - sigma spin (3-2000 px)/binning combo
(1x1/2x2/4x4)/ignore-ROI+ignore-mask checkboxes/dilation spin (0-100 px)/
local-reference checkbox, every change live-pushed to the module in one
call (matching the source's own `_update_image_processing_settings`
pattern - every widget's signal already fires on every change there too).
Two deliberate simplifications from the source, flagged in the file's own
docstring: plain `QCheckBox`es instead of the source's icon-toggle buttons
(same function, no vendored exclusion-icon rendering needed); no "Profile"
button or background-*image* file controls (a real-pixel background
estimate is a different feature from this formula-settings form - see the
architecture sketch's estimation-vs-application split).

**First real use of `CollapsibleSection`'s apply toggle** (built
2026-09-24, unused until now - every other real section so far had nothing
to attach it to). Two-way, wired in `panel.py._build_image_tools_section`:
the header toggle drives `BackgroundRemovalSection.set_enabled()` (which
still pushes every current form value, not just `enabled` - matches the
module's one-call API); a `background_model_changed` from *outside* this
section (e.g. a future session restore) syncs the header back via
`set_applied()` with signals blocked, so that sync doesn't loop back into
another push - the exact `blockSignals`-around-`set_applied` shape the
source's own `_set_section_applied` uses (`gui/main_window.py:5233`,
noted when it was first read during the earlier `CollapsibleSection` port).

Verified: pyflakes-clean. Exercised with real calls
(`QT_QPA_PLATFORM=offscreen`): changing the sigma spinbox and the
ignore-mask checkbox both reach `BackgroundModule.settings()` correctly;
`restore_settings()` (module -> widget direction) updates every form
control without re-triggering a push back (no feedback loop); the header
apply toggle disables background removal in the module when unchecked, and
correctly re-syncs when the module's settings change from outside the
toggle; the full rewrite window still builds. 797 LSPRi tests pass (same
subset as every prior entry this branch).

**Not done this pass**: Transforms/Mask/Chromatic correction (Image Tools'
other three children) are still placeholders - Mask is the most likely
next candidate (most of its controls are also plain forms; only "Drawing"
needs canvas interaction), Transforms/Chromatic correction more likely
wait on the Image panel's own toolbar existing first.

## 2026-09-24 (same day, continued): Image Tools stage's "Mask" section
## built - tool-tuning numbers only, and a real cross-concern fix found
## along the way

Eighth pass the same day, continuing into Image Tools' Mask child.

**`mask_settings.py`, new** - relative-threshold/relative-profile-sigma/
local-contrast-sigma/local-contrast-z/morphology-radius/brush-size, all
live-pushed to `MaskModule.set_tool_settings` on every change, same shape
as Background removal. Relative threshold is shown as a percentage
(0.1-500.0%, matching the source's `mask_relative_threshold_spin`) and
divided by 100 on push, since `MaskSettings.relative_threshold_fraction`
stores the raw fraction.

**Real finding while reading the source's equivalent spinboxes**: in the
stable app, `mask_relative_profile_sigma_spin`/`mask_local_contrast_sigma_
spin`/`mask_local_contrast_z_spin` each feed *two* different concerns from
one shared widget - ROI detection (`_update_roi_detection_settings`,
writing into `window._state.area_roi_settings`) *and* the mask preview
(`_refresh_mask_previews`) - exactly the cross-concern entanglement this
rewrite exists to undo (`docs/rewrite_feature_inventory_2026-09.md`,
`lspri_entanglement_diagnosis_2026_09` memory). `MaskModule.
set_tool_settings` is a clean, Mask-only command; `RoiToolbox.
detection_settings()` is a separate, already-distinct read
(`analysis/engine.py`'s `_build_analysis_engine` already treats them as
two unrelated inputs). This section only calls the former - if ROI
detection ever needs the same numbers, that has to be a deliberate choice
by whoever wires it, not a rediscovery of the old shared-spinbox accident.

**No apply toggle**, unlike Background removal - checked `MaskSettings`
before assuming one belonged: there's no `mask_enabled`-shaped boolean
field. Masking's real on/off state is "does the timeline have any
committed `MaskChange`", not a settings flag - these tunables are inputs
to a not-yet-built "apply" action.

**Histogram-range passthrough, verified explicitly, not just assumed
correct**: `set_tool_settings` takes `histogram_min_value`/
`histogram_max_value` as part of its one combined call, but this form has
no widgets for them (they belong to a not-yet-built Histogram-panel
selection, a different feature from `histogram_highlight_min_value`/
`histogram_highlight_max_value`, which `set_histogram_highlight_range`
owns separately - confirmed by reading both methods, not assumed from the
similar names). Every push reads the module's *current* values for these
two fields first and passes them straight through unchanged, so tuning a
slider can never silently clobber a selection this form doesn't even show.
An initial ad-hoc check of this used the wrong field name and looked like
a bug for a moment (`histogram_min_value` vs. `histogram_highlight_min_
value`) - re-verified against the right one before trusting it.

Verified: pyflakes-clean. Exercised with real calls
(`QT_QPA_PLATFORM=offscreen`): changing the relative-threshold and
morphology-radius controls both reach `MaskModule.settings()` correctly
(threshold converted to/from a fraction correctly); `restore_state()`
(module -> widget direction) updates every form control without
re-triggering a push; `histogram_min_value`/`histogram_max_value` survive
an unrelated form push unchanged, confirmed with a real non-`None` seeded
value; the full rewrite window still builds. 797 LSPRi tests pass (same
subset as every prior entry this branch).

**Not done this pass**: Transforms/Chromatic correction (Image Tools' last
two children) are still placeholders - both more likely wait on the Image
panel's own toolbar/canvas existing first, per the design doc §3 split.
Mask's actual Apply/Reset/Show candidate-computation actions remain
unbuilt too (see `mask_settings.py`'s own docstring) - this pass is tuning
numbers only.

## 2026-09-25: Dataset section rebuilt for real visual/behavioral fidelity
## - folder row split out, real free-standing icons, real Summary layout

Ninth pass, first on a new day. Maintainer asked to go back and match the
stable app's Dataset section in real detail - icons, layout, behavior -
rather than the functionally-equivalent-but-visually-flat version built
2026-09-24. Investigated the source's actual widget construction
(`gui/main_window.py`/`main_window_icons.py`/`layout_builder.py`) before
touching code, not assumed from memory - found three things worth
recording for future sections too:

1. **A shared "free standing" widget family** (the source's own naming):
   `ClickableIconLabel(QLabel)` - a five-line class, plain label + a
   `clicked` signal from `mousePressEvent`, no button chrome at all - plus
   two checkable subclasses (icon swaps on toggle / text itself swaps on
   toggle) not ported yet, no consumer needs them today.
2. **A second, related pattern** for bracketed cycling toggles
   (`[Cube]`/`[Time]`, `[λ,t]`/`[λ]`, `[disk]`/`[RAM]`) - non-checkable
   `QToolButton`, text-only, click cycles state, gold when active/dim when
   not, underline on hover, a `.sync_appearance` callback stashed on the
   button for external refresh. Not needed by anything built yet (first
   candidate would be Metadata's Cube/Time toggle, still out of scope - see
   that section's own state-ownership note).
3. **`OmeZarrExportSummary.field_lines()`** (`dataset/io.py`) already
   returns the exact (label, value) pairs the source's OME-Zarr summary
   block shows - reused directly instead of re-deriving the same
   formatting a second time, once found.

**Folder path field + browse/explorer icons moved out of Summary**
(`dataset_folder_row.py`, new) to their own top-level row, matching the
source's actual placement exactly: `top_row_widget` sits *above* the
nested Summary/Reference/Export/Metadata sections in `dataset_inner`, not
inside any one of them. Now uses real chrome-less icons
(`free_standing.py`, new - `ClickableIconLabel` ported) - tabler
`folder-search` (blue `#38bdf8`) for browse, `folder-open` (amber
`#f59e0b`) for open-in-explorer - replacing yesterday's plain
`QPushButton`s. All the load/candidate-choice orchestration moved here
unchanged; this widget now only shows transient load-status text, not the
dataset's stats.

**Summary rebuilt as real display data** (`dataset_summary.py`, rewritten)
- two real surfaces, matching the source:
- A compact title-row readout (`header_stats_label`, passed as the
  `CollapsibleSection`'s `header_extra`) - **Size/Cubes/Wavelengths only**,
  Images deliberately dropped per the maintainer's explicit call ("doesn't
  matter" for the title row).
- The expanded body, "same as in stable version" per the maintainer -
  paired Images+Size / Cubes+Wavelengths rows, Resolution (read from the
  first image file), Dataset's date (earliest file's mtime) - Images
  *is* shown here, only the title row omits it.
- A conditional OME-Zarr block, hidden entirely for a plain TIFF stack,
  populated from `read_existing_ome_zarr_summary(...).field_lines()` when
  the loaded dataset is a real OME-Zarr export: image size, cube x
  wavelength count, chunk/shard, compression, dtype, image-tools-applied
  (+ rotation/flip/crop only if tools were actually applied at export
  time), pixel size, source folder.

Verified: pyflakes-clean. Exercised with real calls
(`QT_QPA_PLATFORM=offscreen`), two datasets: a plain synthetic TIFF stack
(confirmed title row reads "Size: ..., Cubes: 1, WL: 2" with no Images
figure, body has all six fields, OME-Zarr block correctly hidden) and a
real OME-Zarr export of that same data through `DatasetModule.
export_to_ome_zarr` (confirmed the OME-Zarr block becomes visible and its
seven lines - image size, cube x wavelength, chunk/shard, compression,
dtype, image-tools-applied, source folder - all read correctly from the
real written export); the full rewrite window still builds. 797 LSPRi
tests pass (same subset as every prior entry this branch).

**Not done this pass**: Reference, Export's and Metadata's own icon/layout
fidelity (still last session's simplified forms - Export needs the
chunk/compression/shard tuning surface and live estimates, Metadata needs
its import/export icon buttons and the Cube/Time toggle), and the two
checkable "free standing" widget variants (no consumer yet). Next in the
maintainer's "section by section" plan.

## 2026-09-25 (same day, continued): Reference replaced with a compact row;
## Metadata renamed "Experimental plan", moved first, given real import/
## export and a live comment/step preview

Second pass this session, maintainer's explicit spec for both changes
rather than a straight port.

**"Reference" is no longer a standalone section.** Replaced with a
"Define reference frame:" row (`reference_frame_row.py`, new) sitting
alongside the folder row, outside the nested accordion - real Auto/Manual
icon toggles (`QButtonGroup`, tabler `robot`/`manual-gearbox`, lime
`#84cc16` when active - the exact color the source hardcodes, no
`lspr_ui` theme token for it) plus a live `[Ref.frame: Cube #, WL #]`
readout.

**New module: `ReferenceFrameModule`** (`selection/reference_frame_
module.py`) - not part of the original sketch's module list, added
because nothing else owns "which frame is the reference" (checked, not
assumed - see the 2026-09-24 Export-section entry's finding that
`ChromaticModule`'s same-named `reference_mode` is a different concept).
Deliberately narrow per AGENTS.md's module-boundary rule: it holds no
`SelectionModule` reference and cannot resolve "Auto" mode's frame by
itself - the row widget reads `SelectionModule.current_cube()`/
`current_wavelength()` directly for Auto, and calls `set_manual_frame()`
with an already-resolved cube/wavelength for Manual, the same
"commands take already-resolved values" convention `MaskModule.
apply_candidate` established. **Scoped down from the source's "Auto"
mode, flagged in the module's own docstring**: the source picks the
best-contrast wavelength in the current cube (real pixel loading +
scoring for every candidate); this rewrite's Auto simply mirrors whatever
`SelectionModule` currently shows, live - no scoring algorithm, revisit
once a real image canvas exists to judge the difference against. Wired to
reset (back to Auto, manual snapshot cleared) whenever the dataset loads
or clears, in `app_rewrite.build_main_window` - a manual reference must
never silently point at a frame from an unloaded dataset.

**"Metadata" renamed "Experimental plan", moved first among Dataset's
nested sections** (`dataset_experimental_plan.py`, new, supersedes and
deletes `dataset_metadata.py`). Real import/export this time, not
read-only: a file-path field + Import/Export icon buttons in one row,
ported from the stable app's `MetadataController`
(`gui/metadata_controller.py`). `io/metadata_import.py`'s
`import_metadata_files` turned out to be pure, Qt-free classify-then-
import logic with zero GUI dependency - reused directly, nothing to
strip, the same way `dataset/io.py` already reaches into the shared
(not-yet-relocated) `io/legacy_metadata.py` for legacy parsing.

**`DatasetModule` gained three things** for this: `set_acquisition_
metadata()` (the missing "attach after load" command flagged in the
2026-09-24 Metadata entry - also persists the `analysis/acquisition_
metadata.json` sidecar, matching the source's import behavior, and
re-emits `dataset_loaded` rather than a new signal since every existing
subscriber already reacts correctly to the same mutated object),
`rehydrated_acquisition_metadata()` (expands timing back to real
per-frame entries - needed by both metadata export, which must not
silently lose per-image timestamps to compaction, and the live preview
below, which needs `timing_for()` to actually find entries), and
`dataset_home()` (a narrow folder-path query for file-dialog defaults and
the sidecar path - not "dataset state" in the sense the module-boundary
rule protects, just a folder path).

**Live comment/step preview, linked to `SelectionModule`** - the
maintainer's actual phrase was "linked preview of comments and step based
on actually previewed image." Turned out to be one existing mechanism,
not two: a pump-plan "step" and a "comment" are both
`ImagingAcquisitionMetadata.comment_events` (`lspr_core.imaging_models.
ImagingCommentEvent`'s own docstring confirms a legacy CSV's per-row
"Note pump plan" column imports into this same sparse transition log).
Resolved via `timing_for(cube, wavelength)` -> `acquired_at_unix_ms` ->
`comment_at(...)`, refreshed on `SelectionModule.cube_changed`/
`wavelength_changed`.

**One honest gap, flagged rather than faked**: the file-path field only
ever shows a path after an explicit Import (or Export) this session - it
does not attempt to show whichever file `dataset.io.load_dataset`
auto-discovered at load time, since that path isn't tracked anywhere and
re-deriving it would risk showing the wrong file if discovery logic ever
changes.

Verified: pyflakes-clean. Exercised with real calls
(`QT_QPA_PLATFORM=offscreen`): the reference-frame row's Auto mode tracked
a real `SelectionModule` cube/wavelength change live; clicking Manual
correctly snapshotted the then-current frame and stopped tracking further
selection changes; clicking Auto again resumed live tracking. For
Experimental plan: loaded a real dataset with real legacy sidecar files,
confirmed the live preview showed the correct per-wavelength pump-plan
note at WL470 ("Pump is not running") and WL480 ("Pump is running");
exported the loaded metadata to a real JSON file and re-imported it,
confirming the round-trip preserved the operator field exactly; the full
rewrite window still builds. 797 LSPRi tests pass (same subset as every
prior entry this branch).

**Not done this pass**: the multi-candidate import path (mixing a legacy
CSV with a native file, etc.) wasn't exercised end-to-end, only the
single-file JSON round-trip - `import_metadata_files` itself is ported
unchanged and already has its own real notes/skip logic, so this is a
coverage gap in verification, not a known bug. Export's/Summary's icon
fidelity from two entries ago remains open too.

## 2026-09-25 (same day, continued): three real width overflows found and
## fixed against the Workflow panel's fixed 340px width; a permanent
## automated check added so this stops being a per-section manual step

Third pass this session. Maintainer noticed (correctly) that fixing the
Workflow panel's width (§4, 2026-09-24) creates an ongoing obligation
every section built since then has been silently exposed to: a fixed-
width dock can't grow to accommodate a row that's actually wider than it
- unlike the *display* panels, which the design doc's own "give the user
real freedom" principle explicitly exempts this one from. Asked directly
whether this had already gone wrong and, separately, whether there's a
way to stop it recurring - both real, useful questions, answered by
actually measuring rather than eyeballing the code.

**Measured every section's `minimumSizeHint()` inside the real, fixed-
width dock (`QT_QPA_PLATFORM=offscreen`, not a visual check)** - found
three real overflows, all invisible from reading the source: `Reference
FrameRow` (~650px, later ~392px after a first fix, still over), `Mask
SettingsSection` (~414px), `BackgroundRemovalSection` (~506px), against a
~320px usable budget (340px dock minus headroom for a vertical scrollbar
that only appears once content overflows the window height - a widget
has to already fit before one is ever visible, or everything shifts the
moment it appears).

**Root cause, both forms**: `QFormLayout`/`QHBoxLayout` rows whose label
text is more than a couple words - "Local contrast sigma", "Local
reference normalization", "Define reference frame:" - request whatever
width the label+field naturally need, with nothing to stop that from
exceeding what the fixed-width dock actually has. Qt doesn't clip or warn
about this; the dock (or its content) is simply forced wider than 340px,
which a real user would see as the panel refusing to actually stay
fixed-width, or content getting cut off - exactly the maintainer's "adjust
the width... or put them on other row" framing.

**Fix, three tiers, from most to least reusable**:
1. **`form_rows.py`'s `stacked_field()`, new** - label directly above the
   field instead of beside it (a `QFormLayout` row's width is
   label-width + field-width; a stacked row's is `max(label-width,
   field-width)`, almost always much less). Applied to Mask
   (`mask_settings.py`) and Background removal (`background_removal.py`),
   replacing their `QFormLayout`s outright - this is the reusable piece
   future sections should reach for first, not a one-off patch.
2. **Word-wrap on labels showing unbounded live data**
   (`reference_frame_row.py`) - the `[Ref.frame: Cube #, WL #]` readout
   still overflowed even after switching to a 3-row layout and bounding
   the wavelength to one decimal, because a large spectral-cube index (a
   real possibility for a long time-series dataset - tested with 4 digits)
   makes the text long regardless of formatting. `setWordWrap(True)` is
   the robust fix - it grows down instead of sideways no matter how wide
   the numbers get, rather than chasing "is this text short enough" for
   every possible value.
3. **Abbreviated one specific label** ("Local reference normalization" ->
   "Local reference norm.", full wording moved to the tooltip) where
   neither of the above applied cleanly - a checkbox's own text has
   nowhere to wrap to without the checkbox itself moving.

**Permanent automated check added**, directly answering "how do I not go
wrong again": `tests/test_lspri_workflow_panel_width_budget.py` (repo
root `tests/integration/`) builds the real rewrite window, measures every
`CollapsibleSection`'s `minimumSizeHint()` against the same 320px budget,
and separately injects a large cube index + imprecise wavelength into
`ReferenceFrameRow` to catch dynamic-content overflow a static layout
check alone would miss (the row starts out showing short placeholder
text, so a plain pass/fail on initial state wouldn't have caught the bug
that was actually found). Runs as part of `pytest tests/ -k lspri` - no
longer a manual "remember to measure this" step; a future section that
overflows fails the suite the same way a broken import would.

Verified: pyflakes-clean. The three new/updated width-budget tests pass
individually and as part of the full LSPRi-scoped subset; re-measured
every section headlessly after each fix (not just re-read the code) -
confirmed all 17 `CollapsibleSection`s now fit the 320px budget, including
under the worst-case cube/wavelength values that exposed the reference-
frame row's remaining gap after the first, incomplete fix; the full
rewrite window still builds. 797 LSPRi tests pass (same subset as every
prior entry, plus the 3 new width-budget tests).

**Not done this pass**: the width budget (~320px) is a conservative
estimate reasoned from the 340px dock minus scrollbar headroom, not
independently confirmed against Qt's actual scrollbar width on this
platform/style - close enough to have caught three real bugs, but worth
tightening with a real measurement if a section ever passes this check
and still looks tight in practice.

## 2026-09-25 (same day, continued): nested-section left-indent dropped

Small follow-up to the width-budget pass. `_nested_children` (`panel.py`)
indented nested sections 16px left, matching the source's
`_nested_section_group` exactly - maintainer's call: the dimmed title
color (`_nested_title_color`) already distinguishes nesting depth on its
own, and every pixel counts against the fixed-width budget just measured.
Margin changed from `(16, 2, 0, 2)` to `(0, 2, 0, 2)`; no other change.
Verified: pyflakes-clean, full window still builds, 800 LSPRi tests pass
(the width-budget tests included, unaffected - they measure width against
budget, and this change only makes things narrower).

## 2026-09-25 (same day, continued): Export section rebuilt with real
## chunk/shard/compression/skip-excluded controls; a real blind spot found
## in yesterday's width-budget test and fixed

Fourth pass this session. Maintainer's spec: copy the source's Export
settings for real (not just a bare destination field), Chunk size and
Shard each their own row, other info underneath them, and the Export
trigger as a labeled button placed *after* all the settings - the
source's icon-only button sits first, above them.

**`dataset_export.py` rebuilt** - Chunk size (4-4096px) and Shard
("1 image"/"1 spectral cube") each via `form_rows.stacked_field` (own
row, label above field), then live chunk/total read-time estimate labels
underneath (`dataset.io.estimate_ome_zarr_export_chunk_plane_read`/
`..._dataset_total_read` - pure, cheap functions, called with
`calibration=None` rather than running the source's real disk-timing
probe, so still-real chunk counts with no estimated milliseconds),
Compression/Skip-excluded checkboxes, then the Export/Cancel buttons
last. **No new `DatasetModule` surface needed** - width/height/dtype for
the descriptive folder name (`dataset.io.build_ome_zarr_export_folder_
name`, the same naming scheme the source uses) come from one real plane
loaded via the already-existing `load_plane()`, cached on `dataset_
loaded` rather than re-probed on every chunk-size edit.

**Scoped down from the source, flagged in the file's own docstring**: no
name-prompt dialog (defaults to the dataset's own folder name - what the
source's prompt defaults to anyway), no destination-collision comparison/
replace dialog (`export_ome_zarr_dataset` already writes safely to a temp
sibling regardless, per its own docstring - a missing confirmation
prompt, not a missing safety net), no plan-confirmation dialog.

**A second real blind spot found in yesterday's width-budget test,
fixed**: `test_every_collapsible_section_fits_the_width_budget` measured
every section in whatever state it happened to start in - and Export
starts *collapsed* (`expanded=False`). A collapsed `CollapsibleSection`'s
`minimumSizeHint()` turned out not to reliably reflect its real content
width: the rebuilt Export form's first version (before catching this)
measured ~140px collapsed vs. its real ~392px once actually expanded, all
from one checkbox label ("Compression (lz4 + bitshuffle)") that had never
been laid out at real size while hidden. Fixed the test itself, not just
the one section: it now force-expands every top-level stage in turn (they
stay a real single-open accordion - expanding one collapses the others,
so this can't be done all at once) and every nested child within it,
before measuring anything. **Verified this actually closes the gap, not
just asserted it**: reverted the checkbox-label fix, confirmed the
improved test now fails with the real 392px measurement, then restored
the fix and confirmed it passes again - the same "prove it, don't assume
it" standard the earlier width-budget entry itself applied.

Verified: pyflakes-clean. Exercised with real calls
(`QT_QPA_PLATFORM=offscreen`): loaded a real synthetic dataset, confirmed
the chunk-estimate/total labels update live and correctly on a chunk-size
change; exported it for real, confirming the destination folder name
matches the source's exact naming scheme (`<name>_<w>x<h>_<cubes>x
<wavelengths>_c<chunk><shard><compression>_<dtype>.ome.zarr`); the fixed
test genuinely catches a real overflow (see above) and passes once fixed;
the full rewrite window still builds. 800 LSPRi tests pass (unaffected
count - this pass touched one existing file plus the test file, no new
Workflow-panel section added).

**Not done this pass**: the name-prompt/collision/confirmation dialogs
listed above as scoped out. Calibrated (not just chunk-count) read-time
estimates remain future work, same reasoning as the scope-out.

## 2026-09-28 - Transforms: crop, reset-crop, flip icons

Extended `panels/workflow/transforms_settings.py`'s icon row (rotate /
reset rotation / rotation fill) with a crop group and a flip group: crop
tool toggle (tabler `crop`, sky blue when active), **reset crop** (the crop
glyph plus the same diagonal slash as reset rotation - shared
`_slashed_icon` helper, one extra `<path>` injected into the vendored SVG),
and checkable flip H / flip V (tabler `flip-horizontal`/`flip-vertical`).
Reset crop calls `GeometryModule.clear_crop()` and is disabled when no crop
is set; flips call `set_flip()`; all buttons re-sync from `geometry_changed`
so undo/redo/session restore update them. Row is ~252px min width (budget
320). **Crop tool toggle is not wired to a canvas box-drag yet** - it only
emits `crop_tool_toggled`, same as the rotate tool (Image panel has no
canvas interaction yet). Measure controls still to do.

Verified: pyflakes-clean; headless exercise (flip toggles, crop set/reset,
undo-path re-sync) passed; width-budget test passes.

## 2026-09-28 - Rotate-by-line tool, ActiveTool, controls table

Migrated/rewrote the stable rotate tool. Primary workflow is now IrfanView-
style: activate Rotate, click two points that should be level, the image
rotates by the smaller of the two solutions. The stable app's arrow keys are
kept (0.1 deg, Ctrl 1, Shift 5; Left/Down negative).

**Gestures (maintainer's spec):** LMB click = point 1, next LMB click = point
2 and apply; RMB click cancels point 1 (Esc also does); a dashed rubber-band
line follows the cursor after point 1, with a live "line is X deg from
horizontal - clicking rotates by Y deg" readout. No button is held or dragged.
MMB-drag pans, wheel zooms; **LMB-drag and RMB-drag are switched off**
(`ImageViewBox`) so a shaky click never pans/zooms. Only LMB selects ROIs now
(a middle click used to clear the selection). All of this is one table in
`panels/image/image_controls.py` - the help text and the behavior come from
the same place; extend that table when a tool claims a gesture.

**New pieces**
- `image_tools/geometry/alignment.py` - pure angle math. **Sign verified
  against the real `apply_spatial_preprocessing`, not assumed**: adding +tilt
  (tilt positive = slopes down-right on screen) to `rotation_angle_deg`
  levels a line; with exactly ONE flip on (H xor V) the displayed tilt is
  mirrored so the correction is negated; both flips = 180 deg turn, no
  negation. Tests: `tests/unit/test_lspri_rewrite_rotation_alignment.py`
  (draws a line of known tilt, corrects, measures - all flip combinations
  and an already-rotated start).
- `image_tools/active_tool.py` - `ActiveToolModule`/`ImageTool`: the shared
  "which canvas tool is on" state (rotate/crop/measure, at most one). Replaces
  the stable `window._active_tool` string. Transient UI state on purpose: not
  undoable, not persisted, not a change event. The Transforms buttons drive it
  and follow it; the Image panel routes clicks/keys by it.
- `panels/image/rotate_line_tool.py` - the two-click state machine + rubber
  band. It only ever calls `GeometryModule.set_rotation` (one undo step per
  result, redraw through the usual signal). **Two-click results are rounded to
  0.01 deg** (maintainer's decision): max rounding error 0.005 deg = ~0.2px at
  2500px from the centre, well under the method's own click uncertainty (1px
  over a 1000px baseline = 0.057 deg). Arrow steps (0.1/1/5 deg) are not
  rounded that way, only cleaned to 1e-6 deg against float noise.

**Crop while rotating (maintainer's spec):** rotation is applied before
flip before crop (already the order in `transform.py`). While Rotate is
active the panel renders the image **uncropped** and draws the existing crop
as a fixed dashed outline; the crop is untouched and re-applied on exit.
Caveat known/accepted: crop is stored in pixel coordinates of the rotated
canvas, which changes size with the angle (`reshape=True`), so a fixed crop
box covers slightly different content after a rotation (a few px for
alignment-sized angles). **ROI overlay is hidden while a preview tool is
active** - ROI coordinates are in cropped/processed space and would be drawn
in the wrong place over the uncropped preview.

**Not done:** crop *tool* (box drag) - its button already takes part in the
ActiveTool exclusivity but has no canvas behavior, so it neither shows a hint
nor blocks ROI selection; measure tool; chunk-grid preview is still drawn
against the uncropped image size while rotating (cosmetic).

## 2026-09-28 - ROI/mask remap on rotation/flip/crop (closes the "known gap")

Implements the maintainer's design decision from the same-day discussion:
existing ROIs (and freeform ROI masks) now move with the image when
rotation/flip/crop changes, instead of silently going stale
(`docs/image_tools_coordinate_spaces.md`'s long-standing gap). Verified
math two independent ways before writing any dispatch code - see below.

**`image_tools/geometry/transform.py`: `combined_geometry_affine_xy`/
`remap_point_for_geometry_change`** - the old-processed -> raw -> new-
processed point map, composed from the existing (already-trusted)
`combined_transform_for_box`. The new-side inverse is its own transpose
(rotate+flip is always orthogonal - a product of orthogonal matrices stays
orthogonal), never a general matrix inverse. **Verified two ways, not just
derived**: (1) against `apply_spatial_preprocessing` itself - planted a
point, rendered under old/new settings, compared - agreement within
~0.22px (resampling/centroid noise) across rotation/flip/crop/refine
combinations; (2) a pure round-trip with zero image/interpolation
involved (point -> raw -> new -> raw) matching the original to 1e-14, i.e.
float noise, not approximation error.

**`roi/rasterize.py`: `remap_roi_mask`** - the freeform-mask counterpart,
reusing `_mask_reach_box`/`_warp_roi_mask_into_box` (built for the
*chromatic* affine) with a geometry-derived matrix instead - zero new
raster-warp code. Verified pixel-for-pixel against `apply_spatial_mask`
(already relied on elsewhere) for a real rotation: 0 mismatched pixels out
of 800. **Finding, not assumption**: empirically confirmed (200 randomized
rotate/flip/crop combos, plus a full angle sweep on a 1x1 mask) that a
non-empty mask cannot actually warp to zero area under this pipeline -
rotate/flip are orthogonal, crop only ever contributes a translation, so
the composition is bijective and area-preserving. The `None`-on-empty
return is kept as a cheap defensive guard, not a real code path today -
documented as such rather than oversold. Also found and fixed my own
mistake here: a crop offset does **not** bound the warp's own box math (it
only shifts an origin), so a first draft that clamped the reach box to
`raw_shape` was a dimension-mismatched no-op at best - removed; a box
outside the visible image is left for the point of use to clip, matching
`_blit`'s existing "clip, don't crash" convention.

**`roi/toolbox.py`: `RoiToolbox.remap_all` + `_remap_roi_shape`** -
`detect_rois`'s bulk-command shape (snapshot old state, mutate, one undo
entry via `undo_manager.push`), dispatching **per ROI side by geometry
type** (`sample_geometry_type`/`reference_geometry_type`), per the
maintainer's explicit ask ("distinguish which sub-pipe to use for
different geometries", not a circle-only special case):
circle/annulus -> center point only (radii never scale in this pipeline);
mask -> `remap_roi_mask`; rectangle/polygon -> explicit `NotImplementedError`-
documented branch, reported in `unsupported_shapes_skipped` - visible now,
not a silently-discovered gap once those shapes exist later. Also remaps
`per_wavelength` manual overrides (same processed-space convention as
`center_x`/`center_y`, would otherwise go stale identically). Takes
ready-made `remap_point`/`remap_mask` callables rather than a raw shape +
before/after settings - same one-directional convention `display_position`
already uses for Chromatic; this module still has zero import of
`image_tools.geometry`.

**`roi_geometry_sync.py` (new top-level module, deliberately not inside
`image_tools/` or `roi/`)** - `RoiGeometrySync`: listens to `GeometryModule.
geometry_changed`, remaps only on `"rotation"`/`"flip"`/`"crop"` (skips
`"image_tools_enabled"`/`"rotation_fill"` - neither moves a coordinate -
and `"session_restored"` - a loaded session's ROIs are already consistent
with its own saved geometry; remapping them again would corrupt them).
Keeps its own "last known settings" + cached raw shape (from the new
`DatasetModule.raw_plane_shape()`, a header-only read), re-baselined on
dataset load/clear so a dataset switch never diffs against the previous
dataset's geometry. Emits `status_changed` -> `status_bar.showMessage`
(wired in `app_rewrite.py`, next to `ActiveToolModule`).

**One undo step for a rotation and the ROI shift it causes**
(`panels/image/rotate_line_tool.py`'s `_rotate_by` now wraps its
`set_rotation` call in `undo_manager.begin_batch()`/`end_batch()`).
`GeometryModule.set_rotation` emits `geometry_changed` synchronously inside
`apply()`, *before* its own undo push - so without batching, `RoiGeometrySync`'s
remap-triggered push would land on the stack **before** the rotation's own,
reversing undo order. `_BatchCommand.undo()` reverses in push order
regardless of order pushed, so batching fixes this without either module
needing to know about the other. Same one-line wrap will be needed for
crop/flip once their tools call `set_crop`/`set_flip` from a UI gesture.

Verified: pyflakes-clean across all touched/new files. 16 new unit tests
(`test_lspri_rewrite_roi_geometry_remap.py`: point-remap against the real
transform + round-trip, mask-remap against `apply_spatial_mask`, the
per-shape dispatcher) + 10 new integration tests
(`test_lspri_rewrite_roi_geometry_sync.py`: reason filtering, dataset
lifecycle rebaselining, status text, the real rotate-tool gesture producing
one undo step that reverts both the angle and the ROI position) all pass.
Broader sanity sweep (335 ROI/mask/geometry/dataset/rotate-related LSPRi
tests) passes with no regressions.

**Not done this pass** (flagged, not silently skipped): rectangle/polygon
ROI remap (no such ROI shape exists in the rewrite yet - `_remap_roi_shape`
has the dispatch branch ready, raises until one is built); a rectangle/
polygon's future orientation-angle field will need the same rotation-delta
treatment the point/mask remap already gets, noted in `_remap_roi_shape`'s
docstring for whoever builds that shape.

## 2026-09-28 - Workflow accordion restores the last open stage

`WorkflowPanel` (`panels/workflow/panel.py`) always opened with the Dataset
stage expanded and the other four collapsed, no matter what the user had
open when they last quit - each top-level section's `expanded=` was a
literal baked into its `_build_*_section()` builder, and nothing read or
wrote which one was current. Found while investigating whether Workflow
accordion state was restored at all (it wasn't - see the previous session's
answer in this doc's git history for the full trace through
`CollapsibleSection`, `QMainWindow.saveState()`'s dock-only scope, and
`storage/app_settings.py`).

Fixed the same way `storage/app_settings.py`'s own docstring promised:
"every future app-level setting is one more dataclass field, not a new
mechanism."

- `AppSettings.active_workflow_stage: str | None` - `WorkflowStage.name`
  (e.g. `"IMAGE_TOOLS"`), not the enum itself, so the settings file stays
  Qt/app-free. `None` (missing field, first-ever launch, or an old
  settings file predating this field) means "no saved stage" and falls
  back to the existing hardcoded default (Dataset open).
- `WorkflowPanel.__init__` gained `initial_stage: WorkflowStage | None`.
  When given, it directly `set_expanded()`s each section to match *before*
  the `expanded_changed` -> `_on_section_toggled` signals are wired up a
  few lines later - so seeding the initial state can't itself fire a
  spurious `stage_changed`/re-persist, and doesn't depend on the
  single-open-accordion cascade being reentrant-safe during construction.
- `app_rewrite.py` decodes `settings.active_workflow_stage` via
  `WorkflowStage[name]`, catching `KeyError` (an unrecognised name -
  hand-edited JSON, or a build with different stage names) back to
  `None`/the old default rather than crashing startup, and connects
  `workflow.stage_changed` to `_persist(active_workflow_stage=stage.name)`
  - immediate-persist-on-change, same pattern as `theme`/
  `auto_apply_preset_on_stage_change`, since switching stages is a
  deliberate occasional click, not a continuous drag like window geometry
  (which stays batched to `aboutToQuit`).

Verified: `tests/integration/test_lspri_workflow_panel_stage_restore.py`
(new - no saved stage falls back to Dataset; a saved stage opens instead
and the accordion still has exactly one section open; an unrecognised
saved stage name falls back silently instead of raising; switching stages
calls the settings-changed callback with the new stage's name) +
`test_lspri_rewrite_app_settings.py`'s round-trip test extended to cover
the new field. 14/14 pass across both files plus the pre-existing width-
budget test (unaffected: it builds with default `AppSettings()`, i.e.
`initial_stage=None`, so behaves exactly as before this change).

## 2026-09-29 - Rotate right-click menu, permanent info icon, status bar routing

Three maintainer-requested Image-panel UX changes, all in
`panels/image/panel.py` unless noted.

**Right-click now opens a menu instead of cancelling instantly.**
`RotateLineTool.on_right_click()` (`rotate_line_tool.py`) is gone; a
right-click while Rotate is active now calls the panel's new
`_show_rotate_context_menu()`, which pops a `QMenu` at `QCursor.pos()` with
one action, "Cancel rotation", enabled only when `RotateLineTool.first_
point()` is not `None`. Choosing it calls the same `RotateLineTool.cancel()`
Esc already used - Esc itself is untouched (still cancels point 1 directly,
no menu). Point: an accidental right-click can no longer silently drop an
in-progress point 1 - the user now has to actually pick the action, and a
disabled item explains "nothing to cancel" the same way a standard Undo
entry would, rather than the menu changing shape click to click.

**The always-hidden-until-a-tool-is-active text row is gone**, replaced by
a permanent 18x18 "i" icon (`load_tabler_icon("info-circle", ...)`, the
same vendored icon `panels/dock_container.py`'s panel-help buttons use) in
the top controls row, next to the dataset status label. Hovering it shows
`image_controls.py::controls_text(tool)` - the tool's own controls if it
has a row there, else the plain-image row (left-click selects + the
always-available drag/zoom) - via a plain `setToolTip()` on tool change, no
new state to keep in sync since the tooltip is only ever *read*, not
pushed. `_CANVAS_TOOLS` (only ever used to decide whether to show/hide the
old row) is deleted along with it; nothing else referenced it.

**Live per-gesture status moved to the status bar.** The rotate tool's
transient text (the live angle readout while placing point 2, "switched
off" when Image Tools is disabled, etc.) doesn't belong on a tooltip nobody
is hovering mid-gesture, so `ImagePanel` gained a `tool_status_changed =
pyqtSignal(str)`, emitted from the same place the old row's text used to be
built (`_on_tool_status`, plus an empty-string emit on every tool switch to
drop a stale message). `app_rewrite.py` connects it to `status_bar.
showMessage`, right alongside the two other panels already wired that way
(`workflow.status_requested`, `roi_geometry_sync.status_changed`) - one
status bar for every panel's transient text, not a bespoke label per panel.

**Point-3 correction:** flagged to the maintainer that no "crop two-point"
tool exists on this branch (`panels/image/panel.py`'s own module docstring
has said since 2026-09-23 that the draggable crop rectangle isn't built
here yet), since the only two-click gesture in the app is Rotate's. The
maintainer confirmed that's what they meant - "rotate by two points", not
crop. Changed `RotateLineTool._marker` (`rotate_line_tool.py`) from a
filled circle (pyqtgraph's default `symbol="o"`) to a filled plus-sign
(`symbol="+"`, size bumped 9->11 since a `+`'s "ink" covers less of its
bounding box than a circle's, so the unchanged size would have read as
smaller) - a cross marks the exact clicked pixel instead of covering it, as
the maintainer asked.

Verified: `tests/integration/test_lspri_rewrite_rotate_tool.py` (34/34
pass, including three new/rewritten right-click tests - menu's action
cancels, is disabled with nothing pending, and a dismissed menu leaves
point 1 alone - all driven by patching `QMenu.exec()` rather than letting
it block on a real event loop) + `test_lspri_rewrite_image_panel.py` and
`test_lspri_rewrite_roi_geometry_sync.py` (22/22, unaffected) +
`test_lspri_workflow_panel_stage_restore.py` (4/4, exercises `app_rewrite.
build_main_window` end to end, covering the new status-bar connection).

## 2026-09-29 - Crop tool: click-drag rectangle, size fields, shared context menu

Built the crop tool's canvas behavior from scratch - the module docstring's
"Not built here, deliberately" note about it is gone. Explicitly **not** a
port of the old app's `gui/image_tools_controller.py` (`pg.RectROI` with
per-fraction scale handles the maintainer called "strange"); no separate
handle widgets at all - the rectangle's own border is the grab zone.

**New files, `panels/image/`:**

- `crop_tool.py` - `CropTool`, the gesture/state machine (mirrors
  `rotate_line_tool.py`'s split: owns the pyqtgraph overlay items and the
  transient rectangle state, calls `GeometryModule.set_crop`/nothing else
  for the actual commit). Public API is direct-call testable, no screen
  coordinates: `begin_gesture`/`update_gesture`/`end_gesture` (drag
  lifecycle), `set_size` (the size fields), `apply`/`cancel`,
  `hover_handle` (cursor hinting), `set_frame_size` (the clamp bound).
- `crop_size_controls.py` - `CropSizeControls`, a real `QWidget`
  ("x:"/"y:" `QSpinBox`es + a checkmark `QToolButton` apply - tabler's
  vendored `checkbox` glyph turned out to already be exactly the
  maintainer's "check icon box", no new icon needed). Real widgets, not a
  painted overlay, because they need actual keyboard/click input
  (CLAUDE.md's GUI-testability rule) - `panel.py` positions it in screen
  pixels next to the rectangle's bottom-left corner.
- `context_menu.py` - `show_tool_context_menu(parent, actions)`, pulled out
  of the previous session's one-off `_show_rotate_context_menu` per the
  maintainer's explicit request ("this context menu can be some general
  function/worker/tool, we will use it in more tools... design will
  stay"). Rotate's menu (one action) and Crop's (two) both go through it
  now.

**Gestures** (`_hit_test`, screen-pixel-constant grab margin via
`ViewBox.viewPixelSize()`, so it neither vanishes at low zoom nor swallows
half the image at high): drag an edge to resize one side, a corner to
resize two, the interior to move the rectangle, an empty area (or nothing
yet) to draw a new one. `image_controls.py`'s `ImageViewBox` gained
`set_left_drag_handler` for this - left-button drags did nothing at all
before (by design, so a shaky click could never turn into an accidental
pan); Crop is the first tool to claim them, and only while it is the
active tool (checked inside the handler itself, so `ImageViewBox` needs no
knowledge of which tool, if any, is on).

**Two clamping rules, deliberately different** (both maintainer's exact
spec, see crop_tool.py's module docstring for the full reasoning):
a resize-drag never moves the edge you are not dragging (drag the right
edge into the frame boundary and it stops there); editing the size fields
is anchored at the current top-left corner, but reflects the anchor back
just enough to fit if the requested size does not fit from there. Getting
these two confused was the single biggest risk in the whole feature -
`CropToolTest` has a dedicated test for each direction.

**Pre-fills from the existing crop** (maintainer's explicit choice, asked
via clarifying question): activating Crop with one already applied starts
the rectangle right there; dragging in the darkened area outside it starts
a fresh one instead. **Stays live across repeated applies** - `apply()`
commits to `GeometryModule.set_crop` (one undo step, batched with the ROI
remap it triggers, same reasoning as rotate's `_rotate_by`) but does not
end the session, so a second, third, ... pass can refine and re-apply;
only `cancel()` (reverts to whatever `GeometryModule` currently holds -
*not* the same as the existing "Reset crop" button, which clears the crop
entirely) or deactivating the tool ends one. An external crop change while
active and idle (Reset crop, undo/redo) is adopted rather than left stale
(`CropTool._on_geometry_changed`).

**Darkening overlay** ("illustrate it is cut out"): one `QGraphicsPathItem`
per rectangle, an even-odd-fill path of the full frame minus the crop
rectangle - not four separate strip items - so it never has an edge case at
a corner. Crop joined `_PREVIEW_TOOLS` (was rotate-only) for this to even
make sense: the whole point of the tool is choosing from the *full*
available frame, not just what an old crop already kept.

**Known rough edge, accepted rather than fixed**: `CropTool.set_frame_size`
is fed every render's shape unconditionally, but while no preview tool is
active that shape is the *cropped* one - so for the ~100ms coalesce delay
plus render time right after Crop is switched on, the clamp bound (and
thus the darkening overlay's outer edge) can be briefly wrong before the
next (uncropped) render corrects it. Self-correcting, no data ever
committed during that window - not worth the deeper render-pipeline
plumbing a synchronous fix would need.

**Bug caught by its own test before commit**: `_on_crop_tool_changed`
originally pushed the rectangle's size into the spin boxes *before* their
max - harmless once the max had already been set by an earlier activation,
but on a pre-filled first activation (a real crop already applied, frame
size already known - the common case) `QSpinBox.setValue()` silently
clips to the construction-time default range of `[1, 1]`. Caught by
`test_size_controls_show_the_full_prefilled_size_on_first_activation`,
confirmed by temporarily reverting the fix and watching the test fail
(`1 != 48`) before restoring it.

Verified: `test_lspri_rewrite_crop_tool.py` (new, 43/43 - 29 pure `CropTool`
tests on a real shown/ranged `pg.PlotItem` with no `ImagePanel` at all, 14
through the real panel/`ImageViewBox` wiring) + `test_lspri_rewrite_rotate_
tool.py` (34/34, one test moved from Crop to Measure now that Crop has real
canvas behavior) + `test_lspri_rewrite_image_panel.py`/`test_lspri_rewrite_
roi_geometry_sync.py` (26/26) + `test_lspri_rewrite_analysis_engine.py`/
`test_lspri_rewrite_session.py`/`test_lspri_rewrite_session_autosave.py`/
`test_lspri_workflow_panel_width_budget.py`/`test_lspri_workflow_panel_
stage_restore.py` (60/60) - every test that touches `panels/image/` or
`app_rewrite.py`. 163/163 total. ruff/pyflakes clean.

## 2026-09-29 (same day, continued): size-controls polish, two real bugs from manual testing

Maintainer tried the crop tool in the actual app and found three things
the tests above didn't catch (all in `panels/image/`, GUI feel/interaction
issues no automated test exercises the way a person clicking through it
does):

**Size-controls polish.** `crop_size_controls.py`'s "x:"/"y:" fields used
`QSpinBox`'s own default `sizeHint()` (no explicit width) - much wider
than their 3-4 digit content needs, which read as large gaps between the
two fields and the apply button. Fixed width now, computed from
`QFontMetrics` for `"x: 9999"` plus room for the spin arrows (works out to
~65px per field instead of QSpinBox's default). Layout spacing tightened
2px. Also repositioned: was anchored to the rectangle's bottom-*left*
corner; now bottom-*right*, so the apply button sits flush against the
crop's own right edge with the fields packed to its left, not floating
under the middle of nothing.

**Bug: apply didn't visibly do anything.** Working as built, but not as
intended - `CropTool.apply()` commits to `GeometryModule` correctly, but
Crop stays in `_PREVIEW_TOOLS`, which unconditionally renders the
*uncropped* frame while the tool is active - so applying never produced
any visible change until the tool was switched off some other way. Fixed
one layer up, in `panel.py`'s `_on_crop_apply_requested`: a *successful*
apply now also switches Crop off (`ActiveToolModule.set_active(CROP,
False)`), which is what actually makes the panel render the cropped
result. `CropTool.apply()` itself is unchanged - still just commits,
still doesn't touch its own active state - the "and then exit" policy
lives at the panel level on purpose (crop_tool.py's docstring was updated
to explain the split, since `CropToolTest` still exercises `CropTool` in
isolation, where a second resize-and-apply pass without ever deactivating
is exactly what it's testing). A *failed* apply (Image Tools switched
off) leaves the session running - nothing changed, so nothing to exit.

**Bug: the right-click menu "was visible but completely dead"** - no
hover highlight, no click response, as the maintainer first reported it.
**First diagnosis was wrong.** Suspected a Qt/Windows reentrancy issue
(`QMenu.exec()` called synchronously from inside pyqtgraph's own mouse-
grab bookkeeping) and "fixed" it by deferring the menu via `QTimer.
singleShot(0, ...)` - shipped, and the maintainer confirmed it did not
help, still dead. Asked the maintainer three diagnostic questions (does
clicking elsewhere dismiss it; does Rotate's menu have the same problem;
multi-monitor/mixed-DPI setup) rather than guess a second time blind -
the answer ("the menu is not active when I do nothing with [a] previous
crop, but... it also can be working, both applied and cancel" + "Rotate's
menu is also broken") pointed at the real cause immediately: **every
right-click with nothing pending opened a menu with every one of its
items disabled**. Both menus are single-purpose (Rotate: one action;
Crop: two, sharing one enabled condition) - unlike an Edit menu where
Undo can be grayed out while Cut/Paste stay clickable, disabling the
tool's one shared condition disables the *entire* menu, and a menu with
nothing clickable in it looks exactly like "broken", not "informative
like a standard Undo item" (which was the original, wrong reasoning for
building it that way). Not a Qt bug at all - reverted the `QTimer.
singleShot` deferral entirely (back to a plain, synchronous, return-value
`show_tool_context_menu`) and fixed the actual cause instead:
`_show_rotate_context_menu`/`_show_crop_context_menu` now check first and
open no menu at all when nothing would be enabled, rather than opening
one that has nothing to click.

Verified: `test_lspri_rewrite_crop_tool.py` (47/47 - the size-controls
polish tests plus apply-exits-and-renders-cropped/failed-apply-stays-open
from this entry's earlier pass, plus the right-click tests rewritten to
assert *no menu opens* with nothing pending instead of an all-disabled
one) + `test_lspri_rewrite_rotate_tool.py` (34/34, same rewrite for its
right-click test) + `test_lspri_rewrite_image_panel.py`/`test_lspri_
rewrite_roi_geometry_sync.py` (22/22, unaffected). 103/103 total.
ruff/pyflakes clean.

## 2026-09-29 (same day, continued again): "Cancel" always means "exit the tool"

The "no menu at all with nothing pending" fix above was still not what
the maintainer wanted: "when there is nothing to do, there still should
be cancel option, so the tool using is canceled (same like clicking on
tool icon in workflow)". Reworked once more, in `panel.py`'s
`_show_rotate_context_menu`/`_show_crop_context_menu`:

- **"Cancel" no longer means "drop the in-progress edit, stay in the
  tool"** - it means "exit the tool entirely", the same as clicking the
  Workflow panel's Rotate/Crop button again
  (`ActiveToolModule.set_active(tool, False)`). Deactivating already drops
  whatever was pending as a side effect (`RotateLineTool.set_active`'s
  `_clear_first_point()`, `CropTool.set_active`'s `self._rect = None`), so
  the handler is just the one `set_active(..., False)` call - no separate
  `cancel()` call needed first.
- **"Cancel" is now unconditionally enabled**, for both tools - always
  exiting the tool is always a valid thing to do. This is also what
  finally, properly fixes the "menu has nothing clickable in it" problem
  the previous two entries were chasing: with "Cancel" always enabled,
  there is always at least one clickable item, so the earlier "just don't
  open the menu when nothing is enabled" workaround is gone too - the menu
  always opens now. Crop's "Apply crop" is still conditionally disabled
  (`CropTool.has_pending_changes`) - it is no longer the *only* item that
  can be, so it can't strand the whole menu.
- `context_menu.py`'s module docstring updated to describe *why* "Cancel"
  carries the "always enabled" burden rather than restating the same
  general "don't open an all-disabled menu" rule a third time.

Every right-click test in both tool test files needed rewriting again -
not just re-verifying "no menu opens", but the *opposite* claim ("Cancel"
is enabled and, when chosen, exits the tool) - plus new tests for
canceling with a pending edit (discards it) and canceling with nothing
pending (exits cleanly, `GeometryModule` untouched either way).

Verified: `test_lspri_rewrite_crop_tool.py` (49/49) + `test_lspri_rewrite_
rotate_tool.py` (34/34) + `test_lspri_rewrite_image_panel.py`/`test_lspri_
rewrite_roi_geometry_sync.py` (22/22, unaffected). 105/105 total.
ruff/pyflakes clean.

## 2026-09-29 (same day, continued yet again): apply button's cursor

Maintainer asked whether the cursor changes hovering the crop tool's
apply (checkmark) button - it didn't. Root cause: `CropSizeControls`
(`crop_size_controls.py`) is a real `QWidget` parented to the image
view's viewport, and `panel.py`'s `_on_scene_moved` continuously sets
*that viewport's* cursor to a resize/move shape as the mouse crosses the
crop rectangle's edges (`_CURSOR_FOR_CROP_HANDLE`). A widget with no
cursor of its own shows its parent's - so the whole floating size-
controls widget, apply button included, silently inherited whatever
resize cursor the viewport last had, with no explicit override of its
own to block that. Fixed with two `setCursor()` calls: `ArrowCursor` on
the `CropSizeControls` widget itself (blocks the inheritance for the
whole floating widget - the spin boxes get a sane default too, not just
the button) and `PointingHandCursor` on the apply button specifically,
overriding that again since it is the one thing in the widget that is
actually clickable.

Verified: `test_lspri_rewrite_crop_tool.py` (50/50, one new test asserting
both cursors directly) + `test_lspri_rewrite_rotate_tool.py` (34/34,
unaffected) + `test_lspri_rewrite_image_panel.py`/`test_lspri_rewrite_
roi_geometry_sync.py` (22/22, unaffected). 106/106 total. ruff/pyflakes
clean.

## 2026-09-29 (same day, continued once more): cleanup pass over the whole Rotate/Crop/context-menu stretch

Maintainer asked for a manual review of everything built across this
session's several rounds (rotate right-click menu, crop tool, size
controls, context-menu deferral chased and reverted, "Cancel always
enabled" rework) before committing. Read every touched file fresh looking
for leftovers from the back-and-forth. Two real findings, both fixed with
tests, not just prose:

- **`RotateLineTool`'s live status message was stale.** "Point 1 set -
  click point 2 (right-click or Esc cancels)" dated from *before* right-
  click became a menu (an earlier session) and was never updated when it
  did - it told the user right-click "cancels", when by then it opened a
  menu whose "Cancel rotation" choice exits Rotate mode entirely, a
  materially different action from Esc's in-place point-1 cancel. Now:
  "...(right-click for options, Esc cancels point 1)". The module
  docstring's own description of the split had the same staleness (still
  said "this class only exposes `cancel()` for [the menu] to call", which
  stopped being true the moment "Cancel" started exiting the tool instead)
  - corrected alongside it.
- **`CropTool.cancel()` had silently become unreachable in the real app.**
  Once "Cancel crop" started exiting the tool directly
  (`ActiveToolModule.set_active(CROP, False)`) instead of calling
  `cancel()` first, nothing in `panel.py` called `cancel()` at all any
  more - unlike Rotate, which still reaches its own `cancel()` via Esc.
  Grepped to confirm: zero callers outside `CropToolTest`'s direct unit
  tests. Rather than delete a working, tested, genuinely useful method
  (discard an unapplied resize/move *without* leaving Crop mode - a real,
  different gesture from "I'm done, get me out"), gave Crop the same Esc
  path Rotate already has: `CropTool.handle_key(key)` (mirrors `RotateLine
  Tool.handle_key`'s shape, minus the `modifiers` parameter - Crop has no
  arrow-key behavior to gate on Shift/Ctrl) wired into `panel.py`'s
  `eventFilter` right after Rotate's own call, and a `Control("Esc", ...)`
  row added to Crop's `image_controls.py` entry so the info icon's tooltip
  says so.

Everything else read clean on a fresh pass: no dead imports, no leftover
`QTimer`/callback-API traces from the reverted deferral (confirmed by
grep, not just memory), no duplicate or superseded tests sitting next to
their replacements in either test file's `def test_` listing.

Verified: `test_lspri_rewrite_crop_tool.py` (54/54 - three new for Esc:
discards-but-stays-active, not-consumed-with-nothing-pending, and one
through a real `QKeyEvent`/`eventFilter` round trip mirroring Rotate's
own such test) + `test_lspri_rewrite_rotate_tool.py` (34/34, comment-only
changes) + `test_lspri_rewrite_image_panel.py`/`test_lspri_rewrite_roi_
geometry_sync.py` (22/22) + `test_lspri_rewrite_analysis_engine.py`/
`test_lspri_rewrite_session.py`/`test_lspri_rewrite_session_autosave.py`/
`test_lspri_workflow_panel_width_budget.py`/`test_lspri_workflow_panel_
stage_restore.py` (60/60) - every test that touches `panels/image/` or
`app_rewrite.py`. 170/170 total. ruff/pyflakes clean.

## 2026-09-29 (same day, continued): Measure tool - the third Image Tools canvas tool, closing the gap `GeometryModule`'s calibration commands left open on 2026-09-21

The command layer (`apply_measurement_calibration`/`set_measurement_
anchors`/`set_display_units`/`set_scale_bar_visible`/`can_display_
micrometers`/`microns_per_pixel_scalar`) had sat unused since the
2026-09-21 entry - "the panel layer isn't built" was still true for this
one piece even after Rotate and Crop both got real canvas tools. Built the
GUI layer only; zero changes to that command layer's behavior beyond one
guard (below).

**`measure_line_tool.py`** (`MeasureLineTool`): a near-twin of
`rotate_line_tool.py`'s two-click state machine - maintainer's own choice,
to keep the two Image Tools canvas gestures consistent, over porting the
old app's draggable ruler crosses (`gui/measurement_calibration_mixin.py`'s
`_on_measurement_marker_moved` on `develop`). On the second click it calls
`set_measurement_anchors` (cosmetic, not undo-tracked - fired once instead
of continuously) and emits `measured(dx_px, dy_px)`. No arrow-key nudges
(nothing to nudge - the tool itself never changes a pixel). Stays active
after placing a pair, like Rotate's "ready for another pair", not like
Crop's exit-on-apply - there is no destructive change to force an exit
from.

One deliberate deviation from `RotateLineTool` worth flagging: its rubber
band disappears the instant the second click applies a rotation, because
there is nothing left to look at once the pixels have moved. Measure's
placed line stays on screen instead (a second `PlotCurveItem`/
`ScatterPlotItem` pair, solid rather than dashed) - the whole point of this
tool is comparing the line against image features while typing a distance
and deciding whether to Apply. First draft hid it the same way Rotate does;
caught in review before it reached the maintainer, since a ruler that
vanishes the moment you place it would make calibration nearly impossible
to line up correctly. Clears only on a new first click or on deactivate,
not incidentally.

**`measure_controls.py`** (`MeasureCalibrationControls`): a twin of
`crop_size_controls.py`'s floating-widget pattern rather than a new one -
`dx`/`dy` read-only px labels (`QLabel`, not disabled spin boxes -
CLAUDE.md's testability rule is about *clickable* widgets, a label needs no
input widget at all) + editable um `QDoubleSpinBox` pair (matching the old
app's `measurement_um_x_spin`/`measurement_um_y_spin` exactly: range
0-1,000,000, decimals=0) + the same green tabler `checkbox` apply icon Crop
uses + a borderless `QToolButton` unit-cycle toggle (px -> um -> mm -> px).
Owns no `GeometryModule` reference, like `CropSizeControls` - it only
emits `apply_requested(dx_um, dy_um)`/`unit_cycle_requested()`; `panel.py`
makes the actual module calls and reports the result back via `set_deltas`/
`set_unit_display`, catching the `ValueError`s `apply_measurement_
calibration`/`set_display_units` raise and turning them into status text
(the same convention already used for every other guarded command in this
panel). The um fields reset to 0 on every new measurement (`set_deltas`) -
a freshly placed ruler is a new physical distance, and carrying over a
previously typed number would risk calibrating against the wrong pair of
points.

**"mm" added to `set_display_units`** (maintainer's request, confirmed
before building - the old app only ever had px/um) - one line, extending
the allowed set and the calibration-required gate from `("px", "um")` to
`("px", "um", "mm")`. Pure display formatting: mm is µm/1000, no separate
calibration math, so no new field on `GeometrySettings`. `next_unit()` (a
module-level function in `measure_controls.py`, not a method - so the
widget itself never needs a `GeometryModule` reference just to know what
"cycle" means) does the px -> um -> mm -> px wrap-around; `panel.py` calls
it against `GeometryModule.settings().display_units` and lets
`set_display_units` do the actual validation.

**Not a preview tool** - unlike Rotate/Crop, Measure doesn't join
`_PREVIEW_TOOLS`. Calibration is measured against whatever is currently
displayed (already-cropped/rotated); there is no reason to see beyond the
current processed image the way choosing a *new* crop needs to. The ROI
overlay stays visible while measuring.

**Workflow panel**: `transforms_settings.py`'s `TransformsSection` gained a
second row (maintainer's spec) holding just the one Measure toggle button -
the old app packed all seven Transforms controls (including Measure) into
one long row; here Measure's floating on-canvas controls carry the fields/
apply/unit-toggle the old app put in a second row of this same widget, so
there is nothing left for this row to hold beyond the tool toggle itself.
Icon: vendored tabler `ruler-measure` (already in `lspr_ui`'s icon_assets -
no new dependency), green `#22c55e` active, same literal the old app
hardcodes.

**Test gap closed alongside this**: `GeometryModule`'s calibration commands
had *zero* pytest coverage before this entry - the 2026-09-21 entry says as
much ("verified with real calls, scripted, no pytest harness yet") and
nothing since had added any. New `tests/unit/test_lspri_rewrite_
measurement_calibration.py` (20 tests) pins the symmetric/asymmetric-axis
fallback, all three `ValueError` guards, the "cosmetic vs. undo-tracked are
independent axes" split, and the mm addition - against the module directly,
no Qt event loop needed.

**Removed**: `test_lspri_rewrite_rotate_tool.py`'s
`test_a_tool_without_canvas_behavior_does_not_take_clicks` - its premise
("Measure can be switched on but does nothing on the image yet") stopped
being true the moment this landed, and there is no tool left in the enum
without real canvas behavior to exercise it with.

Verified: `test_lspri_rewrite_measure_tool.py` (22/22, new) +
`test_lspri_rewrite_measurement_calibration.py` (20/20, new) +
`test_lspri_rewrite_rotate_tool.py` (33/33, one test removed as above) +
`test_lspri_rewrite_crop_tool.py` (54/54, unaffected). 129/129 total.

## 2026-09-29 (same day, continued once more): Measure tool - maintainer tried it, four real changes came back

Maintainer tried the first cut in the actual app rather than just reading
the diff (exactly the "static verification has limits" case CLAUDE.md
already anticipates for GUI work) and reported four things, all fixed:

**1. The floating controls' background didn't give enough contrast.** The
first cut used `theme.toolbar_section_bg` (`crop_size_controls.py`'s own
formula, copied as-is) - fine for Crop's widget, which sits over Crop's own
semi-transparent dark overlay, but wrong here: Measure has no such overlay
behind it, so the widget floats directly over whatever the image looks
like, and a *theme* color can't guarantee contrast against arbitrary image
content (a light theme's background, or its text color, can easily land
close in luminance to a bright image). Fix: reuse `crop_tool.py`'s own
`_OVERLAY_COLOR` wash (`rgba(0, 0, 0, 140)`, duplicated as a literal with a
comment cross-reference - that constant is that module's private detail,
not exported) as a fixed, theme-independent background, with fixed light
text (`#f8fafc`/`#cbd5e1`) to match. Fixed dark background + theme-driven
text was the actual bug shape: in a light theme, `theme.text_primary`
would have been a dark color painted on what was *already* a light
background, and would have stayed dark-on-dark once the background below
was hardened without also hardening the text - caught before that
half-fix shipped.

**2. Layout was a single cramped row of 6 widgets and, per the maintainer,
"not well aligned."** Rebuilt as a `QGridLayout`: px labels (row 0) above
their matching um fields (row 1), same x/y columns, Apply spanning both
rows immediately to the right, unit toggle spanning both rows past that -
"px on top, um/mm below" and "toggle to the right of the calibration
button", the maintainer's own ordering. Both columns get a shared fixed
width (`QFontMetrics`-sized for the widest field, same technique
`crop_size_controls.py` uses) so the two rows actually line up instead of
each auto-sizing to its own content - the second, quieter half of "not well
aligned".

**3. Positioning anchored the wrong edge.** First cut put the widget's
top-left corner at the ruler's second point, growing rightward - unlike
`crop_size_controls.py`, which anchors its *last* widget (Apply) at the
crop rectangle's corner and grows left. Wrong for two reasons: it doesn't
match the maintainer's explicit spec ("align the apply button to the
second point... fields should be on the left"), and it could clip off the
right edge of the viewport for any point placed left-of-center - plausibly
why the maintainer's report of "I don't see a unit toggle" wasn't actually
a missing feature (it was in the code the whole time) but the *last*
widget in a row that could run past the visible area. Fixed via
`MeasureCalibrationControls.apply_button_center_x()` - a widget-local
lookup of where Apply's column center actually is post-layout -
`panel.py`'s `_reposition_measure_controls` now solves for the widget's
top-left such that Apply's center lands exactly on the second point,
whatever the layout looks like on either side of it.

**4. Placed points needed to be draggable after all.** The maintainer had
explicitly chosen click-twice-only over draggable crosses when asked
up front (this doc's earlier entry) - tried it, and asked for dragging
back, closer to (but not identical to) the stable app's always-visible
crosses: hover a placed point for a move cursor, drag it to reposition.
Added directly to `MeasureLineTool` rather than as a separate class: both
points are now retained as `_point1`/`_point2` (previously only `_point2`
was kept - `_point1` was thrown away once placement completed, since
nothing needed it afterward) with `hover_handle`/`begin_gesture`/
`update_gesture`/`end_gesture`, the same four-method shape `CropTool`
already established for its own drag - so `panel.py`'s single `ImageViewBox`
left-drag slot needed to become a dispatcher (`_on_left_drag_event`, tries
Crop's handler then Measure's, each still independently declining when it
isn't the active tool) rather than being hard-wired to Crop alone. Cursor
reuses the same `SizeAllCursor` Crop's own interior-drag already means
"move this".

One nuance worth flagging: `measured` gained a third argument,
`is_fresh_placement`. A drag update calls `set_measurement_anchors` live
(cosmetic, not undo-tracked - consistent with the original design) and
must refresh the px labels, but must *not* reset the editable um fields the
way a brand-new two-click placement does - the maintainer is fine-tuning a
point while keeping an already-typed target distance, not starting a new
measurement. First draft reset them unconditionally on every `measured`
emission (copy-pasted from the placement path); caught before it reached
the maintainer, since it would have silently thrown away a typed number on
every drag frame.

Verified: `test_lspri_rewrite_measure_tool.py` (30/30 - eight new: drag
moves point1/point2 independently, an off-point drag and a pre-placement
drag are both left unclaimed, dragging doesn't reset typed um values,
dragging isn't an undo step, the hover cursor, and Apply landing under
point 2) + `test_lspri_rewrite_rotate_tool.py`/`test_lspri_rewrite_
crop_tool.py` (33/33, 54/54 - unaffected by the drag-dispatcher refactor)
+ `test_lspri_rewrite_measurement_calibration.py` (20/20, untouched).
137/137 total.

## 2026-09-29 (same day, continued a third time): Measure tool - screenshot review, unit toggle cut from scope, square-pixel auto-calc added

Maintainer sent an actual screenshot this time (not just a description) -
the floating controls' numbers were unreadable against a bright dataset
image, and the layout read as scattered across a wide stretch of the
image rather than one compact control. Three real fixes plus one scope
decision came back.

**The single shared container background never painted as a visible
block.** The previous entry's fix (a fixed `rgba(0,0,0,140)` background on
`#measureCalibrationControls`, replacing a theme color) was the right
*color* but the wrong *target* - the screenshot showed bare text floating
directly on the image with no dark rectangle behind it at all, spread out
over roughly half the image width. Root cause not fully pinned (plausibly
a `QGridLayout` sizing/stacking interaction with the pyqtgraph-viewport
parent that was never actually exercised visually before this point - see
CLAUDE.md's own caution that static verification has limits), but the fix
the maintainer asked for sidesteps needing to pin it exactly: **individual
chip backgrounds per number**, not one container background. Each
`QLabel`/`QDoubleSpinBox`/the Apply button now paints its own
`rgba(0,0,0,140)` rounded chip via `objectName("measureChip")` + a
`QSS #measureChip` selector, rather than relying on a parent-level
background that apparently never reliably painted. This is also exactly
what the stable app already does for the same problem - its scale-bar
label comment (`gui/measurement_calibration_mixin.py`) explicitly calls
out "a small solid chip, same convention as the ROI/landmark tags" for
text that has to stay legible over arbitrary image content. Each chip
paints itself regardless of container geometry, so this is also more
robust than the container approach even setting the visibility bug aside.

**Condensed**: field width's padding tightened (7 placeholder digits + 20px
pad -> 6 digits + 12px), grid spacing 6px -> 3px, container margins removed
entirely (0,0,0,0) - chips supply their own visual separation now, so the
layout can pack tighter than a shared-background version could.

**Unit toggle removed from this widget, and cut from scope entirely.** Two
separate maintainer decisions: placement ("I was misunderstood that switch
is next to apply button - remove it from there") and scope ("I also decide
we can skip the switch, and do only px to um transform - no other units
for now"). Removed `_unit_button`/`unit_cycle_requested` from
`measure_controls.py`, `next_unit()`, and `panel.py`'s `_on_measure_unit_
cycle_requested`/`_sync_measure_unit_display` wiring. `GeometryModule.
set_display_units` reverted to accepting only `("px", "um")` - the "mm"
extension from two entries ago is gone, with an explicit regression test
(`test_rejects_mm_no_longer_a_supported_unit`) pinning the reversal rather
than silently dropping the case. The underlying `display_units` field
itself is untouched (still exists, still gets set to "um" on a successful
`apply_measurement_calibration` - that was always independent of whether a
UI toggle exists to flip it manually).

**Square-pixel auto-calc of the sibling axis** (new capability, maintainer's
spec): "when one of x,y values is set by user, the other one is
autocalculated... pixels are square and scaling is in both x,y same."
Editing dx_um (on `editingFinished`, matching `crop_size_controls.py`'s
commit-not-every-keystroke convention) fills dy_um with
`dx_um * (dy_px/dx_px)`, symmetrically for dy_um - constructed so that
`abs(computed/target_px) == abs(typed/source_px)`, the same scale, so
feeding both fields through `apply_measurement_calibration` unchanged
reproduces the exact numeric result its own pre-existing symmetric
fallback (only one axis given -> the other inherits the scale) already
produced - this widget needed no knowledge that fallback exists to stay
consistent with it. Guarded against a ~zero source pixel span (a
perfectly vertical or horizontal ruler on the *other* axis - nothing to
derive a scale from, left alone rather than dividing by ~zero).

**A design contradiction caught before it reached tests, not by them**:
the first draft of this feature's docstring claimed a user could type a
second, independent value into the sibling field afterward to "override"
the auto-fill. Re-reading the actual `_sync_sibling` implementation while
writing that sentence showed it was false - editing *either* field always
re-derives the *other* from it, so a second edit re-syncs both fields to
the new scale rather than leaving an independent pair. Decided this is
actually the more correct behavior (there is no way to express "these two
axes really do have different scales" through this UI, matching the
maintainer's own framing of square pixels as the assumed model, not an
optional one) and fixed the docstring to match reality instead of adding
code to make reality match the wrong docstring. One test (`test_editing_
the_second_field_afterward_overrides_the_auto_fill`) was testing the false
claim and passing anyway, for an unrelated reason - see next paragraph -
so it never would have caught this; replaced with `test_editing_the_
other_field_afterward_re_syncs_to_the_new_scale`, which asserts the
re-sync explicitly.

**Three new tests silently passed for the wrong reason, caught in review
before being counted as coverage**: they clicked a second point at
x=100, but `setUp`'s view range is only `xRange=(0.0, 80.0)` - `_in_view`
rejected the out-of-range click, so `on_left_click`'s completion branch
(which calls `set_measurement_anchors` and emits `measured`) never ran,
`_dx_px`/`_dy_px` stayed at the widget's construction-time 0.0/0.0, and the
auto-calc's zero-pixel-span guard made every assertion pass by doing
nothing at all - not by exercising the feature. Two more of the six new
tests actually failed outright on the same mistake, which is what
triggered checking the other four instead of trusting the green run.
Fixed by keeping every click within the configured view range (`crop_
tool.py`'s and `rotate_line_tool.py`'s own tests already do this
correctly - this file's new tests were the one place that didn't).

Verified: `test_lspri_rewrite_measure_tool.py` (31/31 - net +1: six new for
the auto-calc feature, three unit-toggle tests and the `UnitCycleTest`
class's two removed) + `test_lspri_rewrite_measurement_calibration.py`
(21/21 - the combined mm/um test split into three: um-refused, um-allowed,
mm-rejected) + `test_lspri_rewrite_rotate_tool.py`/`test_lspri_rewrite_
crop_tool.py` (33/33, 54/54 - unaffected). 139/139 total.

## 2026-09-29 (same day, continued a fourth time): Measure tool - a real screenshot review, apply now exits, live calibrated readout added

The chip-background fix from the previous entry worked ("the background is
fine" - first confirmed-working round of visual feedback on this feature).
Four more things came back from the same screenshot, one of them a real
missing behavior rather than polish.

**Apply exits Measure mode now** - the one substantive behavior change.
Every other Image Tools tool's Apply/commit action already exits
(`_on_crop_apply_requested`); Measure's first cut deliberately stayed
active ("measuring is likely to need a second look... no destructive pixel
change to force an exit from" - a reasonable guess, wrong per the
maintainer's actual expectation: "it should cancel and settings applied").
`_on_measure_apply_requested` now calls `_active_tool.set_active(MEASURE,
False)` on a *successful* apply only - a failed one (bad input) leaves the
session in the tool, matching Crop's own failed-apply behavior, so a typo
doesn't also cost the placed ruler. Caught while implementing this that no
existing test actually asserted either the old "stays active" behavior or
the new "exits" one - `test_apply_exits_measure_mode`/`test_a_failed_
apply_stays_in_measure_mode` close that gap.

**Apply button moved into the um row and enlarged.** It was vertically
centered across both rows (spanning px row + um row); the maintainer found
it "too small" and wanted it "in same row as second row" instead. Moved to
row 1 only (no rowspan), icon grown to fill nearly the whole chip
(18px -> 22px icon in a 28px -> 26px button, plus explicit `padding: 0px`
in the QSS chip rule to remove Qt's default button padding, which was
fighting the icon-size change on its own).

**Absolute values only.** `Δy -2.7 px` in the screenshot - the maintainer
wants distances, not signed displacements. `MeasureCalibrationControls.
set_deltas` now stores `abs(dx_px)`/`abs(dy_px)` rather than the raw
signed values `MeasureLineTool` computes (which still has to stay signed
internally - `GeometryModule.set_measurement_anchors` needs real point
coordinates, not distances). `set_um_values` (see below) floors the same
way.

**Tool color changed to blue** (`#38bdf8`, `CropTool`'s own literal) for
both the on-canvas line/crosses (`measure_line_tool.py`) and the Workflow
button's active state (`transforms_settings.py`) - was green, matching the
stable app's icon literal (2026-09-28's "port the stable app's choice"
default). Explicit maintainer override: "similar like cropping rectangle...
keep it for most of the tools if not ask otherwise" - noted in both
docstrings as a deliberate deviation from the "match the stable app" rule
this file otherwise follows, and as the new default going forward. The
Apply checkmark's green stays green - that already matches Crop's own
apply button, which keeps its checkmark green despite its on-canvas color
being the same blue; tool-identity color and "confirm" color are already
two different things in this codebase, this didn't need to change.

**New capability: once a calibration exists, Measure doubles as a plain
ruler.** "When some calibration coefficient is applied, and moving those
measurement tool and selecting some distance should automatically use this
coefficient to give numbers in second um rows." `_on_measure_tool_measured`
now checks `GeometryModule.can_display_micrometers()` on *every* placement
or drag update (not just fresh placements): if calibrated, the um fields
are live-filled from the existing microns-per-pixel scale
(`set_um_values`, not `reset_um_fields`) instead of being zeroed or left
untouched. This intentionally overrides the earlier "dragging must not
reset a manually-typed value" rule from two entries ago - that rule was
protecting a first-time-calibration workflow (type a known distance, fine-
tune the points, keep the typed number); once calibrated, the dominant
workflow becomes "just read the distance off", and a stale manually-typed
number sitting in the field while the ruler visibly moves would be
actively misleading. Typing over the live-filled value still works (to
re-calibrate against a different reference), it's just no longer
protected from being overwritten by the next placement/drag - a real,
known trade-off, not an oversight, and easy to revisit if it turns out
annoying in practice.

**Test-writing note, same pattern as two entries ago**: two of the eight
new/changed tests in this round used click coordinates outside `setUp`'s
configured view range, which - as documented there now - silently drops
the click rather than raising anything, so a test can pass while never
exercising what it claims to. Caught before commit this time by
deliberately checking every new test's coordinates against the view range
rather than discovering it via failures; the two pre-existing tests that
happened to pass "by coincidence" (default `GeometrySettings` anchor
values happening to match what a real click would have produced) were
fixed alongside for consistency even though they weren't failing.

Verified: `test_lspri_rewrite_measure_tool.py` (37/37 - six new: apply
exits/stays-on-failure, no-negative-numbers, calibrated-readout on
placement and on drag, and the no-calibration-still-resets-to-zero
counterpart) + `test_lspri_rewrite_rotate_tool.py`/`test_lspri_rewrite_
crop_tool.py`/`test_lspri_rewrite_measurement_calibration.py` (33/33,
54/54, 21/21 - all unaffected). 145/145 total.

## 2026-09-29 (same day, continued a fifth time): Measure tool - live preview while placing, and a third "d" field tied into the same square-pixel model

Two requests this round, one UX (show the fields earlier) and one a real
addition to the calibration-input surface (a third field). Checked with the
maintainer before building the second one, since it was a genuine fork
(read-only display vs. an editable field wired into the calibration input
path) rather than a UI detail - picked "editable, decomposes into dx/dy".

**Live measurement while placing point 2, not just after it's clicked.**
`MeasureLineTool.on_mouse_moved` now does, while hovering with point 1
already down, exactly what a drag of an *already-placed* point does:
calls `GeometryModule.set_measurement_anchors` and emits `measured` using
the cursor as a provisional point 2. The rubber-band line remains the only
visual difference between "still placing" and "placed" - the numbers and
`GeometryModule`'s own state now track the cursor continuously either way.
Concretely this means Apply already works correctly even if point 2 is
never clicked at all - the maintainer can watch the live numbers and hit
Apply directly, which was not previously possible or intended but falls
out for free from treating hover-before-commit and drag-after-commit as
the same underlying gesture.

**`is_fresh_placement` moved from "point 2 just clicked" to "point 1 just
clicked"** - a consequence of the above, not a separate decision. Once
hovering shows live fields, resetting them *again* when point 2 commits
would wipe out anything typed while fine-tuning position before that
click - a real bug caught while implementing, not by a test (there wasn't
one for this exact sequence). Now there is exactly one reset per
measurement session, fired the instant point 1 is placed
(`measured(0.0, 0.0, True)`); every later hover/click-2/drag emission for
that session is `False`. `current_anchor_point()` (renamed from
`last_second_point()` - it now also has to report the live hover position,
not just a committed point 2) is what `panel.py` positions the floating
controls against either way.

**Apply button, third correction**: moved from spanning the px+um rows to
the um row only, per the earlier "align it in same row as second row"
note not being fully satisfied the first attempt covered (it had already
been moved out of a 2-row span into a 1-row grid, but the *icon itself*
still read small inside its chip) - icon grown from 18px to 22px in a
28px->26px button, plus explicit `padding: 0px` in the QSS rule, since
Qt's own default button padding was fighting the icon-size change on its
own.

**"d" (the straight-line distance, hypotenuse) added as a third column**,
restructured as a 3x3 grid with a header row ("dx"/"dy"/"d" labels
replacing the old per-cell "Δx "/"Δy " prefixes, which are now
redundant with the header) - maintainer's spec: "often you know the total
distance between two features but not its x/y components". Made editable,
and wired into the *same* single-scale model dx/dy already used rather
than as a separate calculation bolted on: typing into any one of the three
um fields (dx, dy, or d) is mathematically equivalent to specifying one
isotropic um/px scale (`typed value / that field's own px reference` -
`d_px = sqrt(dx_px^2 + dy_px^2)`), and the other two are recomputed from
that single scale via their own px components. One function
(`_apply_scale`) now backs all three edit handlers, replacing the
dx/dy-only pairwise `_sync_sibling` from two entries ago - a genuine
simplification, not just an addition, once the pattern was framed as "one
scale, three views of it" instead of "keep these two in sync". `d_um`
never reaches `apply_requested`/`GeometryModule.apply_measurement_
calibration` - it stays a derived, always-consistent convenience; dx_um/
dy_um alone are still what gets applied, and they are already guaranteed
consistent with whatever d_um shows by construction.

Verified: `test_lspri_rewrite_measure_tool.py` (46/46 - five new for live
hover preview: shows/hides correctly, live-updates `GeometryModule`,
Apply works without ever clicking point 2, the single-reset-at-point-1
guarantee, hovering before point 1 is a no-op; four new for "d": the
hypotenuse label, editing dx also updates d, editing d decomposes into
dx/dy, and d's own zero-length guard) + `test_lspri_rewrite_rotate_
tool.py`/`test_lspri_rewrite_crop_tool.py`/`test_lspri_rewrite_
measurement_calibration.py` (33/33, 54/54, 21/21 - all unaffected).
154/154 total.

## 2026-09-29 (same day, continued a sixth time): Transforms row reorder, and a cleanup pass over the whole Measure stretch

Maintainer asked for a small icon reorder, then explicitly asked for a
cleanup pass + test run before calling this feature done - the same
"maintainer asked for a manual review before committing" request the
Rotate/Crop stretch got a few entries back, applied here for the first
time to Measure's own five-round history.

**Reorder**: `TransformsSection` goes back to one row - flip H/V moved
from after crop to between rotation and crop, and Measure folded back in
at the end of the same row instead of the second row it got three entries
ago. That second row no longer earns its keep now that Measure's own
fields/apply live entirely in its floating on-canvas controls, not in this
row at all - there was never anything else it would have held. Pure
layout: `QVBoxLayout(row1, row2)` collapsed to one `QHBoxLayout`, no
signal/attribute changes, so no behavioral test needed touching.

**Cleanup pass, read every touched file fresh looking for leftovers from
the five rounds of back-and-forth** (not a re-read of the diffs - the
whole current state of each file, the same method the Rotate/Crop cleanup
entry used and the same reason: diffs show what changed in one step, not
what a change three steps ago left inconsistent with a change two steps
later). Real findings, not just prose - each confirmed by grep or by
tracing call sites, not assumed:

- **`MeasureLineTool._report_measurement`'s `is_fresh_placement` parameter
  was dead.** Moving the "reset" emission to point 1's placement (two
  entries back, for the live-hover-preview feature) left both of
  `_report_measurement`'s two call sites - point 2's click and a
  post-commit drag - passing `is_fresh_placement=False` unconditionally.
  The parameter had nothing left to vary. Removed; the method now always
  emits `False` and says why in one line, instead of a caller-supplied
  value that never actually differed.
- **Two module docstrings claimed Crop was "the one tool" claiming
  left-button drags.** True when written (2026-09-29, before Measure's
  drag-to-reposition existed); false since two entries ago, when Measure
  joined it. Both `panel.py`'s and `image_controls.py`'s module docstrings
  still said it. Fixed to name both tools and how `_on_left_drag_event`
  dispatches between them.
- **`_refresh_tool_info`'s docstring still described Crop and Measure as
  "a tool with no row of its own"** in `image_controls.py`'s
  `_TOOL_CONTROLS` - stale from before either got a real controls row
  (both have had one since they were first built). Fixed to state the
  actual current rule (every real tool has one; only "no tool" falls
  back).

Everything else read clean: no dead imports (confirmed - `QVBoxLayout`
was the one import the reorder made unused, already removed as part of
the edit itself, not left for this pass to catch), no leftover unused
methods, no duplicate or superseded tests sitting next to their
replacements in `test_lspri_rewrite_measure_tool.py`'s now 46-test
listing.

Verified: `test_lspri_rewrite_measure_tool.py`/`test_lspri_rewrite_
rotate_tool.py`/`test_lspri_rewrite_crop_tool.py`/`test_lspri_rewrite_
measurement_calibration.py`/`test_lspri_workflow_panel_width_budget.py`
(46/46, 33/33, 54/54, 21/21, 3/3) - every test that touches
`panels/image/`, `panels/workflow/transforms_settings.py`, or
`GeometryModule`. 157/157 total.

## 2026-09-29/30: Histogram panel built - the first real implementation, not a port

`panels/histogram/panel.py` went from the scaffold's `NotImplementedError`
stub to a real, working panel across two days of maintainer feedback. Where
things ended up, for a future session extending this:

**Stable-app analysis first** (before any code): stable's histogram
(~1,400-1,600 lines across 13 files) is entangled the same way the rest of
that app is - `PlotManager`/`OverlayManager`/`MaskController` all take
`window` and reach into 20-30 attributes. Decision: port the clean math
kernel verbatim (`processing/roi_histogram.py`'s `estimate_roi_intensity_
range` -> `panels/histogram/compute.py`, unchanged), rebuild the
widget/wiring layer fresh against this branch's modules. Two structural
improvements over stable, both deliberate: %/counts and linear/log are
independent toggles (stable coupled log-mode to counts); the x-axis is
fixed `[0, 65535]` always (stable let it float to the observed data range).

**New shared cross-cutting module**: `selection/highlight_range_module.py`
(`HighlightRangeModule`) - the Highlight-range selection, following the
precedent `SelectionModule`/`ReferenceFrameModule` already set for state
several independent modules legitimately need. Maintainer's explicit
direction (2026-09-29): Mask and ROI Toolbox should read this module
directly and never need a reference to `HistogramPanel` at all - this is
why it exists as its own module rather than a `HistogramPanel.range_
selected` signal. **Not yet wired**: `MaskModule.set_histogram_highlight_
range`/`RoiToolbox.set_detection_settings` both already have the consumer-
side method waiting, but nothing calls them from `HighlightRangeModule.
range_changed` yet - real follow-up work, not done here.

**`ImagePanel` gained two new signals** (`image_rendered(image, cube_index,
wavelength_nm)`, `image_cleared()`) - the "one narrow read" Histogram needs
per the original sketch, added rather than having Histogram run a second,
independent render of the same frame. `HistogramPanel` subscribes to only
these two signals and nothing else (`resolve_mask_source`/`affine_for`/
`.rois()` etc. are read as plain queries at redraw time) - deliberate:
`ImagePanel` already re-renders on every geometry/mask/chromatic/ROI change,
so a second subscription to those same signals here would be redundant.

**Panel/plot split** (`panel.py` owns wiring and module queries, `plot.py`
owns the pyqtgraph widget) matches Spectra/Sensorgram's own split.
`HistogramPlot` is the first plot in this app to leave pyqtgraph's native
right-click menu enabled (`panels/histogram/plot.py`'s module docstring has
the reasoning) - every other plot disables it. X is locked to `[0, 65535]`
against every trigger, not just the visible "A" button: `setMouseEnabled(x=
False, y=True)` blocks interactive drag/wheel, and a `sigXRangeChanged`
listener (`_on_x_range_changed`) snaps X back the instant anything else
moves it (the right-click menu's "View All"/"Auto" reaches `ViewBox.
autoRange()` by a door `hideButtons()` alone does not close - both were
needed, confirmed by direct `ViewBox` testing, not assumed).

**Highlight range starts unset and the region is hidden until it has a
value** - the only interactive way to give it one (dragging the region)
requires it to already be visible, so `HistogramPanel._ensure_highlight_
range_seeded` seeds it to the current frame's own observed `[min, max]` the
first time real data arrives, never overwriting an existing value. Resets
via `dataset.dataset_cleared -> highlight_range.clear_range` (wired in
`app_rewrite.py`), so a new dataset gets its own fresh seed.

**Shared `panels/cursor_overlay.py`** (`CursorOverlay`) - a toggleable
crosshair + live value readout, ported from stable's `plot_overlay_
controller.py` cursor-toggle half (its separate "stats" overlay was not
requested, not built). Used by both `HistogramPlot` (snaps to the nearest
bin on the "All pixels" curve) and `ImagePanel` (reads the actual pixel
under the cursor) - the two `value_at` callbacks are the only per-panel
code; positioning is each panel's own job (a `CursorOverlay` has no opinion
on where its icon sits, only how it behaves once placed).

**Settings gear icon + `HistogramPlotSettingsDialog`** - the first
instance of this pattern in the rewrite (Spectra/Sensorgram will likely
copy it once built); non-modal, live-apply, not Ok/Cancel. Axis mode,
scale, bin size, and one line-width control for all curves today - the
maintainer's own framing was "other things which will come later," not a
finished settings surface.

**The Highlight-range readout widget took five rounds to get its sizing
right** (`panels/histogram/highlight_range_controls.py`) - full account,
including the real lesson about `.text()`/`.width()` not being evidence of
anything on their own, is in this app's `CLAUDE.md` "Common Pitfalls"
section. Worth reading before building any other tightly-grouped floating
text/number field in this app.

**Still open, named so a future session doesn't have to re-derive it from
this conversation**: wiring `HighlightRangeModule.range_changed` to Mask
and ROI Toolbox (both consumer methods exist and are tested independently,
just not connected to this signal yet); the "wand" auto-range button
(`compute.estimate_roi_intensity_range` is ported and ready, no UI calls
it); the Residual curve (marked "maybe" by the maintainer, not built).

Verified throughout: `tests/integration/test_lspri_rewrite_histogram_
panel.py` (25/25) and `test_lspri_rewrite_image_panel.py` (12/12, including
the two new cursor-overlay tests), plus repeated full-window offscreen
smoke tests (`QT_QPA_PLATFORM=offscreen`, `build_main_window()`).

**Cleanup pass before commit**: 4 parallel review agents (reuse/
simplification/efficiency/altitude - `/simplify`'s standard method) over
the diff. Fixed, each verified against the actual pyqtgraph/Qt behavior
rather than taken on faith:
- X-axis lock rebuilt on `ViewBox.setLimits(xMin/xMax/minXRange/
  maxXRange)` - pyqtgraph's own purpose-built mechanism, checked against
  the installed source (`ViewBox.updateViewRange` is the one choke point
  every range-changing path funnels through) - replacing the `sigXRangeChanged`
  snap-back listener from two entries ago, which only ever closed doors as
  they were found. Confirmed by the same direct `ViewBox.autoRange()` test
  that caught the original bug: X still doesn't move.
- `highlight_range_controls.py`'s field width now measured via
  `QFontMetrics.boundingRect` (documented by Qt as covering actual
  rendered pixels) instead of `horizontalAdvance` (a cursor-advance
  metric) - the explicit margin stays, as defense in depth, not a
  replacement for measuring the right thing.
- `cursor_overlay.py`'s toggle icon rebuilt on a real checkable
  `QToolButton` (was a `QLabel` + hand-rolled `eventFilter` click
  detection) - this app's own CLAUDE.md GUI-testability rule, missed when
  first built. Shared by both Histogram and Image panels, so the fix
  landed in both places at once.
- `panel.py`: extracted the three-times-repeated mask-to-curve pattern
  into `_set_curve_from_mask`; dropped a redundant `&finite` pre-filter
  now that `compute.population_counts` already does its own (caught a
  real bug introduced while extracting the helper - `mask=None` meant two
  different things for the All-pixels curve vs. the three optional
  curves, fixed before it shipped, not after); the bin-size handler now
  goes through `_schedule_redraw()` like every other trigger, instead of
  bypassing the coalescing timer.
- Docstrings tightened across `panel.py`/`plot.py`/`highlight_range_
  controls.py`/CLAUDE.md - several had accumulated a blow-by-blow "here's
  what we tried" narrative from being written live during debugging;
  that history stays in this log, code comments now describe current
  design and non-obvious why, not a change log.

Consciously not applied (reported, judged out of scope for a quality-only
pass, not silently dropped): moving the "don't overwrite an existing
Highlight range" seed policy from `panel.py` into `HighlightRangeModule`
itself (real, but the reviewing agent's own words were "not broken
today"); relocating the `_blocked` signal-suppression context manager out
of `image/panel.py` into a shared spot now that `plot.py` wants the same
pattern (mechanical, but touches a third file for a ~5-line duplication);
consolidating `panel.py`'s paired `_image`/`_frame` fields into one
(cosmetic, not fixing any actual redundant work); caching raw per-bin
counts to skip mask/ROI recompute on a percent-vs-counts toggle (a real
feature, not a cleanup, and this codebase's own performance philosophy
asks for a measured need first, not a speculative one).

Re-verified after the cleanup pass: 39/39 (Histogram + Image panel
suites), 142/143 on the broader Image-panel-dependent suite (the one
failure is `crop_size_controls.py`'s own pre-existing font-metric-
environment-dependent test, untouched by this work), full-window smoke
test. Nothing committed as of this entry - five back-to-back sessions of
uncommitted changes on `rewrite`, now going to commit.

---

## 2026-09-30: Cube/Wavelength navigation slider ported from the stable app

Maintainer's request: port the stable app's Cube/λ slider (`docs/
image_area_slider_redesign.md`) into the Image panel with as much of its
functionality/design as the rewrite's current backend actually supports,
and name what's still missing for feature parity. The panel previously had
only bare `QSpinBox`/`QDoubleSpinBox` fields for cube/wavelength - no
slider at all.

**Ported as new files, unchanged logic**: `panels/image/data_axis_slider.py`
(`DataAxisSlider` - the custom-painted `QSlider`: tick rail, major-label
overlap avoidance, click/drag-to-jump, `set_reference_highlight`,
`set_tick_cache_state`) and `panels/image/guided_value_spinbox.py`
(`GuidedValueSpinBox` - the wavelength jump field's focus-dependent suffix
trick for `QCompleter` prefix-matching). Diffed against the stable
originals after pasting: only the import blocks changed.

**`panel.py` changes** (not a mechanical port past this point - the
module architecture is different enough that the wiring had to be
redesigned, not copied):

- Two full-width rows (title → slider → spin → icon), replacing the old
  single `QHBoxLayout` of two spin boxes - same layout shape as `gui/
  layout_builder.py`'s `image_slicer_row`.
- **Real correction to a stable-app assumption, not a port**: the stable
  slider's wavelength ticks are set once per dataset load
  (`_wavelength_values` = the dataset-wide union of wavelengths). This
  rewrite's `DatasetModule.wavelengths_for_cube(cube)` is deliberately
  per-cube (a cube can be short a wavelength - see that method's
  docstring), so `_refresh_wavelength_range()` (and therefore the
  wavelength slider's ticks) must be recomputed on every cube change, not
  just at load. Added `test_wavelength_slider_reranges_for_a_cube_short_
  a_wavelength` specifically because the test dataset already exercises
  this case (cube 1 is short 550.0 nm) and the old spin-only code happened
  to paper over it by re-querying on every read; a slider's ticks do not
  self-correct that way.
- **`SelectionModule` kept as the single source of truth, not the slider**
  (a deliberate departure from the stable app's "the slider's own `value()`
  index is truth, the spin box is a display-only mirror synced only when a
  render settles" design - see the stable redesign doc). Both slider and
  spin box call `SelectionModule.set_cube`/`set_wavelength` on change, and
  `_on_selection_cube_changed`/`_on_selection_wavelength_changed`
  (subscribed to `SelectionModule`'s own signals) are the *only* place
  either widget's displayed value is set. This was already this panel's
  own documented rule for the canvas ("redraw because the module emitted a
  change, not because a handler decided to redraw") - applying the same
  rule to the nav widgets closes a real, pre-existing gap: before this
  change, nothing kept `_cube_spin`/`_wavelength_spin` in sync if
  `SelectionModule` were ever changed from outside this panel (it never
  was yet, so this was latent, not observed) - now it is impossible to
  regress. Covered by `test_selection_changed_elsewhere_still_updates_
  the_nav_widgets`. The stable app's render-completion-decoupled sync
  exists there because its redraw path is expensive; the rewrite's nav
  widgets are cheap Qt property writes regardless of how often
  `SelectionModule` fires, so no decoupling was needed to avoid GUI-thread
  cost - the coalesced 100ms redraw timer (unrelated to this widget sync)
  still absorbs the actual pixel-render cost exactly as before.
- **Reference-frame highlight ported and fully wired**, because
  `ReferenceFrameModule` (2026-09-25) and its Auto/Manual resolution rule
  already exist (`panels/workflow/reference_frame_row.py`). Duplicated
  that row's own Auto/Manual resolution logic locally
  (`_resolve_reference_frame`) rather than sharing it, matching this
  codebase's established "one backend, several front doors" convention -
  `ReferenceFrameModule` deliberately holds no `SelectionModule` reference
  (see its docstring), so every front door resolves Auto mode itself.
  Ported the "jump to reference" star-icon button too
  (`_on_reference_jump_clicked`).
- **`ImagePanel.__init__` gained a new required `reference_frame:
  ReferenceFrameModule` parameter.** This rippled into every test file
  that constructs `ImagePanel` directly (6 files:
  `test_lspri_rewrite_canvas_tools_bar.py`, `..._histogram_panel.py`,
  `..._rotate_tool.py`, `..._measure_tool.py`, `..._crop_tool.py`,
  `..._roi_geometry_sync.py`, plus the Image panel's own test file) -
  found by grepping the whole umbrella `tests/` tree, not just the
  submodule, after the first fix attempt only covered the latter and
  broke 125 tests across the other six files. All six now construct a
  plain `ReferenceFrameModule()` and pass it positionally, matching
  `app_rewrite.py`'s real wiring (which already had a `reference_frame`
  instance in scope from the Dataset section's build).

**Deliberately not ported, and why** (each is a real, separate piece of
work, not a "just wire it up" gap):

1. **Cube/Time toggle** (label the cube slider by elapsed acquisition time
   instead of raw index). The stable version reuses
   `AnalysisController._sensorgram_x_values`/`_format_elapsed_seconds` -
   neither has an equivalent on this branch; the Sensorgram panel is still
   a `NotImplementedError` stub (see the 2026-09-30 status doc). Closer
   than it looks, though: `DatasetModule.acquisition_metadata()` already
   exists, so the *data* is there - only the elapsed-seconds-mapping/
   formatting utility functions are missing. Kept the cube title as a
   plain `QLabel("Cube")`, not the stable app's clickable toggle button,
   so there is nothing to click that would silently do nothing.
2. **Image-exclusion button** ("exclude this image/wavelength/cube from
   processing"). `domain/exclusions.py` was never ported to this branch at
   all (flagged with no assigned new home back on 2026-09-20) - the
   backend this button would drive does not exist, so no button was added
   rather than shipping a dead one.
3. **Cache-indicator tick coloring** (blue ticks = "this cube already has
   every selected ROI's spectrum cached"). `DataAxisSlider.
   set_tick_cache_state` was ported (it's free - part of the widget) but
   nothing calls it. The real work is the debounced background scan
   (`AnalysisController._refresh_cube_slider_cache_indicators` in the
   stable app) plus deciding its rewrite-appropriate equivalent against
   `InMemoryProvenanceStore`/`AnalysisEngine` - both exist as backend, but
   there is no Analysis-stage UI or ROI-selection UI yet to select ROIs
   from, so there is nothing to scan on behalf of yet either. Natural
   follow-up once the Analysis stage UI (next on the rewrite status doc's
   plan) exists.
4. **Spin-box background recolor** on reference-match (the stable app also
   tints `spectral_cube_spin`'s background gold/green, in addition to the
   slider handle). Omitted as redundant, not missing - the slider handle
   color already carries the same information once the slider exists,
   which it didn't in the stable app's original single-spin-box design.

**Verification**: pyflakes-clean on every touched file; the Image panel's
own suite grew from 14 to 23 tests (9 new, covering slider ranging/
re-ranging, drag-driven selection changes in both directions, the
external-change sync gap fix, the reference-highlight/jump button, and the
per-cube completer model) - 23/23. Full `-k "lspri and rewrite"` slice:
359/360, the one failure being the same pre-existing `crop_size_controls.py`
font-metric-environment test named in the entry above, confirmed still
failing identically before this session's changes (not a regression).
Full-window offscreen smoke test (`build_main_window()`) still builds and
shows cleanly. Nothing committed as of this entry.

---

## 2026-09-30 (same day, later): Cube sampling interval - reading and displaying the dataset's typical time-between-cubes

Maintainer follow-up to the slider port above, after reading its Cube/Time-
toggle gap item: asked to (a) confirm what acquisition-timing metadata
already exists, (b) fix "a cube's time" to always mean its first-acquired
frame (no start/middle/end choice, unlike the stable app), (c) compute and
show the dataset's typical time between cubes in the Experimental Plan
section, and (d) pick the scientifically correct term for that value.

**Research first, via a dedicated Explore agent** (this session's context
was mid-way through the slider work) - confirmed against real file:line
citations rather than assumed:

- `ImagingAcquisitionMetadata`/`ImagingCubeTiming` (`lspr_core.
  imaging_models`, shared package) already carry real per-(cube,
  wavelength) timestamps (`acquired_at_unix_ms`) - both the native v6.4
  HDF5 path and the legacy `measureing_times.csv` import path populate
  them; the rewrite's `dataset/model.py` already has `CompactImageTimings`
  (`earliest_ms_by_cube`/`latest_ms_by_cube`, verbatim-ported 2026-09-20)
  as the memory-light form. None of this was missing - it exists, is
  shared with the stable app's own data model, and was simply never wired
  to anything that reads `earliest_ms_by_cube` on this branch yet.
- **The stable app's "choice of start/middle/end" is real** (`_cube_time_
  timestamp_rule`, three values `"first"/"last"/"midpoint"`, default
  `"first"`, `gui/main_window.py:325`) - but confirmed **display-only**:
  `analysis_worker_mixin._acquisition_timestamp_ms_for_cube` (what
  actually gets written to `measurement_backup.h5`) always uses the
  earliest frame regardless of that toggle (`analysis_controller.py:206-
  208`'s own docstring says so explicitly). So fixing this rewrite to
  "first frame only, no toggle" isn't a simplification that loses
  information the stable app's *persisted* data actually uses - it matches
  the stable app's own ground truth, only dropping a *display* preference
  the maintainer explicitly said not to carry over.
- **Confirmed nothing computes "average time between cubes" anywhere in
  the stable app** - grepped `interval`/`period`/`cadence`/`framerate`/
  `sampling_interval`/`acquisition_interval` across every `gui/`/`domain/`/
  `io/` file; every hit was an unrelated `QTimer.setInterval` or axis-tick-
  spacing helper. This is genuinely new computation, not a port.
- **No separate "experiment plan" data model exists** - "Experimental
  plan" is just this branch's renamed label for the same
  `ImagingAcquisitionMetadata` the stable app calls "Metadata"
  (`dataset_experimental_plan.py`'s own docstring already said so; the
  research agent confirmed no sibling `ExperimentPlan`/planned-interval
  concept exists anywhere in the imaging-specific code - `lspr_core.
  ExperimentPlan`/`ExperimentPlanStep` is a *different*, singleLSPR-style
  fluidics/pump-plan-step model, not an imaging acquisition schedule).

**Implementation** - three files, in dependency order:

1. `dataset/model.py`: new free function `cube_sampling_interval_s(compact:
   CompactImageTimings) -> float | None` - median of the gaps between
   consecutive cubes' `earliest_ms_by_cube` entries (sorted by cube index,
   not insertion order), `None` below two timed cubes. **Deliberately a
   free function, not a new method on `CompactImageTimings`** - that class
   is a verbatim port meant to stay byte-for-byte diffable against the
   stable app's `domain/models.py` original (module docstring already said
   so); a third companion function alongside `compact_dataset_image_
   timings`/`rehydrated_acquisition_metadata` keeps that property while
   still living next to the data it operates on.
2. `dataset/module.py`: `DatasetModule.cube_sampling_interval_s()` - the
   query surface, going through `compact_dataset_image_timings` (lazy,
   idempotent) rather than reading `_dataset.compact_image_timings`
   directly, matching `rehydrated_acquisition_metadata()`'s existing
   pattern right above it.
3. `panels/workflow/dataset_experimental_plan.py`: `_on_dataset_loaded`
   appends a "Sampling interval: ~X s (median cube-to-cube gap)" line
   (`_format_sampling_interval`, a small local formatter - seconds under a
   minute, M:SS above). **One real ordering trap found and fixed before it
   shipped**: `cube_sampling_interval_s()` triggers `compact_dataset_
   image_timings`, which *empties* `metadata.image_timings` as a
   documented side effect - the existing "N/M timed" header stat reads
   that same list's length. Computing the interval before that stat would
   have silently zeroed it. Fixed by moving the interval computation to
   the very end of `_on_dataset_loaded`, after every other read of
   `metadata.image_timings` - covered by a dedicated regression test
   (`test_the_n_over_m_timed_header_stat_is_unaffected_by_compaction`)
   specifically so this can't quietly regress later.

**Term chosen: "sampling interval"**, not "period" - period implies strict
periodicity (a signal repeating every exactly-T seconds); real acquisition
timestamps have jitter (I/O, hardware settle time, scheduling), so
consecutive cube-start gaps are never exactly equal. "Sampling interval" is
the standard time-series/instrumentation term for "the typical time
between successive samples" without that periodicity claim - the same
word a UV-Vis kinetics run or a plate reader's read interval would use.
Reasoning is written into `cube_sampling_interval_s`'s own docstring so it
doesn't need re-deriving later.

**Median, not mean, of the gaps** - a single atypically long gap (a paused
run, a stage adjustment) would pull a naive average upward for an
otherwise-regular acquisition; the median stays representative of the
common case. Verified directly: a 5-cube series with one 60s gap among
four 8s gaps still reports 8.0s, not ~20s
(`test_one_atypically_long_gap_does_not_skew_the_median`).

**Not built in this pass, deliberately** - the Cube/Time slider-label
toggle itself (ticks/spinbox showing elapsed time instead of raw index)
was the original ask that prompted this investigation, but this session's
concrete deliverable was the sampling-interval reading/display and the
cube-time-definition decision, not the full toggle. That's real, separable
follow-up work (needs the elapsed-seconds-mapping/formatting equivalent of
the stable app's `AnalysisController._sensorgram_x_values`/
`_format_elapsed_seconds`, ported to whichever module ends up owning it -
`DatasetModule` is a reasonable home now that `cube_sampling_interval_s()`
already lives there, but not decided) - now genuinely unblocked (the
metadata read side is proven and tested) rather than blocked on missing
data, which was this rewrite's status doc's original (correct, at the
time) assessment.

**Verification**: pyflakes-clean on all three touched files plus both new
test files. `test_lspri_rewrite_cube_sampling_interval.py` (9/9 - pure
`dataset/model.py`/`dataset/module.py` logic, regular cadence, the
skewed-median case, cube-index-sort-not-insertion-order, sub-cube-count
edge cases, and compaction idempotency) and `test_lspri_rewrite_
experimental_plan_section.py` (5/5 - the real `ExperimentalPlanSection`
widget, including the header-stat ordering regression test above). Full
`-k "lspri and rewrite"` slice: 373/374, same single pre-existing font-
metric failure as every entry above, confirmed unrelated. Nothing
committed as of this entry.

---

## 2026-09-30 (same day, third pass): "cube interval" replaces "sampling interval"; mean+std replaces median

Maintainer feedback on the pass above, same session: rename the term and
switch the statistic. Two decisions revised in place, not superseded by a
different approach:

1. **"Cube interval", not "sampling interval"** - more specific about what
   the actual sample unit is (a cube, this app's own vocabulary for one
   full wavelength sweep = one sensorgram time point), at no cost to the
   "interval, not period" reasoning from the first pass, which still holds
   and is still documented in the function's docstring.
2. **Mean + spread, not a single median** - reversing the first pass's
   choice. The median was specifically chosen to hide a single atypical
   gap (an operator pause) from skewing the "typical" number; the
   maintainer's call is the opposite: report the mean, and make any
   irregularity *visible* via spread stats alongside it, rather than have
   a robust point estimate quietly absorb it.

**Implementation**: `cube_sampling_interval_s(compact) -> float | None` in
`dataset/model.py` became `cube_interval_stats(compact) ->
CubeIntervalStats | None`, a small frozen dataclass (`mean_s`, `min_s`,
`max_s`, `n_gaps`, `std_s: float | None` - `None` with only one gap, since
a spread needs two data points). `DatasetModule.cube_sampling_interval_s()`
renamed to `.cube_interval_stats()` to match. `dataset_experimental_plan.py`
now shows `"Cube interval: ~8.0 ± 0.3 s (41 gaps)"` (or `"~8.0 s (1 gap)"`
with only one gap) as the compact line, with **min/max moved to the status
label's tooltip** rather than crowding the main text - `statistics.stdev`
(sample, n-1) is used for `std_s`. The ordering trap fixed in the previous
pass (compute the interval stats *after* every other read of `metadata.
image_timings`, since compaction empties that list) carries over unchanged
- the fix was about call order, not about which statistic gets computed.

**Renamed test files** to match (same coverage, updated assertions/names,
one new case added): `test_lspri_rewrite_cube_sampling_interval.py` ->
`test_lspri_rewrite_cube_interval_stats.py` (now also covers: the
skewed-gap case asserts mean *and* max/std actually reveal the irregular
gap rather than checking it's hidden; the exactly-one-gap `std_s is None`
case). `test_lspri_rewrite_experimental_plan_section.py` updated in place
(new tooltip-content test; new exactly-one-gap "(1 gap)", no "±" suffix
test).

**Verification**: pyflakes-clean on all five touched/renamed files. New
unit suite 9/9 (`test_lspri_rewrite_cube_interval_stats.py`), new
integration suite 7/7 (`test_lspri_rewrite_experimental_plan_section.py`).
Full `-k "lspri and rewrite"` slice: 375/376, same single pre-existing
font-metric failure as every prior entry, confirmed unrelated. Nothing
committed as of this entry.

---

## 2026-09-30 (same day, fourth pass): Histogram X-axis zoom/pan re-enabled; "autoscale" redefined as "back to the full 16-bit range"

Maintainer bug report: the Histogram plot's X-axis couldn't be zoomed or
panned at all, "Autoscale" (the corner "A" button/right-click menu) should
reset X to the full 16-bit range starting at 0, and the "A" button itself
wasn't appearing when the view was actually out of autoscale. This is a
direct reversal of a design this same file had from its original build
(2026-09-29): X used to be hard-locked to `[0, 65535]`
(`ViewBox.setLimits(minXRange=maxXRange=full_span)`, `setMouseEnabled(x=
False)`, `hideButtons()` removing the "A" button outright) - a prior
maintainer report at the time had asked for exactly that lock, after
"autoranging... jump[ed] to proposed range" (i.e. X refitting to
data). Both reports are real and not in conflict once distinguished:
"don't let X silently jump to a data-dependent range" (2026-09-29, still
true) vs. "let me manually zoom/pan, but give 'autoscale' back the full
sensor range as its meaning" (2026-09-30, this entry) - the fix keeps the
first property while adding the second.

**`plot.py` changes**:
- `view_box.setLimits(...)` keeps `xMin`/`xMax`/`maxXRange` (still can't
  pan past the sensor's real range or zoom out further than seeing all of
  it) but drops `minXRange` - the exact line that made zooming in
  physically impossible.
- `setMouseEnabled(x=True, y=True)` - X now takes wheel/drag like any
  ordinary axis.
- `hideButtons()` removed entirely, so pyqtgraph's own `PlotItem.
  updateButtons()` decides show/hide again (hidden while both axes already
  match auto-range, shown on hover once they don't) - this alone fixes the
  reported "A doesn't show when out of autoscale" symptom, no new
  visibility-tracking code needed.
- **The actual reason "autoscale" needed its own code, not just un-hiding
  the button**: pyqtgraph's native meaning for both the "A" button and the
  right-click menu's "View All"/"Auto" is "fit to whatever data is
  currently on screen" - for an intensity histogram, X is a fixed physical
  range (a 16-bit sensor's possible values), not a data-dependent one, so
  the maintainer's "back to full 16-bit range" is a different meaning than
  pyqtgraph's default. Both doors were confirmed (by reading the installed
  pyqtgraph source, not assumed) to reach different underlying methods -
  the button calls `PlotItem.enableAutoRange()` (continuous auto-tracking
  mode), the menu's `ViewBoxMenu.autoRange` calls `self.view().autoRange()`
  directly (a one-shot fit) - so a single wrapped instance method was the
  only way to give both the same new meaning without duplicating logic
  kept in sync by hand: `__init__` saves the ViewBox's native `autoRange`
  bound method, replaces it on the instance (not the class - every other
  plot in the app is unaffected) with a wrapper that calls the native
  implementation first (so Y still fits data normally) and then forces X
  back to `[0, 65535]`. `_on_auto_button_clicked` (replacing
  `autoBtnClicked`, wired via disconnect/reconnect on `autoBtn.clicked`)
  calls this same wrapped `autoRange()` instead of `enableAutoRange()`, so
  both doors land on one implementation.

**Six tests in `test_lspri_rewrite_histogram_panel.py` updated** - three
renamed/rewritten to pin the new behavior instead of the old lock
(`test_auto_range_button_is_not_permanently_hidden`, `test_auto_range_
resets_x_to_the_full_16bit_range_not_data_bounds` - covers the menu door
via a direct `vb.autoRange()` call, `test_x_axis_mouse_interaction_is_
enabled`), three new (`test_auto_button_click_resets_x_to_the_full_range` -
covers the button door separately, since it's a genuinely different code
path; `test_x_axis_can_actually_zoom_in`; `test_x_axis_cannot_be_panned_
outside_the_sensor_range`). `test_x_axis_spans_the_full_16bit_range_
regardless_of_data` (bin-edge computation, unrelated to viewbox zoom/pan)
was untouched and still passes.

**Verified directly against the running widget**, not just the test
suite: constructed a real `HistogramPlot`, confirmed `mouseEnabled() ==
[True, True]`, `buttonsHidden == False`, a manual `setXRange` zoom holds,
clicking the "A" handler and calling `vb.autoRange()` (the menu's own
call) both reset X to exactly `[0.0, 65535.0]`, and an out-of-bounds pan
attempt gets clamped rather than escaping the sensor range.

**Verification**: pyflakes-clean on `plot.py` and the test file. Histogram
suite grew from 26 to 28 tests, 28/28. Full `-k "lspri and rewrite"`
slice: 378/379 (up from 375/376 - +3 net new tests), same single
pre-existing font-metric failure as every prior entry in this log,
confirmed unrelated. Nothing committed as of this entry.

---

## 2026-09-30 (same day, fifth pass): Canvas tools bar tightened; 1px border seam on both Image-panel bars

Maintainer bug report: the Image panel's canvas tools bar (the vertical
Select/Add ROI icon strip docked to the canvas, built earlier the same day)
was too wide with icons too big, and the bars inside the Image panel have
no visible boundary against the canvas because they share its background.

**Sizing** (`canvas_tools.py`): `_BUTTON_SIZE`/`_ICON_SIZE` shrunk from
`28`/`22` (borrowed as-is from the horizontal Transforms row's own buttons
when this bar was first built) to `22`/`16`; layout margins `4px`/spacing
`6px` tightened to `2px`/`3px` (`_BAR_MARGIN`/`_BAR_SPACING`, new named
constants). Net effect: the bar's own width drops from 36px to 26px, and
`sizeHint().width()` is now pinned by test to exactly `_BUTTON_SIZE + 2 *
_BAR_MARGIN` - i.e. defined to hug its one column of buttons, not an
independent guess.

**Border seam, both bars** - the real fix, not just resizing: this app's
theme already has a token built for exactly this
(`GuiTheme.toolbar_border`, used by several existing `#toolbarSection`
-style QSS rules in `lspr_ui/theme.py` for the stable app), and this
exact `panels/image/` directory already has the convention for applying it
to a non-pyqtgraph widget that floats near the canvas
(`CropSizeControls`/`MeasureCalibrationControls`'s `refresh_theme(theme)`
method + an object-name-scoped inline stylesheet, re-applied at
construction and on every live theme switch via `ImagePanel.
refresh_theme`) - followed the same convention for both bars rather than
inventing new styling:

- `CanvasToolsBar` (new `refresh_theme(theme)` method, object name
  `canvasToolsBar`): `border-right: 1px solid {theme.toolbar_border}` -
  only the right edge, since that is the one side that actually touches
  the canvas; the other three border this panel's own chrome.
- The Image panel's own Cube/λ navigation rows + status row - previously a
  bare `QVBoxLayout` (`controls`) added directly to the panel's layout, not
  a widget, so it had nothing a QSS `border` rule could attach to. Wrapped
  in a real `QWidget` (`self._controls_bar`, object name
  `imageNavigationBar`) with a new `ImagePanel._refresh_controls_bar_theme`
  method: `border-bottom: 1px solid {theme.toolbar_border}` - only the
  bottom edge, where this bar meets the canvas below it.

Both wired into `ImagePanel.refresh_theme()` (the existing live-theme-
switch entry point) via the same `hasattr(self, "_x")` guard already used
for `_crop_controls`/`_measure_controls`/`_cursor_overlay`, so a theme
switch updates the border color exactly like every other themed overlay in
this panel - pinned by two new tests forcing `BRIGHT_THEME` and checking
`toolbar_border` shows up in the resulting stylesheet.

**Verified directly against a running widget**: bar width 26px (was
implicitly ~36px), button size exactly 22x22, both stylesheets contain
`border-right`/`border-bottom: 1px solid #2a313b` (dark theme's
`toolbar_border`) and nothing else.

**Verification**: pyflakes-clean on all four touched files. Canvas-tools-
bar suite grew from 15 to 19 tests (+4: button/bar sizing, border-present-
on-one-edge-only, theme-switch), Image-panel suite grew from 23 to 25
(+2: same border/theme-switch pair for the navigation bar) - 19/19 and
25/25. Full `-k "lspri and rewrite"` slice: 384/385, same single
pre-existing font-metric failure as every prior entry in this log,
confirmed unrelated. Nothing committed as of this entry.

---

## 2026-09-30 (same day, sixth pass): Wavelength slider tick labels lied about what clicking them selects - real bug, fixed

Maintainer bug report with a screenshot: clicked the wavelength slider
where it read "400", the status line showed "Cube 197, 470 nm" - a real
70 nm discrepancy, not a rounding nit. Suspected a "0 nm" wavelength being
involved.

**Diagnosis.** Not a 0 nm issue - `DatasetModule.wavelengths_for_cube`
derives its values purely from real loaded `ImageRecord`s, no synthetic
entry gets injected anywhere in that path (checked `dataset/model.py`'s
`wavelengths_for_cube` and `dataset/io.py`, no such default exists). The
actual bug: `_wavelength_slider_major_ticks` (ported earlier the same day
from the stable app's tick-redesign doc) picked a **rounded 100 nm
boundary** ("400") as the label text, but positioned that label at
whichever **real** wavelength happened to be numerically closest to that
boundary - two different numbers, silently conflated. For a dense,
roughly-uniform wavelength grid the two are close enough that nobody
notices; this rewrite's wavelength set is per-cube
(`wavelengths_for_cube`, "a cube can be short a wavelength another cube
has" - own docstring, 2026-09-23), so a specific cube's actual available
wavelengths can legitimately have a gap right where a round-100 boundary
falls. Cube 197 evidently has nothing near 400 nm but does have 470 nm -
so "closest real value to the fabricated label 400" resolved to index
470, and clicking there is `_on_wavelength_slider_changed`'s job:
correctly resolve *that* index to *its* real value (470), which is right
by definition - the *label* was simply wrong about what index it was
sitting on. This was in fact the exact, already-named risk flagged when
this widget was ported the same day ("Known, deliberate limitation... a
non-uniform wavelength grid would make position drift from what the
labels say... flagged to the maintainer as a bigger follow-up if it ever
matters for real data") - it now does.

**Fix, not a workaround**: labels no longer try to hit round numbers at
all. `_wavelength_slider_major_ticks` now picks evenly-spaced *indices*
(via the same `_nice_count_interval` helper `_cube_slider_major_ticks`
already uses) and labels each with the **real value at that index**,
rounded for display only (`f"{values[index]:.0f}"`) - the same shape the
cube slider already had, which is exactly why the cube slider never had
this bug in the first place: it was never trying to hit a "nice" number,
only ever labeling the real value at a chosen index. A label can now never
disagree with what clicking it selects, for any gap shape. Cosmetic
trade-off, named rather than hidden: labels are no longer guaranteed
round numbers ("471" instead of "470"/"400") - correct-but-plain beats
clean-but-wrong for this app's own stated priority order (correctness
above GUI polish). `from math import ceil, floor` removed from `panel.py`
- no longer used by anything after this fix.

**Verification**: pyflakes-clean. Three new tests pin the exact reported
scenario (a deliberately gappy `(200, 250, 470, 500, 600)` set - the tick
nearest 400 must read "470", never "400"; every label equals `values[index]`
exactly, not just near it), the small-dataset every-point-labeled case,
and the empty/single-point edge cases. Image-panel suite 25 -> 28, 28/28.
Full `-k "lspri and rewrite"` slice: 387/388, same single pre-existing
font-metric failure as every prior entry in this log, confirmed unrelated.
Nothing committed as of this entry.

---

## 2026-09-30 (same day, seventh pass): Wavelength slider gets a "0 nm" origin and a scale-break glyph

Maintainer follow-up to the tick-label fix above: wavelength is a
ratio-scale physical quantity with a real, meaningful zero - a plot of it
should either draw the axis from 0 or explicitly mark that it doesn't, not
silently start at the first real data point as if there were nothing
before it. Asked for the first real tick to stay labeled (already true
since the previous fix - `_wavelength_slider_major_ticks` always includes
index 0) and for a "0" origin plus an axis/scale break between it and the
first real wavelength, the standard convention for "this axis has a real
zero, the gap to the data is real too, but it's compressed rather than
drawn to scale."

**`DataAxisSlider` (`data_axis_slider.py`) gained `set_axis_break(label)`**
- reserves a small fixed zone (`_BREAK_ZONE_WIDTH = 26px`) at the very left
of the track for a fixed origin tick+label plus a break glyph (two short
parallel diagonal strokes crossing the rail - `_paint_axis_break`), then
the real, selectable track begins exactly where it always has, just
shifted right by that reserved width. `None` (the default) disables it
completely - zero behavior change for any caller that doesn't opt in.
**Deliberately opt-in per instance, not a global widget change**: the cube
slider never calls it and renders exactly as before - an index axis (cube
number) has no physically meaningful zero-gap the way a wavelength does,
enumeration already starts at the first real item.

**Implementation notes**:
- `_track_rect()` is the single source of truth both `paintEvent` and
  hit-testing (`_index_from_x`) already read from - growing its left inset
  when a break is set means every other method treats the break zone as
  "outside the track" automatically, no separate case needed in the click/
  drag handlers.
- The origin label, its tick, and the break glyph are drawn entirely
  within the reserved zone (verified by construction: `_BREAK_ZONE_WIDTH`
  comfortably fits "0" + a 3-digit label + the glyph with margin, and
  confirmed by rendering a real slider to a pixmap and inspecting the
  geometry directly, not just trusting the numbers).
- Font setup (`painter.font()`/`setPixelSize(9)`) moved earlier in
  `paintEvent` so the origin label can share it - previously only set up
  inside the `count > 1` tick-drawing branch, which the origin draw needed
  independently of how many real ticks exist.
- `panel.py`: `_refresh_wavelength_range` calls `set_axis_break("0" if
  wavelengths else None)` - always on once a dataset with wavelengths is
  loaded (real wavelengths are never actually 0 nm, so this is
  unconditional, not a threshold check), cleared on an empty dataset. The
  cube slider's own setup is untouched.

**Verified against a rendered pixmap**, not just geometry assertions: grabbed
a real `DataAxisSlider` with a break enabled, confirmed by direct pixel-
region inspection that the break glyph, origin tick, and the real track's
first tick+handle land in the expected positions with no overlap. Label
*text* legibility could not be confirmed the same way - this sandbox's
offscreen Qt platform has no real fonts loaded, so glyphs render as tofu
boxes regardless of what string was requested (this app's CLAUDE.md "Qt
widget sizing verification" pitfall) - label *content* is instead verified
by the unit/integration tests' string assertions, which do not depend on
font rendering at all.

**Verification**: pyflakes-clean. New `test_lspri_rewrite_data_axis_slider.py`
(6/6 - track-rect reservation math, restore-on-clear, same-label-is-a-
no-op, hit-testing clamps into the real track only, paints without error
enabled/disabled/with-and-without-break) plus 3 new Image-panel tests
(wavelength slider gets the break once loaded, cube slider never does,
break clears with the dataset) - Image-panel suite 28 -> 31, 31/31. Full
`-k "lspri and rewrite"` slice: 396/397, same single pre-existing
font-metric failure as every prior entry in this log, confirmed unrelated.
Nothing committed as of this entry.

---

## 2026-09-30 (same day, eighth pass): Axis-break redesign (real bug in the previous pass) + Image panel navigation polish

Maintainer report with a screenshot, on the axis-break feature from the
previous pass, same day: "created two 0 ticks, first one not working
(cannot be dragged there)."

**Root cause, confirmed by direct experiment, not guessed**: verified via
a script that `_wavelength_slider_major_ticks` correctly labels a normal
gappy array's index 0 with its real value (e.g. "470", not "0") - no bug
there. The screenshot's genuine two-"0"s meant the maintainer's *actual
loaded dataset* has a real `wavelength_nm=0.0` record at index 0 (almost
certainly a dark/reference frame acquired and stored alongside the real
spectral images) - which the previous pass's `set_axis_break("0")` had no
way to know about: it always drew a synthetic, fixed "0" origin *outside*
the real value range regardless of what the real data already contained,
so a dataset that already started at a real 0 nm got a fake, non-draggable
"0" duplicate sitting right next to the real, draggable one. Confirmed by
rendering both scenarios (synthetic-only data, and a real 0.0-containing
array) to pixmaps and inspecting them directly - the failure only shows up
in the second case, exactly matching the report.

**Redesign, not a patch**: `DataAxisSlider.set_axis_break()` removed
entirely - no synthetic tick, no reserved zone, no caller opt-in. Replaced
with `_large_gap_boundaries()`, a private method that detects an unusually
large consecutive gap (more than 4x the dataset's own median gap) directly
in the *real* `_tick_values` array already passed to `set_ticks`, and
`_paint_gap_break()`, which draws the same "//" scale-break glyph on the
rail between the two real ticks flanking a detected gap - both stay real,
labeled, and selectable, exactly like every other tick. This self-contained
design (no caller wiring needed at all - `panel.py`'s `set_axis_break(...)`
call site was deleted outright) naturally handles both cases correctly:
a dataset with a real 0 nm frame shows the break between the real 0 and
the real first spectral wavelength (both draggable); a dataset with no 0 nm
data at all simply never invents one. `_wavelength_slider_major_ticks`
(`panel.py`) gained a matching rule - always force-label both indices
flanking a detected gap (mirroring the widget's own detection as an
independent copy, sharing the same `DataAxisSlider._GAP_BREAK_RATIO`
threshold constant rather than a second hardcoded `4.0`) - so the glyph
never ends up flanked by an unlabeled tick.

**Verified against a rendered pixmap of the exact reported scenario**: a
dataset with real wavelengths `(0.0, 470.0, 500.0, 550.0, 600.0)` (0.0 as a
real record, like a dark/reference frame) - the handle sits on the real,
single "0"-labeled tick, the break glyph appears immediately after it, and
"470" labels the next real tick, with no duplicate and no dead zone.

**Same-message follow-up requests, all straightforward, all implemented**:
- **Navigation moved to the bottom of the Image panel** - `_build_ui`'s
  outer layout now adds `canvas_row` before `_controls_bar`;
  `_refresh_controls_bar_theme`'s border flipped from `border-bottom` to
  `border-top` to match (the bar's border always goes on whichever edge
  touches the canvas).
- **"Cube "/" nm" removed from the number fields** - `_cube_spin.setPrefix`
  and `_wavelength_spin.setSuffix` calls deleted; the row's own title label
  ("Cube" / "λ (nm)") already said what the number means, so the field
  repeating it was redundant.
- **Reference-jump ("star") icon removed** - the button, its layout
  placement, and `_on_reference_jump_clicked` (now a zero-caller method)
  all deleted. The separate reference-highlight *coloring* of the slider
  handle (gold/green when viewing the reference frame) is untouched - a
  different feature (a color, not an icon) the maintainer did not ask to
  remove.
- **Titles and number fields aligned across both rows** - "Cube"/"λ (nm)"
  are different lengths, and so are their number fields' typical contents,
  so without a shared width the two sliders started at slightly different
  x positions and the two fields didn't line up on the right either. Both
  title labels now share one `setFixedWidth` (the wider of the two's
  `sizeHint`), both spin boxes share another.
- Unused imports cleaned up after the button removal (`QToolButton`,
  `QSize`, `transparent_icon_button_stylesheet`).

**Tests**: `test_lspri_rewrite_data_axis_slider.py` rewritten for the new
gap-detection design (7/7 - regular grid has no break, break detected at a
real gap, boundary-index direction, multiple gaps, fewer-than-three-points
guard, both flanking ticks stay real/selectable, paints without error).
Three obsolete `set_axis_break`-era tests removed from
`test_lspri_rewrite_image_panel.py`, two new ones added for the matching
force-label rule in `_wavelength_slider_major_ticks`; the two reference-
jump-button tests removed; one new test each for prefix/suffix removal,
shared title/field widths, and canvas-before-navigation layout order; the
border-side test flipped to `border-top`. Image-panel suite 31 -> 32,
32/32. Full `-k "lspri and rewrite"` slice: 398/399, same single
pre-existing font-metric failure as every prior entry in this log,
confirmed unrelated. Nothing committed as of this entry.

---

## 2026-09-30 (same day, ninth pass): "0 nm = dark frame" documented at its canonical spot

Maintainer follow-up, confirming the gap-detection redesign above matches
intent (dataset-driven, no synthetic ticks, no break at all when there's no
real gap) and asking for the "0 nm is a dark/background frame, a different
job than every other wavelength" domain fact to be written down, noting it
was probably already documented somewhere.

**It was** - found and confirmed, not assumed: the stable app's
`gui/analysis_worker_mixin.py` has a real, working consumer of exactly this
convention, `dark_frame_pixel_impact()` (`dark_mask = wavelengths == 0.0`,
used to simulate dark-current subtraction and measure its effect on a
computed formula value like absorbance); `docs/row_banding_artifact_
analysis_2026-09.md` independently calls the same thing "`WL0`, LED off".
Confirms the previous entry's "almost certainly a dark/reference frame"
was correct, not a guess that happened to work.

**Written down at its canonical spot**: `DatasetModule.wavelengths_for_cube`
(`dataset/module.py`) - the query surface every rewrite consumer of
wavelength data actually goes through - gained the confirmed statement,
its code pointers (both sources above), and an explicit warning for future
code: *any code that treats "all wavelengths" as spectral data (a fit, a
spectrum plot, an average across wavelength) must exclude `0.0` explicitly
- nothing in this query surface filters it out automatically.* `wavelengths()`
got a one-line pointer to the same docstring. `dataset/model.py`'s
`ImageKey` (a verbatim port that must stay byte-for-byte diffable against
the stable app's `domain/models.py` original - confirmed stable's own
`ImageKey` also carries no docstring, so adding one here would have been a
real deviation) got a plain `#` comment above the class instead, pointing
at the canonical docstring rather than repeating it. `data_axis_slider.py`
and `panel.py`'s own gap-detection docstrings (previous entry) were
trimmed to cross-reference the canonical spot instead of each carrying
their own copy of the explanation.

**Not yet done, flagged rather than silently skipped**: nothing in the
rewrite's `analysis/` package excludes `wavelength == 0.0` from spectral
computations yet (Spectra panel, any future fit/average-across-wavelength
code) - the stable app's `dark_frame_pixel_impact` is a diagnostic/
measurement tool, not a filter wired into the main compute path either, so
this isn't a regression, but it is the kind of gap the new docstring's
explicit warning exists to prevent going unnoticed once real spectral
compute code is built on this branch.

**Verification**: pyflakes-clean on all four touched files (docstring/
comment-only changes, no logic touched). Full test files covering them
(`test_lspri_rewrite_data_axis_slider.py`, `test_lspri_rewrite_cube_
interval_stats.py`, `test_lspri_rewrite_image_panel.py`) re-run: 48/48,
confirming the docstring-only nature of this pass. Nothing committed as of
this entry.

## 2026-09-30 (same day, tenth pass): the "not yet done" claim above was wrong - `AnalysisEngine` already had the gap live

Maintainer follow-up on the previous entry, asking for the dark-frame
exclusion rule to actually be implemented: does the dataset have a `0.0` nm
frame, and if so, exclude it from every routine that treats "all
wavelengths" as spectral data, while keeping it selectable/previewable in
the Image panel (already true - see the ninth-pass entry, untouched here).

**Correction to the previous entry's framing, found while scoping the
work**: it said "nothing in the rewrite's `analysis/` package excludes
`wavelength == 0.0` from spectral computations yet... this isn't a
regression" on the reasoning that no real spectral compute code existed yet
to have the gap. That reasoning was wrong - `AnalysisEngine` (`analysis/
engine.py`) is not a placeholder here, it's real, wired backend code (the
status doc's own words: "the computational backend is largely complete and
wired end-to-end"). Three of its methods already iterated every wavelength
`DatasetModule.wavelengths_for_cube` returns with no filter -
`_gather_wavelength_inputs` (feeds `compute_cell`), `_gather_current_inputs`
(the planning-time fingerprint), and `_naming` (the snapshot filename
scheme). A dataset with a real `0.0` nm frame would have had it silently
become a point in a stored `CellResult`, then in `formula_spectrum`, then
fed to a gaussian/polynomial fit and eligible to be picked as the
"maximum"/"centroid" metric - a live, reachable bug once a real dataset with
a dark frame is analyzed, not a future risk.

**Fixed**: all three methods now skip `is_dark_frame_wavelength(wavelength)`
(new helper in `dataset/model.py`, next to a new `DARK_FRAME_WAVELENGTH_NM`
constant - both placed there rather than in `analysis/`, matching where the
domain fact is already canonically documented).
`_gather_current_inputs`/`_gather_wavelength_inputs` had to be fixed
*together*, not just the compute side: they build the live/stored halves of
the same fingerprint comparison (`plan_recompute`/`compute_fingerprint`), so
excluding the dark frame from only one would have made every cell look
permanently stale against its own stored fingerprint - the same failure
shape as the placeholder-mask-version bug from 2026-09-23. `DatasetModule.
wavelengths_for_cube`'s docstring was updated to point at `AnalysisEngine`
as the real (not aspirational) enforcement site. Full policy written down at
`docs/dark_frame_wavelength_zero_policy.md`, including what this does *not*
cover yet: a real dark-current-subtraction *compensation* mode is future
work with its own open design questions (deliberately out of scope here,
matching the fractional-pixel-weighting toggle's already-flagged shape).

**Verification**: new `RewriteDarkFrameExclusionTest` (`tests/integration/
test_lspri_rewrite_analysis_engine.py`) - a real one-cube, four-wavelength
(including `0.0`) dataset through the real `_build_analysis_engine` module
graph, asserting the stored cell/formula spectrum/metric all exclude the
dark frame, and that `preview_recompute` reports the cell up to date after
one run (the fingerprint-agreement check, not just a stored-value check).
`test_lspri_rewrite_analysis_core.py` and `test_lspri_rewrite_analysis_
engine.py` re-run in full alongside it.
