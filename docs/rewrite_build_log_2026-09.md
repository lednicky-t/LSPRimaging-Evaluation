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
