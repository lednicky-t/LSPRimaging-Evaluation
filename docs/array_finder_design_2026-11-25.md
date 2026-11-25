# Array finder ("Find*"): fill in an array from the image, guided by ROIs (design, 2026-11-25)

Status: design agreed with the maintainer the same day; **built the same day, tested offscreen, not yet seen by eye** (see "What exists" at the end). The old Array group
(`array_controls.py`, Auto / Semi / Manual) and the manual Array panel stay untouched; the maintainer wants to **compare the old and the new
finder** before deciding which to keep. So the new code shares no logic with `roi/array_detection.py` (that module only gets thin public aliases).

## What it is

A second button in the ROIs tab's Array section opens a pop-out like the manual one. The user guides with 0, 1 or 2 selected ROIs or an area
selection; the tool finds the similar spots in the image, works out whether they form a lattice and shows the array as ghosts (live). Apply makes
the ROIs (one undo step), Cancel makes nothing. The guides narrow the search; the image decides.

| Guide | Used as |
|---|---|
| none | the most common similar feature (spot-size scan, as Detect does) |
| 1 ROI | template (size, shape, contrast) and a first lattice guess from its neighbours |
| 2 ROIs | the same template (mean of the two); by default also the **extent**: the corners of the array (a switch) |
| area selection | its blob is fitted as a circle or rectangle; a poor fit (overlap < 0.9) becomes a pixel mask |

## Pipeline: each step is a gate with its own number (the status line shows the counts: "212 similar -> 196 on lattice -> 190 placed")

1. **Candidates** (`roi/array_finder.py`): normalised cross-correlation of the template with the contrast map (spots positive), at a few
   scales unless the size is fixed. **Similarity** = the correlation a peak must reach; **strength** (advanced) = contrast relative to the seed.
2. **Lattice** (`roi/array_lattice.py`): robust fit of two lattice vectors to the candidates (hypotheses from neighbour vectors, scored by how many
   candidates sit within **position tolerance** x pitch of a node; refitted by least squares). Rectangular, triangular and oblique lattices are all
   "two vectors"; a single row is a 1-D lattice. Similar spots off the lattice are reported as strays (drawn, never made).
3. **Completion**: nodes inside the extent (corner ROIs, else the box of the members) with no spot are **missing**: left out by default, a switch
   places them at the lattice position.
4. **Form**: size per spot (`edge_size`, as Detect does) or the seed's size (**fix size**), within **size tolerance** of the trend; shape = the
   seed's type (**fix type**) or circle / rectangle / pixel mask by overlap with the spot's blob.

## Decisions (maintainer, 2026-11-25)

- Separate icon + pop-out in the Array section (not a mode of the manual panel).
- Missing nodes: leave out by default; a toggle in the pop-out places them.
- Keep both finders until compared.
- All four phases wanted.

## The mask (added 2026-11-25, maintainer: "one spot was not detected because of damage, but the damage is masked")

Switch **Ignore masked pixels** (Advanced, default on; `FinderSettings.use_mask`). The mask in force at the reference frame (`MaskModule.resolve_mask_source`, brought to the frame
by `resolve_external_mask`, as the Image panel and the scan do) is treated like missing data when the picture is prepared: masked pixels are **filled in smoothly from their
surroundings** (`fill_invalid`) before the contrast map and the matching. They are not "outside the image": a spot with masked pixels in it is still searched for, sized and placed.
The mask is part of the search key (a changed mask or the switch is a new search; a slider is not). Synthetic test: a lump over a spot, masked: found with the switch on, missed off.
Limit: the fill is a plain smooth average, so a mask covering most of a spot leaves too little of it to match (the test with a lump bigger than the spot still misses it); the old
Detect does not read the mask at all.

## Known limits / honest uncertainty

- A ROI on the wrong spot silently skews a measurement: nothing is made without Apply, strays and missing nodes are drawn differently, Apply is one undo step.
- The seeds stay where they are (they are the user's ROIs); the new ROIs are placed by the image, so a seed that is 1 px off its spot is 1 px off its neighbours.
- One lattice per run. Several points per cell (dimers, honeycomb) need a basis: not built (same as the manual Array).
- Thresholds (similarity 0.6, strength 0.35, position 0.05 x pitch, size 0.25) are first guesses until compared on real images.

## What exists (2026-11-25)

| Piece | File |
|---|---|
| Lattice fit (pure) | `roi/array_lattice.py` |
| Candidates + the array (pure) | `roi/array_finder.py` (`find_candidates` slow, `build_array` quick) |
| Blob -> circle / rectangle / pixel map (pure) | `roi/shape_guess.py` (overlap 0.85) |
| New ROIs + recipe (pure) | `roi/array_found_plan.py`; command `RoiToolbox.place_found_array` (shares `_commit_array_plan` with `stamp_array`) |
| Threads (search, build) | `roi/array_find_task.py` (joined to the status-bar task indicator) |
| Pop-out, outlines, wiring | `panels/image/array_find_panel.py`, `array_find_ghosts.py`, `array_find.py`, `popout_placer.py`; button in the Array section (`roi_tab.add_array`) |
| Remembered settings | `ARRAY_FIND_KEYS` in `storage/ui_state_keys.py` |

Tests: `tests/unit/test_lspri_array_lattice.py`, `..._array_finder.py`, `..._roi_found_array.py`, `..._shape_guess.py`, `tests/integration/test_lspri_array_find.py`; synthetic images
from `tests/_array_synth.py`. `RoiToolbox` budget raised on purpose (2,500 lines / 114 methods; reason in the budget test).

**Not built (honest list):** the no-guide *survey* (a list of the feature families found, to pick from: with no guide the finder takes the one the size scan finds, as Detect
does); per-spot choice of circle / rectangle / pixel map inside one array (the shape is the guide's, or circles); a lattice whose pitch drifts (a gradient array: the
tolerance is to one constant lattice); several points per cell; re-editing a found array from its recipe (rows, columns, pitch are stored, no spec).

## Old Detect vs Find* on real images (2026-11-25, `04_Bulk_sensitivity...`, rotated zarr, cubes 0 / 150 / 313 x wavelength indices 1, 5, 10, 15, 20, 25 = 18 planes)

Both on Auto (no ROI). Scripts were scratch (`compare_real.py`, `compare_detail.py`, `compare_centres.py`).
- **Found:** both give the 10 x 16 lattice, pitch 80.4 -> 79.4 px with wavelength and the same tilt (< 0.05 deg), the same sample diameter (31.8 - 35.3 px vs 31.9 - 35.2).
  Find* made 153 - 156 ROIs, Detect 160 (4 - 7 of the new tool's empty nodes are the top row, cut by the image edge: their measured size is off by more than 25 %, so they are
  strays, not members; "Fill empty nodes" puts a ROI on them, as Detect does with `inferred`).
- **Time (this laptop, Auto):** Find* 1.5 - 6 s search + 0.5 - 2 s build; Detect 2.4 - 6.4 s. With a ROI as the guide the search is ~0.1 s (synthetic 480 x 360; not timed on real data).
- **Centres:** Detect's and Find*'s centres differ by a median 0.3 - 1.6 px (largest at the shortest wavelengths). Against an independent third estimate (the centroid of each spot's
  half-maximum blob, 152 and 155 spots at wl 1 and 16): **Find\* rms 0.33 / 0.36 px (mean offset 0.1 px), Detect rms 1.95 / 1.03 px (mean offset -0.44, -0.78 px at wl 1).** So on this data
  Find*'s centres are closer to the middle of the spot; caution: the centroid is one more estimator, not ground truth, and the spots have a bright lobe at the lower-right rim (section 2 of
  `roi_feature_detection_2026-10-29.md`) that moves every estimator a little. One dataset only.
- **Danger found on synthetic data:** a round smudge of the spot's size correlates 0.94 with the template; only its position (3.6 px off the lattice, within the 4.4 px of the default 0.1 pitch tolerance)
  separates it. A tolerance of 0.1 of the pitch (4.4 px there) still takes it in, so the default is **0.05 of the pitch** (2.2 px there, 4 px on the real 80 px pitch; the old Detect used 0.25); the real arrays here
  scatter 0.5 - 0.8 px rms, so 0.05 loses no real spot (checked below). The Strict - Tolerant slider moves it between 1 % and 16 %. To be set from more data.
