# Step stats table: the size box seems dead (2026-10-26)

**Report:** after running the analysis again, changing the Step stats "Size" box no longer changed the numbers on the plot.

**Finding:** not a stale-redraw bug. The size reaches the table every time (checked on the real session and in tests). The table is drawn at the **largest size at or below the box value at which every step that can still show `mean ± SD` fits** (`StepStatsItem._fit_point_size`, `panels/sensorgram/stats_table.py`; rule chosen 2026-10-26: one size for the whole table, shrink before the SD is dropped). One narrow step can therefore pin the whole table at the floor (6 pt) and the box has no visible effect.

**Measured** (read-only, real dataset `04_Bulk_sensitivity_pumpplan_0-70percent_to_LbL_with_water_v1_LED_V2`, its saved session and scan, 12 steps, 3 ROI groups drawn as 1 line, 3 decimals, box values 8 - 18 pt):

| plot width | step widths (px, at 1300 px) | drawn size for every box value |
|---|---|---|
| 1300 px | 98 82 79 85 85 82 85 82 62 92 91 22 | 6 pt |
| 1600 px | | 8 pt |
| 2000 px | | 8 pt (box 8), 10 pt (box 10 and up) |
| 2600 px | | follows the box up to 14 pt |
| 3400 px | | 6 pt again |

The 3400 px row is the odd one: the last step (22 px at 1300 px) grows past `MIN_CELL_WIDTH_PX`, so it joins the rule, but it only fits `mean ± SD` at 6 pt, and it then pins everyone to 6 pt. A step that cannot fit at the floor is ignored, one that just fits is not: the cap is not monotonic in the plot width.

**Why it showed up after the rescan:** not established. Anything that changes the width of the text (a different number of decimals, a step that now has an SD) moves the cap. The log of that session shows no error.

**Options (maintainer to choose; nothing changed yet):** (A) honour the box exactly, narrow steps fall back to the shorter variants (one decimal less, then mean alone), which `fit_variant` already does per cell; (B) keep the rule but say what is drawn ("drawn at 6 pt: the narrowest step limits it") in the size box's tooltip; (C) a switch "Fit to narrow steps", on by default.

**How it was measured:** a throw-away script built the app's modules on the real dataset with autosave suspended, drew the Sensorgram, and read `StepStatsItem.layout()` per box value; not kept in the repo.

## Outcome (same day)

The maintainer's report settled it: with the box at 10 pt, every other ROI drew at 10 pt but the ROI they had moved and recalculated drew at 6 pt. The cap depends on the text of the numbers (a minus sign or one more digit is enough: on this dataset ROI 1 drew at 10 pt and ROIs 2 and 3 at 8 pt with the box at 10 and a 1700 px plot), so the same box value meant different sizes for different selections.

**Changed (option A):** the table is always drawn at the size in the box. A step too narrow for it gets shorter text (`fit_variant`: one decimal less, then the mean alone, then nothing; the tooltip keeps the full numbers). `_fit_point_size` is gone; tests in `tests/integration/test_lspri_sensorgram_step_stats.py` pin the new rule. Re-measured on the real data: 160 of 160 ROIs draw at 10 pt with the box at 10; box 6 - 24 gives 6 - 24 pt at 1300 px. The cost: at large sizes narrow steps lose their SD (at 1300 px: 9 pt keeps it everywhere that fits, 12 pt shows the mean alone in the narrowest ones).
