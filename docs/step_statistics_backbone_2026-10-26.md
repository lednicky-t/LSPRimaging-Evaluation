# Step statistics: the backbone (2026-10-26)

Part 1 of "compare the experiment steps with each other". Part 2 (how to show the numbers: Sensorgram, ROI / Group table) is **not built and not decided**.

Code: `analysis/step_stats.py` (pure, no Qt, nothing stored). `DatasetModule.cube_spans_unix_ms` gives each cube's first and last frame time.
Tests: `tests/unit/test_lspri_step_stats.py` (30), `tests/integration/test_lspri_dataset_cube_spans.py` (4). Engineering priority served: 1 (correctness of the numbers), 4 (testable without the GUI).

## What the numbers mean (the "is noise just STD / SEM?" question)

Per ROI and step, from the cubes' finite values (a failed fit is a gap and is skipped; `n` counts what was used):

| number | what it is | note |
|---|---|---|
| `mean` | arithmetic mean | with the baseline correction on, a shift relative to the baseline |
| `std` | sample SD (`ddof=1`) | scatter of single cubes; includes any drift, so an upper bound of the noise |
| `sem` | `std / sqrt(n)` | how well the *mean* is known; **not noise**: shrinks with a longer step |
| `slope_per_s` | straight-line fit against time | the drift rate inside the step (nm/s; the real data are ~0.01-0.1 nm/min) |
| `noise` | SD of the residuals of that line (`ddof=2`) | equals `std` for a flat step; a steady drift or binding trend is not noise |

So: noise is an SD, but of the *detrended* values, and SEM is a different thing (uncertainty of the mean). On the example dataset noise/std is 0.95-1.02 for flat steps and 0.76 in step 7 (60 % EG to water), where the signal still drifts (-0.07 nm/min).
SEM assumes independent cubes; slow wander makes it too small (stated in the module docstring).

## Rules (decisions made while building; all changeable, none is stored)

- **Which cubes**: a cube is used only if *all* its frames lie inside the step's evaluation window. A cube across a boundary is left out of both steps and counted in `n_partial` (the maintainer's "mixed" rule, plan doc 2026-10-26). A cube with no timing is in no step. Checked on the real dataset against `lspr_core.imaging_plan.classify_cube`: 11 mixed cubes, 11 left out, same cubes.
- **Evaluation window** (`StepWindow`, default = whole step): skip the first N s (new solution still arriving), skip the last N s, or only the last N s (equilibrium value). Without it the settling at the start of a step counts as noise: real data, step 9, std 0.213 nm whole step, 0.157 nm after skipping 60 s. Per-step windows are not built.
- **Which values**: the Sensorgram's traces exactly as plotted: spike rejection, **smoothing** and baseline as selected (maintainer's choice 2026-10-26; the first version switched smoothing off, which the maintainer reversed). Smoothing is done **inside each step** (see the next section) unless the maintainer switches that off or the dataset has no steps. Measured on the real data (160 ROIs, median over ROIs, steps 2-8, nm):

  | smoothing | noise | std | sem | step mean moved by |
  |---|---|---|---|---|
  | none | 0.09 | 0.09 | 0.017 | - |
  | Savitzky-Golay 15/2 across the steps (whole trace, then cut) | 0.23 | 0.27 | 0.05 | up to 0.44 (steps 8-9) |
  | Savitzky-Golay 15/2 **inside** each step | 0.03 | 0.04 | 0.008 | <= 0.008 |
  | moving average 15 across the steps | 0.20 | 0.43 | 0.08 | up to 1.2 (steps 8-9) |
  | moving average 15 **inside** each step | 0.011 | 0.02 | 0.004 | <= 0.008 |

  Smoothing across the steps lets the edge cubes of a step carry the neighbour's level (a step is ~27 cubes, the window 15), so the numbers get *worse*; inside the steps they are smoother than measured, as smoothing should be, and the means stay put. Still true when smoothing: neighbouring smoothed values are correlated, so `sem = std / sqrt(n)` is too small by about `sqrt(window)`; quote the noise, not the SEM.

- **Groups** (`group_statistics`): each ROI is reduced to its step mean first, then mean of means, spread *between* ROIs (`ddof=1`), SEM over ROIs, and **median** of the members' noise (the typical ROI). Median, not root mean square: in step 9, 32 of 160 ROIs are disturbed (noise > 1 nm) and the RMS is 1.6 nm against a typical 0.2 nm.
- Formula, fit and statistics are never stored, so there is no file format and nothing can be stale; the numbers are recomputed from the traces.

## Smooth inside steps (2026-10-26)

The maintainer chose, from the two ways to deal with the leakage, to smooth each step on its own and to show exactly that curve.

- **Pure part**: `statistics.apply_statistics(..., segments=)` smooths each run of equal labels alone (spike rejection and baseline unchanged); `step_stats.smoothing_segments(steps, first, last)` makes the labels: step number for a cube wholly inside a step, -1 for cubes in no step (or without timing), and a label of its own for each cube that straddles a boundary, so those stay as measured and do not drag the edge of either step (same cube rule as the statistics, `assign_cubes`). `StatisticsSettings.smooth_within_steps` (default on) is saved with the session like the other statistics settings: no schema bump, a session without the field reads as on (test in `test_lspri_rewrite_session.py`).
- **Display**: the Sensorgram builds the labels on every redraw (`StepOverlays.step_inputs` in `panels/sensorgram/step_overlays.py`: switch on + a smoothing method chosen + the dataset has steps; else None = the whole trace) and hands them to `SensorgramRequest.segments` -> `build_series`; the fitted traces stay cached, only the curves are recomputed. A dragged or reset boundary, a restored session or a changed dataset (`steps_changed` reasons boundary / reset / restored / dataset) redraws when this is active; a note or colour edit does not.
- **Toggle**: `IconToggle` in the Smoothing group after the window box (`within_steps_icon` in `panels/sensorgram/stat_icons.py`: a dashed step boundary with a separately smoothed curve on each side, coloured when on, grey and crossed out when off). Shown only while a smoothing method is chosen, greyed out (keeping its choice) when the dataset has no steps; the tooltip says which.
- **Numbers**: `compute_step_statistics` uses the same labels, so the step statistics come from the plotted curve.
- Not done: spike rejection is still applied to the whole trace (a median filter keeps a jump between two levels, so it does not blend the steps); the icon has not been looked at on screen by the maintainer (no screenshots taken).

## Findings that shape part 2

1. **Absolute step means of different ROIs are 5 nm apart** (the resonances sit at different wavelengths), so the group spread of absolute means says nothing about the experiment. With the baseline subtracted (window 0-250 s) the group means read 0, +2.0, +4.2, +6.3, +8.4, +9.9, +11.6, +13.2 nm for 0-70 % EG and the between-ROI spread is 0.01-1 nm (about 7 % of the shift: ROIs differ in sensitivity). Water afterwards is -0.36 to -0.41 nm (the baseline did not come back).
   Suggested next layer: **per-ROI difference to a chosen reference step** (`mean_step - mean_ref`, uncertainty `sqrt(sem_a^2 + sem_ref^2)`), then group statistics on those differences, independent of the baseline setting.
2. Step 12 is 6 cubes (the last 60 s): a 60 s skip leaves nothing (NaN, not zero).
3. `aggregate_traces` (the Sensorgram's group band) uses `ddof=0` for "sd" and `ddof=1` for "sem"; this module uses `ddof=1` throughout. The "sd" band is therefore narrower by `sqrt((n-1)/n)` (29 % at n = 2, 3 % at n = 16). Not changed; maintainer to decide.

## Cost and where to call it

Real data, 160 ROIs x 314 cubes x 12 steps: 2.6 ms (0.2 ms of it building the trace matrix); with Hampel spike rejection on 17 ms, of which 14 ms is `apply_statistics`, which the Sensorgram already pays on every redraw. Call it in the Sensorgram's own worker job right after the traces are ready, not on the GUI thread.

## The table on the Sensorgram (2026-10-26, first display of the numbers)

Maintainer's brief: a new ribbon section like the plan bar's text position (top or bottom only), the numbers in the colour of the line, one row per line, in the middle of each step, level with the legend's rows, with room on the left so the first step does not sit under the legend; mean ± SD for now.

- **What it shows**: rows = the plotted lines (legend entries), columns = the steps, a cell = `mean ± SD` in the line's colour, centred on the visible part of its step. Hover a cell for the full numbers and what the SD means for that line.
- **The numbers** (`SensorgramRequest.step_membership` -> `build_series` -> `Series.step_values`, computed in the same worker pass as the curves): for every line, ROI or average alike, the mean and SD of **the line as drawn** over the cubes of the step, so they follow smoothing (inside steps or across), the baseline and spike removal exactly as the curve does. First version: an average line showed the spread *between* its ROIs; the maintainer reported the numbers did not react to smoothing (true by construction: that spread comes from the ROIs sitting at different positions, real data 1.587 nm with or without smoothing) and chose the line's own SD instead (average of 6 ROIs, step 3: 0.061 -> 0.039 -> 0.028 nm for none / Savitzky-Golay 15 / moving average 15). The between-ROI spread stays in `step_stats.group_statistics` / `LineStepValues.between_rois` (not shown). Whole steps for now (no trimming window in the UI).
- **Layout** (`panels/sensorgram/stats_table.py` item, `plan/stats_table.py` pure model): drawn in device pixels like the plan bar. The legend moves to the table's edge just inside the plan bar (`SensorgramPlot._place_legend`), the item reads the laid-out legend's row centres and puts each cell on its line's row, and never starts left of the legend's right edge. Room is made like the bar's: the y range is widened by the legend's height at the table's edge (added to the bar's), and the x range on the left by the legend's width, so the first step starts after the legend. Without the table the legend is where it always was.
- **Number format**: one number of decimals for the whole table (two significant digits of the typical SD). A cell shows the longest of `mean ± SD`, the same with one decimal less, the mean alone, that fits its step; a shortened SD that would read `0.0` is skipped (`2.0 ± 0.0` would claim no spread). **Narrow steps get shorter text, not a smaller font** (changed 2026-10-26; until then the whole table shrank to its narrowest step, and the same Text size box value drew at 6 pt for one selection and 10 pt for another, depending on the digits and signs of the numbers, see `step_stats_size_cap_2026-10-26.md`): the font is always the size in the box, a cell too narrow for mean ± SD shows one decimal less, then the mean alone, then nothing (its tooltip still has the full numbers).
- **Only with a legend**: the rows are the legend's rows. The legend (`SensorgramPlot.show_series` / `_fit_legend`) always names up to 6 lines, as before; with more it is kept while the laid-out legend takes at most 50 % of the plot height (a row is ~25 px, so about 9-10 lines on a 500 px plot, more on a taller panel), and never for more than 24 lines (`MAX_LEGEND_ENTRIES`). Originally the limit was a flat 6, so selecting a 7th group took the legend and the table away (reported 2026-10-26). Beyond the limit: no legend, no table, and the toggle's tooltip says why (select fewer ROIs or groups, or make the panel taller). The 160-ROI case belongs in the ROI / Group table (not built). The legend is re-judged at each draw, not on a resize.
- **Ribbon**: group "Step stats" after "Experimental plan": show toggle (`step_stats_icon`), top / bottom pick-up menu, text size. On by default, top; remembered (`sensorgram/stats_visible`, `stats_position`, `stats_text_pt`); greyed out without steps. A moved, reset or restored boundary recomputes the numbers (the fits are cached); a note or colour edit does not.
- **Limits**: while the table is on it owns the legend's place (a hand-dragged legend is put back at the next draw); with the table off a dragged legend stays where it was (a regression of the first version, which reset it at every redraw, was fixed 2026-10-26); the layout is checked by geometry tests (rows level with the legend to 1 px, cells in the middle of the step, nothing under the legend, top and bottom), not yet looked at on screen; "Statistic" is not renamed (the maintainer is considering "Grouping").

## Not built / open for part 2

The Qt module / panel wiring for choosing ROIs is done as part of the table above. Still open: per-step windows (trimming) in the UI, the reference-step difference, stale cubes (the Sensorgram marks out-of-date cubes with hollow rings; the statistics do not look at that yet), masked cubes (`n_masked` is in the scan; not used), CSV export, other statistics in the table (SEM, noise, n, slope), and the ROI / Group table display for many ROIs.
