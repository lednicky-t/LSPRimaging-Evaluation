# Panels narrower than their control rows: scrolling ribbons (analysis, 2026-10-24)

Maintainer request: panels cannot be made smaller than the controls inside them (Image ribbons, the
control row of Spectra / Sensorgram). Let them shrink past that limit; maybe give Spectra / Sensorgram the
same ribbon style as the Image panel. **Built the same day (steps 1-3 below); the outcome is at the end.**
Builds on option A of `panel_layout_workflow_resizable_2026-10-18.md` ("Horizontal scroll area around the ribbon").

## Why panels have a minimum width today (code facts)

Qt makes a widget's minimum width the sum of its children's minimums in a row layout, and a dock cannot be
narrower than its widget. So each control row is the floor:

| Panel | Row that sets the floor | Where |
|---|---|---|
| Image | `ImageToolRibbon` (tabs + `QStackedWidget`, whose minimum is the **widest page**, shown or not) inside `RibbonDrawer` inside `_top_bar` | `panels/image/panel.py` `_build_top_bar`, `tool_ribbon.py` |
| Image | `SliderRibbon` (frame tools, Slideshow, ROIs, Mask groups) inside `SliderRibbonDrawer` | `panels/image/slider_ribbon.py`, `_build_navigation_bar` |
| Spectra | `controls` QHBoxLayout: 3 combos, spin box, 2 check boxes + stretch | `panels/spectra/panel.py:127` |
| Sensorgram | `controls` QHBoxLayout: 4 combos, spin box, 2 check boxes + stretch | `panels/sensorgram/panel.py:150` |
| ROI / Groups | `toolbar` QHBoxLayout of icon buttons + unit toggle | `panels/roi_table/panel.py:243` |
| Workflow | already has a horizontal scroll bar "as needed" (2026-10-18) | `panels/workflow/panel.py` |
| Histogram | no control strip (plot only) | - |

Measured earlier (2026-10-18, before smaller icons): Image 763 px (top ribbon 757, slider ribbon 492 behind it),
ROI 188, Histogram 133. Spectra / Sensorgram floors not re-measured today.

`RibbonDrawer` (`panels/image/ribbon_drawer.py`) already **rolls a ribbon up** (double-click on empty space ->
thin strip with a "..." handle). That solves *height*, not *width*. Narrow width needs scrolling.

## Proposed design

One shared, small widget, e.g. `panels/scrolling_ribbon.py` -> `ScrollingRibbon(content)`:

- a frameless `QScrollArea`, horizontal bar "as needed", vertical bar off, `widgetResizable` so the content
  still stretches when there is room;
- a `QScrollArea` reports a *small* minimum width, so the row stops dictating the dock's minimum;
- its height is fixed to the content's height (a scroll area does not report the content height by itself),
  plus the bar's thickness only while the bar is shown; thin themed bar (colours from `lspr_ui` theme tokens);
- optional: pages of the tab stack that are not shown get an `Ignored` size policy, so each tab scrolls by its
  own width, not by the widest tab's (Mask 637 px would otherwise set the range for the short tabs too).

Use it as the `ribbon` that `RibbonDrawer` already wraps (the drawer only needs `ribbon.sizeHint().height()`
and `maximumHeight`), so roll-up keeps working unchanged.

### Image panel
- Wrap the whole `ImageToolRibbon` (tabs and content scroll together) and the `SliderRibbon`.
  Estimated new Image minimum: set by the dock title bar and the slider rows, perhaps 250-350 px (unmeasured).
  Wrapping only the content row would keep the tab strip visible but leave a floor of about 7 tabs (~400 px, estimate).
- Pins to update: `test_four_columns_fit_the_default_window` and the width-budget tests assert today's floors.

### Spectra / Sensorgram
- Cheap version: put the existing row in the same `ScrollingRibbon`.
- Unified version (recommended): rebuild the row as captioned groups using `ribbon_group.labeled_icon_group` /
  `image/frame_tools.captioned_group` and the frameless 24 px fields of `panels/ribbon_fields.py` (Spectra:
  Formula | Fit (+order) | Metric | Window / Error bars. Sensorgram: Display | Average (centre, band) |
  Smoothing (+window) | Spikes / Baseline), then `RibbonDrawer` + `ScrollingRibbon`. Row grows from ~24 to ~38 px;
  rolled up, the plot gets the height back.
- The roll-up state is a UI choice: remember it (CLAUDE.md "Remember UI state": AppSettings field + `initial_*` + signal),
  same as the Image drawers do.

### ROI / Groups toolbar
- Same wrapper, low value; optional.

## Risks (all GUI wiring; no science code, no threads, no data formats)

1. **Double-click roll-up** is an event filter on `_top_bar` that relies on unaccepted clicks reaching it; through
   a scroll area's viewport this must be re-checked.
2. **Wheel over the ribbon**: forwarding the vertical wheel to the horizontal bar would also fire over combos and
   spin boxes (they change value on wheel). Default: bar + drag only, no wheel forwarding.
3. **Popups anchored to ribbon buttons** (label placement, Auto crop menu, slideshow settings) use global
   coordinates and should stay correct when scrolled; a button scrolled out of view is simply not reachable until scrolled.
4. **Height jump** when the bar appears (a few px). Alternative: always reserve the bar's height.
5. Other things may then become the floor (dock title-bar subtitle columns, ROI / Mask rows) - measure after step 1.

## Steps

1. `ScrollingRibbon` + test (minimum width small; last control reachable by scrolling; height stable).
2. Image top ribbon and slider ribbon; measure the new Image floor; update width-budget tests.
3. Spectra and Sensorgram (unified ribbon style + drawer + remembered roll-up).
4. Optional: ROI toolbar.

Engineering priority served: 6 (GUI polish / convenience); no priority 1-5 conflict.

## Outcome (built 2026-10-24, maintainer decisions: whole ribbon scrolls, arrows like a browser's tab strip, unify Spectra / Sensorgram)

What exists now:

- `panels/scrolling_ribbon.py` - `ScrollingRibbon(content)`: a frameless scroll area with **no scroll bar** and a chevron button at each
  edge while the row overflows (click = one step, hold = repeat, dimmed at its end, mouse wheel over empty ribbon space scrolls; a drop-down or
  number field keeps its own wheel). Height = the row's, fixed vertical policy, so the ribbon never grows taller (the "height jump" risk
  disappeared with the bar). The decision "show arrows" is made on the whole width, not the viewport (the arrows shrink the viewport), so it
  cannot flip back and forth.
- `panels/ribbon_drawer.py` (moved from `panels/image/`): `RibbonDrawer` now wraps the ribbon in a `ScrollingRibbon`. `drawer.ribbon` is the
  wrapper (shown / hidden / animated), `drawer.content` the ribbon passed in, `drawer.ensure_visible(widget)`, `scrolling=False` for the old behaviour.
  Both Image ribbons (tool ribbon with its tabs, slider ribbon) and the new Spectra / Sensorgram rows go through it.
- `image/tool_ribbon.py`: only the **shown** tab sets the ribbon's width (pages that are not shown get an Ignored horizontal policy), so
  a short tab does not scroll as far as Mask; `tab_button(label)`; the panel scrolls a newly chosen tab into view.
- `panels/ribbon_row.py` - `RibbonRow` (captioned groups + dividers, double-click rolls up) and `rolled_ribbon(...)`. Spectra: Formula | Fit
  (+ order) | Metric | Fit window (Set) | Error bars (Show). Sensorgram: Display | Average (centre, band) | Smoothing (+ window) | Spikes
  (Remove) | Baseline (Subtract). Check boxes got shorter texts because the caption now carries the name. `ribbon_fields.style_ribbon_check`.
  Roll-up state is remembered (`spectra/ribbon_open`, `sensorgram/ribbon_open` in `storage/ui_state_keys.py`); `SensorgramPanel.restore_ui_state` is new.

Measured (offscreen, 1400 x 900 window, `minimumSizeHint`): dock minimum width before -> after:

| Dock | Before | After | What now sets it |
|---|---|---|---|
| Image | 763 (ribbon) | **687** | the dock's **title bar**: four fixed-width status fields (frame role 198, cube 81, wavelength 55, time 146 = 480 px) + the title and buttons. The Image widget itself is 130 px. |
| Spectra | 850 | **163** | title bar |
| Sensorgram | 967 | **202** | title bar |
| ROI / Groups, Histogram, Workflow | 228 / 189 / 240 | unchanged | title bar / Workflow floor |
| Log | 809 | unchanged (not asked) | its own control row |

So for the Image panel the ribbons are no longer the limit; the title bar's fixed status columns ("fields will have fixed position",
2026-10-16) are. The slider rows are not a limit either (Cube slider 15 px, ROI / Mask rows 105 px): the axis-zoom / squeezed-wavelength
idea for the sliders is not needed for the floor today.

Tests: `tests/integration/test_lspri_scrolling_ribbon.py` (new, 14), plus ribbon tests in the Spectra, Sensorgram and Image panel suites;
`test_lspri_rewrite_slider_ribbon.py` and `test_lspri_rewrite_image_panel.py` now address `drawer.content` / `drawer.ribbon`.
Not checked by eye (no GUI driving): the look of the arrows, the Spectra / Sensorgram ribbon rows, the wheel on a real mouse.
Pre-existing failure, not from this work (same result on the committed source):
`test_lspri_rewrite_visual_settings_restore.py::test_an_unknown_saved_ribbon_tab_leaves_the_first_tab_open` expects "View"; the first tab is "Image tools".

## Follow-up: the title bar (same day, maintainer answers)

- **Status fields next to the title.** The Image dock's fields are now cube, wavelength, time, role word, comment (the role word was first
  and is empty on most frames, which left a blank ~200 px right after the title). Gap between the title and the first field 16 -> 10 px
  (`_SUBTITLE_TITLE_GAP_PX`); `panels/image/panel.py` `_frame_fields`, tests re-indexed.
- **Fields no longer hold the dock wide.** `dock_container._ColumnsHost` has minimum width 0; `_fit_subtitle_columns` shows the fields that
  fit from the left, whole, and hides the rest from the right (the comment needs 40 px). Image dock minimum **687 -> 137 px**.
  Test: `test_a_narrow_dock_drops_the_trailing_fields_whole_instead_of_staying_wide`.
- **Log panel** left as is (not meant to be docked in normal running). The stale test now expects "Image tools".
