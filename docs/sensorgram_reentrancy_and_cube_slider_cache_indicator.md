# Sensorgram Start/Stop reentrancy fix, and the Cube/Time slider cache indicator

2026-08-27. Two related pieces of work from the same investigation: a bug
report ("I can't run the analysis a second time") that turned out to be a
missing reentrancy guard plus ~90 lines of dead duplicate code, and a new
feature request (color the Cube/Time slider's ticks by whether that cube is
already cached for the current ROI selection). Written up because both
required tracing the full sensorgram start/stop/completion wiring across
`analysis_controller.py` and `main_window.py`, which is worth not
re-deriving next time either area needs touching.

## How to diagnose a "can't run it again" report like this

Don't start from a stuck-flag theory. Check `apps/LSPRi/eva/logs/` first
(`lspr_imaging_{session_stamp}.log`, DEBUG level) - the workflow logger
(`window._append_workflow_log`) already narrates "SG calc start", cache
hits, etc., and `sys.excepthook` (see `app.py`) logs any unhandled
exception to that same file even though it never shows the user a dialog.
Reading the actual session log ruled out a crash in minutes; guessing from
code alone would not have.

## Bug: the live "Start analysis"/"Stop" wiring had no reentrancy guard

The button wiring (`main_window.py`, `analysis_calculate_all_button.clicked`
-> `AnalysisController.calculate_sensorgram()` ->
`window._calculate_sensorgram_for_range()` -> a *private*
`AnalysisController._calculate_sensorgram_for_range()`) went straight from a
cache-miss into `_start_sensorgram_worker()` with no check for whether a run
was already in flight (`window._sensorgram_running`). A second click before
the first run finished would silently launch a second worker, bump
`_sensorgram_request_id` (orphaning the first worker - its own completion
callback becomes a no-op stale-request branch), replace
`_sensorgram_cancel_event` (so Stop could no longer reach the first worker),
and wipe the plot via `clear_sensorgram("")` mid-run.

This existed side-by-side with a *public*, differently-guarded sibling,
`AnalysisController.calculate_sensorgram_for_range()` (no leading
underscore), which already had the right guard (defer via
`_pending_sensorgram_payload` if a run is in flight) - but that method is
only called from the live-preview-selection-change and "Calculate group"
paths, never from the button. There was also a fully dead second set of
completion handlers (`_on_sensorgram_ready` / `_on_sensorgram_partial_result`
/ `_on_sensorgram_failed`, plus their `main_window.py` forwarding wrappers)
that were never connected to any Qt signal - leftover from what looks like
an incomplete refactor where a "v2" implementation was built but the actual
button was never repointed to it. A dead, zero-caller `stop_sensorgram_calculation`
(public) sat next to the live, actually-wired `_stop_sensorgram_calculation`
(private) the same way.

**Fix** (`analysis_controller.py`): added the missing in-flight guard
directly to the live `_calculate_sensorgram_for_range()` (reusing the
existing `_pending_sensorgram_payload` deferred-refresh mechanism rather
than inventing a new one), and deleted the ~90 lines of confirmed-dead
code (`stop_sensorgram_calculation`, `_on_sensorgram_ready`,
`_on_sensorgram_partial_result`, `_on_sensorgram_failed`, and their
`main_window.py` wrappers) after grepping both `apps/` and `tests/` to
confirm zero other callers. Also added debug-visible workflow-log lines for
"SG stop requested" and completion (`SG done`/`SG stopped |
completed/total | prep Xs | fit Ys`) - the live completion handler
(`on_sensorgram_ready`, no underscore) previously logged nothing on
success, which is exactly why the session log used for diagnosis only ever
showed "SG calc start" with no matching "done" line even for known-good
runs.

**What the maintainer actually hit** (per their own account): they started a
run, stopped it after ~1 min, and the concern was less "provably broken"
and more "no visibility into what happened" - the new debug logging
directly addresses that; the reentrancy fix addresses the mechanism that
*could* have caused a stuck state if a second click landed during that
window.

**Follow-up done 2026-08-28**: a deeper redundancy pass (via a planned,
staged refactor - see plan file referenced below) found and fixed one more
live bug in the same family, deleted more dead code, added two small
dedup helpers, and extracted two pure clusters into their own modules.
`analysis_controller.py` went from 3,948 to 3,816 lines (~3.3%) - a real but
modest reduction, by design: several much larger clusters (worker
orchestration, absorbance-result handlers, all the Qt-widget-sync methods)
were deliberately left alone because they don't have a clean, low-risk
extraction boundary (see "still not done" below).

What changed:
- **Real bug, same family as the Start/Stop one above**: the button-wired
  `_calculate_sensorgram_for_range`'s cache-hit fast path ran *before* its
  `_sensorgram_running` in-flight check, not after - so a cache hit for a
  signature different from whatever was currently running could still get
  applied immediately (bypassing the pending-queue mechanism), then get
  silently overwritten again once the in-flight run finished. Fixed by
  reordering the check; the two previously-diverging "start sensorgram"
  implementations (`calculate_sensorgram_for_range` public /
  `_calculate_sensorgram_for_range` private) are now one implementation,
  the public name is a one-line delegating alias. Regression coverage in
  `tests/integration/test_lspri_sensorgram_start_reentrancy.py`.
- Deleted three more fully-dead one-line wrapper methods (`refresh_absorbance_spectrum`,
  `prepare_absorbance_spectrum_payload`, `update_control_state`) plus a dead
  `MainWindow._calculate_sensorgram_for_range` forwarding hop.
- Added `_sensorgram_prerequisite_blocked()` (analysis-enabled/no-dataset/chromatic-setup
  guard, previously duplicated near-verbatim in 3+ places) and
  `_store_in_lru_cache()` (the repeated "assign + move_to_end + evict over
  capacity" cache-store idiom, ~9 call sites) as small shared helpers.
  `_store_in_lru_cache` takes an explicit `lock=` kwarg per call site rather
  than defaulting one way - about half the cache-store sites are reachable
  from a background worker thread and need it, half are GUI-thread-only and
  don't; this distinction was preserved exactly, not collapsed.
- Extracted two clusters that were `@staticmethod`/pure with zero
  `self.window` reads into their own modules: `analysis_result_serialization.py`
  (session-cache JSON <-> `FormulaSpectrumResult`/`SensorgramComputationResult`
  transforms) and `analysis_cache_signature.py` (JSON canonicalization +
  `signature_hash` for the HDF5 backup's dedup column). `AnalysisController`
  keeps the original underscore-prefixed names as `staticmethod(...)`
  attribute assignments (not `def` wrappers) pointing at the moved
  functions, so every external caller that references
  `AnalysisController._method(...)` (several in `main_window.py`) needed
  zero changes.

**Follow-up done 2026-08-28 (later same day): mixin split.** The maintainer
asked whether the clusters flagged above as "not done, deliberately" could
still be split out even though they aren't pure functions. Answer: yes, via
the mixin pattern this codebase already uses successfully for `MainWindow`
itself (`RoiGeometryMixin`/`HistogramMaskMixin`/`MeasurementCalibrationMixin`
- a plain class with no `__init__`, duck-typing off `self` being the
composed instance, combined via multiple inheritance) - a class doesn't need
to be *pure* to move to its own file, it just needs `self.window` to still
resolve, which it always does regardless of which file in the MRO defines a
given method.

Two fresh Explore passes mapped the coupling first (important: don't just
split by "looks like a cluster" without checking cross-references). Finding:
only the chromatic-geometry cluster was genuinely self-contained; the other
three ("sensorgram worker orchestration", "absorbance-spectrum result
handling", "cache-signature builders") cross-call each other roughly two
dozen times, and the cache-signature cluster wasn't even contiguous (4
scattered regions). Splitting those three into 3 separate files would have
made the code *harder* to follow (a reader tracing "run a calculation"
bouncing between 3 files on nearly every line) - so they were bundled into
**one** second mixin instead, which turns nearly all of those cross-calls
back into intra-file calls.

Result: `analysis_controller.py` went from 3,816 lines to **1,458 lines**.
New files:
- `analysis_types.py` (61 lines) - `SpectrumSettingsSnapshot`/
  `SharedWavelengthGeometry` `NamedTuple`s, moved here first specifically to
  avoid a circular import between `analysis_controller.py` and its own new
  mixins (both mixins need these types; if left in `analysis_controller.py`,
  the mixins importing them back would create a cycle).
- `analysis_chromatic_geometry_mixin.py` (213 lines) - `AnalysisChromaticGeometryMixin`,
  the 6 clean [λ] mode methods.
- `analysis_worker_mixin.py` (2,111 lines) - `AnalysisWorkerMixin`, the
  bundled 43 methods (sensorgram start/stop/completion, single-cube
  interactive spectrum handling, cache/signature building and disk-resume
  plumbing). This is intentionally still large - see "why bundled, not
  split further" above; it trades file-count for keeping the actual
  call-graph readable within one file.
- Class declaration: `class AnalysisController(AnalysisWorkerMixin, AnalysisChromaticGeometryMixin):`.
  `AnalysisController.__init__` is untouched (`self.window = window`) -
  neither mixin defines `__init__`, so there's no MRO/init-chaining wrinkle.

Also found and deleted one more dead method during the mapping pass:
`_compute_roi_absorbance_results` (zero callers anywhere, confirmed by grep
before deleting).

This was a **pure code-motion pass - no logic changes** (unlike the earlier
same-day pass, which had one real behavior fix). Executed via an AST-based
extraction script (parses `analysis_controller.py`, gets exact line ranges
per method by name, extracts/removes verbatim) rather than manual copy-paste,
specifically to eliminate transcription risk on ~2,400 lines of code motion -
worth reusing that approach (or writing an equivalent one) for any future
large mechanical move in this codebase. Verified via `py_compile` +
a runtime import/MRO smoke test + the full `tests/*lspri*` suite after each
of 5 sub-stages (0, 1, 2a, 2b, 2c), plus a final grep-based spot check
confirming no method ended up defined in both `analysis_controller.py` and
a mixin (which would silently shadow the mixin's copy, since a class's own
namespace always wins over an inherited one).

**Still not done, deliberately**: the absorbance-spectrum result handlers
inside `AnalysisWorkerMixin` still interleave computation with ~15
plot-widget calls line by line - splitting *those* apart would need a
compute/render redesign, not a file move, and wasn't attempted. All the
`sync_*_controls`/`_on_*_changed`/plot-axis-cursor methods remain in the
base `AnalysisController` class since they exist only to touch specific Qt
widgets - nothing to extract to. The full reasoning and staged execution
plan for this mixin split is preserved at
`C:\Users\Admin\.claude\plans\virtual-gliding-book.md` (Claude Code plan
file, not part of the repo) if this needs picking up again.

## Feature: Cube/Time slider cache indicator

Request: color each tick on the Cube/Time slider (`spectral_cube_slider`,
a `DataAxisSlider` - `widgets.py`) blue if that spectral cube already has
every *currently selected* ROI's absorbance spectrum cached in RAM, gray
otherwise - so selecting an ROI that's never been computed visibly pulls
ticks back to gray (logical AND across the selection, not OR).

### Where the pieces live

- `widgets.py`, `DataAxisSlider`: `set_tick_cache_state(frozenset[int] | None)`
  stores tick *positions* (indices into the array last passed to
  `set_ticks`) to paint in `_CACHED_TICK_COLOR` ("#38bdf8", matching the
  existing multi-ROI accent used elsewhere -
  `AnalysisController._sensorgram_selection_color`). Deliberately not tied to
  `_accent_color`/`set_accent_color`, since that's reused for the handle's
  selection-specific color and would tangle two independent meanings.
- `analysis_controller.py`, `AnalysisController.schedule_cube_slider_cache_refresh()`:
  starts a 150ms debounce timer (`window._cube_slider_cache_refresh_timer`,
  set up in `MainWindow.__init__` next to the other coalescing timers like
  `_sensorgram_live_preview_timer`). Call this, not
  `_refresh_cube_slider_cache_indicators()` directly, from any new trigger
  point - it's cheap and self-coalesces bursts (multi-select drag, rapid
  range edits).
- `AnalysisController._refresh_cube_slider_cache_indicators()`: the actual
  scan. Runs on a background `FunctionWorker`/`window._thread_pool` thread,
  not the GUI thread - see "why background" below. Builds
  `_roi_formula_spectrum_signature_for_cube(roi, cube_index)` for every (selected
  ROI, cube-in-slider-range) pair and checks membership in
  `window._roi_formula_spectrum_cache` under `window._analysis_cache_lock`; a cube
  is "cached" only if every selected ROI hits. Results come back via a
  request-id-guarded callback (`_apply_cube_slider_cache_indicators`, same
  stale-result-rejection pattern as `on_sensorgram_ready`/`on_sensorgram_failed`)
  so a fast follow-up selection change can't have its result clobbered by a
  slower, earlier scan finishing later.

### Why background, not just synchronous

`_roi_formula_spectrum_signature_for_cube` folds in a per-wavelength chromatic
signature lookup (cheap individually - `ChromaticController.model_for_image_key`
is an O(1) dict lookup after a one-time index build, see
`chromatic_controller.py`) but this run pays it N times (once per
wavelength) per (ROI, cube) pair. With the ROI counts this app's logs
actually show in real sessions (up to ~36 selected at once) and the cube
counts (~300+), the full scan is on the order of tens to a few hundred
milliseconds - small per call, but enough to be a visible stutter if paid
synchronously on the GUI thread, which is a hard rule in this codebase (see
CLAUDE.md's "GUI thread blocking" pitfall). Hence: background thread +
debounce, not a synchronous call inline in the selection-change handler.

### The one real correctness trap: position vs. cube-index

`set_tick_cache_state` stores *positions* into the ticks array, not cube
indices, matching how `_major_labels` already works. That means a
genuinely new values array (dataset reload, spectral-cube range changed)
must invalidate any previously-computed cache-state, or a stale position
could color the wrong tick after `_spectral_cube_values` changes shape.
`DataAxisSlider.set_ticks()` handles this itself (compares the new values
list to the stored one; only clears `_cached_tick_indices` on an actual
change) rather than trusting every call site to remember - there are two
call sites for `spectral_cube_slider.set_ticks` in `main_window.py`
(`_configure_navigation_inputs` for real range changes,
`_refresh_cube_time_display` for the Cube/Time *display mode* toggle, which
passes the *same* underlying array just relabeled) and only the first
should ever blank the indicator. See
`tests/unit/test_lspri_data_axis_slider_cache_ticks.py` for the regression
coverage on this specific trap.

### Trigger points wired up

`schedule_cube_slider_cache_refresh()` is called from: ROI selection
changes (`main_window._update_selection_dependent_plots`), dataset
load/spectral-cube-range changes (`main_window._configure_navigation_inputs`,
after `set_ticks` has already run), and sensorgram run completion/failure/
stop (`on_sensorgram_ready`, `on_sensorgram_failed` in
`analysis_controller.py` - a full or stopped run is exactly when
`_roi_formula_spectrum_cache` gains a batch of new entries). It is *not* wired to
fire on every partial-progress point during a long run (`on_sensorgram_partial_result`)
- the debounce timer would just keep getting pushed back for the run's
whole duration anyway, and the completion hook already catches the final
state once the run settles, matching the existing (pre-existing, not
introduced here) cadence of the ROI table's own "already calculated"
blue/white dot indicator (`_refresh_cached_roi_ids_snapshot`).
