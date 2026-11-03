# Voigt fit and analysis speed-ups (2026-11-02/03)

Maintainer: "focus on optimizing calculations and analysis ... add to fits a Voigt peak fitting". All measured on the maintainer's real scan
(`04_Bulk_sensitivity...ome.zarr/sessions/2026-10-22_172035/analysis/scan.h5`, read only: 314 cubes x 160 ROIs x 26 wavelengths), in one thread
(the app runs the Sensorgram's fits in its helper process, so this is the work itself). Uncommitted.

## Speed-ups (same numbers)

| Sensorgram, 160 ROIs | before | after | what changed |
|---|---|---|---|
| polynomial, maximum | 693 ms | 276 ms | `trace_fit.row_patterns` |
| polynomial, centroid | 537 ms | 158 ms | same |
| Gaussian, maximum | 1582 ms | ~1300 ms | same |
| Gaussian, centroid | 2711 ms | ~1600 ms | same + `sampled_curve` / `trapezoid_weights` |
| no fit (points), any window | ~2000 ms (one cube at a time) | 24-166 ms per 40 ROIs | `_points_group` (batched) |

* `row_patterns`: the cubes of a ROI are grouped by their usable wavelengths. `np.unique(mask, axis=0)` sorts rows as raw bytes (~300 of the
  ~460 ms of preparation); the rows' bits packed big-endian into one integer sort in the same lexicographic order. Identical output (pinned
  against `np.unique` for 1-100 columns), so identical fits.
* Gaussian centroid: the curve built in place (bit-identical values) and both trapezoid integrals as one matrix product with the trapezoid
  weights (same sums in another order: <= 7e-13 nm from before, the batch-vs-per-cube tests allow 1e-5 nm).
* "No fit" metric: the same numpy calls as `query.metric_from_spectrum`, a row at a time in one call: exactly equal (0.0 nm) to the per-cube
  path for both metrics and all window modes on the real scan.

Checked on the real scan, batch vs one-cube-at-a-time (`query.fit_and_metric` per cube), 40 ROIs, both metrics, four windows (none, manual,
around max, FWHM): gaps identical everywhere; none 0.0 nm; polynomial <= 1.2e-8 nm (the scaled-variable rounding documented in `trace_fit`);
Gaussian <= 6.8e-13 nm.

**Found, not changed (needs a decision):** Gaussian + "Maximum +- width" window (+-40 nm, ~9 points): 83 s for 40 ROIs (~5 min for 160). On so
few points the batch solver gives up on most cubes (the width runs away: a bell cannot be told from a parabola there), and each goes to scipy's
`curve_fit` (maxfev 10000). Measured 2026-11-03 on 10 ROIs (3,140 cubes): **1,784 cubes go to scipy** (42 s); scipy needs 400-2,000 evaluations
each (capped at 400 every one of them becomes a gap, and the time hardly changes). Its answers there are close to the vertex of a parabola
through the same points: |parabola vertex - today's Gaussian| median 0.0002 nm over all cubes (p95 0.68 nm, max 1.3 nm: the cubes where the bell
is real), against a cube-to-cube noise of 0.087 nm. Options:
1. Run the scipy fallbacks in parallel (lspr_core.perf workers): same numbers, ~cores x faster. Most work, no science change.
2. A runaway width = a parabola: take the vertex of the quadratic fit for exactly those cubes. Seconds instead of a minute; changes those cubes'
   values by ~1e-4 nm typically (to be measured on exactly the runaway cubes before deciding). Science change: maintainer's call.
3. Make them gaps. Fast, but loses half the trace in this setting.

**Spectra panel, batched over ROIs (2026-11-03):** `scan_view.fit_spectra` solves the Gaussian / Voigt fits of all the cube's spectra in one
solver call per set of windowed wavelengths (`_batch_solved`; `query.fit_*(solved=...)` skips the solve, the rest is unchanged). 160 ROIs, one
cube: Gaussian 660 -> 61 ms, Voigt 805 -> 76 ms (FWHM window: 880 -> 320 / 978 -> 390 ms). **Bit-identical** to one spectrum at a time on the
real scan (difference 0.0 for metric and curve, both metrics, two windows; pinned by `SpectraBatchTests`). Polynomial left per spectrum
(253 ms): its batch form keeps the scaled variable and would change the Spectra numbers at the 1e-8 nm level.

Template shift (1.9 s per 160 ROIs): profiled, ~9 ms per ROI in the exhaustive shift search itself (already vectorised, diagnostics off); a
coarse-to-fine search would change numbers - left.

**Proposal, not built (threading / process change: check in first):** fan the Sensorgram's fits out over several helper processes, the count
from `lspr_core.perf` (as the scan does; `trace_pool.py` already anticipates it). The work is already cut into independent chunks of
`BATCH_ROWS` cubes whose rows never see each other, so the numbers are identical at any worker count (a test would pin it, as the rules ask).
Expected on the dev laptop (8 cores, 7 workers): Gaussian 1.3 -> ~0.3 s, Voigt 3 -> ~0.6 s, the Gaussian "Maximum +- width" case ~5 -> ~1 min
(option 1 above). Cost: ~100 MB of RAM per idle helper (numpy / scipy imported), so the pool would shrink when idle as the scan's does.

## Maintenance the same night

* Flaky `test_the_image_panel_reporting_a_new_view_range_persists_it`: the window's first layout starts the Image panel's 600 ms view-range
  report, which raced the test's report under load. The test stops that timer first (deterministic now).
* Flaky warm-pool tests: under load two workers took > 60 s to start; the test waits as long as `WarmPool.start` itself (180 s). In the app, a
  warm-pool start that a switch-off or quit cut short was logged as a WARNING with a traceback ("cannot schedule new futures after shutdown"); it
  is the switch-off working, now logged at DEBUG (`scan_task.py`; `shutdown` bumps the generation as the switch-off does).

## Voigt fit (`analysis/voigt_fit.py`; `query.fit_voigt`; `trace_fit._voigt_group`; Fit menu "Voigt")

Model: `y = amplitude * Re w(z) + offset`, `z = (E - centre + i gamma) / (sigma sqrt 2)`, E the photon energy in eV, w the Faddeeva function
(`scipy.special.wofz`). Gaussian part sigma (inhomogeneous: size/shape spread), Lorentzian part gamma (homogeneous: damping). Decisions made
without the maintainer (all easy to change):

1. **Fitted in photon energy**, drawn and read in nm (the maintainer's own convention in the `lspr-peak-fitting` skill: plasmon widths are energies;
   a peak symmetric in eV is skewed in nm). The maximum is `1239.84198 / centre` exactly; the centroid is taken over the curve in nm as for the
   other fits. On the real scan the Voigt maximum sits **~5.5 nm bluer** than the Gaussian-in-nm one (median -5.48 nm, 5-95 % -6.07 .. -5.14):
   the axis skew, not a different peak. Shifts (the Sensorgram) are what matters and are comparable within one method; positions are not
   comparable across methods.
2. **Flat offset** fitted together with the peak, as the Gaussian (no pre-subtraction). The skill notes this user's backgrounds often rise to the
   red; a linear background term would be the next step if needed.
3. Same fit window, maximum / centroid and metric-range rules as the Gaussian; fewer than 5 points = no fit (a gap).

Solver: variable projection (amplitude and offset solved in closed form), damped Gauss-Newton on (centre, sigma, gamma) with the exact
Faddeeva derivatives, widths bounded (sigma >= 1e-4 of the energy span, gamma >= 0; a width at its bound that the cost pushes further is held),
first guess = the Gaussian solver's fit in energy with gamma = 0. Fallback per spectrum: scipy `least_squares` with the same bounds; then a gap.

Validated: gradient = finite differences; same optimum as scipy run to 1e-15 tolerances (peak within 4e-8 nm); recovers synthetic Voigt,
Gaussian and Lorentzian peaks; batch = per-cube on the real scan (<= 6.8e-13 nm, gaps identical). Speed, 160 ROIs: 3.1 s (maximum), 6.3 s
(centroid: 400-point curves through `wofz` for 50,000 cubes); one cube of 50,240 has no value.

**What the real data say:** gamma goes to 0 in 99.6 % of the cubes: with 26 broad points the ROI spectra are fitted as pure Gaussians in energy;
the Lorentzian part is not resolved (expected for ROI-averaged spectra of many particles: inhomogeneous broadening). A first version iterated
ln(sigma), ln(gamma); the slope in ln(gamma) vanishes as gamma -> 0, every cube took ~45 passes and 160 ROIs took 32 s. Iterating the widths
themselves with gamma >= 0 as a bound: 3.1 s.

Tests: `tests/unit/test_lspri_analysis_voigt_fit.py` (also `row_patterns` and the batched "no fit" metric).
