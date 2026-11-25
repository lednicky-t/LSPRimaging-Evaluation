# Array\* — manual array from the selection (2026-11-25)

Status: built, **not yet seen by eye** (static checks and offscreen tests only). The old Array group (`panels/image/array_controls.py`, Auto / Semi / Manual) is untouched; the maintainer decides later which to keep.

## What it does

ROIs tab, new section **Array\***: one button opens a panel inside the app window (a child of the Image panel, just under the button, over the image; it stays open until Cancel, Apply or the button; not a separate window). With ROI(s) selected, the panel sets a regular array; the copies are drawn as ghost outlines on the image and redrawn at every change; **Apply** makes them as one undo step, **Cancel** or the Array\* button closes it and makes nothing (Esc does not: a new key needs a row in `shortcuts.py` first). The selection (the *seed*) stays where it is and keeps its ids.

## Definitions (pinned by `tests/unit/test_lspri_roi_array_layout.py`; text in `roi/array_layout.py`)

- **Start point** (3 × 3 picker): where in the array the seed sits; the array grows away from it. An even count in the middle uses the lower-index node.
- **Pitch increment** (x and y separately) is a **factor**, 1 = constant pitch: gap k steps from the seed = pitch · factor^k (>1 widens, <1 narrows). Changed 2026-11-25 from an additive length, which had no neutral default.
- **Row offset**: every second row (counted from the seed's row) shifts along x by `offset × pitch x`, the way the array grows. Triangular default 0.5.
- **Angle**: whole lattice about the seed, clockwise on screen. A multi-ROI unit is **not** turned with it.
- **Regular cell** (one switch): rectangular → square cell; triangular → all 60° (pitch y = x·√3/2, row offset 0.5, factor y = factor x). Y fields lock and show the values in force.
- **Type and regular cell** are free-standing icons in one row (no button frames): ONE type icon that shows the current lattice and switches to the other on a click, and one cell icon that shows the cell you get: rectangular square (on) / rectangle (off), triangular equilateral (on) / lopsided (off). The start-point picker sits under them; Columns (x) stand over Pitch x and Rows (y) over Pitch y; "Advanced" is free-standing roll-down text. The panel's background is the window colour (the same as the fields). There is no miniature preview in the pop-out (removed 2026-11-25, maintainer); the canvas ghosts are the preview.
- **Numbering** (ROI ids, separate from the start point): by rows or by columns, from one of the four corners. Drawn as a reading path over nine dots (start dot big, arrow along the first row / column, dashed jumps), click a quarter of the square to pick the corner; **a double-click on the picked corner switches rows / columns** (there is no separate button). It follows the lattice's own rows and columns (turns with the angle). At Apply the array's ROIs (the selection too) take, in that order, the ids they hold together (`StampPlan.id_map`; `RoiToolbox._remap_ids`, shared with `reorder_*`), inside the same single undo step. **So the selected ROI's own number can change** (it is renumbered like any ROI); ROIs outside the array keep theirs, and results, groups and links follow through `roi_ids_renumbered`.
- **Opacity** (2026-11-25, maintainer): a wedge slider (the one the overlays use) at the right end of the top row sets how strongly the **preview spots on the image** are drawn. It **starts in the middle** (the usual look: full outline, fill 25 %). Left of the middle the spots fade (outline down to 10 %, fill with it); right of it the outline stays full and the fill grows to solid (`array_star_ghosts.preview_alphas`). The panel itself stays opaque. Remembered (`array_star/preview_opacity`).
- **Several selected ROIs** repeat as one unit, placed by their centroid.
- Copies keep shape, rim and role; no label, colour or group (what a copy is, `roi/clipboard.py`). Links between the seed ROIs are remade between the copies of one node; a link to a reference outside the selection is not copied.

## Where things are

| Piece | File |
|---|---|
| Node geometry (pure) | `roi/array_layout.py` (`ArraySpec`, `array_layout`, `LATTICE_KINDS`) |
| Copies + recipe (pure) | `roi/array_stamp.py` (`plan_stamp`) |
| Command, one undo step | `RoiToolbox.stamp_array` |
| Recipe fields | `RoiArrayGroup.spec / unit_size / seed_node` (`domain/roi_model.py`); members are node-major |
| Pictograms (painted) and the frameless `FlatIconButton` | `panels/image/array_star_pictograms.py` |
| Start picker (nine painted dots, no buttons) | `panels/image/array_star_picker.py` |
| Pop-out content | `panels/image/array_star_panel.py` |
| Ghost outlines (pure numpy) | `panels/image/array_star_ghosts.py` |
| Wiring, preview, Apply | `panels/image/array_star.py`; section in `roi_tab.add_array_star`; built in `ribbon_build.build_roi_tab`; redrawn by `ImagePanel._draw_roi_overlay` (`on_frame`) |
| Remembered settings | `ARRAY_STAR_KEYS` in `storage/ui_state_keys.py` |

## Decisions and limits to know

- A seed that already belongs to an array is refused with a reason (it would silently leave that array).
- Rows and columns are limited to 200 each: the ghosts are drawn on the GUI thread.
- A pixel-map seed is previewed as a circle of its nominal diameter; the copy itself moves by whole pixels like every pixel map.
- The ghost shows the sample outline only (no rim) and does not mark nodes outside the image yet.
- Re-editing an existing array from its recipe is **not built yet**: the recipe stores everything needed (`spec`, `unit_size`, `seed_node`, member ids).
- Export transforms (`session_carry._carry_array`) move only the recipe's anchor, not its angle / pitch; the members' own positions stay the truth.
- New lattice types: add a `LatticeKind` (regular lattice with a row shift). Lattices with several points per cell need a basis and a design discussion.
