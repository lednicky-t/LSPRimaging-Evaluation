# The plan's steps are the app's ranges (2026-10-29): analysis and proposal

Status: **P1-P3 BUILT 2026-10-29 (uncommitted), see "Built" at the end; P4 (the first consumer, ROI position tuning) is not.** The five open decisions at the end were all accepted by the maintainer the same day, with one change: the roles are marked **B1 / S1** because there may be more pairs later. Maintainer's decision (2026-10-29): there is no separate "ranges" feature. A range is a step of the experimental plan, which already has an
order, a note, a colour, and can be dragged, resized, retyped and recoloured. What is missing is (a) adding, removing and unifying steps, (b) a way to say which step is the
baseline and which is the signal, (c) the plan controls as a ribbon tab with its tools live only there, (d) a step list that other panels can pick from. A first attempt with a
separate `ranges/` module (own session block, own ribbon group, own bands) was built on 2026-10-28 and removed on 2026-10-29, before any commit.

Why this matters beyond ROI position tuning: in practice the user picks a baseline step and a signal step and wants to see which ROI / group gives the best signal. Every such
function needs the same two choices, so they are made once, in the plan, and read by everything else.

## What the plan already gives (checked in the code)

| Need | Where it is |
|---|---|
| Steps with number, note, colour, start / end (unix ms), in time order | `plan/edits.py` `EffectiveStep`, `ExperimentPlanModule.steps()` |
| Edits are an overlay on the measured steps, saved in the session (`"plan"` block, schema 1.5), each keyed by a fingerprint of the measured step (number, start, note) | `plan/state.py`, `plan/edits.py` `apply_edits` |
| Drag an edge (snapped to cubes), retype the note, colour, reset; all undoable | `plan/module.py`, `panels/sensorgram/plan_actions.py` |
| Select one / several steps (click, Ctrl, Shift) and a right-click menu (Edit text, Colour, Default colour, Reset step(s), Reset all) that acts on the selection | `plan_bar.py` (`selected_indices`), `plan_actions.py` `build_menu` |
| The cubes that belong to a step: **whole cubes only**, a cube that straddles the step's edge belongs to neither step | `analysis/step_stats.py` `assign_cubes` |
| Mean, SD, drift, noise per ROI / group and step; the table on the plot; smoothing inside steps | `step_stats.py`, `plan/stats_table.py`, `step_overlays.py` |
| An evaluation window inside a step (skip the first / last seconds, or use only the last N seconds) | `step_stats.py` `StepWindow`: **in the code, not in any UI** |
| A ribbon with tabs where canvas tools are live only on their tab (the ROI gestures work only on the ROIs tab) | `panels/image/tool_ribbon.py` (`ROI_TAB`) |

On the test dataset the maintainer's test ranges are plan steps: cubes 2-32 are inside step 1 (cubes 1-32), cubes 201-226 inside step 8 (cubes 201-227).

## What is missing, and the one real design problem

1. **The overlay can only change measured steps.** Every edit is keyed by a measured step's fingerprint, so a step that is not in the dataset (an added one) or one that must vanish
   (removed, merged away) has no place to live. Needed in `PlanEditsState`: `removed` (fingerprints of measured steps hidden), `added` (user steps: id, start, end, note, colour).
   **Unify needs no new record**: it is "the first step's end moves to the last step's end" (an ordinary edit) plus "the others are removed". Reset brings everything back.
2. **A step has no stable identity others can keep.** Today it is an index into the current list (changes as soon as a step is added or removed) or the fingerprint of a measured
   step (an added step has none). Needed: one stable key per step (measured: its fingerprint; added: its id) and an `order` (1, 2, 3 ... in time) for display.
3. **Saved format**: the `"plan"` block gets the new fields; session schema 1.5 -> 1.6, **additive** (a 1.5 file loads with no removed / added steps and no roles). Needs the
   maintainer's yes before it is written (root rule on saved formats).
4. **A structure change is not a boundary change.** Selection in the bar is by index, and smoothing inside steps and the stats table take the steps as pieces, so adding, removing
   or unifying must (a) clear or re-map the selection and (b) ask the Sensorgram for a redraw like a moved boundary does (`step_overlays._STEP_TIME_CHANGES` gets a "structure" reason).

## Proposed behaviour

**Numbers.** The number a user sees is the **order in time** (1, 2, 3 ...), renumbered when a step is added or removed. Other panels keep the stable key, never the number, so
renumbering cannot make "baseline = step 3" point at another step. (Alternative: keep the plan file's numbers and give added steps new ones at the end; then the order and the
numbers disagree after an insertion. Recommended: order.)

**Right-click menu on a step** (the existing items stay): `Remove step`; `Add step >` `In front of this step` / `Behind this step`; `Unify selected steps` (enabled when two or more
are selected and no other step lies between them); `Use as baseline` / `Use as signal` (a check mark when it is). Remove and Unify act on the selection when the clicked step is in it,
like Colour and Reset do today.

- **Add step**: if there is free time on that side of the step (a gap to the next step or to the edge of the data) the new step fills it; if the steps are joined, the new step is cut
  off the current one, half of it, on a cube boundary. Either way the result is two non-overlapping steps the user then drags. The note is empty, the colour automatic.
- **Remove step**: the step goes, its time belongs to no step (like an idle stretch today). "Reset" or Undo brings it back.
- **Unify**: one step from the first start to the last end, with the first step's note and colour. A gap between them is absorbed.

**Baseline and signal.** Two roles, stored in the plan state (session): *baseline step* and *signal step*. Set from the menu, or from two pick-ups in the tab; the bar shows a small `B` /
`S` chip on them. If a role's step is removed or unified away the role is cleared and the tab says so. Everything that needs a stretch of the experiment reads these two.

**The tab.** The Sensorgram ribbon becomes a ribbon with tabs, as the Image panel's: tab **Plot** (today's groups Live, Statistic, Smoothing, Baseline, View) and tab **Experimental plan**
with the group **View** (the plan bar's controls: show, height, top / bottom, transparency, text menu), a group **Evaluation** (Baseline step, Signal step), and the group **Step stats**.
The new structural items of the menu (remove, add, unify) and the role items work **only while the Experimental plan tab is active**, as the ROI gestures work only on the ROIs tab, so
nothing structural happens by accident while working on the traces. Select, drag, retype and colour stay live everywhere, as today. (To confirm.)

**Shared with other panels.** `ExperimentPlanModule` already hands out `steps()` and signals `steps_changed`. Added: each step's stable key and order; `step(key)`; `cubes_of(key)`
(the whole cubes, from `assign_cubes`); `baseline_key()` / `signal_key()` with their commands; the signal `steps_changed(reason)` gets the reasons `structure` and `roles`. A small
reusable pick-up, `StepPicker` ("3 · 70perEG ..." with the step's colour), that any panel can put in its ribbon and that follows the module (it also shows a step that was removed as missing).

## Who uses it

- **ROI position tuning**: baseline step and signal step from the roles (no cube boxes in the Tuning tab); the cubes are `cubes_of(...)`, optionally trimmed by an evaluation window.
- **"Which ROI / group gives the best signal"**: the same numbers at the ROIs' current positions (a 1x1 lattice, and no scan needed: they come from the stored scan), shown as a map on the
  image and a ranked table. Needs only the roles, `step_stats` and the objectives of the tuning plan; later.
- **Trimming a step for evaluation**: the plan's steps are whole steps, the test ranges were trimmed by a cube (2-32, 201-226). Today the user trims by dragging the edge by a cube;
  `StepWindow` (skip the first / last seconds) could get two fields in the Evaluation group later. Not part of the first version.

## Build order (each part tested on its own)

* **P1** pure model: `PlanEditsState` (removed, added, roles), `EffectiveStep.order` and key, operations add / remove / unify / set role, `ExperimentPlanModule` commands (one undo step each),
  session block and schema 1.6. Tests: planted cases for every operation, unify of non-adjacent steps refused, role cleared when its step goes, old file loads, reset.
* **P2** the right-click menu items and the `B` / `S` chips on the bar (gesture tests with `.click()` / `trigger()`, as the existing plan tests).
* **P3** the tab: the Sensorgram's ribbon with tabs, the plan groups moved into the Experimental plan tab, the structural items gated by the active tab.
* **P4** `StepPicker` and the first user (the Tuning tab, when it is built).

## Decisions (all taken as recommended by the maintainer on 2026-10-29; the one change is on the roles: B1 / S1, more pairs possible)

1. Number = order in time, renumbered [yes], or the plan file's number.
2. Add step: fill the free time next to the step, else cut half off the current step [yes].
3. Unify keeps the first step's note, number and colour [yes].
4. Only the structural items and the roles gated by the tab; select / drag / retype / colour stay live everywhere [yes], or gate all editing.
5. Roles: menu items, `B` / `S` chips and two pick-ups in the tab [yes].

## Built (2026-10-29, uncommitted; the look of the tab, the pickers and the chips is unseen by eye)

**P1, the model.** `plan/state.py`: `PlanEditsState` gains `removed` (fingerprints of hidden measured steps), `added` (`AddedStep`: uid, start, end, note, colour), `next_uid` (a uid is never reused, saved) and `pairs`
(`EvalPair`: number, baseline, signal; a step is named by a reference, `("m", step, start, note)` or `("a", uid)`). Written only when present: a session with only the old kinds of edit is saved byte for byte
as before. **Session schema 1.5 -> 1.6, additive** (a 1.5 file loads with none of it). `plan/edits.py`: `EffectiveStep` gains `order` (the number the user sees), `ref`, `is_added`, `roles` (`("B1",)`); operations
`remove_steps`, `restore_removed`, `unify`, `can_unify`, `add_step`, `set_role`, `roles_lost`; every edit function works on an added step too (its record is changed in place). Unify is "the first step's end moves,
the others are removed", no record of its own. `ExperimentPlanModule`: commands `remove_steps`, `restore_removed`, `unify_steps`, `add_step` (returns the new reference), `set_role`; queries `step_by_ref`,
`baseline_step(pair)`, `signal_step(pair)`, `cubes_of(step)` (the whole cubes), `has_removed`, `can_unify`; reasons `structure` and `roles`; signal `notice(text)` ("Cleared the signal of pair 1: that step is gone.").
Each command is one undo step. Reset all brings back removed steps and drops added ones (roles on measured steps stay).

**P2, the bar and the menu.** `PlanBarItem` draws the roles as chips (`B1` neutral, `S1` accent) hanging from the bar's free edge. The right-click menu (`PlanBarActions.build_menu`) gains, while the structure tools
are on: Remove step(s), Add step > In front of / Behind this step, Unify selected steps (greyed unless the selected steps are neighbours), Restore removed steps, Use as baseline (B1) / Use as signal (S1)
(check marks, for one step only). A new step is selected after Add, the merged step after Unify. `StepOverlays` clears the bar's selection after a `structure` change and redraws smoothing-in-steps and the
step table (a `structure` change is a time change).

**P3, the tab.** The Sensorgram's control row is an `ImageToolRibbon` (the Image panel's tabs) with the tabs **Plot** (Live, Statistic, Smoothing, Baseline, View) and **Experimental plan** (View = the plan bar's
controls, Evaluation = two `StepPicker`s for B1 and S1, Step stats). The structure tools are on only while the Experimental plan tab is shown (`SensorgramPanel._on_ribbon_tab` -> `StepOverlays.set_structure_tools`);
select, drag, retype and colour work on every tab, as before. The shown tab is remembered (`sensorgram/ribbon_tab`). `panels/step_picker.py` `StepPicker` is the shared pick-up other panels can use: it lists the
steps by number, note and colour and reports a step reference.

Tests: `tests/unit/test_lspri_plan_structure.py` (42), `tests/integration/test_lspri_plan_structure_module.py` (17), new menu tests in `test_lspri_plan_bar_gestures.py`, tab and picker tests in `test_lspri_plan_bar.py`.

**Known limits / to look at.** The chips and the picker colours are unseen. A step cut in two by Add takes half on a cube boundary; a tail of less than one cube after the last step is not a step. The `ImageToolRibbon`
lives in `panels/image/` and is now also used by the Sensorgram (a move to `panels/` is a clean-up for later). Unify and Remove act on the selection; Add and the roles on the clicked step only. Not built: more than
one pair in the UI (the model has them), an evaluation window inside a step (skip the first / last seconds: `step_stats.StepWindow` has it, no UI), the first consumer (ROI position tuning takes `baseline_step()` and
`signal_step()` and `cubes_of(...)`).
