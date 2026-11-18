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

### 1.2a Scan side (DONE 2026-11-18, uncommitted when written)
- `frame_reduce.RoiSet.is_ref` (from `AreaRoi.role`). A reference ROI: ring off (`ring_on` False), its region goes to the
  `SAMPLE` column, `REFERENCE` column NaN / 0 pixels; its region loses the pixels of every *sample* ROI (`_exclude_sample_discs`,
  shared by `build_layout` and `build_subset_layout`); its disc cuts no ring and no other region. `reduce_frame` stores a
  reference ROI's value from its own pixels (mean, and median / trimmed mean / plane-fit's mean side), so it is not dropped for
  lacking ring pixels. `ring_reach_matrix` treats a reference ROI as a "ring" of its own disc radius and never as a disc that
  reaches, so `local_roi_keys` (stale cells) and the subset scan follow the same rule.
- The role is in `roi_row_digests` and `RoiSet.key` **only for a reference ROI**. Proof that an all-sample set is untouched:
  a before / after fingerprint of key, row digests, local keys, full and subset pixel lists (both exclusion modes, identity and
  chromatic affine, one free-form mask ROI) and the numbers of all four reduction methods: byte-identical; the three digests of
  a small set are pinned in the test. So `SCAN_ALGORITHM_VERSION` stays 1 and no existing session rescans.
- Changing a ROI's role changes that ROI's cell hash (and the hash of a sample whose ring it used to cut): those cells go stale
  and need a scan, like any geometry change. Linking / unlinking changes no hash.
- `scan_store.layout_json` writes `"role": "reference"` for a reference ROI only.
- Tests: `tests/unit/test_lspri_scan_reference_rois.py` (15; a mutation of the exclusion fails 2 of them).

### 1.2b-d Pairing and display (NEXT; design proposed, awaiting the maintainer's answers)
- **b. One pairing function** `analysis/reference_pairing.py` (pure, no Qt): from a `StoredScan` and the ROIs' roles / links it
  gives, per *sample* ROI, the sample's own mean / SD / counts and its reference's mean / SD / counts: linked R pooled by pixel
  (n = sum n_i; mean = sum n_i m_i / n; variance = (sum (n_i - 1) s_i^2 + sum n_i (m_i - M)^2) / (n - 1), exact), else the
  stored built-in ring column, else "no reference" (NaN, n = 0). Pooling needs only mean / SD / count, which is all the scan
  stores for `mean`, the only method the app ever scans.
- **c. Where it is applied.** Proposed: `ScanResultsModule` hands the display a *paired* `StoredScan` (rows = sample ROIs, the
  `REFERENCE` side = the sample's effective reference), rebuilt on `links_changed` / role change; the readers of
  `mean[..., SAMPLE / REFERENCE]` (`scan_view.py`, `scan_trace.py` x2, `step_diagnostics.py`, `roi_tuning_run.py`,
  `trace_fit` inputs) then need no change and cannot disagree, and the trace caches (keyed on `id(stored)`) drop themselves.
  The raw file stays what `scan_store` and ROI-tuning Accept read. The alternative is the literal "move each reader to the
  function". Reference ROIs are not analysis units: Spectra / Sensorgram / step table / groups / Signal map / S_bulk skip them,
  and a sample without a reference says "no reference".
- **d. Proof test:** a sample linked to a separate reference ROI that has exactly the pixels of its own ring gives numbers
  identical to the built-in ring (and pooled two half-rings equal the whole ring to rounding).
- **ROI position tuning (maintainer, 2026-11-18):** tuning is for sample ROIs only (a reference ROI is never a candidate on its
  own), but a sample's linked reference ROIs **move with it** in a trial: the pair is shifted by the same offset. This is a rule of
  the tuning only; decision 4 (S and R move independently) still holds for every other edit. Consequences to build with it: a trial
  scans the sample and its references at the shifted positions, and Accept moves both and copies the numbers of both into
  `scan.h5`, still bit-identical to a real scan. **Open until the tuning part is built: a reference linked to two samples cannot
  move with both** (candidates: refuse to tune such a sample, or tune it with the reference left where it is and say so).

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
