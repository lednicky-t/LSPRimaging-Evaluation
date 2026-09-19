# Group table ordering ("#" column, Move Up/Down)

2026-09-19. Adds a visible, user-editable order to the Group table's rows.

## Design decision: no new stored field

`AreaRoiGroup` (`domain/models.py:422`) got no new `index`/`order` field.
Instead, the Group table's new leftmost "#" column just displays each
group's existing position in `AnalysisState.area_roi_groups` (1-based), and
"move up/down" is a plain list swap of that list.

Why: list order already silently drove three things before this change -
default new-group palette color (`roi_geometry_mixin.py:_next_group_palette_color`),
the "Apply to existing groups" recolor action
(`roi_geometry_mixin.py:_apply_group_color_palette`), and the sensorgram
"Average by group" legend/bucket order
(`analysis_controller.py:_sensorgram_group_buckets`). A separate stored
`index` field would need to be kept in sync with that list across every
group-creation path (4 of them, see `group_table_controller.py` and
`roi_geometry_mixin.py:_group_selected_rois`) and every deletion/pruning
path (5+, e.g. `delete_group`, `remove_selected_rois_from_group`,
`_ungroup_selected_rois`) - real desync risk for no benefit, since the list
order was already the source of truth everywhere else. Surfacing the
existing list position instead means the number on screen always matches
what the rest of the app actually uses, with zero new persistence or
decode-path changes (JSON array order round-trips through
`storage/workspace.py`'s `asdict`-based encoders/decoders unchanged).

## What changed

- `gui/roi_table_helpers.py`: `GroupTableRowData` gained a `position: int |
  None` field (`None` for the synthetic Ungrouped row, which isn't part of
  `area_roi_groups`). Group table is now 5 columns: `#, Group, Sample,
  Reference, ROIs` (was 4). `group_id` moved from the name item's UserRole
  data to the new position item's (col 0) - `_group_id_for_row` still reads
  column 0, so lookup behavior is otherwise unchanged.
- `gui/group_table_controller.py`: `update_table()` passes `enumerate(...,
  start=1)` as `position`. New `move_group(group_id, direction)` (pure list
  swap by index, no ROI-id remapping needed since `group_id` is stable and
  non-positional - unlike `MainWindow._move_selected_rois_in_table`, which
  swaps and then renumbers ROI ids because ROI id *is* positional) and
  `move_selected(direction)` (toolbar/keyboard entry point, single-selection
  only). Context menu gained Move up/Move down, enabled based on the group's
  current index.
- `gui/layout_builder.py`: two new toolbar buttons (`group_move_up_button`/
  `group_move_down_button`, arrow-up/arrow-down icons), shown/hidden
  alongside the other group-only buttons. **Header-click sorting on the
  Group table is now off** (`setSortingEnabled(False)`) - it only ever
  resorted the on-screen rows, not `area_roi_groups`, so combined with a
  position column it would have let the "#" values and visual row order
  drift apart. Move Up/Down is the one supported way to reorder rows now.
- `gui/image_interaction_controller.py`: PageUp/PageDown on `group_table`
  call `move_selected(-1)`/`(1)`, mirroring the existing `roi_table`
  PageUp/PageDown block (which moves ROIs, not groups).
- `gui/main_window.py`: wired the two new buttons; both follow the same
  visibility toggle as the other group-only controls
  (`_apply_roi_list_view_mode`).

## Where order already came from (unchanged call sites)

- `_group_rois_by_column()` (`roi_geometry_mixin.py:288`) still assigns
  `group_col_{index+1}` / `Column {index+1}` from left-to-right detection
  order - this already matches what the new "#" column would show, no
  change needed there.
- New manual groups still append to the end of the list (`# = len + 1`),
  same as before - now just visibly numbered instead of implicit.

## 2026-09-19 follow-up: Move Up/Down felt slow

First pass copied the "standard mutation sequence" every other
`group_table_controller.py` method uses (push undo point, refresh ROI
overlays, refresh ROI summary, refresh sensorgram, save, refresh both
tables via the shared debounced `window._update_roi_table()`). That's
correct for rename/recolor/add-remove-ROI, which do change ROI-visible
state, but a pure reorder changes none of it - so every one of those calls
except the undo snapshot and the sensorgram redraw (needed for the
"Average by group" legend order) was pure waste on every click, hurting
most on real, multi-hundred-ROI arrays where reordering is done via several
rapid clicks in a row.

Measured with a standalone headless script (200 ROIs / 8 groups, no live
dataset - see CLAUDE.md's Performance Work rule on measuring before/after)
rather than trusting that removing calls "should" help:

| call | measured cost @ 200 ROIs |
|---|---|
| `RoiTableController.update_table()` (full ROI table rebuild) | ~28ms |
| `OverlayManager._update_roi_overlays()` (full overlay rebuild) | ~19ms |
| `UndoManager.make_snapshot()` (`deepcopy(window._state)`) | ~7ms |
| `GroupTableController.update_table()` (group table only, 8 rows) | ~1ms |

`move_group()` now: (1) drops the overlay/summary refresh entirely, and
(2) calls `self.update_table()` (the Group table's own rebuild) directly
instead of `window._update_roi_table()`, whose debounced timer rebuilds
*both* tables - the ROI table rebuild was the single largest cost of a move
click (~28ms, bigger than the overlay refresh already removed), and a
group reorder changes nothing the ROI table displays (a ROI's `Group`
column text and swatch colors come from group *membership* and the
group's own color, never from the group's position in the list). Verified
by monkey-patching `RoiTableController.update_table` with a call counter
across several `move_group()` calls plus event-loop drains: 0 calls, down
from 1 per click before.

Remaining cost after the fix is the undo snapshot's `deepcopy` (~7ms at
this scale, scales with dataset/ROI count) plus the Group table's own tiny
rebuild (~1ms) - both fundamentally necessary (undo correctness; the
visible "#" needs to actually update), so no further trimming was done at
that point. Debug-level stage timing (`Groups | move stage timing | ...`)
was left in place, gated at DEBUG (the app's log file always captures DEBUG
regardless of the console toggle - see `workflow_log_controller.py`), so a
future slow report has a number to start from instead of needing this
investigation repeated.

## 2026-09-19, second pass: real-dataset log evidence

The synthetic 200-ROI benchmark above turned out to understate the real
cost badly. The maintainer's own session log (a real ~160-ROI dataset,
reordering a "Column 15" group created by group-by-column, never yet run
through Start analysis) showed:

```
Groups | move stage timing | undo=343.0ms sensorgram=457.3ms total=804.9ms
```

Two costs the synthetic dataset couldn't reproduce:
- **`_push_undo_point`'s `deepcopy(window._state)`: 300-450ms**, not ~7ms -
  a real `ImageDataset` (records, acquisition metadata, chromatic
  models/landmarks, etc.) is far heavier than the synthetic test's bare
  `AreaRoi`/`AreaRoiGroup` lists.
- **`_render_sensorgram_display()`: 460-620ms**, not near-zero as its own
  docstring claims ("never triggers any computation itself"). True for a
  warm RAM cache, but this selection's ROIs had never been analyzed, so
  every one fell through to `_sensorgram_trace_for_roi`'s disk-backup path -
  a real per-ROI HDF5 read. The log's own `"10/10 selected ROI(s)
  unresolved"` line confirms every lookup was a full miss, i.e. the worst
  case, paid in full on every single click.

Both are now debounced using the exact same `prepare()`/`commit_prepared()`
pattern `_roi_move_undo_commit_timer` already uses for keyboard ROI-nudging
(added earlier for the identical "one deepcopy per repeat" problem - see
that timer's own comment in `main_window.py`): `move_group()` calls
`window._prepare_undo_snapshot(...)` instead of `_push_undo_point(...)`
(captures the pre-burst state once, no-ops on every subsequent call while
one is already pending), and arms two new 's**ingle**Shot' timers -
`_group_reorder_undo_commit_timer` (600ms, commits the prepared snapshot)
and `_group_reorder_sensorgram_timer` (150ms, calls
`_render_sensorgram_display()` once) - restarting whichever is already
running rather than letting them stack. The list swap itself and the Group
table's own "#" refresh (`self.update_table()`, ~1ms) stay synchronous, so
the visible reorder feedback is still instant; only the two expensive,
purely-cosmetic-until-the-user-stops-clicking steps are deferred.

Verified headlessly by monkey-patching `UndoManager.make_snapshot` and
`AnalysisController._render_sensorgram_display` with call counters around a
burst of 6 rapid `move_group()` calls with no event-loop drain between them:
0 snapshots and 0 renders during the burst, exactly 1 of each once the
debounce settles (plus one extra internal snapshot from
`commit_prepared()`'s own before/after signature comparison - pre-existing
to that method, not new). Confirmed the undo entry itself is still correct:
an alternating (net-zero) burst correctly produces no undo entry (same
"skip if unchanged" signature check `push()` already uses), a one-directional
burst correctly produces exactly one.

This still doesn't fix `_render_sensorgram_display()`'s own per-ROI disk
cost for a genuinely cold cache - that's a separate, deeper question about
whether/how to cache a "definitely not analyzed yet" result, and out of
scope here (it's shared, unmodified logic also used by the normal
selection-change path, not something specific to group reordering). The
debounce just stops a *reorder burst* from paying that cost once per click
instead of once per burst.
