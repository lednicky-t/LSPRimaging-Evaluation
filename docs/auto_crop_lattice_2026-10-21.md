# Auto crop to the ROI lattice (2026-10-21)

Maintainer request: next to **Auto rotate** in the Image ribbon's Image tools tab, an **Auto crop** in the Crop
group that crops the image so the export's chunk grid matches the distances between the ROIs (the array lattice).
Rule, as stated: "crop so the outside from the centre of the edge ROIs would be half of the lattice constant (half
of chunk size)". Goal: minimise the exported data and make the chunks line up with the ROI array.

A first version levelled the image and ran the array detection itself. The maintainer then asked for the opposite
split: **Auto crop does the crop and nothing else**, and the three things it needs become visible **dependencies**
the user runs themselves ("So user needs to autorotate first, it will give him check mark there, then needs to
find ROIs, another, and then needs to do CC which will give him all, making this function green").

## What was built

| Piece | Where |
|-------|-------|
| The crop arithmetic and the lattice tilt (pure, no Qt) | `dataset/chunk_plan.py`: `lattice_crop()` -> `LatticeCrop`, `lattice_tilt_deg()` |
| The crop itself + the dependency check | `panels/image/array_actions.py`: `auto_crop()`, `auto_crop_readiness()` -> `AutoCropReadiness` |
| Button, tooltip checklist, pick-up menu | `panels/image/transforms_settings.py`: `set_auto_crop_state()`, `_refresh_auto_crop()` |
| Wiring (state in, navigation out) | `panels/image/panel.py`: `_on_auto_crop_readiness`, `_on_transforms_navigate`; `panels/image/roi_tab.py` forwards |

Behaviour, as agreed:

- **Green** (all three dependencies met): a click crops. One undo step (`GeometryModule.set_crop`'s own "Crop"
  entry), nothing else touched - no rotation, no ROIs placed.
- **Amber** (anything missing): a click **does not crop**; it opens a pick-up menu listing **only the missing
  steps**, each row leading to the tool that does it. "Level the array" runs Auto rotate in this same tab; "Place
  the ROIs" and "Fit the chromatic correction" emit `navigate_requested`, and the Image panel shows that ribbon
  tab (`ToolRibbon.set_category`).
- The **tooltip** always lists all three with a check mark or a cross, plus the measured tilt when there is one,
  and ends with "Click to crop." or "Click to see what is still missing."
- `ArrayActions.auto_crop()` itself still crops whenever it can: the gate is the button, not the action. A caller
  that asks for a crop gets one, or a status line saying why not.

Every dependency is **measured, never remembered**, so all three survive a session reload and follow later edits:

| Dependency | How it is judged |
|------------|------------------|
| Array levelled | `chunk_plan.lattice_tilt_deg` on the ROI centres as they are now, within `LEVEL_TOLERANCE_DEG` (0.2°); with no lattice to measure, what Auto rotate last reported (below) |
| ROIs placed | `RoiToolbox.rois()` is not empty |
| Chromatic correction | the correction is on **and** `ChromaticModule.models()` has fitted models |

The tilt is measured from the ROI centres rather than read from `rotation_angle_deg` or from the tilt the
detection stored on the array group: an array that is already level at 0° then earns its tick, and a rotation made
after the ROIs were placed cannot leave a stale one.

### The level tick before the ROIs exist (fixed 2026-10-21)

First cut: "level" could only be judged from the ROI lattice, so it stayed crossed through the whole first step of
the maintainer's own order of work - Auto rotate runs *before* any ROI is placed. Reported as "the leveling array
is still present even though I run it", and it was the design, not a crash: there was no lattice to measure.

Fix: Auto rotate records the rotation it left behind (`ArrayActions._levelled_at`) and that stands in **only**
while no lattice can be measured **and** the rotation is still the one it set (`_levelled_by_auto_rotate`). It is
a real measurement - Auto rotate measured the tilt on the image and removed it - just not re-verified. As soon as
the ROIs are there the lattice measurement takes over and can overrule it; turning the rotation dial afterwards
voids it. `AutoCropReadiness.level_note` carries the reason in words ("tilt +0.03°", "levelled by Auto rotate",
"run Auto rotate, or place the ROIs to measure it"), so the checklist never has to guess what a missing
measurement means.

Measured while fixing it (`lattice_tilt_deg`, 10 x 16 array at 79.3 px pitch, 0.3 px of centre noise): the tilt is
recovered to within 0.001° from 0° to 1.42°, and at ~3° and above the row grouping breaks down and the function
returns `None` instead of a number. That failure is the safe direction - a tilted array never reads as level - and
it is why `None` has its own wording rather than being shown as a plain cross.

## Why half a pitch does the job

A Zarr chunk grid always starts at stored pixel (0, 0). With half a lattice constant of border beyond the
outermost ROI centres, the grid lines of a chunk size of one pitch - or any whole multiple of it - fall exactly
halfway between the ROI columns and rows. So from the cropped image's own corner: grid offset 0, one ROI (or a
k x k block) centred in each chunk, nothing padded, nothing trimmed. The kept width comes out as about
`cols * pitch_x` on its own, which is why no extra rounding is applied. `LatticeCropTests` in
`tests/unit/test_lspri_rewrite_chunk_plan.py` checks that chain end to end: crop -> `align_grid` gives offset
(0, 0) -> `score_layout` gives 1.0 chunks per ROI -> `export_frame` pads and discards nothing.

## The guard: half a pitch is not always enough

The margin has to hold the edge ROIs' **read area**, not just their centres. `lattice_crop` therefore takes the
ROI read boxes (`chunk_plan.roi_read_boxes`: sample disk, reference ring or free-form mask, united over the
fitted chromatic affines - and deliberately **unclipped**, so a ring already reaching past the current crop
widens the new one instead of being cut to it) and uses `max(pitch/2, reach)` per axis.

That matters on real data: the reference ring's outer radius is 0.86 x the sample diameter
(`processing/roi_array_geometry.estimate_reference_ring_radii`: inner = 1.4 x the sample radius, outer
equal-area), so at the measured 79 px pitch the read box is **87 px wide** (recorded in `align_grid`'s docstring
from the 2026-10-20 session) while half the pitch is only 39.5 px. When the margin had to grow, `widened` is set
and the status line explains it, rather than quietly cutting measurement pixels off the outermost spots
(priority 1: correctness). `clamped` says the image edge was closer than the margin on some side.

**This is also why the chromatic correction is a dependency.** Fitted models are what put the per-wavelength
shift into the read boxes; without them the margin covers the rings only, and an edge ROI could reach outside the
crop at the extreme wavelengths. Fitting CC after cropping and clicking Auto crop again fixes it - the second run
measures the full canvas, so crops never compound (tested).

## What this does *not* fix: chunk size

"Chunk = one lattice constant" is the part of the original idea that does not pay off, for two measured reasons:

- The read box (87 px) is **wider than the pitch** (79 px), so neighbouring ROI read boxes overlap by ~8 px. No
  crop and no offset can put every ROI in a chunk of its own; a grid line between two columns always cuts
  somebody's ring. Larger chunks reduce the share of ROIs affected, they do not remove the crossing.
- 79 x 79 = 6241 px is below `chunk_plan.SMALL_CHUNK_PX` (10 000). The 2026-10-20 benchmark measured whole-plane
  reads ~4x slower than a TIFF at that chunk size, and the analysis reads whole planes.

So Auto crop sets no chunk size (maintainer's decision too). The crop helps every lattice-locked layout, and the
pairing is left to Find best, whose recommendation (`columns`: one pitch wide, full height) remains the best
all-rounder from that benchmark. After Auto crop, `align_grid` lands on offset 0 by itself and the Export tab
picks the new geometry up through `_on_geometry_changed` - the export side needed no change at all.

## Coordinate spaces (easy to get wrong here)

- `GeometrySettings.crop` is measured on the **rotated + flipped canvas** (`transform.py`: rotate -> flip -> crop),
  while ROI positions are in **processed** space, i.e. relative to the crop in force now.
- Auto crop changes no rotation or flip, so the two spaces differ by exactly the current crop's clamped origin
  (`ArrayActions._crop_origin`, clamped the way `_combined_export_transform` clamps). The centres and boxes are
  shifted by it, the crop is computed against the full canvas (`spatial_output_shape` with the crop disabled), and
  the result goes straight to `set_crop`.
- The raw shape comes from `DatasetModule.raw_plane_shape` (a header read, no plane), passed into `ArrayActions`
  as `raw_plane_shape` - nothing is loaded on the GUI thread.
- With the image tools switched off the crop is computed as if they were on (`image_tools_enabled=True` in the
  probe settings), so a stored crop still matches once they are switched back on; the status line says it is not
  applied yet.

## Open

- **Unmeasured:** the background estimate (sigma 48 Gaussian) is computed on the cropped image, so cropping tight
  to the array changes it near the new edges. The manual crop tool has always had this effect - Auto crop only
  computes a rectangle the user could have dragged - but the size of the effect has never been quantified. Worth
  a before/after on one real cube (`.claude/rules/numerics.md`) before relying on a tight crop for results.
- `LEVEL_TOLERANCE_DEG` is 0.2° by argument, not by measurement: Auto rotate leaves ~0.1° of residual tilt, and
  0.2° over a 1200 px array moves a centre by ~4 px, small against a ~40 px margin. If the tick ever flickers in
  practice, that is the number to revisit.
- A "Lattice" entry in Find best (chunk = k x pitch, k chosen so the chunk stays above `SMALL_CHUNK_PX`) would
  make the pairing explicit instead of leaving it to the user. Not built.
- Auto crop takes every ROI in the toolbox. There is no "crop to the selected ROIs" variant, and no handling for
  a dataset whose ROIs are deliberately split into distant groups.
- The pick-up menu navigates to the **tab**, not to the control inside it (no highlight of the Array group or of
  the Chromatic tab's Fit button).
