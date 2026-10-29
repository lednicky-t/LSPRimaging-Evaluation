# ROI position tuning: data-driven centres from the Sensorgram (design + first real-data results, 2026-10-28)

Maintainer's idea: move each ROI's centre over a small lattice, compute the Sensorgram for every shifted copy over a chosen part of the experiment, reduce
each to a number, and use the numbers to pick better centres. Engineering priorities served: 1 (scientific validity: the held-out check below), 2 (data
integrity: nothing is written to `scan.h5`).

## Decisions (maintainer, 2026-10-28)

* **Lattice**: square, odd size (3, 5, 7), `pitch` px apart, the current centre in the middle; whatever ROIs are selected get tuned. The maintainer says the
  ideal position is **at most 5-6 px** from where a ROI sits, so the normal lattice is **5x5 at 1 px**; wide, coarse lattices are wasted time (a 5x5 at 2 px
  was cancelled before it ran). Do not start big searches without asking.
* **Regions**: tuning reads only some cubes. Test ranges: **baseline = cubes 2-32, signal = cubes 201-226** (read as the inclusive range 201..226, 26 cubes;
  the plan's step 8 is about that long). Later: choose steps, other roles.
* **Objectives follow the Sensorgram's settings** (fit method, order, formula, smoothing). The baseline switch is turned off for the objectives (subtracting a
  constant per ROI changes neither delta nor noise; unit-tested).
* **Three stages**: (1) the matrix (expensive, kept in memory, own file later, never `scan.h5`; may be re-used as a scan only if checking it is faster than
  scanning), (2) the user picks the objective (cheap, live), (3) maps + apply (one undo step, then the normal Fast scan makes the official numbers).
* **Thresholding / what to trust is the user's call**, to be found by tests on real data. No surface fit yet ("just points of the lattice" for now).
* **Cross-talk between panels** goes through one module (commands in, signals out), never panel to panel.

## What is built (uncommitted)

* `analysis/roi_tuning.py` (pure, no Qt): `Lattice`, `shifted_roi_set`, `neighbours_within_reach`, `TuningRegions`, `region_membership`, `lattice_objectives`
  (baseline mean, signal mean, delta = signal - baseline in nm, noise = detrended SD of the baseline region as in `step_stats`, snr = |delta| / noise).
  A shifted copy is just a ROI at another place, so the scan, the fits and the statistics are the existing ones.
* `tests/unit/test_lspri_roi_tuning.py` in the umbrella repo (26 tests, pass).
* `tools/roi_tuning_lab/lattice_scan.py`: the lab. Scans every lattice point of the selected ROIs with the real `scan_cubes` in one call, fits with
  `trace_fit.traces_from_inputs`, prints maps and a split-half summary. Read-only; `--check` compares with the stored scan.

## Results on the test dataset (314 cubes x 26 wavelengths, 160 ROIs, session 2026-10-22_172035; polynomial order 9, centroid, absorbance)

ROI geometry: sample disc 33.7 px across, ring 49.5-59.9 px, nearest neighbour 75 px: no two ROIs can interact over any lattice up to a ~28 px move.

**Plumbing is exact** (`--check`): the centre point equals the stored `scan.h5` for all 57 cubes (0 differ); a ROI shifted alone equals the same ROI shifted
together with the others (5 of 5 cubes). So one scan per lattice point serves every selected ROI.

**3x3 at 2 px, all 160 ROIs** (13,338 frames, 106.6 s with 4 processes; 6 ROIs took 13.0 s; chunk reads for every frame, busy time per frame: read 16 ms,
reduce 14.5 ms for 160 ROIs, read 2.3 ms for 6; the 1440 metric traces took 0.9 s):

| | result |
|---|---|
| typical values at the centre | delta 4.02 nm, noise 0.0342 nm |
| **noise map reproducible between halves?** (even vs odd cubes of each region) | **no**: rank correlation 0.01. The 12 % variation over the lattice is the sampling error of a ~30-cube noise estimate (expected about 13 %) |
| **delta map reproducible?** | **yes**: rank correlation 1.00; the halves pick the same best point for 318 of 320 cases |
| snr (= delta / noise) "gain" of the best point | in-sample median +8.8 % (max 39 %), **held-out +0.0 % (153 of 320 positive = a coin flip)**: this gain is selection bias |
| delta gain of the best point | in-sample median +4.3 % (max 8.2 %), **held-out +4.3 % (316 of 320 positive)**: real |
| where is the best delta? | **139 of 160 ROIs at the (+2, +2) corner**, i.e. the edge of the lattice; 7 at (0, +2), 8 at (-2, -2), 2 at the centre |
| gradient of delta over the lattice | +0.91 %/px in y (150 of 160 ROIs agree in sign), +0.30 %/px in x (135 of 160) |
| noise pooled over ROIs | the centre is the lowest by 1-3 % (standard error 0.6 %): a real but tiny trend, invisible per ROI |

What this means: position changes the **sensitivity** reproducibly and the **noise** not at all at this scale, so a signal-to-noise objective built on a noise
number from ~30 cubes mostly chases luck; tuning on delta (sensitivity) alone is reliable. The delta optimum is **beyond the 3x3 lattice for most ROIs**, and it
points the same way for nearly all of them, which looks like one array-wide offset (or a drift) rather than 160 individual ones.

**5x5 at 1 px (the maintainer's normal lattice), 20 ROIs spread over the array** (37,050 frames, 48.9 s, 4 processes; busy per frame: read 3.6 ms, reduce 1.3 ms;
`--check` again identical to `scan.h5`). The lab now prints what the maintainer asked for: per ROI and position one mean +/- std for range A and for range B,
delta with its uncertainty (`LatticeObjectives.delta_sem`) and the change against the centre in sigmas (`delta_change_sigma`); the even/odd test is the optional
`--split-half`.

* The delta surface of a ROI is smooth and almost a **plane** over +/-2 px (ROI 4: 3.74 nm at (-2,-2) to 4.06 nm at (+2,+2), uncertainty 0.007 nm each), so a
  plane gives the direction, not the peak. The largest |delta| is at the **(+2,+2) corner for 17 of 20 ROIs**, at (-2,-2) for 2 (the opposite way: not looked at
  yet), at (+1,+2) for 1.
* Median over ROIs, |delta| relative to the centre: +4.1 % at (+2,+2), +2.6 % at (+1,+1), so the gain is real (median 15 sigma) but **small, about 4 %**
  (uncertainty of one delta is 8 pm on a delta of 4 nm). The step from (+1,+1) to (+2,+2) is smaller than from (0,0) to (+1,+1), so the surface may be
  flattening; extrapolating those two steps puts the peak near (+3, +3) px with roughly +4.6 % (a guess from two points, not a measurement).
* The std of range A: pooled over ROIs it changes by -0.4 to +3.5 % over the lattice (standard error 1.3 %); the largest change per ROI is 12.3 %, the size
  chance alone gives for a std from 31 cubes (~13 %). Position does not measurably change the noise.

**7x7 at 2 px (reach +/-6 px), 8 ROIs** (4 and 68, 84, 100 = the ones that behaved differently in the 5x5, plus 20, 45, 120, 150; 72,618 frames, 64 s). Small pool: treat
the medians as indications.

* **The delta surface is not one hill.** Common to the ROIs is a bump near (+2, +2..+4) of about +4 %. Further out the surfaces turn ROI-specific and rough
  (neighbouring 2-px points differ by 1-3 % with 0.2 % uncertainty each); the largest |delta| of 5 of the 8 ROIs is at the edge or corner of the lattice
  ((-6,-6), (-4,-6), (-6,+6)), where delta is +5 to +12.5 % above the centre.
* **The noise sets the limit, not the signal.** Std of range A relative to the centre, median over the 8 ROIs by distance: 0 px 0 %, 2 px +0.4 %, **4 px +4.8 %,
  6 px +25 %**. At the edge/corner positions with the largest delta the std is +21 to +51 % higher, so delta / std there is 11-30 % *worse* than at the centre.
  Inside +/-4 px the best delta position is +2 to +7 % above the centre with the std within about +/-5 % (ROI 4: -4,+4 +4.1 %; 68: -4,-4 +3.7 %; 84: 0,-4 +5.6 %;
  100: +2,+4 +5.6 %; 120: 0,+4 +6.8 %; 150: +2,+2 +4.2 %). Individual ROIs can still lose: 20 and 45 have +16 and +22 % std at (+2,+2), which is within
  1-2 sigma of the 13 % chance scatter of one std, so not conclusive for one ROI.
* So an objective on signal alone drives ROIs to the edge of whatever lattice is offered (its measured response rises while the measurement gets worse). Needs
  either a cap on the shift (about 4 px on this data) or the noise in the objective (decision pending with the maintainer, who so far prefers "every rise of signal is
  beneficial"). A global quadratic would also fit these rough surfaces badly beyond about +/-4 px: fit locally.

**Objective decided (maintainer, 2026-10-28): maximise S/N = |delta| / noise** ("signal is always accompanied by noise"; no shift cap needed for the argument, but see below).
Offline check on the saved arrays (no new scan; script in the session scratchpad, to be moved into the lab if kept): S/N with the **raw** per-position std of range A is
unreliable. On the 7x7 / 8 ROIs the raw pick promises +23 % median S/N, but judged with a **smooth noise surface** (per ROI, a quadratic fitted to log std over the
lattice) the same positions give -2 % (no cap) / +11 % (cap 4 px): most of the raw gain is a lucky std of 31 cubes (+/-13 %). Raw and smoothed picks coincide for 1-2 of
8 ROIs. With the smoothed noise the median S/N gain is +3.6 % (7x7, no cap; positive for 6 of 8; delta +3.3 %, noise -0.6 %) and +5.8 % (5x5 at 1 px, 20 ROIs; positive for
20 of 20; delta +2.8 %, noise -3.9 %). In every lattice most picks sit at the **edge of the lattice** (18 of 20, 6 of 8 with the cap), so one lattice does not find the
maximum: the practical algorithm is to iterate (scan a lattice, move to the best point by a limited step, repeat until the best point is the centre). The cap-4 figure
(+16.7 %, noise -11 %) comes from a quadratic noise fit whose picks are at the fit's edge: do not trust it.

## Open questions

1. **Is a bigger delta really better?** A ROI that moves where delta is largest could be picking up an artefact (the stage drifted between cubes 2-32 and
   201-226, 300 s to 2000 s later; background scattering that changes with the solution). Check against the images before trusting the direction.
2. **Next lattice**: re-centre on the best point and repeat (coarse to fine; 5x5 at 1 px, the maintainer's normal size) instead of one wide lattice. Needs a
   lattice centre offset (not built; `Lattice` is centred on the ROI by design).
3. **Whole-array mode**: because the direction is shared, a common (dx, dy) estimated from a subsample of ROIs (e.g. 20: 13 s per 9 points for 6 ROIs) is cheap
   and far less noisy than per-ROI picks.
4. Noise needs a different (longer, or pooled) estimate before it can enter an objective per ROI.
5. Not built: surface fit (quadratic, with the guards from the design talk), module, UI (region picking on the Sensorgram, maps, Image overlay), apply + undo,
   separate result file, the faster read-once multi-offset path (the tile-cache prototype in `tools/scan_subset_lab/proto_tile_cache.py` showed it is exact).
