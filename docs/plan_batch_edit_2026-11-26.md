# Plan batch edit: shrink and offset many steps (2026-11-26)

**What**: the Sensorgram's Experimental plan tab has a group "Batch edit" with two icon buttons, each opening a pop-up with a live preview on the plan bar.
Asked for by the maintainer so that a gap can be opened between steps (the solution change is not part of either step) without dragging every edge by hand.

**Decisions (maintainer, 2026-11-26)**
- Acts on the steps selected on the bar; **none selected = all steps**. The pop-up's first line says which.
- **Shrink**: unit cubes / time / percent of the step's own length; from start / end / **both = the amount is split** (half per side; percent is the total, split the same way).
  A step too short to lose that much is **left unchanged** (not cut to what fits); the status line counts them.
- **Offset**: cubes or seconds, signed. Steps **may overlap** afterwards. A step that would leave the data (before the first cube, past the end of the data) is left unchanged.
- Settings (unit, side, the amount of each unit) are remembered (`sensorgram/batch_<kind>/...`).

**Where**
- `plan/batch_edit.py`: pure rules (`BatchSpec`, `plan_batch`, `apply_batch`, `status_text`); `plan/edits.py` `set_bounds`.
- `selection/experiment_plan_module.py`: `plan_batch` (preview, no change), `apply_batch` (one undo step, reason `"boundary"`, notice with the outcome).
- `panels/sensorgram/batch_group.py` (the two pop-ups), `step_overlays.py` (`_on_batch_preview`, `_on_batch_apply`), `plan_bar.py` (`set_preview`, gold dashed boxes), `plan_icons.py` (the two icons).
- Tests: `tests/unit/test_lspri_plan_batch_edit.py`, `tests/integration/test_lspri_plan_batch_edit.py`.

**How an amount becomes an edge** (grid = plotted cubes' start times + the end of the data, as for a dragged edge)
- Cubes count grid points: the first whole cube of a step starts at the first grid point at or after its start, the end is the last grid point at or before it. Shrinking by n
  drops exactly n cubes per side (both, odd n: the start gives up the extra cube).
- Seconds / percent aim at a time and snap to the nearest grid point inside the step, so an amount under half a cube spacing moves nothing ("would not move").
- A step always keeps at least one cube (`ExperimentPlanModule._min_step_length`).

**Preview**: only the plan bar is redrawn (new extents as gold dashed boxes over the old bar). The step statistics, smoothing and curves are recomputed once, on Apply
(`REASON_BOUNDARY` redraw), never live. The preview needs the plan bar to be shown (the status line says so when it is off).

**Why this serves**: priority 2 (data integrity: the measured steps stay untouched, edits are the session overlay, one undo step) and 1 (what is previewed is exactly what is applied:
one function). Dragging an edge by hand is unchanged.

**Not seen by eye**: the pop-up layout, the two icons and the preview boxes were built and tested without looking at the running app. Check them on a real dataset.
Possible follow-ups, not built: a selection that stays when the pop-up opens is by index (an offset can reorder steps; Apply re-selects the same steps by reference).
