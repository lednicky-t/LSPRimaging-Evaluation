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
