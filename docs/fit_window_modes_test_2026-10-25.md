# Fit window modes: max ± width, peak ± k × FWHM (test, 2026-10-25)

Nothing is built yet. This records the test that was asked for before choosing how the "peak ± FWHM" window gets its FWHM.

**Decisions so far (maintainer, 2026-10-25).** Each spectrum (ROI x cube) gets its own window. The centre is the maximum of a lightly smoothed (3-point moving average) raw spectrum. The window is not drawn on the plot (the fit's start and end show it). Three modes in a pick-up menu with their parameters beside the icons, only the chosen icon on the ribbon: manual range (today), max ± user width, peak ± k x FWHM.

**Data.** `scan.h5` of `04_Bulk_sensitivity_pumpplan_0-70percent_to_LbL_with_water_v1_LED_V2` (session 2026-10-22_172035): 314 cubes, 26 wavelengths (470-720 nm, 10 nm steps), first 20 of 160 ROIs = 6280 spectra, absorbance. Peak near 585 nm, FWHM about 109 nm.

**FWHM two ways.** A: half-height crossings of the smoothed spectrum (baseline = its minimum), linear interpolation. B: Gaussian fit of the whole spectrum, FWHM = 2.3548 sigma.

| | A (smoothed) | B (refit) |
|---|---|---|
| time for 6280 spectra | 0.27 s | 20.4 s (74x) |
| failures | 0 | 0 |
| median FWHM | 108.9 nm | 109.4 nm |
| B - A | median +0.4 nm, p5/p95 -1.5 / +1.2 nm | |
| same points selected (k = 0.5 / 0.75 / 1.0) | 98.5 % / 87.7 % / 91.7 % of spectra | |
| median peak difference after the fit | 0.000 nm | |

Scatter of the peak trace (point-to-point, nm, median over ROIs), Gaussian fit: full range 0.431, fixed 540-640 nm 0.355, A and B k=0.5: 0.377 / 0.407, k=0.75: 0.394 / 0.395, k=1.0: 0.433 / 0.436. Polynomial order 3: full 0.577, fixed 0.416, k=0.5: 0.525 / 0.529, k=0.75: 0.781 / 0.787, k=1.0: 0.980 / 1.108 (a cubic through a window that holds the shoulders of the peak is a poor model; window width matters more than how FWHM is measured).

**Caveats.** The 10 nm grid makes the window change in whole points: the point count differs between neighbouring cubes in 0.4 % (k=0.5) to 3 % (k=1.0) of cases, and the centre (grid argmax) jumps in 10 nm steps. Interpolating the maximum (parabola through the top three points) would remove the second effect; not tested. Test scripts were throwaway (scratchpad), not kept.

## Built 2026-10-25 (uncommitted)

Quick FWHM chosen (the two FWHM methods agree; refit is 74x slower). Code: `analysis/fit_window.py` (centre by parabola through the top three smoothed points, FWHM by half-height crossings, "same number of points"), fields on `MetricSettings` (`window_on`, `window_mode`, `window_half_width_nm`, `window_fwhm_factor`, `window_same_points`; a manual range is on when it has a bound, as before, so old sessions load unchanged), used by `query.fit_and_metric` (one spectrum) and `trace_fit` (all cubes at once; they select identical points, pinned by `tests/unit/test_lspri_analysis_fit_window.py`). Ribbon: "Fit window" = Set box + pick-up menu (icon only on the ribbon; width fields in the menu rows; "Same number of points" as the footer). The per-spectrum windows are not drawn.

**Does the window still jump?** Yes, less in some modes, never zero. Same 20 ROIs x 314 cubes; "changes" = share of neighbouring cube pairs whose fitted point set differs; noise = point-to-point scatter of the Gaussian-fit peak trace (nm):

| window | grid max, range | parabola max, range | parabola max, same N |
|---|---|---|---|
| max +/- 50 nm | 2.7 % / 0.360 | **1.2 % / 0.320** | 2.7 % / 0.360 |
| +/- 0.5 FWHM | 3.0 % / 0.377 | 7.6 % / 0.403 | **2.1 %** / 0.395 |
| +/- 0.75 FWHM | 7.3 % / 0.411 | 4.3 % / 0.390 | 5.2 % / 0.425 |

Why: a hard window on a 10 nm grid changes whenever a window edge crosses a grid point. With the parabola centre the edge moves continuously, so it crosses a point whenever it sits within the noise (about 0.4 nm) of one. With "same number of points" the window can only move in whole points, so it changes only when the centre crosses the middle between two points; but then it follows the nearest grid point, so the parabola adds nothing (the same-N columns of the grid and parabola rows were identical for a fixed width). Smoothest trace here: max +/- 50 nm without the same-points rule. The polynomial (order 3) is more sensitive to which points are in the window than the Gaussian is (k = 0.75: noise 0.50 with the parabola range window, 0.88 with the grid or same-N window).

Batch fit and the one-spectrum path agree to 3e-10 nm. Not seen by eye yet: the menu (spin boxes inside a pick-up menu) and the new icons.

## Metric range test: centroid over the fitted curve, "top fraction" (2026-10-25)

Question: does reading the centroid only from the part of the fitted curve above a level (`level = base + f x (top - base)`, weights = curve minus level) make it robust against the fit window? Data: 20 ROIs x the first 160 cubes of the same scan. Noise = cube-to-cube scatter (nm); gain = slope of the metric against a rigid shift of real spectra by -4..+4 nm (4 cubes x 20 ROIs; 1.00 is honest); the figure to compare is noise / gain (the noise in nm of real shift). Gaussian base = fitted offset, polynomial base = lowest point of the fitted curve in the window. Scripts were throwaway (scratchpad).

noise / gain (gain when outside 0.9-1.1), polynomial order 3:

| metric | all points | fixed 540-640 | fixed 520-660 | max ± 50 nm | max ± 0.5 FWHM | max ± 0.75 FWHM |
|---|---|---|---|---|---|---|
| maximum | 0.105 (2.00) | 0.142 (1.17) | 0.123 (1.43) | 0.191 | 0.478 (0.88) | 0.333 |
| centroid as built | 0.120 (0.47) | 0.115 (0.21) | 0.110 (0.34) | 0.023 | 0.799 (1.18) | 0.407 |
| top f = 0.0 | 0.115 (0.66) | 0.115 (0.86) | | 0.081 | | 0.268 |
| top f = 0.1 | 0.114 (0.77) | 0.116 | | 0.095 | | 0.212 |
| top f = 0.2 | 0.110 (0.91) | 0.117 (1.06) | | 0.113 | | 0.143 |
| top f = 0.3 | 0.108 (1.05) | 0.120 (1.11) | 0.108 (1.06) | 0.118 | 0.162 | 0.136 |
| top f = 0.4 | 0.106 (1.19) | 0.122 (1.14) | | 0.128 | | 0.148 |
| top f = 0.5 | 0.105 (1.32) | 0.123 (1.15) | 0.111 (1.20) | 0.143 | 0.306 | 0.140 |
| top f = 0.9 | 0.104 (1.86) | 0.138 (1.17) | 0.121 (1.39) | 0.181 | 0.446 (0.88) | 0.279 |

Gaussian fit:

| metric | all points | fixed 540-640 | fixed 520-660 | max ± 50 nm | max ± 0.5 FWHM | max ± 0.75 FWHM |
|---|---|---|---|---|---|---|
| maximum | 0.116 | 0.119 (1.13) | 0.117 | 0.109 | 0.224 | 0.130 |
| centroid as built | 0.114 (0.45) | 0.122 (0.23) | 0.118 (0.37) | 0.022 | 0.807 (1.18) | 0.409 |
| top f = 0.3 | 0.116 | 0.211 (0.70) | 0.141 (0.69) | 0.054 | 0.862 (1.23) | 0.210 |
| top f = 0.5 | 0.116 | 0.176 (0.79) | 0.136 | 0.079 | 0.810 (1.22) | 0.134 |
| top f = 0.9 | 0.116 | 0.142 | 0.117 | 0.493 (0.80) | 0.925 | 0.130 |

Reading: (1) the centroid as built under-reports a shift (gain 0.2-0.5) when the window is fixed or covers the whole spectrum, and is noisy (0.4-0.95 nm) when the window is a narrow ±0.5-0.75 FWHM; it is quiet only with a wide window that follows the peak. (2) For a Gaussian the top fraction equals the maximum with all points (a symmetric bell), so it adds nothing; it is worse when the fit window truncates the bell. (3) For the polynomial, f around 0.3 gives noise/gain 0.108-0.136 and gain 0.99-1.11 in every window tried: the most window-independent number in the table. (4) The polynomial maximum is not an honest shift for wide windows (gain 2.00 over all points). (5) A no-fit estimate (parabola top of the 3-point-smoothed raw spectrum) has noise 0.22 nm at gain 1.00, about twice the noise of the Gaussian-fit maximum.
