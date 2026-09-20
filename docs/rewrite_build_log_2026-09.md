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
emission; `undo_manager.
undo()` restores the original five ROIs and remaps the still-live part of
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
