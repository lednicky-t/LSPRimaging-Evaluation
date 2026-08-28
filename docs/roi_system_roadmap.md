# ROI System Roadmap

This document is the single, actionable implementation plan for generalizing LSPRimaging
Evaluation's ROI (Region of Interest) system beyond circle-sample / annulus-reference. It
consolidates four earlier planning documents plus an audit of what is actually built today,
so an implementer (human or AI agent) can pick it up later without re-deriving context.

## Relationship to other documents

Four documents already exist that talk about this problem. This roadmap **supersedes them as
the execution plan** — it does not replace their ideas, it reconciles them into one ordered,
buildable path:

| Document | What it contributed | Status |
|---|---|---|
| [`roi_editor_branch_plan.md`](roi_editor_branch_plan.md) | First branch sketch (`RoiPair`/`ShapeSpec`) | **Stale** — its "Current State" section describes `DetectedSpot`/`SpotGroup`, which were renamed away to `AreaRoi`/`AreaRoiGroup` in 2026-08. Keep for historical context only. |
| [`roi_implementation_direction.md`](roi_implementation_direction.md) | Template / Placement / Pair vocabulary, editor tab structure, storage layout ideas | Vocabulary partly adopted below; storage layout superseded by what's actually implemented (see `format_versioning.md`). |
| [`roi_table_direction.md`](roi_table_direction.md) | Compact pair-row table UI spec (columns, status bar, pictograms) | Still the right UI target — referenced directly in Phase 5 below. |
| `GENERAL_ROI_SYSTEM_IMPLEMENTATION_SPEC.md` (external, in `codex_guides`) | Rigorous generic architecture: `ROI`/`MeasurementRegion`/`ROIState`, `ROIManager`, geometry-blind analysis API, HDF5 schema | Its core rule (geometry → mask → statistics, analysis code never branches on geometry) is adopted. Its `ROIManager`/HDF5/many-to-many `MeasurementRegion` machinery is judged over-scoped for this app's actual usage pattern — see "Deliberately deferred" below. |

If a future session picks this up, treat `roi_implementation_direction.md` and
`roi_table_direction.md` as the *vision references* and this file as the *build order*.

## Current state (audited 2026-08-16)

The production analysis path uses `AreaRoi` ([`domain/models.py`](../src/lspr_imaging_app/domain/models.py)),
a single dataclass that bakes one sample region and one reference region together:

- **Circle sample + annulus reference** — fully implemented: detection, the ROI table, array
  stamping, editing dialogs. This is the only geometry that actually drives formula-spectrum
  calculation today.
- **Mask geometry escape hatch** — `sample_geometry_type`/`reference_geometry_type` can already
  be `"mask"` instead of `"circle"`/`"annulus"`, backed by `RoiMask` (cropped boolean array) and
  `crop_mask()`/`expand_mask()` in [`processing/roi_rasterize.py`](../src/lspr_imaging_app/processing/roi_rasterize.py).
  Storage (`workspace.py`) and the formula-spectrum pipeline (`analysis_tasks.py`) both know how to
  *consume* one. Nothing in the GUI *creates* one yet — it only round-trips from a file.
- **Per-frame registration already exists**, just under a different name: `ChromaticTransformModel`
  (one affine matrix per wavelength/spectral-cube index) plus `transformed_disk_mask`/
  `transformed_annulus_mask` in `processing/chromatic.py` is exactly the "store one ROI, apply a
  per-frame transform" pattern the general spec asks for (its §9). This does **not** need to be
  rebuilt — new geometry types just need their own `transformed_*_mask` variant.
- **A second, disconnected editor existed and was removed (2026-08-21)**: `RoiDefinition`
  (`state.rois`, not `state.area_rois`) in `main_window.py`, with Circles / Rectangles / Freehand
  tabs — matching the tab layout `roi_implementation_direction.md` asked for. It never became
  reachable: the Rectangles/Freehand panels were permanent "coming soon" placeholders, their edit
  widgets were instantiated but never added to any visible layout, and the system had zero
  references from `analysis_tasks.py` — it never drove any real spectrum computation. Confirmed
  unfinished scaffolding, not a working parallel path, and deleted outright rather than kept
  around unreachable. Phase 2 below now needs to build the rectangle editor UI from scratch
  instead of "wiring" this scaffolding.
- **Storage is JSON, not HDF5.** `analysis/roi_table.json` (`schema_name="lspri_roi_table"`,
  versioned via `ROI_EXPORT_VERSION`) already round-trips every `AreaRoi` field including mask
  geometry, per `format_versioning.md`. There is no `h5py` usage anywhere in this app — its
  images are TIFF/OME-Zarr, not HDF5, so the general spec's HDF5-centric sections don't fit this
  app's actual file architecture and should not be adopted.
- **Analysis code already branches on geometry type** in a couple of places (`analysis_tasks.py`:
  `if roi.sample_geometry_type == "mask" and roi.sample_mask is not None:`). Small today, but
  exactly the pattern the general spec's §17/§40 warns will get worse as more geometries are
  added — worth fixing before rectangle/polygon geometries multiply the branches.
- **Existing reusable infrastructure for freeform drawing**: `MaskController.apply_mask_brush()`
  in [`gui/mask_controller.py`](../src/lspr_imaging_app/gui/mask_controller.py) already implements
  brush-based paint-a-boolean-mask interaction for the background "figure mask" exclusion
  feature. This is the natural mechanism to reuse for freeform sample/reference ROI drawing
  (Phase 4) instead of writing new interaction code.
- **Tests already cover part of this**: `tests/unit/test_lspri_roi_rasterize.py`,
  `test_lspri_roi_arrays.py`, `test_lspri_roi_table_storage.py` — extend these per phase rather
  than starting a new test module each time.

## Target vocabulary (decision)

To stop three documents pulling in three different naming directions, use this vocabulary going
forward:

- **Sample ROI (sROI) / Reference ROI (rROI)** — the two regions of one analysis unit. Matches
  the rename already completed in the codebase and in `roi_implementation_direction.md`.
- **Pair** — the `AreaRoi` row: one sROI + one rROI + shared metadata (id, label, color,
  created_by, notes). **Not** a many-to-many `MeasurementRegion`. The app has never needed more
  than one sample and one reference area per analysis unit; don't build for a need that hasn't
  shown up (see engineering priority order in `CLAUDE.md` — maintainability over speculative
  generality). If that need ever appears, it's a straightforward later extension of `Pair`
  (`sample_roi_ids: list[str]` instead of one), not a redesign.
- **Geometry type** — `"circle"`, `"annulus"`, `"rectangle"`, `"polygon"` (future), `"mask"`
  (fallback). Kept as a string field on the ROI, exactly as it exists on `AreaRoi` today — no new
  `ShapeSpec`/`GeometryType` class hierarchy. Reuse the field that's already there.
- **Template** — not a persisted class. It's the in-progress shape currently being edited in the
  ROI editor (what `_active_rectangle_template()` already does informally) that gets *stamped*
  into a concrete Pair on click. Formalizing it as its own persisted object (as
  `roi_implementation_direction.md` suggested) would duplicate what a Pair already stores; skip
  it unless a concrete reuse need shows up.

## Phased roadmap

Phases are ordered so each one is independently shippable and testable, and later phases don't
require redoing earlier ones. Each phase names the engineering priority it serves (see
`CLAUDE.md`'s priority order: correctness → data integrity → maintainability → modularity →
performance → polish).

### Phase 1 — Centralize geometry → mask dispatch (maintainability, sets up everything else)

**Goal:** one function that turns `(geometry_type, parameters)` into a mask, so
`analysis_tasks.py` stops branching on geometry type itself.

- Add `processing/roi_geometry.py` with one dispatch function, e.g.
  `rasterize_roi(geometry_type, params, image_shape, transform=None) -> np.ndarray`, delegating
  to the existing `transformed_disk_mask`/`transformed_annulus_mask` (circle/annulus) and
  `expand_mask` (mask fallback).
- Replace the inline `if roi.sample_geometry_type == "mask": ... else: ...` blocks in
  `analysis_tasks.py` with calls to this dispatcher.
- No behavior change — this is a pure refactor. Verify with existing tests
  (`test_lspri_roi_rasterize.py`) plus a regression check that formula-spectrum numbers are
  bit-identical before/after on a saved dataset.
- **Why first:** every later phase adds a geometry type. Doing this now means each new geometry
  is "add one case to the dispatcher," not "hunt down every inline branch again."

### Phase 2 — Rectangle sample/reference pair, wired to real analysis (correctness, closes the biggest current gap)

**Goal:** rectangle becomes a real `AreaRoi` geometry, not just a disconnected overlay in the
unwired `RoiDefinition` editor.

- Add `"rectangle"` handling to `roi_geometry.py`'s dispatcher (straightforward: axis-aligned
  bounding box → boolean mask, matching the general spec's simplicity — store `x, y, width,
  height`, don't store a bitmap).
- Add a `transformed_rectangle_mask` counterpart in `processing/chromatic.py` so rectangle ROIs
  get the same per-wavelength registration correction circle/annulus already get.
- Extend `AreaRoiDetectionSettings`/`AreaRoi` parameter fields for rectangle sample size and
  rectangle reference offset/size (mirroring how circle uses `sample_radius_px` +
  `reference_inner_radius_px`/`reference_outer_radius_px`).
- Build a rectangle editor UI in `main_window.py` that creates real `AreaRoi` pairs directly (the
  unreachable `RoiDefinition`/`state.rois` scaffolding that used to sketch this has been deleted
  — see "Current state" above — so this is new UI work, not a wiring job).
- **Tests:** extend `test_lspri_roi_rasterize.py` with rectangle cases; extend
  `test_lspri_roi_table_storage.py` round-trip for rectangle geometry.
- **Sign-off needed:** this touches the shared `AreaRoi` model and the formula-spectrum pipeline —
  per `CLAUDE.md`, confirm the plan with the maintainer before starting, since it's a
  scientific-calculation-adjacent change to a shared model.

### Phase 3 — Geometry-aware ROI table (polish, low risk, do after Phase 2 lands)

**Goal:** table stops assuming circle/annulus (`C_s, C_r, D_s, d_r, D_r` headers are currently
hardcoded — see `roi_table_helpers.py`).

- Add the `Shape` pictogram/summary column from `roi_table_direction.md` (`circle d=24`,
  `rect 18x12`, etc.).
- Keep the rest of that document's compact-table guidance (status bar as a colored edge, not a
  text column; pair-oriented rows).
- **Tests:** table-rendering tests are lower value here (Qt widget text); a smoke test that the
  right pictogram/text renders per geometry type is enough.

### Phase 4 — Freeform (mask) creation UI (correctness/completeness, medium risk)

**Goal:** turn the already-working mask *storage/rasterization* fallback into something a user
can actually draw, instead of only being reachable by hand-editing a file.

- Reuse `MaskController.apply_mask_brush()` — add a mode where brush strokes accumulate into a
  scratch boolean mask scoped to the ROI editor (not the global background-exclusion mask),
  then `crop_mask()` it into a `RoiMask` on commit.
- Wire the Freehand tab (currently a stub: "Freehand ROI tools are not implemented yet.") to this
  flow for both sample and reference regions independently — this is the concrete feature the
  general spec's whole architecture exists to support (§4.5, §42: mask as universal fallback).
- Freeform arrays are out of scope here — freehand shapes aren't naturally array-stampable in a
  useful way; leave `_add_stamp_array_at`'s "Freehand ROI arrays are not implemented yet." as-is.
- **Tests:** extend `test_lspri_roi_rasterize.py` with a synthetic brush-stroke → mask → crop →
  expand round trip.
- **Sign-off needed:** new interactive GUI workflow touching a shared controller
  (`MaskController`) — confirm scope before starting.

### Phase 5 — Polygon geometry (stretch, only if a real need appears)

**Goal:** vertex-based ROIs, per the general spec's §4.4.

- Only take this on if circle/rectangle/freeform mask genuinely don't cover a real sample
  geometry the maintainer is working with. Freeform mask (Phase 4) already covers arbitrary
  shapes; polygon mainly buys smaller storage and cleaner editing handles for shapes that are
  *actually* polygonal (e.g., lithographed square/hex arrays where users want to type exact
  vertex coordinates rather than paint them).
- If pursued: use `matplotlib.path.Path.contains_points` or `skimage.draw.polygon` for
  rasterization rather than a hand-written point-in-polygon routine (per the general spec's
  §21 guidance to prefer a tested dependency) — check whether either is already a dependency
  before adding one.

## Deliberately deferred (not in scope unless a concrete need appears)

These come from the general spec but don't match this app's actual usage pattern today. Revisit
only if the trigger condition happens — don't build them speculatively (this is the same
"don't design for hypothetical future requirements" rule `CLAUDE.md` states for code, applied to
this roadmap):

- **`MeasurementRegion` many-to-many (multiple sample ROIs / multiple reference ROIs per
  measurement, exclusion ROIs as spatial regions).** Trigger: a real experiment needs to combine
  several disjoint sample regions into one measurement, or needs a spatial (not
  whole-image/whole-wavelength) exclusion region. Today's `ImageExclusionRule` already covers
  "skip this image/wavelength" — a different, already-solved problem.
- **Full `ROIManager` class.** The dispatcher from Phase 1 gets most of the architectural benefit
  (geometry-blind analysis code) without the extra indirection of a manager object owning
  caching/registration/exclusion resolution. Revisit if the dispatcher's call sites start
  duplicating logic around it.
- **HDF5 ROI schema.** This app's images aren't HDF5-based; JSON is already the established,
  versioned canonical format (`format_versioning.md`). Only revisit if LSPRi eva's image storage
  itself moves to HDF5.
- **Weighted/float masks, ROI time-versioning (`ROIState` with validity intervals), ellipse as a
  first-class `AreaRoi` geometry.** No current workflow need identified. Ellipse specifically:
  circle + independent x/y scale is a small extension of Phase 2's rectangle work if it's ever
  needed, not worth doing preemptively.

## Testing requirements (applies to every phase above)

Per `CLAUDE.md`'s data-integrity priority, every phase that touches geometry math or storage
must include:

- A rasterization unit test per geometry type (circle, annulus, rectangle, mask) confirming
  correct pixel coverage, including the "ROI partly/fully outside image" boundary cases the
  general spec calls out in its §29.
- A storage round-trip test (`AreaRoi` → JSON → `AreaRoi`) for every geometry type, extending
  `test_lspri_roi_table_storage.py`.
- A numerical regression check: build a synthetic image with a known sample/reference intensity
  split (per the general spec's §38 example), confirm the formula-spectrum result is identical
  whether the same pixel region is expressed as a circle, a rectangle, or a mask — this is the concrete proof that
  Phase 1's dispatcher actually made analysis geometry-blind.

## Where this document lives

This file is inside the `apps/LSPRi/eva` submodule (its own git repository, not the umbrella
`LSPR-Suite` repo — see `CLAUDE.md`'s Submodule Workflow section). Committing it requires a
commit inside `apps/LSPRi/eva` first, then a submodule-pointer bump commit in the umbrella repo.
