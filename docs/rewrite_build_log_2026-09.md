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
