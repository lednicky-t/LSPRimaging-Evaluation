# Polynomial order: what tells a good order from an over-fitted one (study, 2026-10-25)

Question (maintainer): the Fit group's order field should show only the ordinal (1<sup>st</sup>, 2<sup>nd</sup>, ... up to 15<sup>th</sup>), with good orders highlighted green and over-fitted ones amber; "what testing method do you propose?". This records the measurements behind the proposal. Built the same day: see "Decision and what was built" at the end. Scripts were throwaway (scratchpad).

**Data.** `scan.h5` of `04_Bulk_sensitivity_...` (160 ROIs x 314 cubes x 26 wavelengths, absorbance). Noise = the cube-to-cube scatter in the baseline cubes 231-end, median 0.00049 A, nearly white ("empirical noise"; the scan's pixel-spread error bars are a different, smaller number). Event shift = plateau cubes 201-227 minus baseline cubes; the peak top of the ROI average moved 7.0 nm.

**Quantities.** *In-sample RMS* of the residual; *LOO RMS*: leave-one-out cross-validation error, closed form from the hat matrix `H` of the polynomial (`e_i / (1 - h_ii)`, no refits); *chi2_red* = sum of (residual / empirical noise)^2 / (n - p); *lag-1 autocorrelation* of the residuals along wavelength; *metric noise / event shift / SNR*: the polynomial maximum read as the Sensorgram does (app code), noise = std over the baseline cubes; *jackknife sd*: the standard error of the peak position from dropping each point once.

## All 26 points, orders 1-13 (the code caps the order at half the points = 13)

| order | in-sample RMS (A) | LOO RMS (A) | LOO / noise | chi2_red | max leverage | metric noise (nm) | event shift (nm) | SNR |
|---|---|---|---|---|---|---|---|---|
| 1 | 0.0744 | 0.0807 | 166 | 29565 | 0.15 | - | 1.5 | - |
| 2 | 0.0310 | 0.0360 | 74 | 4934 | 0.30 | 0.046 | 5.74 | 123 |
| 3 | 0.0308 | 0.0400 | 82 | 4559 | 0.47 | 0.120 | 13.95 | 117 |
| 4 | 0.0135 | 0.0188 | 39 | 1358 | 0.63 | 0.054 | 5.90 | 108 |
| 5 | 0.0095 | 0.0177 | 36 | 418 | 0.76 | 0.078 | 8.41 | 106 |
| 6 | 0.0065 | 0.0147 | 30 | 322 | 0.86 | 0.073 | 6.81 | 91 |
| 7 | 0.0027 | 0.0067 | 14 | 40 | 0.92 | 0.081 | 7.03 | 86 |
| 8 | 0.0025 | 0.0108 | 22 | 42 | 0.96 | 0.105 | 7.33 | 69 |
| 9 | 0.0012 | **0.0055** | **11** | 12.7 | 0.98 | 0.104 | 6.81 | 66 |
| 10 | 0.0010 | 0.0098 | 20 | 6.1 | 0.99 | 0.143 | 7.36 | 50 |
| 11 | 0.0007 | 0.0086 | 18 | 4.1 | 1.00 | 0.138 | 6.85 | 49 |
| 12 | 0.00046 | 0.0069 | 14 | 1.8 | 1.00 | 0.184 | 7.13 | 39 |
| 13 | 0.00044 | 0.0159 | 33 | 1.8 | 1.00 | 0.185 | 6.96 | 38 |

Reading: (1) the in-sample error falls with every order (it cannot show over-fitting: at order 12 it equals the noise, 0.00046 against 0.00049, i.e. the curve follows the noise). (2) LOO has a minimum at order 9 (7 is nearly as good) and zig-zags: orders 8, 10 and 13 are clearly worse than their neighbours, so the good orders are not a contiguous block. (3) Even the best order predicts a left-out point with 11 times the noise: **a polynomial over the whole 250 nm range cannot describe this 110 nm wide asymmetric peak down to the noise without also fitting noise**; chi2_red reaches about 1 only at order 12, where the peak position is 4 times noisier than at order 4. (4) The metric's noise rises with the order (0.05 -> 0.18 nm) while its bias is only small from order 6-7 on (the event shift 6.8-7.4 against 7.0): no order is both unbiased and quiet. Orders 3 and 5 have the odd-order failure (a shift of 13.95 / 8.41 nm: the maximum runs to a window end).

## In a fit window (the usual use), polynomial maximum

Fixed windows. The order is capped at half the points.

| window | order | LOO / noise | chi2_red | lag-1 autocorr | metric noise (nm) | event shift (nm) | SNR | jackknife sd (nm) |
|---|---|---|---|---|---|---|---|---|
| 540-640 (11 points, cap 5) | 1 | 84 | 4947 | 0.57 | - | - | - | 122 |
| | 2 | 25 | 326 | 0.45 | 0.070 | 6.29 | 89 | 1.9 |
| | 3 | 10.6 | 25.8 | 0.03 | 0.133 | 8.48 | 62 | 0.87 |
| | 4 | 11.8 | 15.1 | -0.05 | 0.177 | 7.45 | 41 | 1.30 |
| | 5 | **3.7** | 1.58 | -0.58 | 0.186 | 6.94 | 37 | 0.34 |
| 520-660 (15 points, cap 7) | 1 | 118 | 10837 | 0.74 | - | - | - | 178 |
| | 2 | 33 | 814 | 0.62 | 0.053 | 5.69 | 107 | 2.0 |
| | 3 | 30 | 306 | 0.39 | 0.119 | 10.26 | 86 | 2.9 |
| | 4 | 21 | 105 | 0.36 | 0.096 | 6.90 | 71 | 1.7 |
| | 5 | **5.0** | 5.95 | -0.04 | 0.127 | 7.25 | 57 | 0.40 |
| | 6 | 10.4 | 5.91 | -0.08 | 0.170 | 7.34 | 42 | 0.85 |
| | 7 | 8.0 | 2.19 | -0.45 | 0.175 | 6.89 | 39 | 0.45 |

Reading: (1) in a window LOO has a clear minimum (order 5 in both windows) and is higher on both sides: lower = under-fit (smooth structure left: the lag-1 autocorrelation of the residuals is strongly positive, 0.3-0.7), higher = over-fit (LOO rises again, the residuals turn negatively correlated, -0.45 to -0.58, which is partly the mechanical effect of using up degrees of freedom: about -p/n). (2) The residual autocorrelation separates under-fit from adequate without any noise estimate; LOO separates over-fit. (3) The metric's noise again rises with the order, so the LOO minimum is not the order with the quietest peak: it is the order after which more terms only add variance for no gain in prediction. (4) The jackknife spread of the peak follows the same pattern as LOO but is erratic.

## What this says for a "good / over-fitted" indicator

- A verdict has to be **relative**, and depends on the points actually fitted (window), the data and the noise: it cannot be a fixed list of orders. Evaluate it on the points inside the fit window.
- **Cross-validation (leave-one-out, closed form) is the right test for over-fitting**: no noise estimate, no refits, cheap (one QR per order for a shared wavelength grid), and it finds the minimum in every case above. Over-fitting = more terms than the minimum, with a worse LOO.
- **Under-fitting** shows as LOO above the minimum on the low side, with positively correlated residuals.
- The metric itself (peak position) gets noisier at every step up; the indicator must not claim "green = lowest noise".
- For the full range the best polynomial predicts 11x the noise: the indicator should say "no order describes this spectrum to the noise" (worth a hint to use the template shift).

## Decision and what was built (maintainer, 2026-10-25)

**Choices.** (1) The order field becomes a pick-up list that the mouse wheel also steps. (2) Verdict colours over all cubes and the present one: **green** = good at every cube (relaxed the same day to good here and at 95 % of the cubes or more, `GOOD_SHARE`, because with one or two ROIs a few cubes are always out and green never showed), **yellow** = good here but out at more than 5 % of the cubes, **amber** = over-fitted at the present cube. (3) The list stops at the **13th** (the fit already caps the order at half the points: 13 for 26 wavelengths). (4) **Purple** = under-fitted. Grey = not available (above the cap for the points in the window) or not judged. I read "out" as over- or under-fitted, and let the present cube decide amber and purple (a cube that is under-fitted elsewhere makes the order yellow).

**Built.**

| What | Where |
|---|---|
| The verdicts: closed-form leave-one-out for all 13 orders from one QR (nested hat matrices), per cube, median over the selected ROIs (at most 20, evenly spread), relative to the best order within 1.25; `OrderQuality.state` gives the colour for an order and the present cube | `analysis/order_quality.py` (pure), `trace_fit.formula_arrays` (the shared gather) |
| The order picker: 1st to 13th with a raised ending, a coloured dot, the verdict in words, wheel stepping (up = higher, one notch = one step, a touch pad's small deltas add up), tooltips that say why | `panels/spectra/order_picker.py`, `pick_menu.py` (`ordinal_picture`, `set_choice`) |
| The panel: the check runs on its own worker thread when the selection, formula, fit window or scan changes (newest request wins), a cube change only re-colours from the stored verdicts, other fit methods grey it out | `panels/spectra/panel.py` |
| Tests (umbrella repo) | `tests/unit/test_lspri_analysis_order_quality.py` (20: closed form against brute-force refits, the cap, scale and offset, spectra judged alone, synthetic truth, windows, holes, ROI median and sampling, cancel, every colour state), 3 in `tests/integration/test_lspri_spectra_panel.py` (wheel and limits, worker thread and no check on a cube change, colours including the dot pixels) |

**Checked on the real scan** (module and the numbers above agree): one ROI over all points, the median leave-one-out error is smallest at the 9th (0.0050 A), the 7th is 0.0075; in the 520-660 nm window the best order is the 5th at the baseline cubes and the 7th at the shifted cubes (so the 5th shows yellow: good at some cubes, under-fitted at others); in a max +- 50 nm window (11 points) only the 1st to 5th are available and the 5th is good everywhere (the best is at the cap: a wider window could allow more). Cost: 30-50 ms for one ROI, 150-190 ms for 20 ROIs x 314 cubes, so on a worker thread it is invisible.

**Known limits.**
- The verdict says how well an order *predicts*; it does not say the peak position is then quiet or unbiased (the peak noise rises with every order, section above).
- Verdicts are relative to the best order of the same spectra: when the best order sits at the cap, the verdict cannot say whether a higher one would be better.
- With few selected cubes or one ROI the best order of a cube is itself noisy, so yellow is common at the boundary between two neighbouring orders (measured: 5th and 7th in the window).
- The tolerance (25 %) is one number in `order_quality.py` (`TOLERANCE`); it was chosen from the real-data tables, not tuned against a ground truth.
- The colours, the dot and the wheel were not looked at by eye.
