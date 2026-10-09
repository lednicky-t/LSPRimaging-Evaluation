# ROI geometry timeline (Persistent / Individual scope): design (2026-10-08)

Status: **approved and built 2026-10-08** (phases 1-4; section 8 records the decisions and what was built). Engineering priority served: 1 (correctness: a result must come from the geometry the user saw at that cube) and 2 (data integrity: saved format and stored results stay valid). It changes the saved session format and the analysis inputs, so it needs a yes before code.

## 1. What the maintainer asked for

- A scope toggle in the ROIs tab, like the Mask's, default **Persistent**. It governs every geometry edit: dragging, resizing, typing in the table, Array detect / refine / place.
- **Persistent = this cube and every later cube**, until a later persistent edit supersedes it (the Mask rule). If only one cube is ever edited, it is the same as ROIs today.
- **Individual = this frame only.**
- ROI parameters become **frames with different ROI parameters**, stored in the Mask's format.

## 2. What exists today

| Item | Today |
|---|---|
| ROI geometry | one `AreaRoi` record: one centre (reference frame) and one set of diameters, valid for **every cube** (`roi/model.py`). |
| Only per-frame thing | `AreaRoi.per_wavelength`: a manual nudge of the *display* position at one (cube, wavelength), written while viewing a non-reference wavelength. It is a chromatic residual, not a timeline. |
| Mask timeline (the model to copy) | `MaskChange(frame, scope, mask)`; persistent changes keyed by **cube** ("this cube and every cube after, until superseded", cube granularity only); individual changes keyed by the exact **(cube, wavelength) frame**; resolution = individual at the exact frame wins, else the latest persistent at or before the cube (`MaskModule.resolve_mask_source`). |
| Analysis | cells are (ROI, cube); the planner fingerprint takes **one geometry per ROI** (`CurrentInputs.roi_geometries: {roi_id: fields}`, `analysis/planner.py:69`, `engine.py:624`), `compute_cell` takes the ROI and `all_rois` (the sample-disk union that rings subtract). |
| Saved ROIs | session JSON (`storage/session.py`, `storage/workspace.py`: `_encode_area_roi` / `_decode_area_roi`). |

## 3. Proposed model

**Identity is global, geometry is a timeline.** A ROI exists for every cube (id, label, colour, group, array membership, order: unchanged, edited without scope). Only its *geometry* (centre x / y, sample diameter, ring inner / outer diameter) varies along the cubes.

```python
@dataclass(slots=True)
class RoiGeometryChange:          # mirrors MaskChange
    cube: int
    scope: str                    # "persistent" | "individual"
    center_x: float
    center_y: float
    sample_diameter_px: float
    reference_inner_diameter_px: float | None
    reference_outer_diameter_px: float | None
```

`AreaRoi` keeps its current fields as the **base geometry** (what holds before any change, i.e. today's record) and gets one new field `timeline: tuple[RoiGeometryChange, ...] = ()`, sorted by (cube, scope). An empty timeline is today's ROI exactly.

**Resolution** (`RoiToolbox.geometry_at(roi_id, cube)`, pure, the Mask rule):
1. an *individual* change at exactly this cube wins;
2. else the latest *persistent* change with `cube <= this cube`;
3. else the base geometry.

**Edit** (every geometry command gains `cube` and `scope`; `cube=None` keeps today's meaning, edit the base, so nothing existing breaks):
- Persistent at cube k: write (replace) the persistent change at k. Cubes before k are untouched; cubes k.. up to the next persistent change follow it.
- Individual at cube k: write (replace) the individual change at k. Only cube k changes.
- Each edit stays one undo step; undo restores the previous timeline.
- New ROIs (add, detect, Array place) set the **base** geometry: they exist and have that geometry on every cube until edited. (Existence is not a timeline; see question 2.)

**Individual granularity**: by **cube** (all wavelengths of that cube share the ROI position in the reference frame; the chromatic affine moves it to each wavelength as today). The Mask's individual is per exact (cube, wavelength) because a mask is an image; a ROI position is a reference-frame property. The existing `per_wavelength` nudge stays as the separate wavelength-level residual (question 1).

**Consequence to confirm** (question 3): with Persistent = "this and later", dragging a ROI while viewing cube 5 leaves cubes 0-4 where they were. Today it moves the ROI on every cube. A menu action **"Apply to all cubes"** (collapse the timeline into the base) and **"Remove this cube's edit"** are needed so the user can get today's behaviour in one click and can undo clutter.

## 4. What has to change (blast radius)

| Area | Change |
|---|---|
| `roi/model.py` | `RoiGeometryChange`, `AreaRoi.timeline`. |
| `roi/toolbox.py` | `geometry_at`, `geometries_at(cube)` (vectorised arrays for drawing), scope + cube on `move_roi` / `translate_rois` / `place_rois` / `resize_rois` / `reset_roi_diameters` / `refine_rois`; `remap_all` (rotate / crop / flip follows) must remap **every** change, not only the base; `delete`, `renumber`, `detect_rois`, `place_array` carry the timeline. Edit commands reject a timeline edit while an analysis runs only where they already do. |
| Analysis | `CurrentInputs.roi_geometries` keyed by (roi_id, cube) (or resolved per cell); `compute_cell` receives the geometry **at that cube** for the ROI and for every ROI in the sample-disk union; fingerprint code is unchanged (it hashes a geometry dict). Old stored results stay valid: an empty timeline resolves to the same dict as before, so no cell recomputes. An edit at cube k recomputes only the cubes it changes, not all cubes (a gain). |
| Storage | `_encode_area_roi` / `_decode_area_roi` (session JSON): optional key `"timeline"`; a file without it loads unchanged (**no migration needed to read old sessions**); a new session with a timeline is only written when a timeline exists. `docs/schemas/LSPRi-format-versioning.md` gets the new field. |
| Image panel | draw `geometries_at(current cube)`; `RoiGestures`, `ArrayActions` pass `(cube, scope)`; hit test uses the geometry at the cube. |
| ROI table | rows show the geometry at the current cube; a marker where the current cube has its own change; context menu "Apply to all cubes", "Remove this cube's edit". |
| Ribbon | scope toggle (Persistent / Individual) in the ROIs tab, `ui_state` key `roi/scope`, shared state object like `MaskScopeModule` (a `RoiScopeModule`, transient interaction state, not undoable). |
| Cube slider (optional) | ticks where any ROI has a change (to see the timeline). |

## 5. Phases (each ends green and usable)

1. **Model + resolution + storage + analysis**, UI unchanged (scope fixed to Persistent at the base). Provable: all existing tests pass, a session round-trips with and without a timeline, the planner leaves old results alone. No visible change.
2. **Toolbox commands with cube / scope**, remap_all and undo for timelines, unit tests. Still no UI.
3. **Panel: draw and edit at the current cube**, scope toggle, "Apply to all cubes" / "Remove this cube's edit", table marker.
4. **Array actions** with scope (detect / refine write persistent or individual changes).
5. Later (not part of this): **track across cubes**: run Refine on each cube and write individual changes automatically, for stage drift over hundreds of frames.

## 6. Risks / things I have not read yet

- `analysis/tasks.py` `compute_cell` and `analysis/engine.py` are large; the exact per-cell plumbing for geometry (and for the sample-disk union) must be read in full before phase 1. This design assumes it can resolve geometry per cube without touching the reduction math.
- `roi_geometry_sync.py` / `remap_all` semantics for timelines (rotation changes every change, one by one).
- 314 cubes x 150 ROIs of individual changes is 47 k small records: fine for JSON, but the session write cost and the undo snapshot size need a measurement before tracking (phase 5) is built.

## 7. Questions for the maintainer

1. **Individual granularity**: by cube (my proposal) or by exact (cube, wavelength) like the Mask?
2. **Existence**: should adding / deleting a ROI also be on the timeline (a ROI exists only from cube k on), or stay global (my proposal; much simpler, ids and the table stay stable)?
3. **Consequence of "this and later"**: OK that moving a ROI at cube 5 no longer moves cubes 0-4, with "Apply to all cubes" as the one-click way to get today's behaviour?
4. **Order**: build phases 1-2 first (no visible change, safe), then the UI?


## 8. Decisions and what was built (2026-10-08)

Decisions: individual granularity **by cube** (the other wavelengths follow through the chromatic correction); ROI **existence stays global**; **Persistent = this cube and later** (the Mask rule) with "Apply to all cubes" as the one-click way back to the old behaviour; build **everything in one go** (phases 1-4).

| Piece | Where |
|---|---|
| Model: `RoiGeometry`, `RoiGeometryChange`, `RoiTimeline` (immutable; `geometry_at`, `with_change`, `without_cube`, `mapped`), `AreaRoi.timeline`, `geometry_at()`, `resolved_at()` | `roi/model.py` |
| Toolbox: `geometry_at`, `rois_at(cube)`, `has_timeline`, `timeline_cubes`; `cube=` / `scope=` on `move_roi`, `translate_rois`, `place_rois`, `resize_rois`, `resize_roi`, `reset_roi_diameters`, `refine_rois`; `apply_to_all_cubes`, `remove_cube_edit`; `display_position(s)` follow the cube; `remap_all` remaps every change | `roi/toolbox.py` |
| Scope state + where an edit lands: `RoiScopeModule`, `RoiEditTarget` | `roi/scope.py` |
| Storage: session JSON schema **1.1 -> 1.2**, optional per-ROI `"timeline"` (old files load unchanged, a session without a timeline writes `null`) | `storage/session.py` |
| Analysis: engine takes `rois_at` / `has_timeline`; the planner reads `(roi_id, cube)` geometries; the sample-disk union and the background exclusion digest are per cube; a ROI edit recomputes only the cubes it changes | `analysis/engine.py`, `analysis/planner.py`, `app_rewrite.py` |
| UI: Scope toggle (ROIs tab, between the view groups and Array), draw / hit test / edit at the current cube, table rows at the current cube, right-click "Apply to all cubes" / "Remove this cube's edit", Array refine with scope | `panels/image/roi_scope_toggle.py`, `panel.py`, `roi_gestures.py`, `interaction.py`, `roi_context_menu.py`, `array_actions.py`, `panels/roi_table/panel.py`, `panels/histogram/panel.py` |
| Remembered | `roi/scope` in `storage/ui_state_keys.py` |

**Rules as built**
- A Persistent edit **on the first cube of the dataset** is written to the ROI's *base* geometry (no timeline), so editing the first cube still changes every cube, exactly as before. A timeline appears only for an edit on a later cube or with Individual scope.
- New ROIs (add, detect, Array place) set the base geometry. Mask-geometry ROIs and groups, colours, labels, order are not on the timeline.
- Old results stay valid: with no timeline the geometry dict hashed per cell is the same as before, so nothing recomputes.
- Undo of an edit restores the previous timeline in one step (a drag is one step).

**Not built**
- A marker on the cube slider and in the table for "this cube has its own geometry" (the right-click entries are enabled only when it does).
- Phase 5: track a drifting stage across cubes (Refine on each cube writing individual changes).
- Measured cost of 314 cubes x 150 ROIs of changes (session write, undo snapshot size): not measured yet.
- The Image panel's per-cube ROI **labels** and the Spectra / Sensorgram panels read only identity (id, colour), so they need no change.

**Tests**: `tests/unit/test_lspri_roi_timeline.py` (resolution, commands, undo, remap, storage), `tests/integration/test_lspri_rewrite_roi_timeline_engine.py` (the real engine: an edit recomputes only the changed cubes, results correct), `tests/integration/test_lspri_rewrite_roi_timeline_ui.py` (toggle, canvas edits, overlay, hit test, menu, table), session round-trip tests in `test_lspri_rewrite_session.py`, one Array-refine test.

## 9. Measured cost, worst case (2026-10-08)

Worst case = a drift-tracking run: every one of 150 ROIs has its own individual change on every one of 314 cubes (47 100 changes). Typical use (a handful of edits) costs nothing measurable. Script: one-off, numbers below from a single run.

| What | Result |
|---|---|
| Writing the changes one command per cube (314 x `translate_rois` on 150 ROIs) | 14.4 s (46 ms per command: `with_change` rebuilds the sorted tuple, O(n) per ROI per edit) |
| `rois_at(cube)` for one cube | 10 ms (fine for a redraw); 3.3 s for all 314 cubes, which is what one analysis planning pass does when a timeline exists |
| `display_positions` at a cube | 6.7 ms |
| Session JSON encode (also done by each autosave "unchanged?" check) | 1.25 s, 6.6 MB |

Consequences for phase 5 (tracking): it needs a **bulk write** command (one undo step, all cubes, one sort at the end) instead of one command per cube, and the autosave check and the session file should not re-encode a 6.6 MB timeline each time (cache the encoded timeline per ROI, or hash it). Nothing to do for the current feature.
