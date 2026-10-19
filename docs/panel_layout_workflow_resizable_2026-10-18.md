# Panel layout: Workflow resizable, four columns, Image minimum width (2026-10-18)

Maintainer request: make the Workflow panel "as other panels" (its fixed width and
fixed position were a 2026-09-24 decision, now reversed), allow four columns instead
of three, and explain why the Image panel cannot be made narrower.

## What changed

- `PanelContainer` (`panels/dock_container.py`): `fixed_width` is gone, replaced by
  `min_width` (a floor, not a clamp). Collapsing remembers the width and gives it back
  on expand (a chain of `resizeDocks` steps, see "Qt behaviours").
- `app_rewrite.py`: Workflow is now a column of the **same split tree** as the other
  panels (it used to be alone in `LeftDockWidgetArea`). Default look is unchanged
  (Workflow full height on the left, 340 px). `setCorner` call and the
  `FixedWidthSeparatorGuard` are gone. `_DOCK_LAYOUT_STATE_VERSION` 3 -> 4.
- `panels/workflow/panel.py`: horizontal scroll bar "as needed" (was "always off").
- `panels/layout_presets.py`: `apply()` now falls back to show/hide when Qt rejects a
  saved preset blob (before, it silently did nothing).
- Tests: `tests/integration/test_lspri_workflow_dock_resizable.py` (new),
  `test_lspri_workflow_panel_width_budget.py` (fixed -> default width).

**One-time cost:** the version bump makes Qt reject every window layout and custom
layout preset saved before this; the default layout is built and a preset falls back to
showing/hiding panels. Re-arrange and "Save Current Layout to Active Preset" once.

`panels/fixed_width_separator_guard.py` (the event filter that made the separator beside the
fixed Workflow inert) was deleted with the maintainer's OK; it is in git history.

## Smaller ribbon icons (same day, maintainer request: "save some space")

Ribbon icon buttons 28/22 px -> **24/18 px**. `panels/ribbon_icon_size.py` (a leaf module, so no
import cycle) is now the single source: `image/general_group.py` re-exports it to the ribbon files,
`mask_edit_common.py` (the Mask tab's edit / PNG / scan buttons) imports it, and the eight files that each
kept their own copy (mask scope/overlay/edit-tool, ROI scope, histogram-highlight overlay, Image-tools
row, text-angle control) now import it. The captioned-group height (was a 42 px literal in two files) is
derived (`ribbon_icon_size.CAPTIONED_GROUP_HEIGHT`), so the ribbon rows are also 4 px lower. To try another size, change
the two numbers in `ribbon_icon_size.py` only. Not touched: the 22/16 px canvas bar (`canvas_tools.py`, the
slider bar, frame tools), the 32/30 px choice pickers (label placement, group-label icons) and the
26/22 px "apply" chip next to the Calibrate fields.

## Qt behaviours found (all measured, offscreen, 1400 and 1900 px windows)

1. **No central widget => extra window width goes to the left dock area.** With Workflow
   alone in `LeftDockWidgetArea` and no clamp, a 1900 px window gave it 798 px, and every
   further pixel of widening went to it; `resizeDocks([workflow], ...)` did nothing.
   Inside the shared tree, widening is shared (Image gets most) and `resizeDocks` works.
2. **`resizeDocks` closes only part of the gap per call** (asked 200 from 345: 223, 203,
   200; asked 357 from a 36 px strip: 313). Repeat until within a pixel. Naming a
   neighbour dock as well (`[workflow, image]`) is within ~5 px in one call, which is
   what the initial size uses.
3. **A saved state under another version is rejected, not repaired** (existing note at
   `_DOCK_LAYOUT_STATE_VERSION`): restoring an old-shape blob maps sizes onto the wrong
   nodes.

## Why "max three columns": there is no such limit in the code

The number of columns is limited by the sum of the panels' **minimum widths** in the
window (a column that cannot fit is simply not offered as a drop target).

| Panel | Minimum width |
|---|---|
| Workflow | 340 (fixed) -> now 240 (floor; widest stage needs 213 + 6 frame + ~15 scroll bar) |
| Image | **823** (content 817) |
| ROI / Groups | 212 |
| Histogram | 133 |
| Spectra / Sensorgram | 116 / 143 |

Workflow + Image + ROI = 1375 px: three columns just fit a 1400 px window (window minimum
1379). A fourth column needed 1520 px. After this change four columns (Workflow | Image |
ROI | Histogram) needed **1420 px**. Then the ribbon icons were made smaller (section below):
Image 823 -> **763**, ROI table 212 -> 188, so four columns need **1336 px** and fit the
default 1400 px window (pinned by `test_four_columns_fit_the_default_window`). Image's 763 px is
still the largest part, so the remaining lever is the Image panel's ribbon (options below).

Numbers in the table and in "Why the Image panel cannot be narrower" are the *before-icons*
measurements (2026-10-18, first pass); with 24/18 px icons the Mask tab is 637 px (was 697),
View 592 (633), `imageTopBar` 757 (817).

## Why the Image panel cannot be narrower

Measured `minimumSizeHint().width()`:

- `imageTopBar` **817** = the tool ribbon (`ImageToolRibbon`, 779) + the "i" button and margins.
  The ribbon is a `QStackedWidget`; a stack's minimum is the **widest of all its pages,
  shown or not**: Mask 697, View 633, ROI 381, Background 291, ... plus the "General" column.
- `imageNavigationBar` 512 (the slider ribbon, 492): fixed-width spin boxes and axis titles
  around the Cube / lambda sliders.
- The plot canvas itself has no minimum worth mentioning.

So it is the **ribbon rows** (buttons, spin boxes, check boxes laid out side by side), not
the image, that stop the panel shrinking. Qt refuses to make a window narrower than its
children's minimums, so the dock cannot go below 823 px.

### Options for letting it shrink (not built; waiting for the maintainer)

| Option | What happens when the panel is narrower than the ribbon | Risk |
|---|---|---|
| A. Horizontal scroll area around the ribbon (recommended) | The ribbon keeps its layout; a thin sideways scroll bar appears. Nothing is removed or re-laid-out. | Low-moderate: GUI wiring only (the top bar's double-click roll-up, the cursor overlay parented to the top bar, popovers anchored to ribbon buttons, ribbon height with/without the scroll bar). No science code touched. |
| B. Overflow ">>" menu (Office / VS Code style) | Controls that do not fit move into a menu. | Higher: every control must be re-parentable; many have live state, popovers, shortcuts. |
| C. Wrap rows (flow layout) | Ribbon grows taller as it narrows. | Higher: ribbon height is assumed fixed (`_ROW_HEIGHT`, `RibbonDrawer` animates max height). |
| D. Force a smaller minimum and let it clip | Controls silently cut off, unreachable. | Not acceptable. |

With A on both bars the Image minimum would be about 520 px (the slider bar, 512, becomes
the limit), so four columns need roughly 240 + 520 + 212 + 133 = ~1110 px.

## Number fields next to the sliders removed (same day, maintainer request)

The two borderless number fields right of the Cube and wavelength sliders (`CubeSpinBox`,
`GuidedValueSpinBox`, with the wavelength autocomplete `QCompleter`) were removed from the Image
panel's navigation bar (`panels/image/panel.py`, `_build_navigation_bar` and the refresh/handler
code that fed them). Each slider row is now title + slider; the ROI and Mask rows lost their
matching right-hand spacers (`mask_rows.py`), so every strip still ends where its slider ends.

- **What is gone:** typing an exact cube / wavelength (and the autocomplete), and Up/Down/wheel
  stepping while a field has focus. Sliders, the wheel on a slider, Play and the frame tools
  are unchanged. The current cube, wavelength and time stay visible in the Image dock's title bar.
- **Effect on width:** the sliders are ~22 px longer. The Image minimum stays 763 px: it is set by the
  tool ribbon (757 px) and, below that, the slider ribbon's icon groups (492 px), not by the slider rows.
- `panels/image/cube_spin_box.py` and `guided_value_spinbox.py` have no users left (kept, awaiting
  the maintainer's OK to delete).
- Tests: spin-field tests in `test_lspri_rewrite_image_panel.py` now drive the sliders or the selection
  module; the number-field width / font-change tests and the completer test were deleted with the
  feature; `test_the_sliders_have_no_number_fields_next_to_them` pins the new shape.

## Ribbon dividers and the top bar's bottom line (same day, maintainer request)

- **Slider ribbon (below the image):** the | between groups was centred on the whole group (caption above the
  icons included), ~5 px above the icons' centre. `FrameToolsWidget.add_separator()` now aligns it to the bottom of
  the row (where the icon row is) and makes it as tall as the icon buttons (22 px, was 16).
- **Tool ribbon (top, the tabs):** its dividers (`vertical_separator`) ran the full row height, caption included,
  with the line's centre ~11 px below the icons' (measured: icons y 21-45, line 21-67). New
  `ribbon_group.RibbonSeparator` / `ribbon_separator()`: the widget still fills the row, but the line is drawn only
  `BUTTON_SIZE` px tall from `top_offset` (0 = the row's top, where the groups' icons are; the General divider uses
  `_TAB_HEIGHT + 1`). Used by the Mask, View, ROIs, Background, Image tools and Chromatic tabs and the General
  divider. `vertical_separator` stays for the Group-labels popover.
- **Chromatic tab:** its three groups were `AlignVCenter` (icons 4 px lower than the other tabs); now `AlignTop`,
  like View / Mask / ROIs / Background, so the divider lines up with its icons without a magic offset. (Image tools
  keeps its own 4 px row margin, so its icons are still 4 px lower than the other tabs.)
- **Bottom line under the top bar removed** (`panel.py` `_refresh_top_bar_theme`): no `border-bottom`.
- Tests: `test_the_tool_ribbon_dividers_span_the_icons_not_the_captions`,
  `test_the_dividers_line_up_with_the_icons_not_with_the_captions` (slider ribbon); both fail on the old look.
  `test_lspri_rewrite_visual_settings_restore.py` `_flushed()` now waits for the write instead of a fixed 600 ms
  against a 400 ms save pause (it failed once with 16 test files running at once).
