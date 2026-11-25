# Array — manual array from the selection (2026-11-25)

Status: built, **not yet seen by eye** (static checks and offscreen tests only). **The old Array group (Auto / Semi / Manual detector, Refine, Reorder menu) was retired on 2026-11-25** (see the end of this file) and this section took its name: it was called Array\* until then.

## What it does

ROIs tab, new section **Array**: one button opens a panel inside the app window (a child of the Image panel, just under the button, over the image; it stays open until Cancel, Apply or the button; not a separate window). With ROI(s) selected, the panel sets a regular array; the copies are drawn as ghost outlines on the image and redrawn at every change; **Apply** makes them as one undo step, **Cancel** or the Array button closes it and makes nothing (Esc does not: a new key needs a row in `shortcuts.py` first). The selection (the *seed*) stays where it is and keeps its ids.

## Definitions (pinned by `tests/unit/test_lspri_roi_array_layout.py`; text in `roi/array_layout.py`)

- **Start point** (3 × 3 picker): where in the array the seed sits; the array grows away from it. An even count in the middle uses the lower-index node.
- **Pitch increment** (x and y separately) is a **factor**, 1 = constant pitch: gap k steps from the seed = pitch · factor^k (>1 widens, <1 narrows). Changed 2026-11-25 from an additive length, which had no neutral default.
- **Row offset**: every second row (counted from the seed's row) shifts along x by `offset × pitch x`, the way the array grows. Triangular default 0.5.
- **Angle**: whole lattice about the seed, clockwise on screen. A multi-ROI unit is **not** turned with it.
- **Regular cell** (one switch): rectangular → square cell; triangular → all 60° (pitch y = x·√3/2, row offset 0.5, factor y = factor x). Y fields lock and show the values in force.
- **Type and regular cell** are free-standing icons in one row (no button frames): ONE type icon that shows the current lattice and switches to the other on a click, and one cell icon that shows the cell you get: rectangular square (on) / rectangle (off), triangular equilateral (on) / lopsided (off). The start-point picker sits under them; Columns (x) stand over Pitch x and Rows (y) over Pitch y; "Advanced" is free-standing roll-down text. The panel's background is the window colour (the same as the fields). There is no miniature preview in the pop-out (removed 2026-11-25, maintainer); the canvas ghosts are the preview.
- **Numbering** (ROI ids, separate from the start point): by rows or by columns, from one of the four corners. Drawn as a reading path over nine dots (start dot big, arrow along the first row / column, dashed jumps), click a quarter of the square to pick the corner; **a double-click on the picked corner switches rows / columns** (there is no separate button). It follows the lattice's own rows and columns (turns with the angle). At Apply the array's ROIs (the selection too) take, in that order, the ids they hold together (`StampPlan.id_map`; `RoiToolbox._remap_ids`, shared with `reorder_*`), inside the same single undo step. **So the selected ROI's own number can change** (it is renumbered like any ROI); ROIs outside the array keep theirs, and results, groups and links follow through `roi_ids_renumbered`.
- **Opacity** (2026-11-25, maintainer): a wedge slider (the one the overlays use) at the right end of the top row sets how strongly the **preview spots on the image** are drawn. It **starts in the middle** (the usual look: full outline, fill 25 %). Left of the middle the spots fade (outline down to 10 %, fill with it); right of it the outline stays full and the fill grows to solid (`array_ghosts.preview_alphas`). The panel itself stays opaque. Remembered (`array_star/preview_opacity`).
- **Several selected ROIs** repeat as one unit, placed by their centroid.
- Copies keep shape, rim and role; no label, colour or group (what a copy is, `roi/clipboard.py`). Links between the seed ROIs are remade between the copies of one node; a link to a reference outside the selection is not copied.

## Where things are

| Piece | File |
|---|---|
| Node geometry (pure) | `roi/array_layout.py` (`ArraySpec`, `array_layout`, `LATTICE_KINDS`) |
| Copies + recipe (pure) | `roi/array_stamp.py` (`plan_stamp`) |
| Command, one undo step | `RoiToolbox.stamp_array` |
| Recipe fields | `RoiArrayGroup.spec / unit_size / seed_node` (`domain/roi_model.py`); members are node-major |
| Pictograms (painted) and the frameless `FlatIconButton` | `panels/image/array_pictograms.py` |
| Start picker (nine painted dots, no buttons) | `panels/image/array_picker.py` |
| Pop-out content | `panels/image/array_panel.py` |
| Ghost outlines (pure numpy) | `panels/image/array_ghosts.py` |
| Wiring, preview, Apply | `panels/image/array.py`; section in `roi_tab.add_array`; built in `ribbon_build.build_roi_tab`; redrawn by `ImagePanel._draw_roi_overlay` (`on_frame`) |
| Remembered settings | `ARRAY_KEYS` in `storage/ui_state_keys.py` |

## Decisions and limits to know

- A seed that already belongs to an array is refused with a reason (it would silently leave that array).
- Rows and columns are limited to 200 each: the ghosts are drawn on the GUI thread.
- A pixel-map seed is previewed as a circle of its nominal diameter; the copy itself moves by whole pixels like every pixel map.
- The ghost shows the sample outline only (no rim) and does not mark nodes outside the image yet.
- Re-editing an existing array from its recipe is **not built yet**: the recipe stores everything needed (`spec`, `unit_size`, `seed_node`, member ids).
- Export transforms (`session_carry._carry_array`) move only the recipe's anchor, not its angle / pitch; the members' own positions stay the truth.
- New lattice types: add a `LatticeKind` (regular lattice with a row shift). Lattices with several points per cell need a basis and a design discussion.

## Added later the same day (maintainer)

- **Reorder** (third icon of the Array section, `panels/image/array_reorder.py`): the old Array group's Reorder icon; it opens a small pick-up with the same `NumberingPicker` as the pop-outs (click a corner, double-click the picked corner for rows / columns) and a tick. The tick renumbers the **selected ROIs, or all if none are selected** (`reorder_roi_numbers` -> `RoiToolbox.reorder_subset`, one undo step; other ROIs keep their numbers). The order is `roi/ordering.order_by_grid_from_corner` (rows / columns found by position, as the old Reorder menu; a strongly tilted array may need Auto rotate first). Not remembered across restarts. No dropdown arrow on the icon. **Live preview** (`array_reorder_preview.py`): while the pick-up is open the image shows the numbers the ROIs in scope would get (accent colour, even with ROI labels off); the real labels of those ROIs are muted (`RoiLabelItem.set_muted`) until it closes. The numbers come from the same `plan_reorder` the tick uses.
- **Select whole array**: right-click a ROI that belongs to an array > "Select whole array (N ROIs)" (`roi_context_menu.SELECT_ARRAY`); the members come from `RoiToolbox.array_groups()`. Left out when all are selected or the selection is locked.
- The Groups section lost its label-menu icon (the View ribbon has it; the original stays built and hidden because the View copy mirrors it) and the Group pick-up menu became three icons: Group / Ungroup, Group by rows, Group by columns.

## Retiring the old Array group (2026-11-25, maintainer)

- **Removed**: `panels/image/array_controls.py` (the ribbon section, its settings pop-over), `array_actions.py`, `array_reorder_picker.py` (the old Reorder menu), `array_mode_picker.py`, `roi/array_task.py` (thread runner), `roi/array_pipeline.py` (detect / refine / place), `roi/ring_size.py`, the old detector `find_array` / `lattice_nodes` / `refine_centers` in `roi/array_detection.py` (what the finder still shares stays there), the toolbox commands `detect_rois`, `place_array`, `refine_rois`, and the old remembered settings (`array/...` keys, `ARRAY_KEYS` -> now the pop-out's keys). Tests of those went with them (`test_lspri_rewrite_array_actions.py`, `..._roi_array_pipeline.py`, `..._roi_array_commands.py`, `..._roi_ring_size.py`).
- **Kept, moved**: `LengthField` -> `length_field.py` (the pitch fields; the "auto" variant went), the Find icon -> `array_find_icon.py`, the Reorder icon -> `array_reorder.py`.
- **Auto rotate / Auto crop** (Image tools tab) now live in `panels/image/auto_transforms.py`. **Auto rotate runs on the array finder** (the engine behind Find): reference wavelength of the cube on screen, guided by the selected ROIs (none: the most common round spot), using the Find pop-out's own settings (dark / bright, mask, thresholds) except that corner ROIs never cut the extent; its own `ArrayFindTask`, progress joined to the task indicator as "Auto rotate" and cancellable there; a tilt beyond 45 degrees is refused (the finder may have taken columns for rows). The old "tilt above 1 degree: rotate and detect again?" question is gone with Detect. **Auto crop** is unchanged (ROI lattice, readiness checklist).
- **Rename**: the section, button, tips and messages say "Array" (not "Array\*"); files `array_star*.py` -> `array*.py` (`array.py`, `array_panel.py`, `array_ghosts.py`, `array_pictograms.py`, `array_picker.py`), `ArrayStarController` -> `ArrayController`, `ArrayStarPanel` -> `ArrayPanel`, `ARRAY_STAR_KEYS` -> `ARRAY_KEYS`. The saved-setting keys keep their `array_star/...` strings so remembered values survive. Find keeps its "Find\*" name.
- **Old sessions**: nothing in a saved session changed. A session made with the old detector still loads (its array recipe, `RoiArrayGroup` with rows / cols / spacing, is read as before); only the buttons that made it are gone.
