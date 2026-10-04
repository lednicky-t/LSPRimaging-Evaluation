# LSPRi Evaluation rewrite: status and plan (2026-09-30)

Written by an unattended overnight session (maintainer asleep, no check-ins
possible) to re-orient a cold future session and to give the maintainer a
single place to read "where are we, what's next, what needs a decision."
Supersedes nothing - `AGENTS.md`, `rewrite_architecture_sketch_2026-09.md`,
and `rewrite_build_log_2026-09.md` remain the design/history authorities;
this is a snapshot and a plan layered on top of them. Read this first when
resuming cold, then go to the build log for the "why" behind any specific
decision mentioned here.

**One piece of found work was completed and committed as part of producing
this document** - see "Work done this session" at the end. Everything else
below is analysis; no other code was written.

---

## 1. Current state, stage by stage

`rewrite` is 59 commits ahead of `develop` as of this session (58 pre-existing
+ this session's commit). Roughly **26,000 lines** of new-tree code exist
under `src/lspr_imaging_app/{dataset,roi,image_tools,analysis,panels,
selection,undo,storage,diagnostics}/`, alongside the still-fully-working old
app (`gui/`, `domain/`, unchanged). The honest one-line summary: **the
computational backend is largely complete and wired end-to-end; almost none
of it has a real UI in front of it yet**, except Dataset and Image Tools'
Transforms/Mask/Crop/Rotate/Measure, which do.

### Dataset — backend real, UI real
- `dataset/module.py`'s `DatasetModule` is fully built (load/clear/export,
  background-thread loading via `AnalysisWorker`/plain `threading.Thread`,
  never `QThreadPool`, per the zarr-crash invariant). `dataset/io.py` is the
  verbatim `io/dataset.py` port (2026-09-20).
- UI: the Workflow panel's Dataset section is real and detailed - folder
  row, Summary/Experimental-plan/Export nested sections, chunk-grid preview
  toggle wired to `ImagePanel`. This stage is functionally the most complete
  in the whole rewrite.

### Image Tools — backend real, UI real for Transforms/Mask, placeholder for Chromatic/Background
- `image_tools/geometry/module.py` (crop/rotate/flip + Measure's calibration
  commands), `image_tools/mask/module.py` (algorithmic + file mask,
  paint/morphology), `image_tools/chromatic/module.py` (landmarks, refit,
  `affine_for`/`warp_mask`), `image_tools/background/module.py`
  (estimate/apply split) are all built. **Correction (2026-09-30, this
  claim was wrong in this doc's first version and in AGENTS.md until fixed
  the same day)**: Geometry, Chromatic, and Background are wired to the
  shared `undo.undo_manager`; **Mask deliberately is not** - its own module
  docstring documents that the old app's `mask_controller.py` never
  undo-tracked mask edits either, so this matches existing behavior on
  purpose, not a gap. See §3.
- UI: Transforms (crop/rotate/flip/Measure) has real, maintainer-tested
  controls including right-click menus and live previews - this is the
  single most polished corner of the rewrite. Mask has a real settings
  section. **Chromatic correction and the ROI editor's own detection
  controls are still `_section_placeholder(...)` stubs in
  `panels/workflow/panel.py`** (`_build_image_tools_section`) - the backend
  exists, nothing drives it from this panel yet. Background removal has a
  real section (`background_removal_section`).

### ROI Selection — backend real, UI is a single placeholder
- `roi/toolbox.py`'s `RoiToolbox` command API (move/resize/delete/detect/
  group operations), `roi/detection.py` (verbatim port), `roi/rasterize.py`
  (binary + fractional/supersampled rasterization, §6a, both geometry types)
  are all built and wired to undo.
- UI: `panels/workflow/panel.py`'s `_build_roi_selection_section` is still
  exactly one placeholder ("Circles" -> `_section_placeholder("Circle ROI
  detection/editing")`). AGENTS.md/the architecture sketch call for three
  sub-tabs here (Finding / Editing / Groups) - **none exist yet.** There is
  also no ROI/Group table panel (see below) and no way to select/move/group
  an ROI from the Image panel's own interaction layer yet either, as far as
  this session could verify from the code (not exhaustively tested live -
  see "How this was verified" at the end).

### Analysis — backend real and wired end-to-end, UI is three placeholders
- `analysis/store.py`, `provenance.py`, `planner.py` (`plan_recompute`),
  `worker.py`, `tasks.py`, `engine.py` (`AnalysisEngine`) are all built.
  Per `analysis/provenance.py`'s own docstring, the originally-sketched
  `ProvenanceStore.fingerprint_for` abstract interface was **deliberately
  never implemented as a separate class** - `InMemoryProvenanceStore` (in
  `store.py`) is the real, permanent design (HDF5 doesn't support safe
  concurrent read/write, so everything is bulk-loaded into memory once at
  construction). This is a real, considered design decision, not an
  unfinished stub - don't "finish" `ProvenanceStore.fingerprint_for` without
  reading that docstring first.
- Weighted reduction (§6a's `weighted_mean`/`median`/`trimmed_mean`/
  `plane_fit`) is built and numerically verified against its unweighted
  counterpart, but **not yet wired**: `analysis/tasks.py`'s `compute_cell`
  still calls the binary `rasterize_sample`/`rasterize_reference`, not
  `rasterize_fractional` - this was already known and flagged in the
  architecture sketch (§9, item 2), still true.
- UI: `panels/workflow/panel.py`'s `_build_analysis_section` is three
  placeholders (ROI's math / Metric trace / Statistics). The Run/Stop/Live-
  preview controls that would call `AnalysisEngine.run_analysis(scope)`
  don't exist in any panel yet.

### Panels — Image and Histogram are real; ROI table, Spectra, Sensorgram are 100% scaffolding
- `panels/image/panel.py` - real, first panel built, gained `image_rendered`/
  `image_cleared` signals 2026-09-29/30 for Histogram to consume.
- `panels/histogram/panel.py` + `plot.py` - real, built 2026-09-29/30 (see
  build log's most recent entry for the full account: self-sufficient
  design, `HighlightRangeModule` as new shared cross-cutting state, shared
  `CursorOverlay`, non-modal settings dialog, a 4-agent `/simplify` cleanup
  pass already applied and verified).
- `panels/roi_table/panel.py`, `panels/spectra/{panel,plot}.py`,
  `panels/sensorgram/{panel,plot}.py` - every method still raises
  `NotImplementedError`. **Zero real UI exists for ROI table display,
  Spectra, or Sensorgram.** These are the three panels the original
  "sensorgram bugs motivated this rewrite" diagnosis cared about most, and
  they're the least-built part of the whole effort so far - worth naming
  explicitly rather than letting recency bias (Histogram/Measure/Crop/Rotate
  were the last few days' work) suggest otherwise.

### Cross-cutting: Selection, Undo, Storage/Sessions, Diagnostics — all real
- `selection/` (`SelectionModule`, `ReferenceFrameModule`,
  `HighlightRangeModule` - the newest, added with Histogram) - real, small,
  matches the sketch's "one intentionally shared piece of state" design.
- `undo/manager.py` - real, one shared `undo_manager`, `FunctionCommand`,
  `begin_batch`/`end_batch`. Wired into 4 of 5 command-owning modules (ROI
  Toolbox, Geometry, Chromatic, Background); `Mask` is a deliberate,
  documented exception, matching the old app's own never-undo-tracked mask
  edits (see §3). AGENTS.md's text was stale on both points until corrected
  2026-09-30.
- `storage/session.py`, `session_coordinator.py`, `session_index.py`,
  `app_settings.py` - real. Sessions (create + switch only, blank start, no
  rename/duplicate/delete - deliberately scoped that way, see AGENTS.md) and
  app-level settings (last-dataset, theme, layout presets, **and now nested
  Workflow-section expand state**, see "Work done this session") are both
  built and tested.
- `diagnostics/` (72 lines) - the `@instrumented` decorator exists and is
  used everywhere (every command method above is wrapped in it), giving the
  timing/DEBUG-logging visibility the rewrite wanted "for free" from day
  one. No performance panel/viewer consumes this data yet - that was always
  scoped as a later, optional addition (sketch §3), not a gap.

### Dev-preview shell
`app_rewrite.py`/`main_rewrite.py` (`lspri-evaluation-rewrite` console
script, its own Suite Launcher card) constructs every module above and wires
them into the `WorkflowPanel` tab strip. It builds and smoke-tests cleanly
offscreen (`QT_QPA_PLATFORM=offscreen`, confirmed again this session). What
it cannot yet do, because the panels above don't exist: detect/edit/group an
ROI, run an analysis, or see a spectrum/sensorgram plot. A live demo today
would show a real, responsive Dataset/Image-tools workflow and then hit a
wall at ROI Selection.

---

## 2. Remaining work, and the recommended approach for each piece

| Area | What's left | Recommended approach | Why |
|---|---|---|---|
| ROI Selection UI | Finding/Editing/Groups sub-tabs in the Workflow panel, wired to `RoiToolbox`'s existing command API | Build fresh against the real API - **not a port**, since the old app's `roi_geometry_mixin.py`/`group_table_controller.py` duplicated logic that `RoiToolbox` now centralizes | AGENTS.md/sketch §7 already specify the three-sub-tab shape and "one backend, several front doors" - this is implementation of an already-settled design, not a new design decision |
| ROI/Group table panel | Entire panel (`panels/roi_table/panel.py` is one `_redraw` stub) | Build fresh, calling `RoiToolbox`'s command API for rename/recolor/reorder/bulk-select, per sketch §7's "naturally tabular" framing | Same reasoning - design settled, not built |
| Chromatic correction UI | **Built 2026-10-04 as the Image panel's "Chromatic Corrections" ribbon tab** (maintainer decision: not in the Workflow panel): `panels/image/chromatic_tab.py`; automatic landmarks only, no manual landmark editing | Run icon + gear popover (Default/Detailed switch, landmark count, wavelength spread, show landmarks, apply correction, clear, advanced); detection in `image_tools/chromatic/auto_landmarks.py`, thread wrapper `auto_task.py`; one static correction from the reference cube (see `chromatic_landmark_lab_findings_2026-10-04.md`) | Done; needs the maintainer's visual check |
| Analysis stage UI | Run/Stop/scope controls + ROI's-math/Metric-trace/Statistics sections | Build fresh against `AnalysisEngine.run_analysis()`/`get_metric()`/`get_spectrum()`/`status_summary()` | Backend complete and already the most heavily-tested area (per commit history: "Wire AnalysisEngine to the real modules - closes 4 gaps, fixes 2 real bugs") |
| Spectra panel | Entire panel | Build fresh, reading `AnalysisEngine` + `RoiToolbox` (coloring/selection) + `Selection`, own fit-curve display, **never fit on the GUI thread** (non-negotiable invariant) | New display code against a stable, already-built backend |
| Sensorgram panel | Entire panel | Same as Spectra, plus reading Dataset's acquisition metadata for the pump-plan-step overlay (proposed, not yet built anywhere) | This is the panel the entire rewrite exists to fix - see §3's pitfall about not reintroducing its old bug pattern |
| Fractional-pixel-weighting wiring | `analysis/tasks.py`'s `compute_cell` still calls binary rasterization, not `rasterize_fractional` | **Corrected while writing this doc: not simply mechanical.** `compute_cell` (18 parameters already, `analysis/tasks.py:228`) has no "fractional enabled" concept anywhere yet - adding one means deciding where the toggle lives (per-analysis? persistent setting?), threading it through `compute_cell`'s call chain from `engine.py`/`worker.py`, and deciding whether it becomes part of a cell's provenance fingerprint (enabling it would change every already-computed cell's *result* without changing its *inputs* under the current fingerprint scheme - a real gap the simplified "any input change triggers recompute" rule doesn't obviously cover, since this isn't an input, it's a reduction-method variant). Both raster and weighted-reduction math are done and verified; the wiring has open questions, moved to §6 | Both halves (raster + weighted reduction) are already built and independently verified - only the integration has unresolved design surface |
| Highlight-range → Mask/ROI wiring | `HighlightRangeModule.range_changed` isn't connected to `MaskModule.set_histogram_highlight_range` or `RoiToolbox.set_detection_settings` yet | **Half mechanical, half an open question - see §4** | `set_histogram_highlight_range(min, max)` matches the signal's payload directly; `set_detection_settings(settings: AreaRoiDetectionSettings)` does not - see §4, item 1 |
| AGENTS.md undo section | **Done 2026-09-30.** Said "RoiToolbox is the first module wired... Geometry/Mask/Chromatic/Background should adopt the identical pattern once built" - actually inaccurate two ways: three of the four (not all four) had already adopted it, and Mask deliberately never will | Updated to name Geometry/Chromatic/Background as wired, and to document Mask's exception explicitly (matches the old app's own mask_controller.py, which never undo-tracked mask edits either) | Pure doc-correction, zero code risk |

**Pattern to keep watching for** (already happened twice, will likely happen
again): when an architecture-sketch assumption meets the real code, it's
been wrong at least twice so far - `io/dataset.py` didn't split by format as
assumed (2026-09-20), and `processing/chromatic.py` had an undocumented
dependency on `roi_detection.py` (same day). Both times, the mismatch was
surfaced explicitly and the sketch/AGENTS.md was corrected in place rather
than silently worked around. Whoever builds the ROI-selection or Analysis UI
next should expect the same kind of surprise and handle it the same way -
flag it, don't guess.

---

## 3. Comparison to the stable app's approach, and why the rewrite diverges

| Stable app pattern | Rewrite's replacement | Why (from the entanglement diagnosis / feature inventory) |
|---|---|---|
| No event system - every "X changed" is a direct method call to a hand-maintained list of consumers (`_update_roi_overlays()`: 69 call sites/13 files) | Typed per-module `pyqtSignal`s, each module owning its own signals | The direct-call pattern is *why* `main_window.py` grew to 8,601 lines - every new consumer meant editing the producer |
| Cosmetic and computational changes both trigger the same refresh call (e.g. recoloring a group re-runs `_render_sensorgram_display()`) | `CosmeticChange`/`ComputationalChange` as distinct dataclass payload types, not a naming convention | A convention can be forgotten; a different Python type cannot - directly targets the recoloring-triggers-recompute bug class |
| 8 independently-maintained sensorgram caching tiers, later found to disagree with each other (root cause of the disappearing-plot bug and others) | One store (`data.h5`/`InMemoryProvenanceStore`), one recompute rule (`plan_recompute`, any-input-change-triggers-recompute) | Finding #2 in the feature inventory: *"two independently-maintained code paths answering 'is this fresh' differently" was the empirical root cause*, not a hypothetical |
| `_push_undo_point` deep-copies the entire app state (every ROI, mask array, chromatic model) on every edit | One shared `undo_manager`, each command pushes a small typed `FunctionCommand` closure over its own module's state | Deep-copying everything is exactly the cost this architecture exists to avoid |
| ROI/Image/Group-table each hold their own partial copy of ROI state, occasionally duplicating group-mutation logic (`roi_geometry_mixin.py` vs `group_table_controller.py`) | "One backend, several front doors" - `RoiToolbox` owns all state, three UI surfaces all call the same command API | Explicit 2026-09-20 correction to the original "thin view" sketch, after recognizing duplicated *logic* (bad) is different from duplicated *affordances* (fine, even desirable) |
| Settings persistence needs hand-duplicated save/restore code in two places per section, no registry (18 sections currently) | Same pattern actually, so far (`AppSettings` dataclass fields + explicit wiring per field in `app_rewrite.py`) | **Not yet resolved** - flagged as a real open risk in §4, not solved by the rewrite yet |

**Where the rewrite is deliberately *not* copying a "worse" stable pattern**
that a maintainer coming from the stable app might expect: analysis never
runs implicitly (stable's live-preview curve fit runs on the GUI thread
every 80ms during a sweep, a real diagnosed-but-unfixed bug); masks stay
authored in raw pixel space and forward-transform per use site (stable
already does this correctly - kept, not changed); the `cv2` chromatic
fast-path's validated numeric tolerance versus the `scipy` reference is
being kept as-is, not re-litigated.

---

## 4. Pitfalls specifically ahead

1. **The settings-persistence pattern is on track to repeat the stable
   app's exact "18 hand-duplicated sections, no registry" problem.**
   `AppSettings` (`storage/app_settings.py`) has grown one field at a time
   (theme, layout presets, `active_workflow_stage`, and now
   `expanded_subsections`), each requiring matching wiring added by hand in
   `app_rewrite.py`'s `_persist`/`_persist_subsection` functions and in
   `WorkflowPanel.__init__`. This is exactly the anti-pattern the feature
   inventory flagged in the *stable* app (`layout_state_controller.py`'s "18
   settings sections, no central schema"). It hasn't caused a bug yet
   because the rewrite is still small, but the shape is identical - worth a
   real design pass (a settings-field registry, or at least a documented
   convention) before a sixth or seventh field makes the hand-wiring
   unmanageable, rather than waiting for it to actually hurt.
2. **The Highlight-range → detection-settings wiring is not simply
   mechanical, and shouldn't be treated as one** (see §2's table). Piping
   `HighlightRangeModule.range_changed`'s `(min, max)` tuple into
   `RoiToolbox.set_detection_settings(settings: AreaRoiDetectionSettings)`
   requires either constructing a full settings object (need to decide
   which other fields it should carry - presumably "whatever's already
   there, plus this range") or adding a narrower setter - and
   `set_detection_settings` emits `geometry_changed` (a `ComputationalChange`),
   meaning **wiring this naively would make dragging the histogram
   highlight region invalidate every ROI's analysis data on every drag
   frame.** That may or may not be the intended behavior (maybe it should
   debounce until drag-release, maybe highlight-range shouldn't feed
   detection settings live at all, only on an explicit "apply as detection
   range" action) - this needs a maintainer decision, not a guess, before
   anyone wires it.
3. **The dev-preview currently demos worse than the actual amount of
   finished work would suggest.** Dataset and Transforms are polished and
   responsive; the very next stage (ROI Selection) hits three placeholder
   labels. Anyone (including the maintainer, after a break) judging
   progress by clicking through the preview app will undercount how much
   real backend work is done, and overcount how close the app is to
   feature-complete. Worth keeping the written status (this doc, the build
   log) as the source of truth for "how much is left," not a click-through.
4. **Don't let the ROI Selection or Analysis UI build turn into a new
   god-controller.** The old app's `analysis_controller.py` (1,909 lines)
   reached into 30+ `window.*` attributes it didn't own - the single
   largest coupling number in the whole diagnosis. When building the
   Analysis stage's UI against `AnalysisEngine`, watch for the same
   pressure re-emerging in whatever class ends up owning the Run/Stop/scope
   controls plus the three result sections - if it starts reaching past
   `AnalysisEngine`'s four public query methods into engine/store internals,
   that's the exact pattern repeating.
5. **A previously-built, never-wired parallel ROI editor was deleted as
   dead scaffolding once already (2026-08-21, in the stable app)** - the
   feature inventory calls this out explicitly as a precedent not to
   repeat. The rewrite's ROI Selection UI is now the second attempt at ROI
   editing UI in this codebase's history; worth actually wiring it to
   `RoiToolbox` and shipping it incrementally (even just "Finding" first)
   rather than building all three sub-tabs in isolation and risking a long
   unwired stretch again.
6. **A pre-existing, environment-dependent test failure exists and is not a
   regression to chase**: `crop_size_controls.py`'s own font-metric test
   failed in the last full related-suite run (142/143) per the build log's
   most recent entry - already known, already attributed to font/rendering
   environment differences, not caused by anything in this session's work.
   Don't spend time "fixing" it without first confirming it still fails and
   understanding why (see this app's `CLAUDE.md` "Qt widget sizing
   verification" pitfall about not trusting `.text()`/`.width()` alone).
7. **Fixed 2026-09-30**: `AGENTS.md`'s undo section was stale, and this
   doc's first version repeated its mistake uncorrected - both said all
   four Image Tools modules were wired to undo. A closer re-check found
   `MaskModule` deliberately isn't (its own docstring explains why - see
   §3). Both docs now name Mask as an intentional exception rather than
   claiming uniform coverage. Worth remembering as a small case of the
   pattern this doc warns about elsewhere: a claim copied from one doc into
   another without re-checking the code carries the first doc's error
   forward - re-verify against the actual file, every time, even when a
   summary already exists.

---

## 5. Next 3-5 steps, ordered

1. **Build the ROI Selection Workflow-panel UI's "Finding" sub-tab only**
   (detection settings + a Detect button, calling `RoiToolbox.detect_rois`/
   `set_detection_settings`) - smallest complete vertical slice through an
   already-fully-built backend, and directly unblocks manual testing of
   everything built so far (nobody can currently create an ROI through the
   rewrite-preview UI at all). Verify: pyflakes-clean, offscreen smoke test,
   a new integration test following the Histogram panel's just-established
   pattern (build the window, drive it via direct method calls, no
   coordinates).
2. **"Editing" and "Groups" sub-tabs**, same panel, same backend - natural
   follow-on once Finding proves the pattern.
3. **Build the ROI/Group table panel** (`panels/roi_table/panel.py`) against
   the same `RoiToolbox` command API - the sketch already specifies which
   operations belong here (rename/recolor/reorder/bulk-select).
4. **Decide the fractional-pixel-weighting toggle/provenance question**
   (§6, item 6 below) before wiring `rasterize_fractional` into
   `compute_cell` - this turned out to have real open design surface once
   inspected for this doc, not the "small mechanical" task it first looked
   like. Worth resolving early since it's architecture sketch §9's last
   open item, but it's a decision, not a warm-up task.
5. **Build the Analysis stage's Run/Stop/scope controls**, the smallest
   possible slice first (Run button + a plain "N/M analyzed" status label
   reading `AnalysisEngine.status_summary()`) before the ROI's-math/Metric-
   trace/Statistics display sections - this is what finally makes
   end-to-end testing of the whole computational pipeline possible through
   the UI instead of only through unit tests.

Do **not** treat this as a rigid sequence - if the maintainer wants Spectra/
Sensorgram sooner (they're arguably the most-wanted panels, being the
original motivation), steps 1-3 (ROI selection UI) are a hard prerequisite
either way, since neither panel has anything to display without a way to
create/select ROIs first.

---

## 6. Open questions needing the maintainer's decision

1. **Highlight-range → detection-settings wiring** (§4, item 2): should
   dragging the histogram highlight region live-update detection settings
   (and thus invalidate analysis data), only apply on an explicit action, or
   not feed detection settings at all? Both consumer methods exist and are
   tested; only the *policy* is undecided.
2. **Settings-field registry** (§4, item 1): worth a real design pass now
   (5 fields deep) before it grows further, or continue the current
   one-field-at-a-time pattern until it's a proven, measured problem (the
   codebase's own stated performance philosophy - "measure first" - arguably
   applies to internal maintainability debt too, but that's the maintainer's
   call, not an inferrable default)?
3. **ROI toolbox's exact UI home**: AGENTS.md/sketch already say three front
   doors (Workflow sub-tabs, Image panel, ROI/Group table panel) call one
   backend - still open, per the architecture-vision memory, is *which one
   is built first and whether the ROI/Group table panel is the "toolbox"
   home or just one more front door*. This doc's §5 plan assumes "build
   Workflow sub-tabs first, table panel third" but that's this session's
   ordering judgment, not a maintainer decision.
4. **Masking's home panel** - still explicitly unaddressed per the
   architecture-vision memory; Mask has a real Workflow-panel section
   already (`MaskSettingsSection`), which may already answer this in
   practice - worth confirming explicitly rather than leaving it as an
   open question the code has quietly already resolved.
5. **The recompute-dependency "solo worker"** and **background-removal's
   estimate/apply split** - both floated in early brainstorming, neither
   picked up since (`plan_recompute` exists and handles the locality rules
   from sketch §6 directly; a separate "which ROIs need recompute" reasoning
   component doesn't seem to have been needed once `plan_recompute` was
   actually built - worth confirming this open question is actually
   resolved-by-implementation rather than still pending).
6. **Fractional-pixel-weighting toggle and provenance** (§2's table,
   corrected entry): where should the "use fractional weighting" choice
   live (per-analysis-run, a persistent setting, or per-ROI)? And since
   toggling it changes a cell's *computed value* without changing any of
   its currently-tracked *inputs*, should it become part of
   `ProvenanceRecord`'s fingerprint (making the simplified "any input
   change triggers recompute" rule cover it cleanly), or handled some other
   way? This is architecture sketch §9's last formally-open item and hasn't
   had a design pass since the raster/reduction math itself was verified
   2026-09-22.

---

## Work done this session

Found ~5 sessions' worth of uncommitted, complete, already-tested work
sitting in the working tree at session start (present since before this
conversation began, per the initial git status): `panels/workflow/panel.py`
and `storage/app_settings.py` had the full implementation of nested
Workflow-section expand/collapse persistence (`expanded_subsections`),
matching wiring already committed in `app_rewrite.py` at `HEAD` that
referenced these not-yet-existing pieces. Verified before touching anything:
13/13 new+existing tests passed
(`test_lspri_workflow_panel_subsection_restore.py`,
`test_lspri_rewrite_app_settings.py`), plus the two other tests touching the
same `WorkflowPanel` file (`test_lspri_workflow_panel_stage_restore.py`,
`test_lspri_workflow_panel_width_budget.py`, 7/7), pyflakes-clean, and a
fresh offscreen smoke test of the whole rewrite-preview window. Committed:

- Submodule (`apps/LSPRi/eva`, `rewrite` branch): `6b699b1` - "Persist
  nested Workflow section expand/collapse state across restarts"
- Umbrella (`main`): `ddd16f6` - "Add regression tests for Workflow
  nested-section restore" (test files only; the umbrella's tracked
  submodule pointer for `apps/LSPRi/eva` was deliberately left untouched,
  still pointing at stable `develop`, per existing policy)

Neither was pushed anywhere - both sit as local commits for the maintainer
to review.

## How this was verified

Everything in §1/§2 is grounded in reading the actual current files (not
just the build log's prose) as of this session: `grep -rl NotImplementedError`
across every new-tree package, direct inspection of `app_rewrite.py`'s
wiring, and `panels/workflow/panel.py`'s section-builder functions. This
was **not** verified by clicking through the running app - per this
project's `CLAUDE.md`, driving the GUI (screenshots/automation) needs the
maintainer's go-ahead first, and this was an unattended overnight run with
no one to ask. If anything above turns out to contradict what the app
actually does when run, trust the running app and correct this doc.
