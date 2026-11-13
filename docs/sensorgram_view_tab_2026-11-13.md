# Sensorgram "View" tab (2026-11-13)

The maintainer asked for a **View** tab in the Sensorgram's ribbon, like the Image panel's, that holds every option of how the plot is drawn, and then, round by round on the same day,
for what is in it: toggles for the shaded SD / SEM band and the anchors; a compact menu for the size of the points, the width of the lines, how they are joined and "reset to default";
control of the legend (visible or not even with many lines, text size, vertical spacing that also moves the step statistics, whether it shows the number of ROIs, where it sits);
the plan bar's and the step table's controls as menus of the same kind; the markers B1 / S1 / Sb (shown, where, in what colour); the room left for the legend before the first step;
and a link between the legend's and the stats' text size. The rounds are told in order below; **"The tab" and "The controls" describe the end state**.

All of it changes how an already-computed result is drawn. **No number changes** (no scan, no fit, no statistic); the options are remembered like every control.
Unseen by eye when written.

## The tab

`panels/sensorgram/panel.py`: tabs are now **Plot | View | Experimental plan** (`VIEW_TAB`). Remembered under `sensorgram/ribbon_tab` (an unknown remembered tab is still ignored).

| Tab | Groups |
|---|---|
| Plot | Live, Statistic, Smoothing, Baseline, Export (its old "View" group moved) |
| View | **Show** (band, anchors, mask marks, out-of-date marks), **Points & lines**, **Legend**, **Steps**, **Evaluation**, **Stats** |
| Experimental plan | Edit, Evaluation, Checks (its two View groups moved) |

- **Steps** and **Stats** are the plan bar's and the step table's own controls, **moved** from the Experimental plan tab (the same widgets, `plan_group.py`, `stats_group.py`).
  A consequence to know: tuning the plan bar's look while editing the plan now means switching tabs. Moving them back is two `add_group` lines in `panel.py`.
- **One kind of menu (second round).** Every group with options (Points & lines, Legend, Steps, Evaluation, Stats) is a `GroupMenuButton` (`group_menu.py`): one icon in the ribbon, a click opens a captioned
  panel, a click outside closes it. Before, Points & lines and Legend had a private copy of it, the plan bar spread its controls over four ribbon fields plus a "Step labels"
  pop-up (`plan_labels_menu.py`, deleted: nothing used it any more), and the step table had three fields. The group names lost their "View": **"View steps" -> "Steps", "View stats" -> "Stats"**.
  - **Steps panel:** Show, Height, Edge, Transparency, then a heading "Step labels" with Text, Refractive index, Aligned to, Position, Size: the old pop-up's rows, flattened into
    the same panel (a pop-up inside a pop-up would have been the alternative; the pick-up buttons inside it are as they always were). The Edge row hides together with its
    caption while the height is Full (`GroupMenuButton.set_row_visible`).
  - **Stats panel:** Show, Edge, Text size, **Legend room** (third round, below).
  - Both are greyed out together with their controls when the dataset has no steps, the tool tip says why (as the toggle's did).
  - The menu button's picture is the toggle's own pictogram (`plan_bar_icon`, `step_stats_icon`) in the text colour; it does not show whether the bar or the table is on.
- The plan's structure tools (`_on_ribbon_tab`) are unchanged: live only on the Experimental plan tab.
- The SD / SEM **choice** (which band) stays on the Plot tab, in Statistic: it is part of how the line is made. Only *showing* it is on the View tab.

## The controls (`view_controls.py`, `SensorgramViewControls`)

- **Band toggle** (`band_icon`, a trace in a translucent band, crossed out when off): hides the band of an average. Greyed out unless the plot shows an average (there is no
  band on a single ROI); the tool tip says why. Keeps its remembered position while greyed.
- **Anchor toggle** (Tabler `anchor` / `anchor-off`): hides the anchors **and the baseline made from them** (maintainer, same day: "include also the baseline
  visibility"): the hollow squares of the Anchor points method, and the dashed baseline curve through the anchors. The curve exists while Subtract is off (with Subtract on
  the traces are already corrected and it is not drawn either way) and for the Range method's marked anchor steps too, which have a curve but no squares. So the toggle is
  greyed out only when neither exists (Range with nothing marked). Hiding changes no point, no number and no correction: the curve is still computed and still
  subtracted, only not drawn. The setting is `show_anchors`; its name kept from the first version, its meaning is both.
  The Range method's draggable baseline *selector band* is not part of it: that one has its own pencil (Baseline group, Plot tab).
- **Points & lines** pop-up (the menu's face is the straight picture of `line_icons.py`, see "Pictures"), rows: **Points** (show / hide toggle + size 1-14 px), **Lines** (toggle + width
  0.5-4 px in steps of 0.5), **Joined** (Straight / Spline, two picture buttons), **Reset to default** (all on, 4 px, 1 px, Straight). The two toggles are the same picture split in two
  (`points_toggle_icon`, `lines_toggle_icon`). The first build had "size 0 = none"; the Points toggle replaced that (a remembered 0 is ignored with a log warning and the default is used: the
  value was a day old and unseen).
  - **Hiding the lines** keeps their items, invisible: the dots are drawn out of the auto-range, so without the lines the view would have no extent
    (`_INVISIBLE_PEN`, a transparent pen is not left out of the range as no pen is). The legend's colour marks are drawn from a pen-bearing curve that is not in the plot, so they
    stay. The lines are left out of a layered SVG export while hidden. With both hidden only the legend is left.
  - **Spline** (`curve_smoothing.py`): a **PCHIP** curve through every point (monotone cubic Hermite: it never leaves the range of its two neighbours). A natural cubic spline was
    not used on purpose: on 400 points with 0.3 nm noise it left the neighbours' range in 18 % of the drawn samples by up to 0.15 nm, i.e. it draws peaks that are not in the
    data; PCHIP in none. 6 points are drawn per interval, so every measured point is on the curve. A gap in the data stays a gap (each stretch of finite points on its own); an
    x that does not increase draws that stretch straight. The band's two edges are smoothed on the same x. **Not smoothed:** the dots, the mask and out-of-date marks, the dashed
    baseline curve, the step statistics: they stay on the measured points / as computed. Display only: no number, export or fit reads the curve.
  - **Cost of Spline** (measured, scratch script, 400 cubes per line, `show_series` + a full paint): 160 lines 66 -> 204 ms, 40 lines 31 -> 51 ms, 5 lines unchanged; the
    interpolation itself is 45 ms of that at 160 lines. Said in the tool tip. Cheaper ways exist (one PCHIP call for all lines that share an x: 8.6 ms instead of 45 ms; fewer
    drawn points per interval) but were not needed for an opt-in option.
- **Legend** pop-up (Tabler `list-details`), rows: **Legend** (Auto / Show / Hide), **Text** (6-24 pt, with the chain button of the text-size link), **Row gap** (-6 to +24 px), **ROI count**
  (the `(n=12)`), **Position** ("at the edge of the plot"), and **Reset to default**.
- A reset changes only its own menu, and reports one change, not one per box.

## Third round, same day: the legend room in the Stats menu, and the Evaluation menu

### Legend room (Stats menu)

When the step table is on, the plot widens the x axis **before its start** (to negative time or cube numbers) so that the legend the table's rows line up with does not sit on
the first step (`SensorgramPlot._room`, `_apply_room`). That room is a row of the Stats menu now: **"Legend room"**, in percent of what holds the legend and its margins
(legend width + 16 px), in steps of 25 %, **0 = "off"** (no room is made, the axis starts at the data). 100 % is what the plot always did and the default
(`StatsTableSettings.legend_room_pct`). Less than 100 % lets the first step's numbers start closer to the legend (a cell never draws under it, it shrinks instead). Like the
table itself it is not applied while the user's own zoom is kept: the reset-view "A" button makes the room (said in the tool tip). A hidden legend already takes no room.
Percent rather than pixels so that it still fits when the legend's text size or names change.

### Evaluation menu (the markers B1 / S1 / ... / Sb on the plan bar)

A new group **Evaluation** on the View tab (between Steps and Stats; an icon of a step's bar with a dim and a solid chip): `PlanBarControls.evaluation_menu`, rows **Markers**
(show / hide) and **Place**. The options are part of `PlanBarSettings` (`show_roles`, `roles_edge`, `roles_side`), remembered (`sensorgram/plan_roles_visible` and
`sensorgram/plan_roles_place`) and greyed out without steps like the Steps menu. Hiding the markers changes nothing of the evaluation.

- **Place = a dice** (`place_picker.PlacePicker`, `plan_icons.pip_icon`): five pips in a 3 x 3 grid like the 5 of a die, the four corners and the middle, exactly one solid, the
  others small rings. **Compact** (maintainer: "it takes too much space, the points are far apart"): 16 px cells with no gap between them, 48 px for the whole die, pips drawn 14 px;
  the first build used ribbon-sized buttons (24 px) with 2 px gaps, 76 px. The pips are a small `IconToggle` subclass that puts its size back after every `refresh_theme` (which gives a
  plain `IconToggle` the ribbon's 24 px again); pinned by a test, also after a theme change. (The first build of the day had two rows, Edge: Auto / Top / Bottom and Side: Left / Right; the maintainer asked for this simpler picture.)

  | Pip | Edge | Side |
  |---|---|---|
  | top left / top right | top | left / right |
  | bottom left / bottom right | bottom | left / right |
  | **middle** | **auto** | left |

  The middle is "auto" and keeps the left end (`bar_model.ROLES_PLACES`, `roles_place_of`; a test pins that `PlanBarSettings()` is the middle). It was "exactly the old default" until the chips
  moved into the bar (item 9 under "Decisions").
  **What the dice cannot say:** auto at the *right* end (it was possible with the two rows, nobody asked for it; a saved "plan_roles_edge" / "plan_roles_side" of the first build
  is ignored, a day old and unseen).
- **Placement** (`PlanBarItem.role_chips` lays the chips out, `_paint_role_chips` paints them): a row of chips at the left or right end of each step's bar segment, in the order
  of its roles read left to right on either side. *Edge* (**changed the same night, item 9 under "Decisions"**: this paragraph is the first build, where the chips hung below / above a bar
  that was not full): the chips are inside the step's bar; **auto** (the default) is the middle of the bar's height (the top of a full bar), **top** / **bottom** the top / bottom of the bar.
  The layout is pinned by a test for every combination of bar position, edge and side.
- **Colours** (`plan/role_colors.py`, pure): **a pair is one colour**. The signal chip (`S1`) is the pair's colour from a palette of six (theme accents: sky blue, gold,
  green, red, purple, teal; a seventh pair starts again, the Evaluation pop-up allows eight); the baseline chip (`B1`) is the same colour x 0.55 (darker) and a little more
  transparent (0.88 against 0.95 opacity). Before: the baseline chip neutral grey, the signal chip the accent blue, for every pair.
- **`Sb`** (a step of the S_bulk set) is a **grey from dark to white**, by the step's **level among the S_bulk steps**: the lowest dark (`#141414`, not pure black, so that it keeps
  a shape on a dark plot), the highest white, the others in proportion to the level (linear in the level, not in the rank), a step with no data or an S_bulk set with no spread the
  middle grey. A one-pixel outline in the text colour is drawn round these chips only: a white or a black chip needs an edge on a plot of either colour.
  - *What "shift" is here:* the median of the lines inside the step as drawn (`SectionLevels.median`, the Data text mode's level): the shift when Subtract is on, the position in nm
    when it is off; the order is the same. It follows the lines on screen (another selection of ROIs can change a grey), which is what "based on the shift" asks for; the order of the
    steps by refractive index would be the alternative and does not depend on the selection (not built).
  - The greys are made once per plan (`PlanBarItem.set_plan`) from **all** steps of the S_bulk set, never from the steps in view, so panning and zooming never recolour a chip
    (pinned by a test).
- **The Evaluation pop-up on the Experimental plan tab** (where the pairs are set) now names its rows in the same colours (`evaluation_group._style_labels`: the signal's the pair's
  colour, the baseline's the same hue x 0.75, lighter than its chip because it is text), so a row and its chips are seen to belong together. It said "as its chip on the plan
  bar" before. Not asked for, kept consistent.

## Fourth round, same day: pictures for the joints, a text-size link, the legend at the edge

### Pictures (`line_icons.py`)

- The **Points & lines** menu's face is a painted picture (it was Tabler's generic `chart-dots` first; that icon was vendored and deleted again the same day): the straight picture below (`points_lines_icon`).
- The **Points** and **Lines** show / hide toggles (`points_toggle_icon`, `lines_toggle_icon`; Spectra's pictograms until the last round) are the joint picture **split in two**: the Points
  toggle is the three dots alone, the Lines toggle the line alone. On: the dots in the text colour, the line in the gold accent, as in the pictures above; off: both in the dim colour and
  **crossed out** with the same stroke as the other toggles of the View tab (band, anchors, marks). The dots are a little larger than in the choice pictures (2.0 against 1.6, they stand
  alone). Pinned by tests: each toggle has only its own colour when on, none of it when off, and the stroke runs through the middle of the icon only when off.
- **Joined: Straight / Spline** are two picture buttons (`line_icons.joint_picture`, shown through `group_menu.icon_choice_buttons`, `PictureChoice` buttons of which one is pressed). They are
  drawn **like the baseline shape pictures** (constant, linear, ...; `baseline_controls.shape_picture`): **three dots in the text colour and the line through them in the gold accent**, in
  20 x 20 shown pixels. The same three dots both times: straight with a sharp point at the middle dot, spline as a round hill. The curve is the plot's own PCHIP
  (`curve_smoothing.smooth_run`, so the picture promises nothing the plot does not do: it turns round exactly at the dot and never overshoots). The words are the tool tips. The pressed
  button has the same frame as a pressed text segment (`PictureChoice`, `segment_stylesheet(padding=...)`), so the picture keeps its own two colours in both states (an `IconToggle`
  shows its state by nothing but its icon colour).
  - **History (maintainer, three rounds).** (1) Four dots on a gentle zigzag, one colour, Catmull-Rom curve: "very similar to the straight line" (32 % of the ink that either picture has
    differed between them at 18 px; measured as pixels whose opacity differs by more than 40/255). (2) Five zigzag dots, one colour, 59 %: "terrible". (3) This: three dots of another
    colour than the line, in the baseline pictures' style: 63 % differ. A test keeps it above half. Not looked at by eye.

### Linking the legend's and the stats' text size (`text_link.py`)

A **chain** button beside the text-size box of both menus (Legend "Text", Stats "Text size"; Tabler `link` / `link-off`, already vendored) makes the two sizes one: while it is on, changing
either box changes the other, so the stats numbers and the legend they line up with stay the same size. Off at the start (nothing changes until it is used) and remembered
(`sensorgram/text_size_linked`, bound to the Legend menu's button).

- **Who wins when it is turned on:** the menu you pressed it in (its size is copied to the other). *Not* a fixed side, because the box you are looking at is the one you mean.
- **One state, two buttons:** pressing either presses both (`TextSizeLink`, made by the panel because it owns both menus).
- **Ranges:** while linked both boxes take the narrower of the two ranges, so "the same size" is always possible (today both are 6 - 24 pt, so nothing is cut); each has its own range again
  when unlinked.
- **Restoring:** the two sizes come back one after the other (the Legend menu's first). A first build let the second one's value flow through the already-active link and overwrite
  the first (found by a test). Now `begin_restore()` / `end_restore()` around the two restores copy nothing until both are back, then, if linked, make them equal, **the Legend
  menu's winning**: the result does not depend on the order, and a saved pair that disagrees cannot show a linked pair that differs.
- The Legend menu's **Reset to default** also turns the link off (first, so that putting the legend's size back does not drag the stats size along); the stats size itself is not part of
  that reset and keeps what it had.

### The legend at the edge ("Position" row of the Legend menu)

With the step table on, the plot puts the legend (and with it the table's rows) **just inside the plan bar**: its y offset is the bar's height plus a margin. A new checkbox,
**Position: "at the edge of the plot"** (`SensorgramViewSettings.legend_over_bar`, remembered as `sensorgram/view_legend_over_bar`), makes it **ignore the bar and what hangs from it**
(the evaluation markers): the offset is the margin only, so the legend goes to the very edge, over the bar.

- **The table's rows go with it.** They line up with the legend's rows by design (`StepStatsItem._row_centers`), so they move up by the same amount and lie over the bar. The legend
  alone moving, the rows staying under the bar, would have broken that alignment; say so if that is what was meant.
- **The room is shared, not added** (`SensorgramPlot._room`): the plot reserves free space on that edge for the bar and for the table's rows so that they do not cover the traces. At the
  edge they overlap, so the room for the two is the larger of them instead of their sum, and the traces get back what the overlap saves. The Data text mode's label headroom is still added.
  Without the option the sum is computed in the same order as before.
- **Where it changes nothing** (pinned by a test): the bar on the other edge from the table, a full bar (behind the traces, takes no edge), and the table off (the legend is where it
  always was, top left, wherever the user dragged it).
- Under the plan bar's own step text, the stats numbers can now share a place; that is the cost of asking for the edge. Reset in the Legend menu puts it back.

## Decisions made for the maintainer (change them if they are not what was meant)

1. **Legend mode is three-way, not on/off.** *Auto* is what the plot always did (names up to 24 lines, and with more than 6 it stays only while it takes at most half the
   plot height). *Show* overrides both guards ("even though there might be too much plot lines"). *Hide* never draws it. The default is Auto, so nothing changes until the
   tab is used. A long Show legend can cover the plot, and Show with ~160 ROIs builds 160 legend rows (slower); that is the user's choice, said in the tool tip.
2. **Hide keeps the legend's entries, only not drawn.** The step table lines its rows up with the legend's rows; keeping them keeps those rows where they are. A hidden
   legend takes no room on the left and no longer keeps the numbers clear of it (`SensorgramPlot._room`, `_legend_rows`; pinned by a test).
3. **Row gap = pyqtgraph's `verSpacing` on top of today's look** (0 = as before, negative = closer; measured: 22 px per row at 9 pt, 16 px at -6, 32 px at +10, 30 px at
   14 pt text). The step table's rows follow the legend's, so "the vertical spacing also influences the stats spacing" holds by construction (pinned: gap +8 moves
   the table's rows by +8 px). The table's own text size is still the "Text size" row of the Stats menu.
4. **"Number of ROIs" means the count in a group or average name**: `Group A (n=12)` / `Average of 12 ROIs` against `Group A` / `Average`. A single ROI's line
   (`ROI 5`) has no count and is never changed. Implemented as `Series.short_label` (set in `scan_trace._combine`); the label itself, the SVG layer names, the tooltips
   and the step table keep the full text.
5. **The dots keep their guard, but it is now visible and a setting** (changed later the same day, after the maintainer found "the Points toggle is not working": with more than 10
   lines the toggle stayed on and drew nothing). Dots are drawn only for up to the **Dot limit** lines (`SensorgramViewSettings.dots_max_lines`, default
   `DEFAULT_DOTS_MAX_LINES` = 10, range 1-10000; 64,000 dots cost ~1 s per redraw, see `docs/spectra_sensorgram_draw_cost_2026-11-02.md`; the box is in the Points & lines menu, remembered
   as `sensorgram/view_dots_limit`, put back by its reset). While more lines are drawn than the limit, the **Points toggle shows itself off** (`points_toggle.py`: the off picture and a
   tool tip saying why) **without changing the remembered choice** (it is still checked, `settings().show_points` is still the choice, the store is not written), so the dots come
   back by themselves when fewer lines are drawn. A click on it while capped does not toggle: it asks (`ask_to_raise_dots_limit`: "Raise the limit to N" / "Keep the limit", Enter and
   Escape keep) and, on agreement, turns the choice on and raises the limit to the number of lines in one reported change. The panel tells the controls the line count after every
   draw (`SensorgramViewControls.set_line_count`, from `_draw_last`); the plot applies the limit itself. The hook for the picture is `IconToggle._shown_on` (Spectra's toggles: unchanged).
6. **Line width is the one option with a cost.** 1 px is Qt's fast path; the plot comment of 2026-11-02 measured 140 ms against 30 ms per redraw for 160 lines x 400
   cubes at 1.5 px. The default stays 1 px and the tool tip says so. Per the rule "a control means the same for every selection", the width is never reduced
   automatically for many lines.
7. **The legend's inner margin is 3 px, not pyqtgraph's 9** (`plot._LEGEND_MARGIN_PX`; the maintainer asked "why is there 25 px if the padding is only 6 px?"). The first row's text sat 6 (the
   edge offset) + 9 (pyqtgraph's inner margin) + half a row = 25 px below the top; now 19 px with the stats table on. With the table off the legend stays where it always was (its offset
   grew by the 6 px the margin gave up, `_LEGEND_OFFSET_PX`). The stats rows follow the legend's rows and the room kept for them shrank by 12 px, so the traces gain it.
   **Found on the way, and fixed with it** (`plot._Legend`): pyqtgraph sizes the legend as the sum of its rows' heights, ignoring the margins and the Row gap; Qt raises that to the layout's
   minimum when the minimum is larger. With 9 px margins that was nearly always so; with 3 px and a negative Row gap it was not, and the spare height was shared out between the
   rows (a gap of -3 px moved four rows by -2.25 px). The same already happened with the old margin from 6 rows on (a gap of -6 px with 12 rows put the rows 34 px apart instead of
   16). `_Legend.updateSize` now asks for no extra height and lets Qt raise it to the layout's own minimum. Measured at gap 0 (the default) the row pitch is 22 px both before and after.
   Do not size it by the layout's size hint: that hint counts the margins or not depending on the moment (a first try added them twice and stretched the rows to 24 px).
8. **Four reports after seeing it (maintainer, 2026-11-13, evening)** and what was done (the numbers are plot pixels, measured offscreen; nothing seen by eye):
   - **Stats rows overlapped the step names when they were aligned to the top of the bar.** Two causes. (a) With the "at the edge" tick on, the rows went over the bar, on top of names written in
     it: now the tick applies only to a bar that holds no names (aligned to the data), `plot._legend_ignores_bar`; with names in the bar the rows stay under it. (b) A Full bar is the whole plot, so
     its names (any of the five positions) could land on the rows at the plot's edge: the plot hands the band the rows take to the bar (`PlanBarItem.set_keep_out`, from `_place_legend`) and
     `bar_model.text_top_y(keep_top, keep_bottom)` places the names outside it, all five positions staying distinct. The tick is still there; its tool tip says when it has an effect.
   - **Names vanished when the bar was 5 % or lower** (`text_top_y` dropped the text when the strip was lower than a line). Now the strip is never lower than one line of text plus its padding
     (`PlanBarSettings.strip_height_px`; about 21 px at 9 pt, i.e. 5-6 % of a normal plot, so the lowest Height values give the same bar; said in the Height tool tip). Every user of the
     bar's height goes through it: the bar's layout and hit area (`plan_bar.layout`, `_strip_data`), the legend and rows under it (`plot._bar_height_px`) and the room kept for them (`plot._room`).
     Chosen over letting the text hang out of a thin bar: that would have put it on the legend and over the plot background, where its colour (chosen for the fill) can be unreadable.
   - **Text-size link for the step names.** The Steps menu's Size row (it was there, last row) got the chain button; `TextSizeLink` now takes any number of menus, still one state, one rule: the
     chain in the Legend, Steps and Stats menus is one button, pressing it gives all three the size of the menu you pressed it in, the Legend's wins on restore. If the maintainer wants the step
     names linked but the stats not (a chain of its own per menu), that is a change of `TextSizeLink`, not of the menus.
   - **Gap above the legend when everything is hidden.** With the stats table off the legend sat at 8 + 9 = 17 px from the top (table on: 9). Its offset is now the same `_LEGEND_EDGE_PX` as with
     the table on, so hiding the stats does not move it.
9. **The evaluation chips now sit inside the bar (maintainer, 2026-11-13, night): "the evaluation labels are put under the step bars when the bar is not full height, which is not good. Can
   their position work with the height of the step bars?"** Before, with a bar of a few percent the chips hung below it (a bar at the top) or above it (a bar at the bottom), among the traces and
   exactly where the legend and the stats rows sit (the plot never reserved room for them). Now (`plan_bar._chip_top`, `_chip_block`):
   - **Always inside the step's own segment of the bar, whichever edge it is on**, so they follow its height. Dice: *top* = at the top of the bar, *bottom* = at its bottom, *middle (auto)* = in
     the middle of the bar's height (a full-height bar: at its top, as before, because its middle is the data). Left / right = the step's end, as before. A bar barely higher than a chip keeps the
     chip inside it (the 2 px margin shrinks first). The thinnest bar is one line of text + padding (`strip_height_px`), a little higher than a chip at every size 6-24 pt (a test), so the chips
     always fit. The earlier meaning "top / bottom = that edge of the *plot*" is gone for a bar that is not full: a top pip with a bar at the bottom used to put the chips at the top of the plot,
     away from the bar.
   - **A name on the chips' line gives way sideways** (`_clear_of_chips`; the chips and the names share a one-line bar): a name that still clears the chips stays centred on the step as before; one
     that would touch them is centred in what is left beside them and shortened to fit; under the 40 px limit once the chips are taken off it is dropped (as for any narrow step; its tool tip has
     it). A name on another line (a thicker bar, a full bar, aligned to the data) takes nothing. Applies to a full bar too (its chips at the top and names at the top stop used to overlap).
   - **The anchor mark** (lower left corner of a Range baseline anchor step) is on the same line as the chips on a thin bar: the left chips start after it (`_anchor_spot`).
   - The Place tool tips, the Evaluation icon (chips inside the bar) and the "at the edge" tool tip ("markers in it") were rewritten. **Not changed:** the "at the edge" legend over a bar covers the
     chips too (it covers the whole bar; its tool tip says so); a Full bar's chips at its top are not kept out of the stats rows' band (names are; nobody reported it).
   - Test pins: `EvaluationChipsTest` (inside the bar at every edge / side / height, the middle follows the height, fits at 6-24 pt, anchor, name gives way / stays centred / other line / dropped).

## Where

| What | File |
|---|---|
| The options as one value (ranges, defaults, `legend_text`) | `panels/sensorgram/view_settings.py` (no Qt, but the package import pulls Qt in) |
| The controls, the two pop-ups, remembered state | `panels/sensorgram/view_controls.py` |
| Drawing: width, dots, lines, spline, band, legend mode / size / gap / visibility, room | `panels/sensorgram/plot.py` (`set_view`, `show_series`, `_style_legend`, `_room`, `_legend_rows`) |
| The smooth curve (pure, no Qt) | `panels/sensorgram/curve_smoothing.py` |
| The one kind of menu, and the choice rows | `panels/sensorgram/group_menu.py` (`GroupMenuButton`, `segmented_buttons`, `PictureChoice`); used by `view_controls.py`, `plan_group.py`, `stats_group.py` |
| The pictures of Points & lines | `panels/sensorgram/line_icons.py` |
| The text-size link | `panels/sensorgram/text_link.py` (`TextSizeLink`), made by `panel.py` |
| The Evaluation menu and its dice | `panels/sensorgram/plan_group.py` (`evaluation_menu`), `place_picker.py`, `plan_icons.py` (`role_chips_icon`, `pip_icon`) |
| The evaluation chips: layout, colours | `panels/sensorgram/plan_bar.py` (`role_chips`), `plan/role_colors.py`, `plan/bar_model.py` (`ROLES_PLACES`) |
| The tab, the wiring, the greying, the anchor squares honouring the toggle | `panels/sensorgram/panel.py` (`_on_view_changed`, `_sync_controls`, `_draw_last`) |
| The band icon | `panels/sensorgram/stat_icons.py` `band_icon` |
| The legend mode's and the line style's remembered rows | `storage/ui_state_keys.py` `SENSORGRAM_LEGEND_MODE`, `SENSORGRAM_LINE_STYLE`; the others are `store.bind` keys named in `view_controls.py` |
| Icons (vendored, umbrella repo) | `packages/lspr_ui/src/lspr_ui/icon_assets/` `anchor-off`, `list-details`, `share` (anchor tools) |

How a change flows: a control -> `settings_changed` -> `SensorgramPanel._on_view_changed` -> `SensorgramPlot.set_view` -> `_draw_last` (redraws the last result; hidden
panel: deferred by the gate like every redraw). Nothing is recomputed.

## Tests

- `tests/unit/test_lspri_role_colors.py` (9): the palette, the darker baseline, the greys. `tests/unit/test_lspri_plan_bar_model.py` (`RolesPlaceTest`): the five places.
- `tests/integration/test_lspri_sensorgram_view_tab.py` (75), in layers, driven through methods and signals, never by screen coordinate:
  - on their own: the settings and the label rule; the spline curve (through every point, never beyond its neighbours on noise, gaps, what cannot be smoothed); the pictures (size, two
    colours, how different the two joints are, the toggles split in two and crossed out, the button frame); the text-size link on bare widgets (including the restore order and the ranges);
  - `SensorgramPlot` with hand-made series: width, dots, the dashed curve's width, hiding points / lines / band, the spline's density and its band edges, hidden lines keeping the view and
    the legend's colour, the three legend modes, Show beating the half-height guard, text size and gap moving the rows, counts, a hidden legend's room and rows, the stats rows following the
    gap, the legend room, the legend at the edge (its offset, the rows following, the shared room, where it changes nothing);
  - `PlanBarItem.role_chips`: the default where the chips always were, every bar position x edge x side, the colours, the grey stability while panning;
  - `SensorgramPanel`: the tab and its groups (moved, not copied), options reaching the plot, the resets (also the remembered legend mode and line style), the greyed toggles, the anchors toggle for
    both methods and under Subtract, the Steps / Stats / Evaluation menus, the dice, the link and the legend position, remembered state in another panel.
- Updated for the new layout: `test_lspri_plan_bar.py`, `test_lspri_sensorgram_panel.py`, `test_lspri_sensorgram_step_stats.py` (which tab holds which captions).

## Open

- **Nothing of the View tab has been looked at in the running app.** In particular: the six pair colours on a dark and a light plot, the grey `Sb` chips and their outline, the chips in each
  corner (they can sit on the stats table's rows, which also hang below a bar at the top: that is what the dice is for), the legend room steps, the legend over the bar (stats numbers sharing
  a place with the bar's own text), the Straight / Spline pictures and the Points / Lines toggles, the chain button, the look of the five pop-ups (the Steps panel is the tallest: ten rows and
  a heading), the band icon at 18 px, and the width of the View tab's row.
- The Spectra panel has its own View menu (`spectra/view_menu.py`); the two are similar but not shared code.
- A "Show" legend with very many lines is slow to build; no limit was put on it.
