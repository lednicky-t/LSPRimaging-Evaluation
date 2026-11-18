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
- **Built (minimal, 2026-11-18; this is where phase 1.2 ends):** `RoiTuningModule._prepare` drops reference ROIs from the selection
  (`RoiToolbox.sample_ids`; a selection of references only says "Only reference ROIs are selected ...") and **refuses a sample that has
  linked references** with the reason ("ROI 5 is linked to reference ROIs, and position tuning does not follow links yet ...";
  `RoiToolbox.linked_sample_ids`), so a linked sample is never silently scored against its moving ring. An unlinked sample, a
  reference-free session and Accept are untouched (Accept copies only a sample's own region and ring, which the file stores). Tests:
  `test_lspri_roi_tuning_module.py` (2 new, mutation-checked), `test_lspri_roi_links.py`.
- **Potential work, not needed now (maintainer, 2026-11-18: "no need now, mark it down"): link-aware tuning.** For a sample **with
  linked references** a trial position would be scored against its *linked* reference (fixed in place), not against the moving ring,
  and Accept would still copy only what the file stores for a sample (its own region and its ring), bit-identical to a real scan;
  the links apply afterwards through the pairing, as for every other number. Sketch: the trial matrix keeps its scan-level numbers
  and `TuningMatrix.trace_inputs()` is the one place that turns them into traces, so a linked sample's `REFERENCE` arrays there
  would be replaced by the paired reference of the stored scan (`reference_pairing`, constant over the lattice positions; the same
  cubes and wavelength axis as the plan, refused with a message if they differ or the linked cells were never scanned). A trial
  that moves a sample's disc into a linked reference's region changes that region (it loses the pixels of every sample disc):
  `ring_reach_matrix` already counts a reference ROI as a neighbour of such a sample, which is the existing "interacting pair" rule
  (flagged "approximate", no Accept copy); the exact alternative is to scan the linked references as unmoved extra rows of every
  trial, at the cost of widening the matrix and every consumer of its ROI axis. Until then a linked sample is refused (above).
- **Also noted, for phase 3:** with only reference ROIs selected, Spectra and Sensorgram draw nothing and the reason ("Only reference
  ROIs are selected ...") goes to their status text, which is not visible (Spectra's status row is hidden by the maintainer's
  2026-10-23 decision; the Sensorgram's reaches the status bar only for a run that became visible). Nothing differs from before for
  a session without reference ROIs, and no gesture creates one before phase 3, so this edge case cannot be reached yet; decide
  the visible form together with the "R7" label and the Linked view.

### 2 Shapes (back end): design decisions of 2026-11-18 and build order

**Maintainer's choices (2026-11-18), both the larger option; each has a consequence to keep in view:**

1. **Circles convert to the same border description as every shape: gap g and thickness t from the shape's edge** (not "circles keep
   their inner / outer diameters"). Border pixels are those at signed distance g .. g + t from the edge (positive = outside; a circle's
   signed distance is |p - c| - R, so a *negative* gap, a ring that starts inside the sample, stays expressible: the golden set has one).
   For a circle the pixel test stays the squared-distance test of today; only where its radii come from changes: `ring_in_r = R + g`,
   `ring_out_r = ring_in_r + t`, evaluated in that fixed order.
   *Consequence A, exactness:* the cell hash contains the ring radii as float64 bytes. A one-ulp difference (1e-10 in a diameter was
   enough in the 2.0 mutation check) changes the key of that ROI without changing a single pixel, so the session would rescan for nothing.
   The migration therefore stores `g = inner_r - R`, `t = outer_r - inner_r` and **verifies bit for bit** that `R + g` and `(R + g) + t`
   give the old radii (true whenever the subtractions are exact, Sterbenz: R/2 <= inner_r <= 2R and inner_r <= outer_r <= 2 inner_r,
   which covers every ring seen so far); a ROI that fails the check is reported by name and its cells rescan, never silently.
   *Consequence B, behaviour:* today a ring is **absolute** (inner / outer diameter): resizing a circle's sample leaves its ring where it
   was. With g / t the ring is defined from the sample's edge, so **resizing the sample carries the ring with it**. This is what decision 5
   asks for, but it is a visible change for circles; the migration keeps every existing ROI's numbers (it writes each ROI's own g / t
   from its effective legacy ring), the change only shows when a sample is resized afterwards. The shared default ring becomes a default
   (gap, thickness), derived from the default diameters (28 / 36 with a 20 px sample = 4 / 4); a ROI whose legacy ring is not exactly
   that gets its own border at migration.
2. **One small parameter object per shape** instead of loose fields: `Circle(diameter_px)`, `Rectangle(width_px, height_px, angle_deg)`,
   `MaskShape(mask)`, plus `Border` (own gap and thickness, or "shared default", or "none"), in a new pure module `domain/roi_shapes.py`
   (added to the `lspr-science-no-qt` contract). `RoiGeometry` (the per-cube timeline entry) carries shape and border instead of
   `sample_diameter_px` and the two ring diameters, so a rectangle can change size per cube like a circle. `AreaRoi` loses
   `sample_diameter_px`, `sample_geometry_type`, `sample_mask`, `reference_geometry_type`, `reference_mask`, `reference_inner/outer_diameter_px`.
   *Consequence C, reach:* about 21 files read `sample_diameter_px` (overlay, `roi_queries`, `roi_gestures`, `render` cache key, ROI table,
   `chunk_plan`, `ring_size`, `detection`, `array_actions`, `group_*`, `provenance`, `scan_store.layout_json`, `session`, `session_carry`,
   `background/estimate`, `chromatic/affine`, `frame_reduce`, `rasterize`). Each one is looked at and given an explicit shape rule; there
   are **no compatibility properties** that pretend a rectangle has a diameter. The ring columns of the ROI table and the "Edit reference
   ROI region" dialog show diameters today; in phase 2 they show the circle's diameters *derived* from g / t (no new screen), and phase 3
   decides how to present gap / thickness.
   A free-form **reference** region (`reference_geometry_type == "mask"`) goes away: it is a reference ROI (role) linked to the sample
   (decisions 1-3). Nothing in the UI ever created one; the migration reports any it finds by ROI id instead of dropping it.

**Data safety for saved sessions (needs the maintainer's yes before 2.5; nothing below is built):** session schema 1.12 (additive, a
minor bump). A 1.11 file is read as before and converted in memory by the checked migration above; it is written in the new form by the
next autosave. Before the first overwrite of a 1.11 `session.json` a copy `session.json.pre-1.12` is written once next to it. A build older than
1.12 cannot read a 1.12 session correctly (it would fall back to its 20 px default diameter), so the file's schema version is what stops it.
The old-format fixture `tests/data/lspri_golden_rois_session_1_11.json` is the migration's proof: it must decode to the pinned digests below.
`scan.h5` (`lspri_scan` 1.1) records each ROI's geometry in `layout_json`; whether that record changes shape or stays informational is decided
in 2.5 (the cell hashes, not that record, decide what is stale).

**Build order (each step is committed locally only after asking):**
- **2.0 Baseline (DONE 2026-11-18, before any source change).** `tests/unit/test_lspri_scan_circle_golden.py`: 12 deliberately awkward circles
  (shared / own / no / zero-thickness / inside-the-sample / inner-0 rings, non-integer diameters, windows leaving the image, overlapping
  discs, empty sample, one reference ROI) pinned as four SHA-256 digests: **keys** (set key, row digests, local keys, reach matrix; exact bytes),
  **pixels** (full and subset layouts for identity / chromatic / rotated affines, both exclusion modes, window boxes; exact), **numbers**
  (`reduce_frame`, four methods, with and without ignore mask and NaN pixels; counts exact, values to 10 significant digits because the plane
  fit is not bit-reproducible across platforms) and **mask** (today's scipy-warped free-form ROI, to be replaced in 2.2 by a measured
  decision). Mutation check: a 1e-10 diameter change moves the keys only; 0.4 px moves keys, pixels and numbers. Rule for every later step:
  *only `golden_rois()` may be edited (to build the same circles in the new model); the pins must not move.*
- **2.1 + 2.3 + 2.5 Model, per-ROI geometry, border, session (BUILT 2026-11-18; maintainer: "the app is in development, no proper migrations or
  compatibility needed, just upgrade it"; so no backup copy and no per-ROI reporting, only a short reader of the old form).**
  - `domain/roi_shapes.py` (pure, in the `lspr-science-no-qt` contract): `Circle`, `Rectangle(width, height, angle_deg)` (angle clockwise on the
    screen, about the centre), `MaskShape`, `Border(gap, thickness, enabled)`, `NO_BORDER`; `ring_radii` (the one place a circle's ring radii are
    computed: `R + g`, never below 0, then `+ t`), `ring_diameters`, `effective_border`, `reach_radius`, `nominal_diameter`, `remap_rectangle`,
    `scaled_shape`. `RoiMask` moved there (re-exported by `roi_model`).
  - `AreaRoi.shape` / `AreaRoi.border` (`None` = the shared default) replace `sample_diameter_px`, `sample_geometry_type`, `sample_mask`,
    `reference_geometry_type`, `reference_mask`, `reference_inner/outer_diameter_px`; `RoiGeometry` is `(center_x, center_y, shape, border)`;
    `AreaRoiDetectionSettings` has `border_gap_px` / `border_thickness_px` (4 + 4 = the old 28 / 36 ring around the 20 px default sample) and
    `default_border()`. `default_border: Border` replaces every `(default_inner_diameter_px, default_outer_diameter_px)` pair in the scan,
    the rasterizers, the overlay, the histogram, `chunk_plan.roi_read_boxes` and `tasks.compute_cell`.
  - Circles: the pixel test and the squared-distance code are untouched. **Measured (2026-11-18, old sources of commit 24f0179 against the new ones,
    `tests/unit/test_lspri_scan_circle_golden.py`):** the float64 radii of the 12 awkward circles are bit-identical (`RADII` pin), the pixel lists of
    every layout / subset / window box and the reduced numbers are identical (`PIXELS`, `NUMBERS`), and the sample pixels of a free-form ROI are
    identical (`MASK_SAMPLE`). The cell keys (`KEYS`) were **re-pinned**: a circle's digest no longer contains the shared default diameters (its
    border is already in its radii), so every stored `scan.h5` hashes differently and rescans once; no number changes.
  - `roi/rasterize.py` routes by shape. A rectangle is found by an exact signed distance in source space; the border of a rectangle or a mask is
    the band `g <= signed distance <= g + t` (a rectangle's has rounded outer corners; a mask's distance is measured from pixel centres with the edge
    half a pixel out, an exact-along-the-axes approximation of the staircase edge, `_mask_sdf`, cached). A mask's own region keeps the scipy warp.
    All tested against brute-force implementations written in the test (`tests/unit/test_lspri_roi_shapes.py`).
  - Non-circular ROIs go through the per-ROI rasterizer in the scan (`RoiSet.vectorised` is True only for circles) and count as "reaching
    everything" for stale cells: correct, but slow (this is 2.2 below).
  - Toolbox: `add_shaped_roi(x, y, shape, border=, role=)` (one undo step; `add_roi` is a circle wrapper); the diameter commands (`resize_rois`,
    `resize_roi`, `reset_roi_diameters`, `refine_rois`, `place_array`) keep their **diameter-based API for circles** and store the border that gives
    those diameters (so gestures, the ROI table and `ring_size` did not change); they refuse a ROI that is not a circle. **A change of the sample
    diameter alone now carries the ring with it** (maintainer, 2026-11-18); a ring edit stores an own border at the (new) sample size; a ROI
    without an own border keeps following the default. `remap_all` (rotate / flip / crop) turns a rectangle's angle (`remap_rectangle`; also in the
    timeline and in the export's session carry), re-warps a mask as before.
  - Session schema 1.12: ROI and timeline entries hold `shape` and (only when own) `border`; settings hold `border_gap_px` / `border_thickness_px`.
    A 1.11 file is read as circles whose ring is converted per ROI (`_legacy_shape_and_border`: a ROI keeps exactly the ring it was measured
    with, using the old file's own default ring diameters); the old-format fixture `tests/data/lspri_golden_rois_session_1_11.json` reads back
    to the pinned radii, pixels and numbers. A free-form *reference* region (never created by the UI) is dropped with a warning.
  - Display until phase 3: the overlay draws a rectangle or a mask as the circle that holds it (`nominal_diameter`) and hit-testing uses that
    circle; the ROI table shows 0 for the diameters of such a ROI (shape key "other" for a rectangle). Phase 3 draws and edits each shape.
- **2.2 Scan speed for non-circles (OPEN).** Vectorised path for rectangles and masks in `locate_pixels`; bounding boxes in `ring_reach_matrix` /
  `roi_window_boxes` (and so in `local_roi_keys`) so a rectangle or a mask no longer "reaches everything" and a partial rescan works. A mask's pixel
  membership through a fast lookup may differ from the scipy warp at half-pixel ties: count them against the `MASK_SAMPLE` pin and report.
- **2.4 Remaining consumers (OPEN).** Overlay and hit-testing per shape, the histogram's region for a rectangle border, `region_at`'s ring search for
  non-circles (a circle's ring is found by distance; a rectangle's border is only reachable through `rasterize_reference`), the scan's stored
  `layout_json` (readable record, already shape-aware).
- Original outline of this phase (kept): one geometry function per shape (circle, rectangle with rotation, mask), used by the scan, the overlay,
  the histogram, the wand's ROI layer, chunk planning and the session carry.
- Fast vectorised path for rectangles and masks (inverse-map the window's pixels, test / look up); masks get a bounding box
  for the reach test (`ring_reach_matrix`) so partial rescans keep working.
- The general border (distance g .. g + t), cached in source space.
- Before / after: `tools/scan_golden_check.py` must show circle numbers bit-identical.

### 3 UI
- ROI from selection (rectangle selection -> rectangle ROI; lasso / wand / brush -> mask ROI), labelled S or R.
- Link / unlink gestures on the image with link lines drawn; a "Linked" view in the ROI table (R rows under their S).
- The Shape picker's greyed entries (`panels/image/roi_shape_picker.py`) become live.
- Each new screen gets the GUI rules' description first (`.claude/rules/gui.md`).
