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

### 1.2b Pairing function (DONE 2026-11-18)
- `analysis/reference_pairing.py` (pure, no Qt; in the `lspri-science-no-qt` contract): `pair_scan(stored, rois) -> PairedScan`
  (`.stored`, `.sources`, `.problems`). `.stored` is a `StoredScan` with **one row per sample ROI** (the file's row order) whose
  `REFERENCE` side is the sample's effective reference: one linked reference = its stored region copied as it is (every method,
  exact, no arithmetic); several = **pooled by pixel** from stored mean / SD / count (n = sum n_i; M = sum n_i m_i / n;
  (n - 1) S^2 = sum (n_i - 1) s_i^2 + sum n_i (m_i - M)^2), `mean` only (a median cannot be pooled from medians; the app scans
  `mean` only); none linked = the stored ring column untouched; no ring either = no reference (already empty in the file).
  Reference ROIs have no row. A cube where a linked reference was never scanned has `cell_hash` 0 for that sample; a link to a
  ROI that is not in the file (added after the scan) gives the sample no reference and a `problems` line ("scan again").
- **No reference ROI and no link in the session: `pair_scan` returns the stored scan itself, nothing copied (0.08 ms).**
- Cost, measured on a scan of the real dataset's size (314 cubes x 160 ROIs x 26 wavelengths, 73 MB of arrays): 150 samples with
  one linked reference each 81 ms, with two pooled 205 ms; the paired copy holds as much memory again as the arrays it was made
  from (73 MB) while any link exists. Applying the same pooling inside each reader instead would cost the 205 ms again at every
  full trace refresh and about 23 ms at every cube step of the Spectra panel: this is why the paired copy is built once (off the
  GUI thread) when a scan loads or a link / role changes.
- Proof tests (`tests/unit/test_lspri_reference_pairing.py`, 16): a separate reference ROI that has exactly the pixels of a
  sample's ring, run through the real `reduce_frame`, gives the ring's mean / SD / counts **bit for bit**; the ring's two
  halves pooled give the whole ring's to 1e-12 relative; pooling equals the numpy mean / SD of the concatenated pixels for 2
  and 3 regions, with empty and one-pixel regions; `prepare_spectra` on the paired scan uses the reference ROI's numbers.
  Mutating the pooling formula (no between-region term, mean of means, n instead of n - 1) fails the tests each time.

### 1.2c Wiring and analysis units (DONE 2026-11-18, unseen by eye)
- `ScanResultsModule(rois=roi_toolbox.rois)` keeps the raw `StoredScan` (`_stored`: what the stale check compares with) and
  hands the display the paired one: `stored()` is the **paired** scan, `reference_note(id)` / `reference_notes()` say why a
  sample has no reference. The pairing is made when a file arrives (the file's own object when the session has no reference ROI
  and no link: no copy, no thread) and when `request_pairing()` is called; with a reference ROI or a link it runs on a thread
  (`analysis.scan_paired` in the log: ms, rows, linked), and the old view stays until the new one is ready. A failed pairing is
  said (`message()`), never replaced by the raw numbers.
- `app_rewrite._wire_analysis_scan`: `roi_toolbox.links_changed` -> `results.request_pairing()` (a link, an unlink, a role change,
  a restored session, a deleted reference). Links ask for **no staleness check and no scan**; a role change also emits
  `geometry_changed`, which already asks for the check, and the changed role makes that ROI's cells stale (tested).
- A file that arrives is published (panels, staleness check) only when its pairing is in place; the flag that says so is kept
  when a link replaces a pairing in flight (`_publish_after_pairing`; tested by calling `_on_loaded` and `request_pairing` back to
  back).
- The readers of `mean[..., SAMPLE / REFERENCE]` (`scan_view`, `scan_trace` x2, `step_diagnostics`, `trace_fit` inputs) are
  **unchanged**; the trace caches key on `id(stored)` and the panels drop their caches on `results_changed`, so a new paired scan
  refreshes them. The step table's rows are stamped with the scan object too, so after a link they read "not current" until the
  user recalculates (the app calculates only on request).
- Analysis units are samples only. One rule, `RoiToolbox.sample_ids(ids)` (a reference ROI is dropped; an unknown id is kept),
  applied where a list of ROIs enters a calculation: Spectra and Sensorgram (the selection; a selection of references only says
  "Only reference ROIs are selected ..."; the drift controls' list too), `StepTableModule` (`recalculate`, `count_not_current`
  and every group stamp, through `_sample_members`, so a group holding a reference ROI is calculated and judged "current" from
  its samples), `SignalMapModule` (`roi_ids`, group rows). Data checks, mask marks and the Sensorgram axis read the paired scan,
  which has no row for a reference ROI. `prepare_spectra(reference_notes=...)` says "No reference: ..." instead of "No wavelength
  has a value" for a sample without one.
- Not done, deliberately: the ROI / Group table has no "R7" label and no Linked view (phase 3); there is no gesture that creates
  a reference ROI or a link yet (the toolbox commands exist). **ROI position tuning still scores a sample against its own ring
  and ignores links, and its selection can hold a reference ROI: it is phase 1.2d.**
- Tests: `tests/integration/test_lspri_reference_roi_wiring.py` (10, real scan, real wiring).

### 1.2d Position tuning and linked references (after 1.2c)
- **ROI position tuning (maintainer, 2026-11-18, corrected the same day):** tuning is for sample ROIs only (a reference ROI is never
  a candidate on its own). **A linked reference ROI never moves with its sample** (decision 4 holds for tuning too: S and R move
  independently). The only reference that moves with a sample is its **analytical** one, the built-in border (gap and thickness
  around its shape, today the ring), because it is defined from the sample's own geometry; that is how tuning already works. An
  earlier note of this day said linked references move with the sample: that was a misreading and is withdrawn.
- What that leaves to build: for a sample **with linked references** a trial position is scored against its *linked* reference
  (fixed in place), not against the moving ring, and Accept still copies only what the file stores for a sample (its own region and
  its ring), bit-identical to a real scan; the links apply afterwards through the pairing, as for every other number. A trial
  that moves a sample's disc into a linked reference's region changes that region (it loses the pixels of every sample disc):
  `ring_reach_matrix` already counts a reference ROI as a neighbour of such a sample, which is the existing "interacting pair" rule
  (no Accept copy); the score of such a position is the part to decide when it is built (1.2d).

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
