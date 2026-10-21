# ROI drag went slow: Auto crop's readiness check (2026-10-21)

Maintainer report: "the GUI getting slow when moving ROI", with a session log. Cause found, fixed and measured.
Self-inflicted: the readiness check added to Auto crop earlier the same day.

## What the log said

```
13:31:36.424  RoiToolbox.translate_rois dur_ms=371.53
13:31:36.925  RoiToolbox.translate_rois dur_ms=449.45
13:31:37.304  RoiToolbox.translate_rois dur_ms=344.93      ... and so on, per mouse step
```

Every sample of a ROI drag cost 200-450 ms. The session: 314 cubes x 27 wavelengths (8478 planes), 160 ROIs, the
chromatic correction on and fitted. `RoiToolbox.place_array dur_ms=496` in the same log is the same cost from one
emit, which was the clue: the time is not in the ROI write, it is in what the write's signal triggers.

## Root cause

`translate_rois` -> `_write_states` emits `geometry_changed` **synchronously**, so every listener's time is inside
the measured call. `ArrayActions` (2026-10-21) had connected Auto crop's readiness check straight to that signal,
and the check called `_lattice()`, which built **every ROI's read box unioned over every fitted chromatic
affine** - to answer three yes/no questions for a button's colour and tooltip.

Measured (160 ROIs, `scratchpad/affine_scaling.py`):

| Fitted chromatic models | Readiness check, per ROI edit |
|------------------------:|------------------------------:|
| 27 | 4.5 ms |
| 1 000 | 88 ms |
| 3 000 | 277 ms |
| 8 478 (this session) | 1 300 ms |

The log's 200-450 ms per sample sits in that band. Two things made it expensive: `ChromaticModule.models()`
sorts and copies every fitted record, and `chunk_plan.roi_read_boxes` loops over each unique affine doing an SVD
plus a 160-ROI transform.

## The fix

1. **The readiness check only needs the centres.** No read boxes, no affines: "are there ROIs" is the ROI count,
   "is the array level" is `chunk_plan.lattice_tilt_deg` on the centres, "is the chromatic correction fitted" is
   the new cheap `ChromaticModule.has_fitted_models` property instead of building `models()`.
2. **Debounced** (`_READINESS_DEBOUNCE_MS` = 150 ms, `_schedule_auto_crop_readiness`): a drag emits
   `geometry_changed` per mouse step, and nobody reads a button's tooltip mid-drag.
3. **The crop itself** (one click, where the read boxes really are needed) no longer unions a box per fitted
   frame either. `_chromatic_reach` reduces every model to two conservative numbers - the worst centre
   displacement and the largest stretch, both vectorised over all models at once - and the base read boxes are
   grown by them. Slightly more generous than the exact union, in the safe direction (a wider margin never cuts
   into a ROI), and it took the click from 1.3 s to 281 ms at 8478 models.

## Verified

Real `ImagePanel` + `HistogramPanel`, 160 ROIs selected, every listener connected
(`scratchpad/drag_cascade.py`, which prices each listener by disconnecting it):

| | before | after |
|---|---:|---:|
| one drag sample, synthetic session (27 models) | 7.1 ms | **1.1 ms** |
| of which the readiness check | 3.7 ms | ~0 (debounced) |
| Auto crop click, 8478 models | 1 300 ms | **281 ms** |
| readiness check itself | 666 ms* | 1.6 ms |

\* at 8478 models. The synthetic harness cannot hold the maintainer's 8478 fitted models, so the drag-sample row
is the synthetic session; the model-count table above is what maps it onto the real one.

Pinned by two tests in `tests/integration/test_lspri_rewrite_array_actions.py`: the readiness check must never
build the fitted-model list (it counts calls to `models()`), and a burst of 20 ROI edits must recompute the
checklist exactly once.

## Follow-ups, both done the same day on the maintainer's go-ahead

### 1. The same affine union in the Export tab

`export_roi_layout.gather_export_roi_layout` unioned the read boxes over `chromatic.models()` exactly as the
readiness check had, and `export_tab._on_roi_layout_changed` runs it on the GUI thread on every ROI change
**whenever the grid position is not "Corner"** (~600 ms-1.2 s per ROI edit at 8478 models). It did not bite in
this log, because the position was "Corner".

Fixed by sharing the arithmetic instead of repeating it: `chunk_plan.affine_reach` (worst displacement, largest
stretch - vectorised over all affines) and `chunk_plan.grow_boxes` (grow each box about its own centre) are now
the one implementation, used by both `ArrayActions._lattice` and `gather_export_roi_layout`. The Export tab also
asks `has_fitted_models` before touching `models()` at all.

A unit test pins the property that makes the replacement safe: the grown boxes **contain** the exact per-affine
union, over 12 randomised sets of near-identity affines. Too big by a pixel costs a chunk or a slightly wider
crop margin; too small would cut into a measurement, so only one direction is acceptable.

### 2. A ROI move no longer re-renders the frame

`panels/image/panel.py` carried this comment since the rewrite: *"ROI/selection changes only move the overlay,
never the pixels - but they still go through the same path. Splitting 'redraw overlay only' out is a real
optimization once there is a dataset big enough to measure it against."* This session is that dataset
(`render.frame dur_ms=50-180`, `prep_ms=45-130`, once per ROI edit, for pixels that did not change).

What a ROI can actually change in a `RenderRequest` is one thing only: the **background estimate's exclusion**
(`rois=` in `_redraw`, gated on `flatten_background_exclude_area_rois` inside `apply_preprocessing`). So:

- `_rois_change_pixels()` = the exclusion is on **and** the estimate is in use (flattening on, or the estimate is
  what is being shown).
- `_schedule_roi_redraw` (ROI edits) sends the full redraw when that holds, and `_schedule_overlay_redraw`
  otherwise - a second coalescing timer that only runs `_draw_roi_overlay` (`render.overlay_only` in the log).
- **Selection changes are always overlay-only**: the exclusion uses every ROI, never the selection.
- The Histogram panel now redraws itself on a ROI change (`_on_roi_geometry_changed`). It used to get that for
  free from the re-render it no longer triggers; its heavy counting is on its own worker thread, so this costs
  the GUI thread nothing.

Measured on a real `ImagePanel`, 160 ROIs, 1276 x 800 (`scratchpad/roi_edit_render.py`):

| ROI edit, mode | render requests submitted |
|---|---:|
| flattening off (the usual case) | **0** (was 1 per edit) |
| flattening on, ROIs excluded | 1 - unchanged, the pixels really do change |
| flattening on, exclusion off | 0 |

Each avoided render is the 50-180 ms of render-thread preprocessing the maintainer's log shows per edit, with the
GIL contention that goes with it. The GUI thread's own cost is the same either way (~3-4 ms to redraw the overlay
for 160 ROIs, which the old path paid as well). Three tests in `test_lspri_rewrite_image_panel.py` pin all three
rows of that table, including that the overlay still follows the moved ROI.

## Still open

- `histogram.compute` recomputes the whole-plane counts on a ROI change although only the ROI curves can have
  changed (20-180 ms, but on the histogram's worker thread, not the GUI thread). Splitting the plane counts from
  the ROI curves would remove it; not done, and not urgent while it stays off the GUI thread.
- The drag itself still writes one undo entry per mouse sample into the batch, and `session.capture` (~110-180 ms)
  runs on the GUI thread on its autosave tick. Neither showed up as the cause here.
