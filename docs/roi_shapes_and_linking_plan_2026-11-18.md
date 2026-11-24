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
- **2.2 Scan path and reach of rectangles and masks (DONE 2026-11-18, unseen by eye: no GUI involved).**
  - *Measured before (160 ROIs, 1200 x 1000 frame, one layout = one pixel list of every ROI for one affine; a scan builds 26 of them per worker):*
    160 circles 20 ms; 140 circles + 10 rectangles + 10 masks 117 ms; 160 rectangles 324 ms; 160 masks 1,054 ms. With any rectangle or mask in the
    set, **every other ROI counted as its neighbour** (5,980 pairs for the mixed set, 25,440 for 160 rectangles): moving one made every cell stale.
    *After:* mixed 28 ms, 160 rectangles ~100 ms, 160 masks 99 ms; **0 false neighbour pairs**. Circle-only sets are unchanged (20 -> 15 ms is noise;
    the golden pins KEYS / PIXELS / NUMBERS / RADII / MASK_SAMPLE all hold untouched).
  - *How:* one function finds a non-circular region **inside its own box** (`rasterize._region_hits`, never a full plane): `rasterize_sample`,
    `rasterize_reference`, their patch variants, `union_roi_masks` (histogram) and the scan (`sample_flat_indices` / `reference_flat_indices` in
    `locate_pixels`) all use it, so the scan's pixels equal the reference rasterizer's **by construction** (also tested for 240 random ROIs x 5 affines).
    No separate "fast lookup" for masks (the plan allowed one and asked to count the differences): the box-bounded scipy warp is pixel-identical to
    the old full-canvas warp for every ordinary affine (0 differences in 250 interior cases each for a chromatic and a rotated affine), so nothing could
    disagree. **Two measured differences, both in the old full-canvas call:** a mask touching the image edge keeps pixels scipy's border handling used to
    drop (about 5 % of random edge-touching cases, 1-70 px), and an affine whose shift is an exact half pixel breaks the rounding tie the other way
    (an arbitrary tie, not a measured case). The patch variant was always box-based, so the full-plane one was the odd one out.
  - *Reach:* `RoiSet.sample_r` / `ring_out_r` are the **reach** (radius of a disc about the centre that holds the shape / its border) for a rectangle
    or a mask (`roi_shapes.reach_radius`, `border_reach_radius`); `ring_reach_matrix` and `roi_window_boxes` need no special case any more (`bounded`
    is kept in the return value, always True). The cell hash of a rectangle or mask ROI changes once (rescan once; none exist in a real session).
    A mask's border needs `MASK_BORDER_SLACK_PX = 1.5` beyond `reach + gap + thickness` (proved <= 1.21, measured worst case 0.49 px over 4,000 random
    masks and five affines); with no slack 1 pair in ~1,250 that meet is missed (test with a fixed seed). The property tests
    (`tests/unit/test_lspri_roi_shape_reach.py`) compare every window box, read box and neighbour answer with the reference rasterizer, with no margin.
  - *Found on the way:* `chunk_plan.roi_read_boxes` under-covered a **mask's border by up to ~1 px** (a partial read of an export could have missed a
    row of border pixels; fixed in `region_source_bounds`); `_mask_sdf`'s cache held 64 maps (an EDT per call at 160 masks; now 512).
  - Mutations that the tests catch: no mask slack, border reach = shape reach, mask reach from the box middle, pixels shifted by 1.
- **2.4 Display and pointing for every shape (DONE 2026-11-18, UNSEEN BY EYE).**
  - Drawing: circles and rectangles are rows of the same arrays (`overlay_geometry.rectangle_points`: rotated, rounded corners for a border, bent by the
    affine like a circle); a mask is drawn as its **traced pixel outline** in a small painted item (`panels/image/shape_paths_item.py`, one path per ROI,
    even-odd so a hole stays empty), the border of a mask as the traced band. A circle-only session takes exactly the old code path.
  - **Half-pixel convention (observed here; FIXED later the same day, see "Half-pixel fix" below):** the rasterizer treats a pixel *index* as its coordinate,
    the canvas draws pixel `(row, col)` over `[col, col+1)`, and circles were drawn around the stored centre as it stood, so a circle was about 0.5 px
    (both axes) off the pixel set it measures. A mask is drawn along the cells of its pixels (`overlay_geometry.CELL_OFFSET`), so a lasso-made mask
    outlines exactly where it was drawn.
  - Pointing (`roi/shape_hits.py`, same region test as the scan): `roi_at` is exact for rectangles and masks (a hole is not a hit), the wand's ROI layer
    finds a rectangle's or mask's border, `rois_touching` uses the ROI's pixels, and a selected **rectangle** can be grabbed by its inside
    (`SelectedApertures.body`) and moved; no edge handles (they resize a diameter).
  - **A pixel mask is drawn and pointed at from its pixels through the frame's affine, never from the ROI's centre** (the scan does exactly that; the centre is
    only the label point). Found in review before it shipped: with a centre-based drawing, dragging a mask would have moved its drawn outline while the
    measured pixels stayed (a ROI that looks moved and measures the old place). Then (replaced 2026-11-19, see "Moving a pixel map") `RoiToolbox.move_refusal` refused `translate_rois` / `place_rois` while a
    pixel mask is among the ROIs (nothing is half-moved; the message says to delete it and make a new one), a drag that starts with a mask selected is refused
    once with that message (`RoiGestures._begin_edit`), and a mask has no inside to grab. To make a mask movable, `translate_rois` has to shift
    `RoiMask.x0 / y0` by whole pixels (a drag is fractional): built 2026-11-19, "Moving a pixel map".
  - The histogram's region uses the box-bounded union (above). `scan_store.layout_json` was already shape-aware.
- Original outline of this phase (kept): one geometry function per shape (circle, rectangle with rotation, mask), used by the scan, the overlay,
  the histogram, the wand's ROI layer, chunk planning and the session carry.
- Fast vectorised path for rectangles and masks (inverse-map the window's pixels, test / look up); masks get a bounding box
  for the reach test (`ring_reach_matrix`) so partial rescans keep working.
- The general border (distance g .. g + t), cached in source space.
- Before / after: `tools/scan_golden_check.py` must show circle numbers bit-identical.

### 3 UI (BUILT 2026-11-18, UNSEEN BY EYE; maintainer's answers of the same day)

Decisions (maintainer): **right-click menu** Add ROI > Circle / Rectangle / Current selection > All together as one ROI | Separate blobs;
**Make this ROI > Sample / Reference**; ROIs made from a selection are **pixel-mask references, linked to nothing**; **no ROIs-tab icon** for
creating from a selection; **Link / Unlink = select, then press**: icon buttons in the ROIs tab (Selection group), context-menu entries, **Ctrl+L / Ctrl+K**;
the Shape picker keeps **Circle + Rectangle** (Polygon / Freeform removed: a lasso becomes a pixel mask); everything in one go.

What exists:
- `roi/mask_regions.py` (selection -> regions: all together or one per 8-connected blob, reading order, centroid), `RoiToolbox.add_mask_rois` (one undo
  step, one signal), `RoiToolbox.resize_rectangles` (width / height / angle, timeline-aware like `resize_rois`), `roi/link_actions.py` (which ROIs a
  selection means for Link / Unlink, and the status line each says: Link = every selected sample to every selected reference; Unlink = those two sets, or
  all links of the selected samples, or the selected references from every sample that uses them).
- `panels/image/roi_creation.py` (`RoiCreation`: the one object between menu / buttons / keys and the toolbox; a refusal goes to the status bar; the
  display selection is carried back into the reference frame by the inverse chromatic affine), `roi_context_menu.py` (new entries; `ADD_ROI` stays the
  "Circle" choice), `rectangle_size_dialog.py`, ROIs tab Link / Unlink / Links buttons, Shape picker (Circle, Rectangle; remembered; sets what the Add ROI
  tool places). **(The Shape picker, the click-to-add Add ROI tool and `ImageTool.ADD_ROI` were deleted 2026-11-24: the tool's bar had been off the
  screen since 2026-10-07, so nothing could arm it and the picker steered nothing. Add ROI > Circle / Rectangle is the right-click menu's.)**
- Display: dashed **link lines** from each sample to its references (the Links eye, on by default, remembered), the label **"R7"** for a reference ROI
  (samples keep their plain number), a reference ROI wears the reference colour and has no border drawn; the table's first column shows "R7" with a tooltip
  (what a sample is measured against, or which samples use a reference), the Shape column says "Rectangle" with a pictogram and the size in its tooltip.
- Shortcuts: `links.link` (Ctrl+L), `links.unlink` (Ctrl+K) in `shortcuts.py` (application-wide). Ids use the prefix `links.`: the prefix `roi.` collides with
  log event names in the shortcut test's source scan.

**Screen descriptions (GUI rules).** *Rectangle size dialog:* purpose: set width, height, angle of the selected rectangles; actions: type values, OK / Cancel;
layout: a three-row form (Width px, Height px, Angle deg clockwise) and OK / Cancel, filled from the first selected rectangle; states: with several
rectangles only the fields changed are applied; validation: width and height >= the minimum ROI size (the toolbox refuses with a message); errors: on the status bar.
*ROI menu additions:* an "Add ROI" submenu (entries greyed when they cannot apply: no room under the lock / selection, no area selection), "Make this ROI"
submenu (the role the whole selection has is checked), "Rectangle size...", "Link references" and "Unlink" with their keys (greyed unless the selection holds
something to link / take apart).

**Built after the first Phase 3 round (same day, "you can do all"):**
- **Linked view** of the ROI / Groups table (view menu: "Linked view (each sample with its references)", remembered, key `roi_table/linked_view`):
  `rows.build_linked_sections` makes one section for each sample with linked references, headed "7 measured against R3, R4", holding the sample's row and then
  its references' rows (a shared reference appears in each of its samples' sections), then "Not linked" (samples on their own border or with nothing, references
  nobody uses). The headers are read-only synthetic labels with no step numbers of their own (`GroupInfo.synthetic`: the "Ungrouped" numbers must not appear on
  them), each remembers its own collapsed state (`collapse_key`), nothing is dragged or dropped there, and groups come back when it is switched off.
  **The table now also refreshes on `links_changed`** (it did not: a Link / Unlink left "R7" and the link tooltips stale; found while building this).
- **Rectangle handles on the image** (`panels/image/rectangle_handles_item.py`, `roi/shape_hits.py` `rectangle_handle_points` / `rectangle_handle_zone` /
  `rectangle_size_at`, `RoiGestures` mode "rectangle"): a selected rectangle shows white squares on its corners and edge middles and a round knob on a stem beyond
  its top edge. Dragging an edge sets the width or height, a corner both, the knob turns it; all about the centre, in the rectangle's own axes under the chromatic
  affine, **every selected rectangle gets the value under the cursor** (as a circle's diameter does), a circle among them is untouched, the whole drag is one undo
  step, sizes clamp at the minimum ROI size. The knob sits `KNOB_PX` = 22 screen px out; the hover cursor is the resize arrow, or an open hand on the knob. The drawn
  handle positions and the hit test come from one function and a round-trip test (drag to another rectangle's handle = that rectangle's size / angle) pins them.
  A corner zone never reaches a tiny rectangle's middle (found by a test: a 6 x 6 rectangle had nothing to grab by). Shortcut table row `mouse.view.rectangle_handles`.

**Still not built:** a drag-to-draw rectangle (a click places a 20 x 20 px square) and link-aware ROI position tuning (plan 1.2d). (Moving a pixel mask: built 2026-11-19, "Moving a pixel map".) (W / H / angle as table columns: built 2026-11-19, next section.)

### Width, height and angle columns (BUILT 2026-11-19, maintainer: "move to point 2"; UNSEEN BY EYE)

Three fixed columns **W**, **H**, **θ** in the ROI / Groups table (`rows.COLUMN_WIDTH / COLUMN_HEIGHT / COLUMN_ANGLE`, `RECTANGLE_COLUMNS`), after D_r, in the Geometry view.
- **Only a rectangle has a value**: a circle, a mask and a reference-ring cell are blank and not editable (the same rule the diameter columns follow through `SHAPE_FIELDS`;
  a rectangle's D_s / d_r / D_r are blank). W and H are lengths in the display unit (px or µm); **the angle is degrees clockwise whatever the unit is** (`parse_angle`, test
  `test_a_length_follows_the_unit_but_the_angle_never_does`: with the angle parsed as a length, 45 typed in µm stored 90).
- **Edits** go through `RoiToolbox.resize_rectangles` (the same command as the handles and the size dialog: one undo step, a selection of several rectangles all get the
  value, a circle in the selection is skipped, width / height below the minimum are refused on the status bar). Copy, paste, fill down, Up / Down in the editor and Ctrl+wheel
  work as on the diameters (an angle steps 1 degree, Shift 5; it has no lower limit and is stored as typed: 370 stays 370). Sorting works (a circle has no width: it sorts first).
- **The three columns are shown only while the table holds a rectangle** (`RoiTreeModel.has_rectangles`, `RoiTreeView.refresh_column_visibility`, which the panel already calls
  after every rebuild): in a circle-only session the table looks exactly as before, no empty columns. A column the user hid stays hidden. This is the rule the old
  `SHAPE_FIELDS` note announced for extra geometry columns ("shown only when some ROI has one"); to show them always, drop the `no_rectangles` clause.
- **Why appended, not inserted (the plan's worry about renumbering):** the new columns are numbers 8-10, after Shape (7), so every saved hidden-column key and frozen column
  (fixed columns are saved by number) still means the same column; step columns are found by `COLUMN_COUNT + i` and saved by step reference, so they follow by themselves.
  The saved width list has 11 entries now; a list of 7 or 8 is still read (the columns it did not have keep their default width). A build older than this one reads the
  11-entry list as unreadable and falls back to default widths (harmless).
- Tests: `test_lspri_rewrite_roi_table_rows.py` (angle parse / show / step, rows carry W / H / θ, sorting, the Geometry view's column list), `test_lspri_rewrite_roi_table_panel.py`
  `RectangleColumnsTest` (16: visibility with and without rectangles, user-hidden, cells and editability, edit + undo, selection, refusals, unit, sort, copy / paste / wheel, editor
  keys, old width lists, painting). Mutations caught: angle parsed as a length; columns never auto-hidden. Updated: the freeze test's "far right column" was the last *fixed* column
  and is now the last one on screen (D_r).

### Area column (BUILT 2026-11-19, maintainer's request; UNSEEN BY EYE)

A fixed column **Area** (`rows.COLUMN_AREA` = 11, `COLUMN_COUNT` = 12) in the Geometry view, **on screen right after Shape** (before D_s / W): pixels with unit px (a count, no decimals), µm² with unit µm (one decimal).
- **What it is:** the area of the ROI's own region, `roi_shapes.shape_area_px`: circle π (D/2)², rectangle W x H, mask = its pixel count (exact). It is the *shape's* area, not the number of pixels the
  scan measured: that whole number moves with the centre's sub-pixel position and with each wavelength's chromatic shift (so no single count exists) and can differ by a pixel or two at the edge.
  The cell's tool tip says so. A reference ROI shows its own region; the border ring is not counted.
- **Read only:** not in `EDITABLE_COLUMNS` / `NUMERIC_COLUMNS` (no paste, fill down, wheel step or cell picking), right-aligned through the model's `_RIGHT_ALIGNED_COLUMNS`. Sorts like any column.
- **Appended, then moved on screen** (the W / H / θ approach): it is model column 11, `RoiTreeView.set_view_mode` moves it after Shape, so every saved hidden-column key and frozen column still means the same
  column. A saved width list of 11 entries is read (Area keeps its default; `panel._LEGACY_COLUMN_COUNTS`). The delegate's "no hairline on the last column" now asks the header for the rightmost
  column on screen (`RoiTableDelegate._last_column_on_screen`), not the model's last one (which would have been the Area, in the middle).
- Tests: `test_lspri_rewrite_roi_table_rows.py` (area per shape, unit, sort), `test_lspri_rewrite_roi_table_panel.py` `AreaColumnTest` (11: position, Simple view, px and µm values, resize, read only,
  tool tips, sort, hide, old width list, rightmost column).
- **Decided (maintainer, 2026-11-19): the shape's area, as built.** Not the pixel count of the layout at the reference wavelength (that needs the image size and a rasterize per ROI on every table
  refresh). Not built, only if ever wanted: the scan's own `n` per step as a step column.

### Mouse wheel edits the numbers of a selected row (BUILT 2026-11-19, maintainer's proposal; UNSEEN BY EYE)

In the Geometry view, **the wheel over a number of a selected ROI row steps that number** (x, y, D_s, d_r, D_r, W, H, θ); Shift = the large step; Ctrl+wheel does it over any row's number (as
before). Over anything else (an unselected row, a group header, the label, the shape, a blank cell) the wheel scrolls the table as usual (`RoiTreeView.wheel_steps`, `wheelEvent`).
- **Steps:** a length 0.5 px (Shift 5 px; in µm a whole µm, Shift 10 µm); an angle 1 degree (Shift 5). One step per full notch (120 units): smooth wheels and touchpads add up their small turns
  (the count restarts when the direction changes).
- **What a step does to several selected ROIs:** a **position moves them all together** by the step (`translate_rois`: the arrangement stays; typing an x or y still sets one value
  on all of them = lines them up); a **size or angle is set on all of them** to the hovered ROI's stepped value (as typing is, and as the rectangle handles do). A pixel mask in the
  selection is refused with the usual message and nothing moves.
- **One undo step per notch** (a 10-notch scroll is 10 undo steps). Merging a burst into one entry would need an open undo batch across a timer; `begin_batch` raises when another batch is
  open (an image drag within the pause), so it was not done. Say if it is wanted.
- **Found on the way:** the table shows a change only after a ~100 ms pause, and a step read the number the cell *showed*, so a second notch inside that pause started from the old value
  and lost a step (the old Ctrl+wheel had the same flaw). A step now starts from the stored value (`panel._live_edit_text`, `model.edit_text_of`); the test fails with the old code
  (`test_fast_notches_do_not_lose_steps`). Each notch is a geometry change, so the cells it touches go stale like after any edit; the scan is not rerun.
- Gestures are rows in `shortcuts.py` (`mouse.roi_table.step`, `mouse.roi_table.step_any`, Help -> Keyboard shortcuts). Tests: `WheelEditTest` (10, real `QWheelEvent`s). Mutations caught: step from
  the displayed text, no "selected" condition, positions set absolutely.
- **Trade-off to watch:** scrolling the table with the pointer resting on a *selected* row's numbers now edits them. Scroll with the pointer on an unselected row or the label column, or
  deselect first. Everything is undoable.

### Copy and paste of cells (BUILT 2026-11-19, maintainer's design; UNSEEN BY EYE)

**Picking cells.** A click on a row selects it as always. **A plain click on a number of an already selected row, then dragging, picks cells** (Geometry view, number columns only, no
Ctrl / Shift / Alt): the row selection stays as it is (also with several rows selected: the click does not collapse it to one row), and the picked range is *the dragged rectangle
restricted to the selected rows* x the number columns in it (the Shape column between y and D_s is never part of it). It is drawn with a tint and a border where the block ends.
A click anywhere else, another ROI selection, Esc, the Simple view or hiding a picked column lets go of it (rows no longer selected drop out of the range one by one). The table
scrolls while the pointer is at or past its edge during the drag; the wheel only scrolls during a drag. **Drag and drop of ROIs (reorder, group) now starts from the ID / Shape cells**
or from an unselected row; from a selected row's number it is the range. A double-click still opens the editor: the press that starts a range lets Qt's own press run (that is what
Qt checks before it edits), and the drag-and-drop state it starts is stopped. Code: `RoiTreeView._begin_cell_range / _extend_range / _autoscroll_tick`, state in `cell_marks.py`.

**Copy** (Ctrl+C, the menu): the picked cells, or with none picked the current cell. The clipboard gets the numbers as the table stores them (pixels, degrees, so a unit change in
between is harmless; MIME `application/x-lspr-roi-cells`, `cell_clipboard.py`) **and** tab-separated text as shown (for other programs). The copied cells get the **marching ants**
(dashed border that moves, 120 ms steps; stops while the panel is hidden) until **Esc**, "Cancel cell selection" in the menu, or until something else is copied (here or elsewhere:
the clipboard's `dataChanged` is watched). The copy is remembered by ROI id and column, so the ants survive the table being rebuilt, sorted or re-grouped.

**Paste** (Ctrl+V, the menu), `cell_clipboard.plan_paste` (pure, tested):
- **Only matching columns are filled** (d_r into d_r, W into W; a position never into a width). The target is the picked cells if some are picked (their rows and columns), else the selected ROIs
  with every column the copy has (the current row alone if nothing is selected).
- **One copied row fills every target row; several copied rows repeat in order** down the target rows until they run out. **One target row and several copied rows: the block runs on down from
  that row** (Excel), is cut where the table ends (said), and **what was pasted is then selected** and picked (so it can be copied again).
- A cell with no value to copy, or a target row with no such cell (a circle has no width), is skipped and counted in the status line ("2 rows have no W"), never filled with a guess.
- **A paste that cannot be done is refused with a small tip at the pointer** (and in the title bar): no matching column ("copied d_r, selected D_s"), nothing to paste into, or a value that breaks a
  rule. **All or nothing**: `RoiToolbox.set_cell_values` checks every ROI first with the rules of typing the value (sample diameter / width / height >= 2 px, ring `0 <= inner < outer`, a
  pixel mask's position refused) and names the first ROI that fails.
- **One undo step and one signal** however many ROIs and columns (`set_cell_values`: each ROI gets its own numbers, which `resize_rois` / `place_rois` cannot say; it shares the circle and
  rectangle rules with them through `_resized_circle` / `_resized_rectangle`, tested equal). Text from another program still pastes its first value into the current column, as before.
- **Menu** (right-click a ROI row): Copy cells (Ctrl+C), Paste cells (Ctrl+V), Cancel cell selection (Esc), enabled when they can act. A right-click keeps the picked cells.

Tests: `test_lspri_roi_cell_paste.py` (26: payload, paste rules, marks' edges, `set_cell_values`) and `CellCopyPasteTest` in `test_lspri_rewrite_roi_table_panel.py` (27, real mouse events).
Mutations caught: no Qt press when a range starts (double-click stops editing), unselected rows in the range, columns not matched, no repeating pattern.
**Not done / open:** an Excel-style multi-value paste *from another program* (its first value only, as before); Shift+click / keyboard extension of the range; dragging the range's edge.

### Half-pixel fix (DONE 2026-11-18, maintainer's request: "circle half-pixel offset, can you fix it?"; UNSEEN BY EYE)

**The finding, stated exactly.** Two coordinate systems meet at a ROI. *Pixel-index coordinates* (pixel `(row, col)` is the point `(col, row)`): the scan, the
detectors, the ROI's stored centre (the ROI table's X / Y), a mask's pixels and `RoiToolbox.display_positions`. *View coordinates* (what the pointer reports and the
canvas draws in; pixel `(row, col)` is the cell `[col, col+1)`, its middle at `col + 0.5`). A circle was drawn around the stored centre as it stood, so it sat
0.5 px up and to the left of the pixels it measures; a ROI placed by a click was stored in view coordinates, so it measured pixels 0.5 px down and to the right of
where it was clicked. (The detectors, e.g. refine, work on pixel arrays and always stored index coordinates, which is why the drawing, not the storage, was
the odd one out.)

**The fix is at the view boundary only; no stored number changes.** Nothing in the scan, the rasterizer, the cell hashes, the circle golden pins, a saved session
or `scan.h5` is touched (`SCAN_ALGORITHM_VERSION` is not bumped, no scan goes stale). Where the shift lives:
- `overlay_geometry.CELL_OFFSET` (docstring: the whole convention) and `RoiQueries.view_centers(frame, affine)` = `display_positions + CELL_OFFSET` (the shift
  is added **after** the affine: the affine maps pixel-index coordinates to pixel-index coordinates). Every drawing of a ROI (`ImagePanel._draw_overlay_items`:
  circles, rectangles, borders, labels, link lines, rectangle handles, signal-map colours) and every question of "where is the pointer relative to a ROI"
  (`roi_at`, `region_at`, `rois_in_rect`, `rois_touching`, `selected_apertures` -> the border / handle / move / resize gestures) takes its centres from there.
- `RoiQueries.display_to_reference(x, y)` (view point -> stored centre) subtracts the shift first. It is the exact inverse of `view_centers` (round-trip tested).
  Used by the right-click Add ROI > Circle / Rectangle, and **now also by the Add ROI tool's click** (`RoiCreation.add_picked`), which used to store the raw pointer
  position (so it also ignored the chromatic affine away from the reference wavelength; both are fixed by the one call).
- `tuning_overlay` (the crosses of ROI position tuning) adds the shift to its points, so a cross at a ROI's own centre is the middle of that ROI's circle.
- Masks needed no change (`shape_paths_item.placed` and `shape_hits` already used `CELL_OFFSET`); a mask's stored centre is the mean of its pixel indices, so its
  label point is shifted like every other centre. `shape_hits` and the gestures work on differences between the pointer and a centre, so the shift cancels there.
- The ROI refine action (`array_actions`) deliberately still passes pixel-index centres: it reads pixels.

**Before / after** (`scratchpad` script, 300 random ROIs per row, the pixels the scan reads, `rasterize_sample`, against the middle of the drawn outline, both in
view coordinates; same code as `tests/unit/test_lspri_roi_cell_offset.py`, which keeps the check):

| shape, frame | mean offset before (x, y) | after (x, y) | worst single ROI before -> after |
|---|---|---|---|
| circle 12 px, identity | (-0.500, -0.501) | (-0.000, -0.001) | 0.663 -> 0.164 px |
| circle 20 px, identity | (-0.502, -0.504) | (-0.002, -0.004) | 0.646 -> 0.148 px |
| circle 40 px, identity | (-0.501, -0.500) | (-0.001, -0.000) | 0.579 -> 0.079 px |
| circle 12 / 20 / 40 px, chromatic and rotated frames | -0.50 .. -0.50 | within +-0.005 | worst 0.66 -> 0.18 px |
| rectangle 30 x 14 at 33 deg, identity / chromatic / rotated | (-0.50, -0.50) | within +-0.002 | 0.59 -> 0.09 px |

The residual per ROI (worst 0.18 px for the smallest circle) is the pixel grid's own rounding of which pixels count; averaged over ROIs it is zero.

**What the maintainer will see.** Every circle and rectangle is drawn half a pixel further down and to the right than before, now on the pixels it measures
(the measured pixels and numbers are exactly as before). A ROI added by a click is stored half a pixel back from the click, so its table X / Y read the pixel index of
its middle pixel (click in the middle of pixel 100 -> X = 100.0), which is also what the cursor readout's `(col, row)` shows. ROIs already in a session keep their
stored centres. Tests that gave the pointer in stored coordinates now go through a `scene()` helper that adds half a cell; the ones that checked a drawn or
placed position say "stored + `CELL_OFFSET`" (gestures, timeline, overlay controls, tuning overlay, phase 3 UI, queries); three mutations (no shift in
`view_centers`, in `display_to_reference`, in the tuning crosses) each fail at least one test.

**Chromatic landmark overlay (checked and fixed 2026-11-19).** `landmark_overlay.py` (fitted dots, tracked crosses, misfit lines) drew pixel-index points unshifted: the
tracker (`auto_landmarks`: LoG peaks and `cv2.getRectSubPix`, both pixel-index) and the affines are in pixel-index coordinates, the canvas is not. Measured on the
synthetic sweep (`tests/_lspri_chromatic_synthetic.py`, known disk centres, 12 landmarks): the dots sat **(-0.50, -0.50) px** from the middle of the disk they mark
(the tracker itself is unbiased); after adding `CELL_OFFSET` in the overlay's one `shown()` step, after the affine, the offset is **(0.000, 0.001) px, worst 0.011 px**
(reference wavelength, correction on or off). Display only: no tracked number, model, scan or session changes. Test:
`test_lspri_rewrite_chromatic_tab.py::test_landmarks_are_drawn_on_the_features_they_mark` (fails with the shift removed). (A pixel map could not be moved then; since 2026-11-19 it can, by whole pixels: see "Moving a pixel map".)

### Rectangle speed, border drag, rotate cursor (DONE 2026-11-18, maintainer's three requests; UNSEEN BY EYE)

The maintainer reported: rectangle ROIs draw / redraw slowly, especially while resizing or rotating; the cursor while rotating should be a rotate cursor, not the "hold
handle" hand; and a rectangle's reference ring (border) cannot be dragged the way a circle's ring can.

**1 Speed: what was measured.** Offscreen, a 1200 x 1000 frame, ROIs on a lattice, a real window with every panel; times per drag step (toolbox write + overlay draw) and
for a repaint of the view. The scripts were scratch (not kept).
- One rectangle selected among many: draw and paint were about the same as circles (draw 9.6-11.9 ms vs 6.5 ms at 150 ROIs, repaint 22 ms both). Not where the report came from.
- **Many rectangles selected** (a resize or a turn applies to every selected rectangle, so this is the usual case): the repaint cost **3.4 x** that of the same number of
  selected circles (150: 112-128 ms vs 34 ms; 500: 332 ms vs 108 ms, a busy machine), and the toolbox write (0.5 ms) and the overlay data (6-12 ms) were not the problem.
  Per overlay item, with all selected, the rectangle handles alone were 26 ms of 49 ms at 500; the rectangles' own outlines were cheaper to paint than circles' (4 vs 14 ms).

**Causes and fixes** (`panels/image/rectangle_handles_item.py`, `roi/shape_hits.rectangle_handle_arrays`):
1. The item worked out every handle position in Python inside `boundingRect()` (Qt calls it several times per paint) and again in `paint()`. Now one numpy calculation
   (`rectangle_handle_arrays`, `(N, 9, 2)`; `rectangle_handle_points` is its one-rectangle form, so what is drawn is what the hit test grabs) made when the rectangles or the zoom
   change and kept until then. A test counts the calculations: none for 25 `boundingRect()` calls and two repaints, one after a change.
2. **A path of 4,000 antialiased squares costs 43 ms to paint, one `drawRect` per square 7.5 ms, `drawPoints` with a square-capped cosmetic pen 2.7 ms** (benchmarked in
   isolation, 500 rectangles = 4,000 squares): a cosmetic point *is* a square of constant screen size. My first rewrite (one path) was slower to paint than the code it replaced;
   the benchmark is what showed it. A handle is a dark point under a smaller light one, the knob a round-capped pair, the stems one path.
   **Rule: many small marks are drawn with `drawPoints` and a cosmetic pen, never as antialiased path subshapes** (eva `CLAUDE.md`, Pitfalls).

**Result** (repaint of the view, every ROI selected; the old item loaded from git next to the new one, same machine, back to back): 150 rectangles **53 -> 12 ms**,
500 rectangles **170 -> 37 ms**; the same numbers of selected circles took 11 and 36 ms. Repeated on a busier machine: rectangles 21-22 ms vs circles 19 ms (150), 55-56 vs 56-57 ms (500):
rectangles now paint like circles. A drag step (write + draw) is 4-7 ms at 150 and 13-21 ms at 500.

**Still slow, not rectangle-specific (not changed):** `roi_overlay.set_fill_outlines` (the reference-ring fill: one `addPolygon` per ring, about 9 ms of a 24 ms draw at 500 ROIs,
circles pay it too); the Histogram's ROI-mask union (`histogram/roi_masks.py`, 130 ms for 150 circles, 175-260 ms for 150 rectangles, on a worker thread restarted by every
drag step: it costs the GUI thread about 2 ms per step through the interpreter lock, and the ROI-count limit for doing it inline is 40). Both are candidates if a drag still
feels slow with hundreds of ROIs.

**2 The border band of a selected rectangle can be dragged** (`roi/shape_hits.py`, `RoiGestures` mode `"border"`, `RoiToolbox.set_borders`).
- The band is the pixels `gap` to `gap + thickness` from the rectangle's edge (`roi_shapes.Border`); the two edges of the drawn band are the offset outlines
  `rectangle_points` draws (rounded outside, a smaller sharp rectangle inside). `rectangle_signed_distance` is the distance a border is measured by (positive outside), the same
  for every frame (identity, chromatic, rotated, stretched: tested against the drawn outlines). `rectangle_border_zone` says which edge a point is on: the zone reaches the screen tolerance
  either side of the edge but at most half the thickness (as a circle's ring), and returns its distance to the band edge and to the rectangle's own edge, so the nearer wins
  (a thin gap does not hide the width / height handles; the knob always wins).
- **Inner edge: sets the gap** of every selected rectangle to the distance under the cursor (each keeps its own thickness, as a circle's ring keeps its width); it cannot go past the middle
  of a rectangle's shorter side (a negative gap, a band starting inside the shape, is allowed). **Outer edge: sets the outer distance**, so each rectangle's thickness is that distance
  minus its own gap (never below `MIN_RING_GAP_PX / 2` = 0.5 px). One undo step for the drag; the first edit gives the ROI a border of its own (it followed the shared default before; undo
  puts that back).
- Grabbable only for a selected rectangle with a border that is enabled and has a thickness, that is not a reference ROI (it is one region with no border), and while the reference rings are
  shown (an invisible border must not be grabbable). A circle in the selection, an unselected rectangle and a pixel mask are left alone (a mask's border has no drag: not built).
- Help -> Keyboard shortcuts: the rectangle row now says so.

**3 Cursors** (`panels/image/rotate_cursor.py`, `interaction.py`, `RoiGestures.rotating` / `resize_direction`).
- The knob shows a **rotate cursor** (Qt has none: the app's own Rotate-tool glyph, Tabler `rotate-clockwise-2`, white over dark so it reads on any image, 26 px) on hover and **for the whole
  turning drag**, also when the press came without a hover first. Nowhere else.
- A rectangle's edge, corner or border edge shows the two-headed arrow along **its own drag axis** (width edge: the rectangle's x axis on screen; height edge: its y axis; a corner: its
  diagonal; a border edge: the outward normal where the pointer is), not along the radius from the centre (which was diagonal for a point on an edge away from its middle). A circle keeps the radial arrow.

**Tests:** `tests/unit/test_lspri_rectangle_border_geometry.py` (vectorised = one-rectangle handles, signed distance against the drawn outlines in four frames, zones, nearest-edge distances,
cursor directions), `tests/integration/test_lspri_rectangle_border_panel.py` (the drag with every rule above, undo, clamps, multi-select, reference ROI, hidden rings, the cursors on hover and
through a drag, no recalculation per `boundingRect()` call, handles painted where they can be grabbed). Mutations the tests catch: no handle cache, open hand again on the knob, no cursor at drag
start, a border drag that stores nothing. Updated: `test_lspri_rectangle_handles_panel.py` (view coordinates, the rotate cursor).

**Open / maintainer's decisions:** handles are drawn for every selected rectangle (150 selected = 150 sets of handles, as before; only the cost is gone); a pixel mask's border cannot be dragged;
the rotate cursor's look (size, colours) is unseen by eye.

### Rim pairs, linked pairs, the Type column (R1-R5 and the Offset / Thickness columns BUILT 2026-11-19; UNSEEN BY EYE)

**The idea (maintainer).** Two kinds of sample / reference pair, and moves between them: a **rim pair** (the sample measures against its own built-in border) and a **linked pair** (the
sample is linked to a reference ROI of its own). The table's Shape column becomes **Type** and shows shape *and* kind; its icons are coloured by kind. Terminology: **"rim"**
in everything the user reads (labels, tool tips, undo labels, the shortcut table, docs); the code keeps `Border`, `border` and the session keys (renaming those would change saved
files and the cell hash for nothing).

**Decisions (maintainer, answers to the questions of 2026-11-19).**
1. **Rim pair -> linked pair:** the rim's pixels become a **pixel map**, a new reference ROI linked to the sample (a frozen copy of the rim as it is at the reference wavelength: it
   no longer follows the sample, as decision 4 above says; a map moves by whole pixels on its own, "Moving a pixel map"). One new reference per sample, however many blobs the rim has. Only for a sample that
   has a rim and no links. ~~The sample's own rim is kept, dormant~~ **Changed the same day (maintainer): the sample becomes a rim-less ROI**, see "Turn rim off" below. One undo step.
2. **Pixel map -> rim pair:** a map that is a sample has a rim like any shape (`Border(gap, thickness)`, the shared default 4 + 4 px unless it has its own). New command **Add rim**
   (a free reference or a rim-less sample becomes a sample with a rim; one undo step). Gap and thickness are editable in the table: **the same two columns for every shape**, Offset and Thickness (maintainer, later the same day: they replaced the
   circle's ring-diameter columns d_r / D_r instead of being added; see "Offset and Thickness columns" below).
3. **Each blob of a map gets its own rim; the rims together are one reference region.** The band is the union of the bands of the blobs (8-connected blobs, as `mask_regions`),
   each measured from its own edge; the pixels of the map's *other* blobs are left out of a blob's band. (Measured from the map as a whole, a point near blob B but in the band of blob A
   is excluded; per blob it is included. Identical for a one-blob map: kept bit for bit.) A change of what a multi-blob map's rim covers, so such a ROI's cell hash gets a marker
   (the `|reference` pattern: only an affected ROI changes, no `SCAN_ALGORITHM_VERSION` bump).
4. **Type column** (was Shape): one pictogram of the shape (circle, rectangle, map) with a **ring drawn round it when the ROI measures against its own rim** (a linked sample, whose rim is
   dormant, and a reference show the plain shape). The icon is coloured **by kind**, not by the ROI's own colour (the dot in the first column keeps that): **green** = a sample with a
   rim or with links, **amber** = a sample with neither (no reference: its numbers are missing), **blue** = a reference linked to a sample, **gray** = a reference nobody uses. The colours
   apply to the Type icons only (not to the dot, the image or the plots).

**Build order (each step tested before the next; relevant test files only until the end).**
- **R1** per-blob rim in `roi/rasterize.py` + cell-hash marker; before / after on realistic masks; `test_lspri_roi_shapes.py`, golden pins untouched.
- **R2** toolbox: `rim_to_reference` (needs the frame shape: the panel hands it over), `add_rim`; tests incl. a **numbers check**: the new reference's pixels against the rim's, per wavelength
  of a realistic chromatic affine (the map is warped, the rim is measured: the two pixel tests differ at the edge; quantify).
- **R3** menus (the one ROI menu builder, `menu_icons.ICONS` lines), status-bar messages.
- **R4** Type column: keys, icons, colours, tool tips; then Gap / Thickness columns.
- **R5** wording: "ring" -> "rim" in the user's text; docs; the eva `CLAUDE.md` lines.

**What was built (2026-11-19) and measured.**
- **R1 per-blob rim** (`roi/rasterize._mask_band`, `_mask_band_at`; `mask_regions.EIGHT_CONNECTED` / `blob_count`): a pixel map's rim is the union of the bands of its 8-connected blobs, each from
  its own edge, without the pixels of the other blobs; a one-blob map is bit for bit what it was (the old whole-map band is the one-blob case). The cell hash of a many-blob map with a rim carries
  `|blob-rims` (`frame_reduce._update_with_free_shape`), so only such a ROI goes stale; every pin of `test_lspri_scan_circle_golden.py` is untouched. **Measured** (1 ROI = 10 x 16 disc blobs of 10 px,
  1200 x 1000 frame, rim 4 + 4 px): pitch 40 and 25 px identical (44,160 and 41,220 rim pixels); pitch 20 px 20,346 -> 32,694 pixels, 16 px 5,247 -> 23,415, 12 px 2,580 -> 11,772: **only gains**
  (the whole-map band had dropped every pixel near a neighbouring blob), never a pixel lost; also **3-5 x faster** to build (31-71 ms against 206-292 ms: a small distance map per blob instead of one
  for the whole bounding box). Tests: `tests/unit/test_lspri_pixel_map_rim.py` (9, brute force with `cdist`; the whole-map mutation fails 2).
- **R2 toolbox** (`RoiToolbox.rims_to_references(sample_ids, image_shape)`, `rim_refusal`, `add_rim`, `can_add_rim`): the rim's pixels at the reference wavelength (identity affine, the sample's own pixels
  left out) become one pixel-map reference ROI per sample, linked; (the sample's own rim was kept dormant at first, since changed: "Turn rim off" below); one undo step; refusals (a reference, already linked, no rim, rim outside the image)
  change nothing. `add_rim`: an unused reference becomes a sample, a sample with its rim off gets the shared default (4 + 4 px); one undo step (batch). **Measured, the conversion's numbers**
  (1200 x 1000 sloped noisy frame, noise sigma 6 counts, reference-mean SEM 0.3): at the reference wavelength **identical** (0 pixels differ, circle and rectangle); under the golden set's chromatic
  affine (shift ~2.5 px, scale 0.4 %) about 3 % of the rim's pixels differ (circle D 20: 13 out, 12 in of 405; rectangle 24 x 12: 23 and 23 of 436), under a ~10 px shift 7-10 % (the map is warped
  nearest neighbour, the rim measured exactly: a pixel in on one side for a pixel out on the other); the reference mean moves by **0.1-0.25 counts on 1000 (100-250 ppm), the size of its own noise**,
  with no sign. A converted reference is a frozen copy of the rim at the reference wavelength. Tests: `tests/unit/test_lspri_rim_pairs.py` (15).
- **R3 menus**: right-click ROI menu **Add rim** and **Turn rim into linked reference** (left out when they cannot be used; `roi_context_menu.py`, `roi_creation.py`, `interaction.py`; icons
  `add_rim` = the ROI tab's sample-in-rim picture, `rim_to_reference` = the link icon, in `menu_icons.ICONS`; no new vendored icon). The status line names what was done and how many selected
  ROIs were left out. Tests: `test_lspri_roi_phase3_ui.py`.
- **R4 Type column** (was Shape): `rows.icon_of` / `kind_of` (`ICON_*`, `KIND_*`), `type_icons.py` (painting, `kind_color`), `RoiRow.type_icon` / `.kind`, `TYPE_ROLE`; a ring (blue, the reference colour)
  round the shape only while the ROI measures against its own rim; fill: green (rim or links), amber (no reference), blue (reference used), gray (reference free); the dot of the first column keeps
  the ROI's colour. The cell text is "Circle with rim", "Pixel map", ...; the tooltip adds what it is measured against and the size; the column sorts the kinds together. `row.shape` (the editing key,
  `SHAPE_FIELDS`) is unchanged. Tests: `test_lspri_roi_table_type_column.py` (13, pictograms painted into an image and read back).
- **R5 wording**: "ring" and "border" became "rim" in everything the user reads (labels, tool tips, status and error messages, undo labels, the shortcut table); code identifiers (`Border`, `ring_radii`,
  `border` in the session file, `scan.h5` `layout_json`) are unchanged on purpose: a rename there would change saved files and cell hashes for nothing.

**Turn rim off, and the cube the conversion reads (2026-11-19, later; maintainer's report and decisions; tested headless, UNSEEN BY EYE).**
- **The bug.** A sample dragged on any cube after the first is stored as a *timeline change* ("from this cube on"), not in the ROI's base position. `rims_to_references` rasterised the base position, so the new
  reference landed where the sample used to be, and the sample kept its own rim besides (dormant) and the link. Reproduced: base (40, 50), moved on cube 5 to (120, 100): the reference was centred at (40, 50).
  Fix: `rims_to_references(..., cube=)` reads `resolved_at(roi, cube)`; the Image panel passes the cube on screen (`RoiCreation.rim_to_reference`). The new reference has no timeline: it holds on every cube.
- **`RoiToolbox.turn_rim_off(ids)`** (right-click ROI menu, "Turn rim off"; icon = the ROI tab's ring crossed out): the sample becomes a rim-less ROI. Only `Border.enabled` changes: the shape (circle, rectangle, pixel
  map) is untouched and **gap and thickness are kept** (`roi_shapes.border_switched_off / _on`; a ROI that followed the shared default keeps the default's numbers as they were). `add_rim` is its inverse and
  brings the same rim back (the shared default size only for a rim that never had a size, `NO_BORDER`). Offered for any sample whose rim is on, linked or not, so it also clears the dormant rim of a sample
  that was linked by hand. Nothing to change = no undo step. **No format change**: the session already stores gap, thickness and the switch together, in the base geometry and in every timeline change.
- **"Turn rim into linked reference" now ends with the sample rim-less** (one undo step: new reference, link, rim off). The linked reference is the only reference the sample has; unlinking leaves it with none until
  Add rim.
- **Scope (maintainer): the rim's size follows the scope rules (persistent / individual at the cube, like any geometry edit); the on / off switch is for every cube** (`RoiToolbox._switch_rims` writes the base geometry and
  every timeline change in one step; `_rim_states` reads them all). So a ROI is "with rim" or "rim-less", never one on some cubes and the other on the rest, and the Type column, the overlay and the pairing never
  disagree with the cube on screen.
- **Rescans (maintainer: ROIs work independently of their links; only what changed is scanned again).** The scan stores every ROI on its own and pairs afterwards, so a conversion makes stale only the ROIs whose own pixels
  change: **the converted sample** (it reads no rim any more; its cell key holds the rim) **and the new reference** (never scanned). Every other ROI keeps its cell key; undo restores every key.
  Test: `OnlyWhatChangedIsScannedAgainTest`. **Measured limit (existing, not new):** the cell key of a ROI also holds the *whole row digest* of every neighbour whose sample disc its own rim can touch, ring flag
  included. On a lattice where rims reach neighbouring discs (circles of 20 px, rim 4 + 4 px, pitch 28 px) turning one rim off makes its 4 direct neighbours stale too (5 of 35 ROIs); at pitch 40 and 60 px only
  the ROI itself (1 of 35). The neighbours' pixels do not change (they exclude the *sample discs* of the others, which a rim switch leaves alone), so those rescans are needless. A fix (only the neighbour's sample-disc
  part into the key) would change every stored cell hash of a packed lattice: it needs the `restamp` recognition or a one-time rescan, so it is a decision for the maintainer, not part of this change.
- Tests: `tests/unit/test_lspri_rim_pairs.py` (28: the cube bug, every cube, undo, save / load, scan keys), `tests/integration/test_lspri_roi_phase3_ui.py` (46: the menu, the panel path on cube 1).

**Offset and Thickness columns (BUILT 2026-11-19, maintainer: "the rim columns should be the same for rectangle, circle and map, thus offset from edge and thickness of band/rim"; UNSEEN BY EYE).**
- **Headers are short: `d` (offset) and `w` (thickness)** (maintainer, same day; `COLUMN_TITLES`; the header tool tips spell them out, like D_s / W / H). Model columns 5 and 6 (`rows.COLUMN_RIM_OFFSET`, `COLUMN_RIM_THICKNESS`, were the circle's `COLUMN_RING_IN` / `COLUMN_RING_OUT`, the ring's inner and outer *diameters* d_r / D_r) now show the rim's
  **offset** from the shape's edge (negative = the rim starts inside the shape) and its **thickness**: the same two numbers for a circle, a rectangle and a pixel map, in the display unit. No new
  column and no renumbering: every saved hidden-column key, frozen column and width list still names the same two columns (a saved width may be too narrow for the longer title "Thickness"; drag it).
- A ROI has the cells while it has a rim to measure (`RoiRow.has_rim`: not a reference, rim not switched off; a linked sample's dormant rim counts); otherwise blank and not editable. Grey italic
  while the rim is the shared default's. `rows.has_cell` is the one answer; `SHAPE_FIELDS` now lists only D_s (circle) and W / H / θ (rectangle).
- Editing: `RoiToolbox.set_rim(ids, offset_px=, thickness_px=)` (any shape; one undo step; refuses a reference, a ROI without a rim, a non-finite number, a negative thickness) and the same rule inside
  `set_cell_values` (paste, fields `rim_offset_px` / `rim_thickness_px`: all or nothing). A ROI that follows the default gets a rim of its own with the typed number and the default's other one. Up / Down and the
  wheel step an offset through zero (`step_text(signed=True)`); a thickness stops at 0. A circle's numbers are the same rim `resize_rois` stores for ring diameters (identical radii, tested): the circle's
  ring drag on the image still works in diameters, the table shows the result as offset / thickness. A pixel map's rim can now be sized (per blob, see above).
- **Not changed:** `resize_rois(reference_inner_diameter_px=...)` and the cell fields `reference_inner_diameter_px` / `reference_outer_diameter_px` stay in the toolbox API (the image's ring drag and the Array
  tools use diameters); the table no longer uses them. Tests: `test_lspri_roi_table_rim_columns.py` (12); the five older table test files were updated from diameters to offset / thickness.

**Not built / open.**
- The Linked view's row order and the table's colour for a *dormant* rim are as described above; nothing marks that a linked sample still has a rim it is not using (the tooltip does not say either).
- **Unseen by eye:** the pictograms (sizes, the jagged map-with-rim outline), the menu entries and their icons.

### Moving a pixel map (BUILT 2026-11-19, maintainer: "can you make map ROI movable, same like rectangles or circles?"; UNSEEN BY EYE)

A pixel map (`MaskShape`: a ROI made from a selection, or the rim of a sample turned into a reference) moves like a circle or a rectangle: drag it on the image, type X / Y, wheel over X / Y,
Shift dialog, paste cells. It used to be refused (`RoiToolbox.move_refusal`, section 2.4), because only its label point would have moved.

**The rule (the one decision, flagged to the maintainer): a map moves by whole pixels.** Its pixels *are* the ROI; moving it by a fraction would mean resampling them (grey edge pixels, other pixels
measured). So `RoiToolbox._at_position(g, x, y)` (the one place a position becomes a geometry, behind `_set_positions`, `translate_rois`, `place_rois`, `move_rois` and `set_cell_values`) shifts the
pixels by `whole_pixels(asked - centre)` in each axis (`roi_shapes.whole_pixels`: nearest whole number, **a half goes away from zero**, so a wheel step of 0.5 px moves a map one pixel either way) and
moves the label point (the centre) by exactly that. The mask array is shared (`shifted_mask` changes only `x0 / y0`), nothing is resampled; the stored pixels are bit-identical, only their place changes.
What the scan measures moves with it: a test compares `rasterize_sample` before and after with `np.roll`.

- **Typed / pasted / wheel numbers:** the X / Y the table shows afterwards are the map's real ones (up to half a pixel from what was typed: type 25.4 for a map at 20.0 and it goes to 25.0).
  When **every** ROI of the call is a map that would move less than half a pixel (and nothing else in the call changes), the call raises "ROI 7 is a pixel map: it moves by whole pixels, and that
  is less than half a pixel, so nothing was moved." (status bar), instead of doing nothing silently. A mixed selection moves what it can (the circles take the 0.3 px, the map stays).
- **Drag** (`RoiGestures._update_move`): circles and rectangles follow the pointer smoothly as before; the maps get **whole-pixel steps worked out from the whole distance dragged so far**
  (`_map_applied`), never per mouse step: 40 steps of 0.14 px each would otherwise move a map 0 px (a mutation test of exactly that fails both drag tests). A map is grabbed on one of its
  **pixels** (`SelectedApertures.body` now has a test for a map; a hole is not a grab, a click beside it starts the rubber band as before). One undo step for the whole drag.
- **Display speed:** `MaskOutlines` now remembers a map's traced outline (sample and rim) **by its pixels, not its place**; a new position costs a translated copy of the path. Measured
  (160 blobs of r = 4 px in a 674 x 880 box, 7,781 pixels): outline of a map at a new position **44.5 ms (retrace) -> 2.9 ms**, its rim 3.8 ms, the toolbox write 0.29 ms per step
  (`scratchpad/map_drag_cost.py`, not kept in the repo).
- **Not changed:** ROI position tuning still refuses maps (`check_tunable`: it scores shifts of a circle's centre); a linked reference still never moves with its sample; a map moved partly
  or wholly outside the image is clipped by the rasterizer like any ROI there (no clamping, as for a circle). Session files are unchanged (a map stores its `x0 / y0` and its centre).
- **Tests:** `tests/integration/test_lspri_roi_phase3_ui.py` (grab by pixel, whole-pixel move with a circle + undo, the scan's pixels move, typed / tiny / wheel halves, drag in small steps,
  drag with a circle), `test_lspri_rewrite_roi_table_panel.py` (wheel, typed value, paste), `test_lspri_roi_cell_paste.py`, `test_lspri_rim_pairs.py`.

## Rim as large as the ROI (2026-11-24)

Right-click ROI menu, **Rim as large as ROI** (shown for a sample whose rim is on): `RoiToolbox.match_rim_area` sets the rim's **thickness** so the rim has the same area as the sample ROI; the offset and the shape stay. The scope / cube rules are those of any rim size (`RoiEditTarget`, as the table's Offset / Thickness cells). One undo step.

* Solver: `roi/rim_area.py` (pure). Circle and rectangle are **exact** (ring `outer = sqrt(R^2 + inner^2)`; rectangle: quadratic from the rounded-corner area `wh + 2(w+h)s + pi s^2`). A pixel map is **counted** the way the scan reads its rim (`rasterize._mask_band`, blob by blob, no cache) and the nearest count is taken; never an empty rim (a one-pixel line jumps from 0 to twice its own count).
* The target is the shape's **geometric area**, not a pixel count: pixel counts of a circle move with its sub-pixel centre and the chromatic shift. On a 24 px circle at an integer centre the sample reads 441 px (area 452) and the rim 464 px, a 5 % gap that is the pixel grid, not the solver. The area is **not cut by the image edge** and not reduced by neighbouring ROIs' pixels.
* Tests: `tests/unit/test_lspri_rim_area.py`.
