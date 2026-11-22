# Ribbon of the LbL and S_bulk plot windows (2026-11-22)

Maintainer's request: give the two floating plots (LbL series, S_bulk) a ribbon like the Sensorgram's View tab: the whole Points & lines menu (point size moves into it), the
Legend menu, a visible / hidden switch for the SD values and their style (bars at the points or a band), and pictures instead of the Lines and Shift drop-downs.

**Correction the same day.** The first build shared the *values* between the Sensorgram and the two windows. The maintainer meant the opposite: **the code and the look of the menus are
shared, the values are separate** ("different settings can be set in different plots; when I add a function to the view menu, e.g. another mark shape, it is added in every plot menu").
That is what stands now.

## The rule

One definition of the menus, `PlotLookControls` (`panels/sensorgram/plot_look.py`). Every plot makes **its own instance** with its own remembered keys and its own defaults.
**To add an option** (e.g. a marker shape): one field in `view_settings.SensorgramViewSettings`, one control and one row in `PlotLookControls`, one line in its `settings()`, `restore_ui_state`
and reset; every plot's menu then has it. Each plot must then *draw* it (the Sensorgram in `plot.py`, the windows in `bulk_plot.py` / `lbl_plot.py`; the shared drawing pieces are in
`plot_window_draw.py`). `tests/integration/test_lspri_plot_window_look.py` fails if a window's menu has a row the Sensorgram's has not, or if a window lacks a row for a reason other
than the ones listed below.

## What was built

| Piece | File | What it is |
|---|---|---|
| The menus | `panels/sensorgram/plot_look.py` | `PlotLookControls` (Points & lines, Legend, with Reset) and `UncertaintyControls` (show, SEM / SD, Bars / Band). Options that only make sense in one place are switches of the constructor: `dot_limit` (Dot limit + the Points toggle that shows itself off), `legend_position` ("at the edge", it places the legend against the plan bar), `joined` (Straight / Spline). `PlotLookKeys.with_prefix(...)`: where a plot remembers its values. |
| Sensorgram | `panels/sensorgram/view_controls.py` | now a thin owner of the band and anchors toggles plus one `PlotLookControls(dot_limit=True, legend_position=True)`; same attribute names as before; same saved keys (`sensorgram/view_*`). |
| The windows | `lbl_plot.py`, `bulk_plot.py` | each has `self.look` and `self.uncertainty`, keys `sensorgram/{lbl,bulk}_plot_look_*`, starting look 9 px points / 1.5 px lines (`WINDOW_LOOK_DEFAULTS`: the windows' own, the Sensorgram's 4 px / 1 px suit hundreds of lines). |
| Pictures | `window_look_icons.py` | error-bar toggle, Bars / Band, Accumulated (dashed baseline, strokes up from it), Per cycle (staircase, stroke between neighbours). The Lines buttons reuse the Sensorgram's `display_picture("individual")` / `("average_by_group")`. |
| Drawing helpers | `plot_window_draw.py` | the Sensorgram's own `_Legend` (text size, row gap, Hide), a legend entry whose sample is not in the plot (a hidden line keeps its colour in the legend), the shaded band. |
| Maths | `analysis/lbl_series.py` | `lbl_points(..., spread="sd")`: the bars of a shift combine the steps' SDs in quadrature instead of their SEMs. `LblPoint.sd` added (default NaN). |

## Decisions (flag if not meant)

1. **Left out of the windows' menus**: the Dot limit (a redraw-speed guard for hundreds of lines; the windows draw at most 12), "Legend at the edge" (no plan bar), and the Joined row in the
   S_bulk window (its line is the straight fit; the value is kept).
2. **The Sensorgram's SD / SEM band** (spread between ROIs) is its own toggle on the Show group; the windows' Uncertainty is the spread of the cubes inside a step. Different numbers, so no link.
3. **S_bulk**: the Lines toggle shows / hides the fitted line; the fit line's own uncertainty band follows the Uncertainty toggle; the horizontal bars (the RI's Std) are part of Uncertainty.
   **The fit is always weighted with the SEM**: SD only changes what is drawn.
4. Point size range is 1-14 px in every plot (the windows had 3-30).
5. LbL SD bars: ``sqrt(SD_a^2 + SD_b^2)`` of the two steps, the same rule as the SEM's.
6. The windows' old `*_plot_point_size` keys are no longer read (the size comes back to the 9 px default once).

## Tests

`tests/integration/test_lspri_plot_window_look.py`: one definition (the rows), separate values and keys, what each option draws in each window, no number changes with the look, the remembered
state, the SD arithmetic, the pictures. `test_lspri_sensorgram_view_tab.py` / `_view_kept.py` (the Sensorgram's side, unchanged) pin that the refactor kept the View tab as it was.

Unseen by eye: the pictures (20 px), the ribbon's room (the LbL window is 640 px wide), and how a hidden-line legend sample looks.

## Follow-up the same day

- **Lines (both windows) and Shift (LbL) are pick-up menus** (`PickMenu`, the kind of the Sensorgram's Plot tab): the picked choice's picture is the button (no frame, no word on the face), the menu lists
  picture + name. The "Lines" / "Shift" captions are gone. `plot_look.lines_pick_menu` builds the Lines one for both windows; `restore_pick` remembers a pick without reporting it.
- **"Calculate groups" is in the plot area** (`plot_window_draw.PlotAreaButton`, a child of the plot widget): in the middle when nothing is drawn, top right when some lines are drawn and others
  are out of date (so it never covers data). It left the row.
- **The Band picture is a wide band** (about a third of the picture, outlined, over flatter dots) so it differs clearly from Bars; a test pins its coverage. The Uncertainty menu's own face is still the Bars picture.
