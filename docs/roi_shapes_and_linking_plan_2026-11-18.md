# ROI shapes and sample / reference linking: plan (2026-11-18)

Supersedes `roi_system_roadmap.md` as the build order (that file is from 2026-08, its paths are stale, and it ruled out one
reference shared by several samples, which is now wanted). Its rule "analysis never branches on geometry" still holds.

## Decisions (maintainer, 2026-11-18)

1. **Roles.** Every ROI is a *sample* (S, an analysis unit) or a *reference* (R, one region, no ring, no links, not an analysis
   unit). One id list for both (R is a ROI like any other; the UI may show it as "R7").
2. **A sample's reference**, in this order: its linked R ROIs; else its built-in reference (the ring; later the border of any
   shape); else none (results missing, said so). Linking an S that has a ring does not delete the ring: the links win, the
   ring diameters are kept, unlinking brings it back (undo instead of a refusal).
3. **One R may serve many S, or none. An S may link several R.** Several R are combined **pooled by pixel** (the number you
   would get if their pixels were one region), computed from the stored per-region mean / SD / count, no rescan.
4. **No attached / offset reference.** S and R each keep their own x, y and move independently (chromatic correction
   included). Moving one never moves the other.
5. **Built-in border of any shape** = pixels at distance g .. g + t from the shape's edge (gap g, thickness t). For a circle
   this is exactly today's ring. Rounded outer corners on rectangles are fine.
6. **Shapes:** circle, rectangle, pixel mask. No polygon type: a lasso becomes a pixel mask.

## Why shapes vs pixel masks matters little for speed

The scan finds a ROI's pixels once per chromatic affine and caches the layout (`frame_reduce.LayoutCache`); every frame
after that costs the same for any shape. Masks are slow today only because they take a per-ROI fallback path
(`frame_reduce.locate_pixels`, the `~rs.vectorised` loop) and count as "reaching everything" (`_UNBOUNDED_REACH_PX`), so
moving one marks every ROI stale. Both are fixable (phase 2). Mathematical shapes win on editing (change one number),
exactness under the affine, and array stamping, not on scan speed.

## Key design: the scan stores regions, pairing happens after

The scan already stores per ROI row two columns, `SAMPLE` = the ROI's own region, `REFERENCE` = its built-in reference. An R
ROI fills only its own region. Pairing (which reference numbers go with which sample) is applied on the stored numbers by
one shared function, so **linking and unlinking never need a rescan** (a `RoiLinkChange`, not a `RoiComputationalChange`).
An R region leaves out the pixels of every sample, like the ring does today (`exclude_all_sample_rois`).

## Phases

### 1.1 Model, registry, commands, session (DONE 2026-11-18)
- `AreaRoi.role`, `AreaRoi.reference_roi_ids` (`domain/roi_model.py`); the rules as pure functions in `domain/roi_links.py`
  (`reference_source`, `link_refusal`, `cleaned_links`).
- `RoiToolbox`: `set_roi_role`, `link_references`, `unlink_references` (one undo step each, refusal = `ValueError` with a
  status-line message, nothing changed); queries `references_of`, `samples_of`, `reference_source`; signal `links_changed`
  (`change_events.RoiLinkChange`). Links follow `_renumber` (reorder), `delete_rois` and `place_array` (via
  `_survivor_links`); a link to a removed R is dropped and reported through `links_changed`. `restore_state` repairs and
  logs an inconsistent file.
- Session schema 1.11: `role` / `reference_roi_ids` written only when not default. The export's session carry copies ROIs
  with `dataclasses.replace`, so the fields travel with no change there.
- Tests: `tests/unit/test_lspri_roi_links.py`.
- Nothing reads the roles yet: until 1.2, an R ROI is still scanned and shown as if it were a sample.

### 1.2 Scan + pairing (next)
- Scan: an R ROI reads only its own region, minus every sample's pixels; its ring is off. The role goes into the per-ROI
  digest (`frame_reduce.roi_row_digests`) and `RoiSet`. Bump `SCAN_ALGORITHM_VERSION` only if a stored number of an existing
  (all-sample) session can change: it must not.
- One pairing function (in `analysis/`) returning, per sample: S mean/SD/n and R mean/SD/n (built-in, or linked pooled by
  pixel). The ~6 readers of `mean[..., SAMPLE]` / `mean[..., REFERENCE]` move to it: `scan_view.py`, `scan_trace.py` (2),
  `step_diagnostics.py`, `roi_tuning_run.py`, `trace_fit.formula_arrays` inputs.
- Analysis units = samples only: Spectra, Sensorgram, step table, groups, Signal map, S_bulk skip R ROIs; a sample without
  a reference shows "no reference" instead of numbers.
- Wire `links_changed` to whatever caches derived numbers (`analysis/trace_store.py`, step table).
- Proof test: S linked to a separate R ring with exactly the pixels of its own ring gives identical numbers to the built-in
  ring.
- Open: ROI position tuning moves samples; a shared R is not tuned (decide when 1.2 is built).

### 2 Shapes (back end)
- One geometry function per shape (circle, rectangle with rotation, mask), used by the scan, the overlay, the histogram,
  the wand's ROI layer, chunk planning and the session carry.
- Fast vectorised path for rectangles and masks (inverse-map the window's pixels, test / look up); masks get a bounding box
  for the reach test (`ring_reach_matrix`) so partial rescans keep working.
- The general border (distance g .. g + t), cached in source space.
- Before / after: `tools/scan_golden_check.py` must show circle numbers bit-identical.

### 3 UI
- ROI from selection (rectangle selection -> rectangle ROI; lasso / wand / brush -> mask ROI), labelled S or R.
- Link / unlink gestures on the image with link lines drawn; a "Linked" view in the ROI table (R rows under their S).
- The Shape picker's greyed entries (`panels/image/roi_shape_picker.py`) become live.
- Each new screen gets the GUI rules' description first (`.claude/rules/gui.md`).
