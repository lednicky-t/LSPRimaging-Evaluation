# Sensorgram drift baseline (anchor points), 2026-11-09

## What and why

The Sensorgram's baseline used to be one window's mean per ROI: it removes an offset, not a drift. The **Anchor points** method lays a curve through
points the user clicks on the plot (e.g. late in the `H2O` step at the start and at the end) and subtracts it. This works like OriginLab's
anchor-point baseline: first pick where the baseline is, then how to connect the anchors.

## History (all 2026-11-09)

1. Built first with two anchor sources: plan steps with a chosen note (their last N %, one note for all ROIs plus per-ROI overrides) **and** clicked
   points. Then the plot got anchor marks, and the plan bar was locked during the edit.
2. The maintainer found two sources hard to follow and asked to **remove the steps**. Removed: the anchor note, per-ROI note overrides, the
   "last N %" box, the redraw on a retyped note or a moved step boundary, `Series.anchor_label`. The method is renamed `"steps"` -> `"anchors"`;
   a session saved with `"steps"` loads as `"anchors"` (`StatisticsSettings.__post_init__`), and the old keys are dropped on load
   (`session._decode_settings` keeps known fields only).
   - A one-shot "place points on the steps named X" helper was offered; not built (not asked for).

## Where

| What | File |
|---|---|
| Maths (pure, Qt-free, in the `.importlinter` Qt-free list) | `analysis/baseline_drift.py` |
| Applied in the fixed order (spikes -> smoothing -> baseline) | `analysis/statistics.py` `apply_statistics_with_fit` |
| Settings (saved with the session, additive keys, no schema bump) | `analysis/settings.py` `StatisticsSettings.baseline_method/_shape/_anchor_points/_point_halfwidth/_anchor_steps/_step_trim` |
| A ROI's own points follow ROI renumbering | `AnalysisSettingsModule.remap_roi_ids`, wired in `app_rewrite._connect_roi_renumbering` |
| Lines carry `baseline_curve`, `baseline_note`, `anchor_rms`, `anchor_x/_y/_base` | `analysis/scan_trace.py` `_build_lines` / `_attach_drift` |
| ROI-table step columns use the same anchors | `analysis/step_table.py` `_request` (`cube_numbers=`) |
| Ribbon controls + Anchors menu | `panels/sensorgram/baseline_controls.py` |
| Dashed curve, hollow-square anchor marks, edit mode | `panels/sensorgram/plot.py` `show_series`, `set_anchor_mode`, `_on_scene_clicked` |
| Plan bar lock during the edit | `panels/sensorgram/plan_bar.py` `set_locked` |

## How it is used

Method **Anchor points** -> turn on the edit toggle -> left click adds a point at the nearest cube, right click on a point's square removes it (a
right click elsewhere opens the anchor menu) -> pick a **Shape** -> **Subtract**. The Baseline group shows: Method, Shape, edit toggle, Anchors menu.

Method **Range** with anchor steps: see "Range method with anchor steps" below.

## The rules, and the reasons for them

- **One anchor per point.** Each is the mean x and mean y of the cubes within ± `baseline_point_halfwidth` (default 2, set in the Anchors menu) of the
  clicked cube, in file order.
- **Storage.** `(roi id, cube number)` pairs; roi id 0 = shared by every ROI. Cube numbers, not plot x, so a Cube / Time switch keeps them in place.
- **Scope.** New points go to all ROIs or to the lines shown only (Anchors menu, remembered under `sensorgram/anchor_point_scope`). A ROI's points are
  the shared ones plus its own.
- **Removal.** A right click within 10 screen px of an anchor square (`plot._ANCHOR_HIT_PX`, `anchor_square_at`) deletes that point wherever it is
  kept (shared, and the shown ROIs' own): what you see is what goes. Before 2026-11-09 evening any right click removed the nearest point, which was
  too easy to do by accident.
- **Anchor menu.** A right click away from every square opens a menu (`DriftBaselineControls.fill_context_menu`, popped non-blocking by
  `panel._show_anchor_menu`). Its entries: add a point here; remove the nearest; new points for all ROIs or for the lines shown; clear all; stop
  editing.
- **Icons (2026-11-09).** Both edit toggles are the standard pencil (`baseline_controls.pencil_icon`).
  - The window method got its own pencil (`panel._band_edit`). It shows or hides the baseline selector band, so the band menu has no show / hide
    toggle any more (`RoiOverlayControls.set_toggle_shown(False)`); the remembered key is still `sensorgram/baseline_band_visible`.
  - The Anchors button is Tabler's `anchor` icon (vendored into `lspr_ui/icon_assets/`, umbrella repo) plus the number of points. The drop-down
    repeats the count at the head of its row.
- **Anchors drop-down.** One row of icon buttons (scope all / shown, ± cubes, clear) and a small dim line with the counts and the fit notes.
- **Renumbering.** A shared point stays; a ROI's own point follows its ROI or goes with it. Not undo-tracked.
- **No silent fallback.** Too few anchors for the shape: the line stays uncorrected (NaN baseline) and a note says why ("no anchors ...", "linear
  needs 2 anchors with values, found 1"). This follows the rule that controls mean the same for every selection. The notes say "anchors", not
  "anchor points", because the Range method's anchors are plan steps.
- **Edges.** Polynomials extrapolate past the first and last anchor. PCHIP is held flat there.
- **x units.** The fit uses the plot's x axis (seconds, or cube number), the same axis the window method uses.
- **Per ROI before averaging.** A group or average line combines its members' corrected traces, and its dashed curve combines their curves the same way.
- **Window method unchanged.** It stays the default for old and new sessions. A test pins that it gives bit-identical numbers.
- **Clicks that went missing (fixed).** The maintainer found most clicks placed no point. Two causes:
  - The per-cube dots (a pyqtgraph `ScatterPlotItem`) accept every left click landing on a dot. They do nothing with it, but the plot skipped
    accepted clicks, so a click on a trace was lost. `plot._on_scene_clicked` now still counts a click a scatter item took. This applies to the
    cube-cursor click too.
  - pyqtgraph turns a press into a drag once the mouse moves 5 px, or moves at all after 0.5 s held. In the edit mode only a move of more than 12 px
    is a drag (`_ANCHOR_MOVE_PX`). The scene's limits are restored when the mode ends.
- **Every point is drawn, and the cursor counts what is missing.** A single point of a linear baseline used to show nothing (the squares came only
  with a fit), so it looked as if it was not added.
  - `scan_trace._attach_drift` now attaches the anchors even without a fit. They have no `anchor_base`, and the trace is uncorrected, so the squares
    sit on the trace.
  - In the edit mode the cross-hair carries a gold badge with the points the shape still needs (`plot.set_anchors_needed`, `_anchor_cursor`).
  - The count comes from `DriftBaselineControls.points_needed`: the shape's minimum minus the points of the shown line with fewest. It goes
    2 -> 1 -> gone for Linear.
- **Marks.** Hollow squares only: the dashed vertical line and band per point were removed at the maintainer's request. Each line's anchors: hollow squares in the line's colour,
  at the trace level with Subtract off (the dashed curve passes through or near them), near zero with Subtract on. An average shows them only when
  all members have anchors at the same x.
- **Edit mode.** The cube cursor does not move on a click; the plot's menu does not open on a right click; the plan bar takes no gesture and its
  selection clears; opening the Experimental plan tab ends the edit. Gestures are rows in `shortcuts.py` (`mouse.sensorgram.anchor_add` / `_remove`).

## Range method with anchor steps (2026-11-09, evening)

The maintainer asked for step anchors back, in another form, inside the old "window" method (now labelled **Range**; the saved key is still `"window"`):

- **No step marked:** the mean of the dragged range, unchanged (a test pins it bit-identical).
- **Steps marked:** each marked step, minus a border offset at both ends, is an anchor region; the chosen Shape connects them. The range band is then
  not used and not drawn.
  - The offset is in cubes ("offset N" in the ribbon, `StatisticsSettings.baseline_step_trim`, default 2). It is shown only while steps are marked,
    together with Shape.
- **Marking.** With the Range pencil on (`panel._band_edit`), right-clicking a plan step adds items at the top of the step's normal menu:
  - "Make this step a baseline anchor" (or "Make the N steps ...", for a selection);
  - "Remove this step from the baseline anchors";
  - "Clear all anchor steps".
- **Where the items are not offered.** Not on the Experimental plan tab, where steps are edited.
  - The items are *added* to the plan menu, not shown instead of it. The first build replaced the menu, but the pencil is on by default, so colour and
    edit text were lost on the Plot tab (`test_lspri_plan_bar_gestures` caught it).
- **Storage.** Steps are kept by `EffectiveStep.ref` in `StatisticsSettings.baseline_anchor_steps`, so a moved boundary moves the anchor. A removed
  step's ref matches nothing.
- **Marks.** Each anchor step shows a small anchor in its lower-left corner on the plan bar (`PlanBarItem.set_baseline_anchors`).
- **Marked but nothing left.** If every marked step has no cube left after the offset, the lines stay uncorrected with "no anchors ..." (no silent
  fall back to the range).
- **Code.**
  - `baseline_drift.step_regions`, `uses_anchor_steps` and `window_step_regions`;
  - `SensorgramRequest.baseline_step_regions`;
  - `StepOverlays.baseline_step_regions` and `anchor_step_indices`;
  - the step table computes the same regions from its whole-step membership (`step_table._request`).

## Check-in pass (2026-11-10)

A review of the whole uncommitted change before committing. Cleaned:

- The anchor icon on the plan bar was rebuilt from its SVG on every repaint; it is now kept per colour and size on the plan bar item
  (`PlanBarItem._anchor_icons`; on the instance, so no pixmap outlives the application).
- Removed dead code: `baseline_drift.BASELINE_METHODS`, the never-passed `anchors` parameter of `step_stats.trace_matrix`,
  `baseline_controls.anchors_summary`, a second numpy import.
- Fit notes, tooltips and docstrings that still said "Window mean", "anchor points" for step anchors, or "right click removes the nearest point".

Found and left for a decision (see Open): marks of removed steps. The ROI position tuning without the drift baseline was fixed the same day (section
"2026-11-10" below).

### A test file that aborted (`tests/integration/test_lspri_sensorgram_step_stats.py`, umbrella repo)

With this work in the tree the file died every time under the project's runner ("Fatal Python error: Aborted", no message, in the fifth legend
test's `processEvents()`), while it passed under pytest and with the committed code. What was measured:

- With Python's garbage collector switched off it passes: the abort needs a collection.
- A small script (five bare `SensorgramPlot`s: build, show, `show_series`, close) aborts in the **committed** code too, at 5 of 36 collector timings
  tried (`gc.set_threshold`), the default 2000 among them. So the fault is older than this work; the new code only moved the moment at which the
  collector runs in that test file.
- It does not abort (0 of 36 timings each) when the waiting Qt events are delivered before the plot is closed, or when the plot is deleted with Qt
  right after closing. It also needs `show_series` (0 of 15 without it).

So: `show_series` leaves Qt events waiting; a plot that is only closed stays alive until the collector frees it, and if that happens while the next
plot is being built, the next `processEvents()` aborts. Which event and which object is **not** known (no C stack on this machine).

**A second file, same family:** `tests/integration/test_lspri_data_checks.py` crashed with an access violation in the one full LSPRi run (215 of
216 files passed). It passes with the collector off; the committed code crashes at 4 of 5 collector timings tried, this work at 3 of 5. Its
`tearDown` called `deleteLater()` on its widgets, but no test delivers that request, so they also waited for the collector. `ChecksBase.tearDown`
now delivers the deletions (`sendPostedEvents(None, DeferredDelete)`): 7 of 7 runs pass (2 plain, 5 timings).

Fix for the first file: the test's plots are now deleted with Qt at the end of each test (`_destroy` in that file). The app builds its Sensorgram plot once and keeps
it, so this is not expected in normal use; it may be related to the crashes on closing the app. `test_lspri_plan_bar.py` and
`test_lspri_plan_bar_gestures.py` build bare plots the same way and were left as they are (they pass).

## 2026-11-10: the ROI position tuning uses the drift baseline; too few marked steps are explained

### ROI position tuning (`analysis/roi_tuning_*.py`)

Before, a trial switched the baseline off (`replace(statistics, baseline_enabled=False)`): right for the range's mean, which is one number per
trace and changes neither the step difference nor the noise, but wrong once a *curve* is subtracted, because a curve changes both. The trial's
numbers then differed from what the Sensorgram and the ROI table's step columns show.

Now, when a curve is subtracted (`baseline_drift.curve_subtracted`: Subtract on, and Anchor points or Range with marked steps):

- `RoiTuningModule._drift_anchors` turns the anchors into plain cube numbers per ROI (`anchor_cubes_by_roi`; `AnchorCubes`). The cubes outside the
  two steps become `TuningRequest.extra_cubes` and are **scanned too**; the preview says how many ("..., 2 more for the drift baseline's anchors").
- `objectives_from_traces` fits the curve on every (position, ROI) trace (`regions_from_cubes` -> `trace_matrix(anchors=...)` -> `fit_drift`),
  against the Sensorgram's x (`x_is_time`; the app wires `SensorgramAxis.uses_time`, and an axis switch refits: `refit_for_axis`).
- Why a trace of only some cubes gives the same correction: each anchor reduces to (mean x, mean y), so the curve depends on the anchor cubes
  alone. Pinned by `AnchorCubesTest.test_a_trace_cut_to_some_cubes_gets_the_same_correction_as_the_whole_trace` (1e-9).
- A baseline change after a trial refits without a scan, as fit settings always did. If the new anchors need cubes the trial did not scan, the
  result goes **out of date** with its own message ("... now uses N cubes the trial did not scan; run it again") instead of being refitted wrong.
- With no curve subtracted nothing changed: pinned bit-identical to the old call (`ObjectivesFromTracesTest`).

Before/after on synthetic traces (drift 0.02 nm per cube, baseline step 35 cubes before the signal step, true step 3.0 nm): the difference read
3.7 nm uncorrected and 3.0 nm with the linear curve off. Not checked on real data yet.

Known limits: (1) with smoothing on, anchor cubes outside the two steps are smoothed in pieces cut at gaps in cube number, so an anchor's level
can differ slightly from the Sensorgram's (same with smoothing "none", the default); (2) more cubes per trial means a longer scan; (3) a change
of the plan's step borders after a trial does not refit (as before).

### Too few marked steps for the shape (`panels/sensorgram/baseline_controls.py`)

With Linear (the default) the first marked step corrects nothing: two are needed, and no simpler shape is swapped in (no silent fallback). The
reason used to be invisible for the Range method: the status row is hidden since 2026-10-23 and the Anchors menu belongs to the Anchor points
method only. Now `DriftBaselineControls.steps_note` is shown

- at the pointer right after marking a step or choosing a shape (`say`, wired to `QToolTip.showText` in the panel): "Linear needs 2 anchor steps,
  1 marked: mark 1 more, or choose the Constant shape. Until then the lines are not corrected.";
- as a greyed first line of the step's right-click menu ("Linear needs 2 anchor steps: 1 marked"; or "Not corrected: ..." with what the fit said,
  e.g. when the offset leaves no cube of the step);
- in the Shape pick-up's tool tip ("Not corrected now: ...").

The count is of marked steps the plan still has (`steps_in_plan`), so a mark left behind by a removed step does not count.

## 2026-11-10: the curve is fitted through every cube of the anchors (maintainer: "replace the original")

The maintainer asked why two marked steps still got "needs more anchors", and whether the fit should use all the points of a step (minus the
borders). Until then each anchor was collapsed to one point (mean x, mean y) and the shape was fitted through those points, so a trend *inside* a step
was lost, and a Quadratic / Cubic needed 3 / 4 steps.

- **Now** (`baseline_drift.fit_drift`): Constant / Linear / Quadratic / Cubic are least-squares fits through **every cube of the anchors**, each cube
  once (overlapping regions are a union), equal weight. `MIN_ANCHORS` is `constant 1`, every other shape `2`: two anchors are enough for any degree as
  far as the data go (the fit needs cubes at degree + 1 different x, else "needs cubes at N different x in the anchors"). One anchor alone is still
  refused for the lines and curves, so a trend in one stretch is never carried over the whole experiment (and no shape is swapped for a simpler one).
- **PCHIP stays on the anchor means** (it interpolates; it cannot follow a trend inside an anchor). Said in its tool tip.
- **The anchor marks** and `DriftFit.anchors_x / anchors_y / anchors_base` stay one point per anchor (its mean x and y), so the plot, the group
  averaging in `scan_trace._attach_drift` and the "where an anchor sits after correction" look are as before. `DriftFit.level` (what
  `Series.baseline` reports) is now the mean of every anchor cube, not the mean of the anchor means (same number when the anchors are equally long).
- **Longer anchors count more** (more cubes): a consequence of the equal weight per cube, deliberate.
- **Measured** on a simulated run (5 steps of 40 cubes, anchors = first and last H2O step, 2 cubes trimmed per border, 300 noise seeds; script
  `compare_fit.py` in the session scratchpad, not kept; **not real data**):
  - *Linear drift*: Linear is **unchanged** (identical to 4 decimals, old vs new). Quadratic / Cubic give the same bias but about **twice the spread**
    of the middle steps' means (0.0042 vs 0.0020 nm at 0.01 nm noise): extra freedom costs noise.
  - *Curved drift* (0.3 nm of bend over the run): Linear leaves a bias of -0.040 nm in the middle steps (old and new alike); the new Quadratic / Cubic
    remove it (bias 0.000, rms error of every cube 0.035 -> 0.010 nm at noise 0.01). **This is where the change pays.**
  - *Exponential settling at the start* (tau 300 s, trim 2 cubes): Linear +0.085 nm, new Quadratic -0.035, new Cubic -0.035 with a spread half of the
    Quadratic's. A longer border offset matters more now.
  - *Why Linear did not change:* the two anchors are far apart and short, so the line is set almost entirely by the difference between the two
    levels; the slopes inside a step hardly weigh in. Only the curved shapes use the within-step trend.
  - Cost: a quadratic fit through 3 anchors of 188 cubes, 0.37 -> 0.40 ms per ROI.
- Pinned by `tests/unit/test_lspri_baseline_drift.py` (`test_the_curve_follows_the_trend_inside_the_anchors`, `test_a_longer_anchor_counts_more...`,
  `test_a_cube_in_two_overlapping_anchors_counts_once`, `test_pchip_stays_on_the_anchor_means`, `test_too_few_anchors...`).
- **The ROI position tuning is unaffected** in how it scans (it still scans exactly the anchors' cubes, and the curve depends only on them); a test
  pins that a trace cut to some cubes gets the same correction as the whole trace.

## Open

- Before/after check on a real dataset with repeated buffer steps (not done yet, for either method, nor for the tuning; the numbers above are
  simulated).
- **A removed step keeps its mark.** Its ref stays in `baseline_anchor_steps` and matches nothing. If it was the only marked step, the method still
  counts as "steps marked": the range band stays hidden and the lines are uncorrected with "no anchors ...". The way out is "Clear all anchor steps"
  on any step's menu. Dropping such refs when the plan changes would be simple, but then undoing the step's removal would not bring the mark back.
- Free anchor *regions* (a dragged range instead of a point); exponential / smoothing-spline shapes.
- Copying anchors between plots (the maintainer's "translation function").
- The dashed curve is not in the CSV export; the export record has `anchor_rms_nm` and `baseline_note` per line.
