# ROI position tuning: build plan (2026-10-28)

Results and decisions so far: `roi_position_tuning_2026-10-28.md`. This file is the plan for the app feature, written before coding (GUI rule: describe a new
screen first). Engineering priorities served: 1 (a ranking is only shown with the numbers behind it; checked against the normal scan), 2 (nothing is written to
`scan.h5`; ROI positions change only on an explicit, undoable Accept), 3-4 (pure maths apart from Qt, tested without a window).

## The idea in the maintainer's words (2026-10-28)

The tool does not have one fixed objective. It **establishes the values for every position**; a pick-up menu in the ribbon chooses what to rank by (signal |A-B|, std,
S/N with smoothed noise, or anything else we add). The Image area then shows, per tuned ROI, the lattice of centres as crosses coloured from worse to best, and the
ROIs drawn as if they stood at their best position. A last button accepts and rewrites the ROI positions. Iterating (re-centre and repeat) is not needed now.

## Three stages, three owners

1. **Matrix** (heavy, once per Run, objective-free): for the selected ROIs, every lattice position, the cubes of ranges A and B: scan with the real `scan_cubes`
   (shifted copies of the ROIs, exactly as `tools/roi_tuning_lab/lattice_scan.py` does), fit with the Sensorgram's metric in the helper process
   (`trace_pool.TracePool`, never in a thread next to the GUI), keep the **traces** `[position, roi, cube]` plus what they were made with (lattice, ranges, ROI positions,
   metric / formula key). **Kept in RAM** (maintainer's last word on 2026-10-28: simpler than a file; an earlier answer asked for a file), never in `scan.h5`; a ribbon **Export** icon writes the numbers out for anyone who needs them (format still to agree). What it holds: section "Second round" below.
2. **Values** (cheap, live, on a change of the menu): `analysis/roi_tuning_objectives.py` turns the traces into one number per (position, ROI) for the chosen
   objective, oriented so that larger is better, plus the best position per ROI. Switching the objective, the ranges inside the scanned cubes or the statistics settings
   never rescans.
3. **Show and accept**: panels only display the module's answers and send commands; "Accept" moves the ROIs through the ROI toolbox as one undo step.

Cross-talk goes through one module, `RoiTuningModule` (a `QObject`): commands in (`set_lattice`, `set_ranges`, `set_objective`, `run`, `cancel`, `accept`, `clear`),
typed signals out (`state_changed`, `matrix_changed`, `view_changed`). The ribbon tab, the Image overlay and the Sensorgram's range bands all talk to the module, never to each other.

## Objectives (a registry, one entry each: key, label, tooltip, function, higher-is-better)

| key | what it ranks | direction |
|---|---|---|
| `signal` | \|mean B - mean A\| | higher |
| `noise` | std of range A | lower |
| `snr_raw` | \|delta\| / std of range A at that position | higher |
| `snr_smooth` (initial choice) | \|delta\| / a smooth noise: per ROI, a quadratic in (dx, dy) fitted to log(std of A) over the lattice | higher |
| later | detrended noise, delta / its uncertainty, ... | |

Why `snr_smooth` exists: the std of range A from ~31 cubes is only known to +/-13 %, so `snr_raw` mostly picks a lucky noise value (7x7 lattice, 8 ROIs: +23 % promised, about
0 to +11 % real; with the smoothed noise +3.6 %). The menu shows both so the user can see the difference. Scores are shown normalised inside each ROI's own lattice.

## Screen: the "Tuning" ribbon tab of the Image panel (after "Analysis")

```text
Screen purpose: choose how far and over which part of the experiment to try other centres for the selected ROIs, run the trial, see which centre is best by a chosen
  criterion, and accept the new centres.
Main user actions: select ROIs (existing selection) -> set lattice and ranges -> Run -> pick "Rank by" -> look at the crosses and the preview -> Accept (or Clear).
Layout (one ribbon row, captioned groups, like the Analysis tab):
- Left (ribbon):   [Run / Stop] | [Lattice: size 3,5,7,9 | pitch px] | [Range A: from - to] | [Range B: from - to] | [Rank by: pick-up] | [Crosses] [Preview] | [Accept] [Clear]
- Center (Image):  per tuned ROI, crosses at the lattice centres coloured worst -> best (a perceptually even ramp that also reads on grey images; the best cross is larger),
                   the proposed ROI circles and rings drawn dashed at the best centres while Preview is on; the real ROIs stay where they are until Accept.
- Right (Sensorgram): ranges A and B as light bands on the plot (read from the module; dragging them is a later step).
- Bottom/status area: the task indicator row (progress, ETA, Cancel); the tab's "i" text: what the last run did, how many ROIs would move, how far.
Important states: no dataset; no ROIs selected; running (Run is Stop, the other controls are disabled); matrix ready; matrix out of date (ROI geometry, masks, chromatic,
  background, frame roles, metric or formula changed after the run: crosses dimmed, Accept disabled, Run enabled); nothing would move (best = centre for all).
Validation rules: >= 1 selected ROI; no free-form (mask) ROIs among them (skipped, said so); not with a ROI geometry timeline (refused in this version); lattice size odd;
  pitch > 0; each range's first <= last, both made of cubes of the dataset; A and B do not overlap; before Run the tab says "N positions x R ROIs x C cubes = F frames,
  about T s" from the rate measured on this machine, and asks before anything over a few minutes.
Error messages: plain sentences in the status bar and the "i" text ("Select at least one ROI first.", "Range A and range B overlap.", "ROI 12 is a free-form ROI and cannot be
  tuned by moving its centre.", "The ROIs changed after the trial; run it again.").
```

Every control is remembered (`ui_state.bind` / `storage/ui_state_keys.py`): lattice size and pitch, both ranges, "Rank by", Crosses, Preview. A range is a cube number pair as the Sensorgram shows them.

## Build order (each step testable on its own; I stop for a look after D1 and D2)

*Changed 2026-10-29: there are no separate "ranges". A range is a step of the experimental plan, and the Tuning tab takes the **baseline step** and the **signal step** from the plan module instead of having its own cube boxes. The plan gets what it lacks (add / remove / unify steps, baseline / signal roles, a tab, a step list shared with other panels) first: `plan_steps_as_ranges_2026-10-29.md`. A first attempt with a separate `ranges/` module was built on 2026-10-28 and removed again; it was never committed. Everywhere below, "range A / range B" means "baseline step / signal step".*

* **C1** `analysis/roi_tuning_objectives.py` (pure): the registry above; the smooth noise; best position and 0..1 scores per ROI; tests with planted optima. Moves the logic out of the scratchpad script.
* **C2** `analysis/roi_tuning_run.py` (pure function with `progress` / `cancelled`): lattice scan + traces -> `TuningMatrix`; integration test that the centre position equals the normal scan and that a ROI shifted alone equals it shifted with the others.
* **C3** `RoiTuningModule` + `RoiTuningTask(ThreadedTask)`: the typed signals, staleness rules, task-indicator wiring in `app_rewrite.py`, `invalidate()` on dataset / session change.
* **D1** the ribbon tab and its wiring (controls only, remembered, button tests with `.click()`).
* **D2** the Image overlay (crosses, preview circles) and the Sensorgram range bands.
* **E** Accept: a small public method on the ROI toolbox for many different positions in one undo step (`_set_positions` is private; `move_roi` moves one ROI), tests, docs.

## Cost (measured with the lab, 4 processes)

25 positions (5x5) x 20 ROIs x 57 cubes = 37,050 frames in 49 s; 49 positions x 8 ROIs: 72,618 frames in 64 s; 9 positions x 160 ROIs: 13,338 frames in 107 s (so 160 ROIs at 25 positions would take
roughly 5 min: an extrapolation). Reading dominates (3.6 ms read vs 1.3 ms reduce per frame for 20 ROIs) and each position reads the same chunks again; reading each frame once and
cutting all lattice positions from it is the next speed-up (the tile-cache prototype was exact), not part of the first version.

## Not in the first version

Iterating; a "minimum gain" threshold (the maintainer will decide it after tests); tuning other things than the centre (diameters);
a ROI geometry timeline; the read-once kernel; the drift check of the delta direction against the images (open, see the results record).

## Second round: the maintainer's answers to this plan (2026-10-28)

1. **The matrix stays in RAM, with an Export icon in the ribbon** (final answer; the first answer was "a separate file", the second "maybe just RAM, simpler"). It holds the **scan-level numbers** (mean, STD and counts per position, ROI, cube, wavelength, side, the same arrays as `scan.h5`) and not only the fitted traces, because then (a) a change of the Sensorgram's fit method or formula re-ranks without a rescan, (b) the numbers of the accepted position can be copied into `scan.h5` (see 4). Estimated size in RAM, 160 ROIs x 25 positions x 57 cubes x 26 wavelengths x 2 sides: about 50 MB for the means alone, about 140 MB with STD and counts (rough, to be measured). Consequence of RAM: closing the app or switching the dataset or session drops the matrix and the crosses; Accept then needs a new Run. It records what it was made with (ROI geometry of the tuned ROIs, ranges, lattice, scan settings fingerprint) so the tab can say "out of date". The Export icon's format is still to agree (a CSV of the objective values per ROI and position is the obvious start).
2. Cross colours not red-green; Accept gets a new public toolbox method (many positions, one undo step): agreed.
3. **The Image area gets a toggle: Original / Best** (instead of a Preview switch): the ROIs are drawn at their original or at their best centres; the crosses stay.
4. **"There is no recomputation."** Confirmed by the maintainer ("Yes, you can add those in data, so they are not recalculated again"): the numbers of the best position are already in the matrix, so Accept recomputes nothing for the cubes in the ranges: their scan cells are written into `scan.h5` from the matrix, stamped with the hash of the moved ROI. Cubes outside the ranges go out of date as after any ROI move (hollow rings) until the normal Fast scan. Needs the matrix to hold scan-level numbers (1) and the written cells to carry exactly the hash a real scan of the moved ROI would give (a test must pin that: seeded cell == scanned cell, byte for byte).
5. **Ranges are the plan's steps (decided 2026-10-29, replacing the first answer, which asked for a separate ranges section).** Each step already has an order, a note, a colour, and can be dragged and resized; the Tuning tab takes the baseline and the signal step from the plan module. On the test dataset the maintainer's ranges are plan steps: cubes 2-32 sit in step 1 (cubes 1-32, "H2O") and 201-226 in step 8 (cubes 201-227, "70perEG ... H2O"), a cube straddling a boundary belongs to neither step.

## Built 2026-10-29: C1 to C3 (the maths, the run, the module; no screen yet)

All pure or headless; nothing is visible in the app until D1/D2. Tests: `tests/unit/test_lspri_roi_tuning_objectives.py` (29), `tests/integration/test_lspri_roi_tuning_run.py` (20), `tests/integration/test_lspri_roi_tuning_module.py` (25); the shared synthetic dataset is `tests/_lspri_scan_data.py`.

* **C1** `analysis/roi_tuning_objectives.py`: the registry (`snr_smooth` default, `snr_raw`, `signal`, `noise`), `smooth_noise` (per ROI, `exp` of a quadratic fitted to `log(noise)` over the lattice; needs 6 usable positions that determine the surface, otherwise the ROI has no value and is not moved), `rank` -> `Ranking` (values, best position per ROI, 0..1 scores per ROI, improvement over the centre).
* **C2** `analysis/roi_tuning_run.py`: `run_lattice_scan` (scan-level numbers of every position in RAM: mean, std, three pixel counts, plus the unmoved cell hashes), `compute_traces` (fits through a runner = `TracePool.run`, or in the caller's thread for tests), `objectives_from_traces`, `estimate_cost`, `check_tunable` (plain sentences). Pinned: the centre position equals the ordinary scan number for number; every position equals a scan of a ROI table with those ROIs moved; a ROI shifted alone equals it shifted with the others (when no pair interacts); the same numbers with 1 and 2 worker processes.
* **C3** `analysis/roi_tuning_task.py` (`RoiTuningTask`, scan + fits off the GUI thread, fits in the helper process), `roi_tuning_module.py` (`RoiTuningModule`: lattice, objective, crosses / Original-Best switches, run / cancel / clear / invalidate / mark_stale / refit / export_csv, `proposed_centres()`), `roi_tuning_export.py` (CSV, one row per ROI and position).

### Decisions I made without asking (say if any should change)

1. The noise in `snr_*` and `noise` is the **detrended** SD of the baseline step (`StepStats.noise`, as `LatticeObjectives.snr` already used), not the plain SD of range A the 2026-10-28 offline check used. The numbers differ a little when the baseline drifts.
2. A ROI moves only for a **strictly better** position: a tie keeps the centre.
3. All selected ROIs are shifted **together**, one scan per position. Where two ROIs are close enough to touch each other's reference ring (`interacting_pairs`), the numbers of those are approximate; the module says so in its message. Tuning those ROIs one at a time is not built.
4. The result goes **stale** on any change that can alter a scan number (the same signals the scan results module watches), without comparing hashes: safe but strict. `base_hashes` is stored so a precise check (and E, Accept) can use it later. A change of fit method, order, formula metric or smoothing settings **re-fits** in the background instead (no scan). A change of the formula key arrives through the detection settings and so marks the result stale.
5. A trial needs a baseline step of **at least 3 cubes** (a noise needs it) and two different steps; the message names the step.
6. A stopped trial returns nothing (a partial matrix would rank positions on unequal data).
7. No limit on the pitch; the lattice sizes offered will be 3, 5, 7, 9 (`LATTICE_SIZES`); default 5 x 5 at 1 px.
8. A template-shift metric needs its reference cubes among the cubes of the two steps; where they are not, its traces are gaps (no ranking for that ROI).
9. Export is a CSV (UTF-8, comma): `roi_tuning_export.COLUMNS`. Format not agreed with the maintainer.

### Built 2026-10-29: D1, the Tuning ribbon tab (controls only, no overlay yet)

`panels/image/tuning_tab.py` (`TuningTab`, display only: buttons emit requests), added to the Image panel's ribbon after Analysis (`TUNING_TAB`, its "i" text is live), wired in `app_rewrite._wire_roi_tuning` (`_Modules.roi_tuning`, task-indicator connects, Stop through the indicator's Cancel, `invalidate()` on dataset / session change, `mark_stale()` from the scan-staleness signals, the export file dialog). Groups: **Try** (play, the same button is Stop while it runs, never greyed out: its tooltip says what it would do or why it cannot) | **Lattice** (3 x 3 .. 9 x 9, pitch px) | **Rank by** (drop-down) | **Show** (crosses, star = Original / Best) | **Result** (clear, export). Every control is remembered (`tuning/lattice_size`, `tuning/pitch_px`, `tuning/objective`, `tuning/show_crosses`, `tuning/show_best`). Tests: `tests/integration/test_lspri_roi_tuning_tab.py` (23: the tab alone, the wiring, a whole trial through the buttons, export, Clear, out of date, dataset closed).

* **Accept is not on the tab** (it is step E; a button that does nothing would mislead).
* Try is refused with a sentence while an analysis scan or a session load is running.
* The result can be looked at after it went out of date (Clear and Export stay on); the overlay (D2) will dim its crosses.
* Found while testing: a trial whose steps give no usable numbers said "the current centre is the best"; it now says that no ROI could be ranked.

### Built 2026-10-29: D2, the Image overlay (crosses and ROIs at their best centres)

`panels/image/tuning_overlay.py` (`TuningOverlay`: one pyqtgraph scatter item of '+' crosses, drawing only), `ImagePanel.set_tuning_view` (+ `_with_tuning_best`, `_tuning_best_key`), `RoiTuningModule.view()` -> `TuningView` (plain arrays + a `revision`), wired by `_wire_roi_tuning(show_view=image_panel.set_tuning_view)`. Tests: `tests/integration/test_lspri_roi_tuning_overlay.py` (20), plus one in the tab tests.

* **Crosses:** one per tuned ROI and tried centre, at the ROI's centre + the offset carried to the frame on screen by the same chromatic affine as the ROI circles; viridis ramp, worst (dark purple) to best (yellow) *inside each ROI's own lattice*; the best centre of each ROI has a larger cross; grey = no value; faint (35 %) while the result is out of date. Clicks pass through to the image.
* **Star (Original / Best):** with the star on, the tuned ROIs' circles, rings, labels and selection ring are drawn at their best centres (their display centres move by the affine's image of the shift). **The ROI table is never touched**; only the drawing moves. Costs nothing while no trial exists (one `None` check per overlay draw). The ROI overlay's layer cache includes the view's revision in its epoch, so a cached picture of the old positions is never shown.
* Not looked at: the star moves the ROIs of the *current* ROI table by the stored offsets; if a ROI was moved by hand after the trial the result is out of date and the crosses are faint, but a star-on drawing still shifts the ROI from where it now is, not from where the trial started it.
* Crosses and the star are hidden together with the ROI overlay while the rotate / crop preview is on (ROI positions mean nothing over the uncropped picture).

### Built 2026-10-29: E, Accept (move the ROIs, copy the trial's numbers into scan.h5)

`analysis/roi_tuning_accept.py` (`accepted_moves`, `seedable_columns`, `seed_scan_file` -> `SeedReport`), `RoiToolbox.move_rois` (new public method: different positions for many ROIs, one undo step, one signal, all-or-nothing, 0.1 px rounding), `RoiTuningModule.accept(store_path, label)`, `RoiTuningTask.seed` (the file write runs on the task thread), `ScanStore.prepare(create=False)`, the check-mark **Accept** button on the Tuning tab (live only for a current result in which a ROI would move), wired in `_wire_roi_tuning` (refused while an analysis scan runs or the session loads; the scan results module is told to reload afterwards and does not look at the file while the task runs). Tests: `tests/integration/test_lspri_roi_tuning_accept.py` (26) plus Accept tests in the tab tests and rounding tests in `test_lspri_roi_tuning.py`.

**The rule the maintainer set ("there is no recomputation") and how it is kept exact.** A seeded cell must be the cell a real scan of the moved ROIs writes. The test scans the moved layout for real into a second file and compares, array for array: mean, std, the three pixel counts, the cell hash, the recipe and the layout text, for the moved ROIs in the cubes of the two steps; every other cell of the file is shown unchanged; and the cells a Fast scan would still have to do are exactly the moved ROIs in the cubes outside the two steps. What makes that true, and what refuses it:

1. **The trial scans the centre the toolbox will store.** ROI centres are stored rounded to 0.1 px. `roi_tuning.moved_centre` does the same rounding (a shift of (0, 0) is no move: nothing rounded), and `shifted_roi_set` uses it, so the position that was measured is the position that is stored. (This changes `shifted_roi_set` for shifts that are not multiples of 0.1 px: the lab's earlier numbers at 1 and 2 px pitch are unaffected.)
2. **Neighbours.** A ROI's numbers depend on its own pixels and on the sample discs of ROIs near its reference ring. ROIs that are in an interacting pair (`TuningMatrix.interacting_pairs`) are moved but their numbers are **not** copied: they are scanned the normal way.
3. **What the trial was made from still holds.** Before the move the module takes a snapshot of the app; the task recomputes the cell hashes of the *unmoved* layout from it and compares them with `TuningMatrix.base_hashes`. Any difference (a mask, chromatic correction, background, a ring diameter ... changed after the trial) and **nothing is written**. This is the precise check that the "stale" flag only approximates.
4. **The file is only added to.** `ScanStore.prepare(create=False)` raises for a missing or incompatible file and leaves it exactly as it is (the normal `prepare` would back an incompatible file up and create a new one). Tested: no file is created, a file with another ROI axis keeps its bytes and gets no backup.
5. The cells are written through `ScanStore.write_batch` (the crash-safe order the scans use), stamped with the hash of the moved layout taken from a snapshot **after** the move.

Sequence at Accept: refuse if a trial runs, the result is out of date, nothing would move, a ROI geometry timeline exists, or a tuned ROI is not where the trial started it (marks the result out of date) -> snapshot before -> `move_rois` (one undo step: Edit > Undo puts the ROIs back) -> snapshot after -> clear the trial -> write the numbers on the task thread -> scan results reload. Without a scan file the ROIs still move and the message says a Fast scan computes them. **Undoing the move makes the seeded cells out of date** (they were made for the new centres): it costs a scan, never a wrong number.

Things I decided without asking: a ROI that is moved but sits next to another ROI is not seeded (see 2); a result whose trial inputs changed after the trial still moves the ROIs when Accept is pressed (the move is what you asked for) but writes no numbers; Accept spends the trial (crosses and result are cleared); the Accept button is the check-mark icon.

### Built 2026-10-29: interpolating the best position between lattice points (settings pop-up, "Interpolate", off by default)

`roi_tuning_objectives.refine_best` (pure, vectorised: one matrix product for all ROIs, 0.16 ms for 160 ROIs), `rank(..., interpolate=)`, `Ranking.subpixel / refined / edge`, `RoiTuningModule.set_interpolate`, a switch in the Tuning settings pop-up (remembered `tuning/interpolate`). Tests: `RefineTest` and `RankInterpolateTest` in `test_lspri_roi_tuning_objectives.py`, interpolation cases in the Accept, module and tab tests.

* **Method.** Around the best lattice point a quadratic `c0 + cx x + cy y + cxx x^2 + cxy x y + cyy y^2` is fitted by least squares to the 3 x 3 lattice values (the window is pushed inward at the edge of the lattice) and its top is taken. A planted tilted peak is found exactly (tests). The objective that is interpolated is the one chosen in "Rank by" (oriented so that larger is better).
* **When the lattice point is kept (no interpolation):** a lattice smaller than 3 x 3; a missing value in the window; a surface with no top (not curved downward in both directions: a ramp or saddle - the usual picture when the best point is at the lattice edge because the peak is beyond it); a top more than one pitch from the best point (outside the fitted window: extrapolation) or outside the lattice. A step smaller than 0.05 px is no move (ROI centres are stored to 0.1 px).
* **Consequence for Accept:** an interpolated centre was never scanned, so there are no numbers to copy. Accept moves the ROI there (rounded to 0.1 px like every move) and **does not seed its cells**; the next scan computes them (`seedable_columns` leaves it out, `SeedReport.not_saved` names it; the field was `skipped_interacting`). The other moved ROIs are still seeded and still bit-identical to a real scan (tested with one interpolated ROI beside two seeded ones).
* **Edge warning (new, always on):** the status line says how many ROIs have their best centre on the outer ring of the lattice ("the peak may lie further out"). Your own measurements (17 of 20 ROIs best at the (+2,+2) corner) say this is the common case on the test data, and interpolation cannot help there: a bigger lattice or a re-centred one can.
* The scores, the improvement and the crosses still describe the measured lattice; with the star on, the ROI is drawn at the interpolated centre.

### Not built yet (after E)

The "ask before a long run" step (the cost is known before the run: `estimate_cost`; the module's `preview()` shows positions x ROIs x cubes but no time yet).
