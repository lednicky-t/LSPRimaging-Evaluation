# Sensorgram out-of-date marks (2026-11-03)

**Question (maintainer):** the Sensorgram points carry a circle; with the mask button on there is a second, orange circle. Is the first one a ROI position change? If it is not a mask, it should look different and have its own button in the View group.

## What the first circle was

The **out-of-date mark** (`plot.py`, `show_series`, z-order `_Z_RINGS`): a hollow circle in the *line's own colour*, drawn on every point of a cube whose stored scan cells do not match the app as it is now. It had no button. A line that happens to be orange gave two orange circles. It is not a mask mark (`MASK_COLOR` orange: triangle = persistent, circle = individual; `sensorgram_mask_marks_2026-10-24.md`).

"Out of date" is any input of a cell's hash (`scan_fingerprint.py`): ROI geometry, the ignore-mask pixels in the ROI's window, chromatic correction, background, geometry (rotation / crop), frame roles, detection settings. A moved ROI is one cause among those.

## What it was on the maintainer's session (checked read-only, 2026-11-03)

Session `2026-10-22_172035` of the bulk-sensitivity dataset. Log `analysis.scan_checked`: **314 of 314 cubes out of date**, no whole-file reason, in every session since 2026-11-02, with no edit made in the session itself (the state was already stale on opening).
`scan.h5` was last written 2026-10-27 12:48 (layout 5, recipe 1). Comparing its stored ROI layout with `session.json`: **one ROI differs, ROI 70, moved by (-1.4, -0.4) px = 1.46 px**; the other 159 ROIs and the geometry and background settings are identical. So yes: a ROI position change, and only one ROI.

## What changed

- The mark is now a **hollow grey diamond** (`STALE_COLOR`, `panels/image/mark_colors.py`; size 10, 1.5 px), a shape and a colour no mask mark and no line uses.
- A **clock button** in the Sensorgram ribbon's View group (`stale_marks_toggle.py`), next to the mask button. On by default, remembered (`sensorgram/stale_marks`). It hides **the marks only**; the lines and points stay, "Save data" keeps its out-of-date column, and the (hidden) status row text still says "OUT OF DATE". Because the status row is hidden, the button's tool tip carries the live count ("314 of 314 cubes are out of date").
- Tests: `tests/integration/test_lspri_sensorgram_panel.py` (`test_the_out_of_date_marks_are_grey_diamonds_and_the_clock_button_hides_them_and_is_remembered`).

## Not changed, open (for the maintainer)

The marks are **per cube, not per line**: `stale_cube_indices` (`scan_store.py`) returns a cube if *any* cell of it is stale, and `_stale_mask` hands the same mask to every line. One moved ROI therefore puts a diamond on all 160 lines at every cube (the existing test `test_a_moved_roi_rings_the_out_of_date_cubes` moves ROI 2 and expects ROI 1's line to be marked). Making it per line needs the cell-level stale matrix `[cube, roi]` kept by `ScanResultsModule` and mapped through each line's `member_ids` (as `mask_marks` does); a change to the module's query interface, so it waits for a go-ahead.
Colour and shape are unchecked by eye (GUI-testing rule).
