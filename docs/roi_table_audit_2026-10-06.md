# ROI / Group table audit: stable app vs rewrite (2026-10-06)

Audit only (code read, nothing run). Purpose: decide what the rewrite's `panels/roi_table/` should do before building it. Decisions at the bottom are **open**, to be settled with the maintainer; append the outcome here.

## 1. State of the rewrite

- `panels/roi_table/panel.py` is a 44-line stub (`_redraw` raises `NotImplementedError`; nothing starts its timer, so it is inert). It is already constructed and docked as "ROI / Groups" in `app_rewrite.py:790,900`.
- Backend exists in `roi/toolbox.py`: `rois()`, `groups()` (dict order = group order), `group_for_roi`, `move_roi`, `resize_roi`, `delete_rois` (renumbers IDs 1..N), `create_group` (empty), `rename_group`, `recolor_group`, `reorder_group`, `add_to_group`, `remove_from_group` (prunes empty group). Signals: `geometry_changed`, `cosmetic_changed`, `roi_ids_renumbered`.
- Selection is `selection/module.py` (`selected_roi_ids`, `set_roi_selection`, `roi_selection_changed`). The Image panel already drives it (`panels/image/panel.py:2096-2104`).
- Pixel-size reads: `GeometryModule.can_display_micrometers()` / `microns_per_pixel_scalar()`.
- **The Image panel draws every sample circle in one colour** (`_sample_curve`, `_reference_curve`, `_selection_curve`). Group/ROI colours exist in the model but are rendered nowhere yet.
- `AnalysisEngine` has `get_metric` (None = not analyzed), `status_summary()` (totals only), `store_updated`, `preview_recompute`. **No per-ROI "analyzed / stale" query.**

## 2. What the stable table does (`gui/roi_table_controller.py`, `group_table_controller.py`, `roi_table_helpers.py`)

Two tables in one panel, toggled "ROI" / "Group" in the panel title.

**ROI table**, 9 columns: `ID | Group (editable) | C_s swatch | C_r swatch | D_s | d_r | D_r | x | y`. Sortable, extended multi-select, 18 px rows.
- Colour resolution: per-ROI colour, else group colour, else global default (sample and reference separately).
- D_s / d_r / D_r editable inline; px or µm per display setting. x, y = reference-frame centre, read-only.
- Double-click: C_s / C_r open a colour dialog; D_s / d_r open an input dialog.
- Context menu: Group..., Select group members, Ungroup, Destroy group.
- Keys: Ctrl+Z/Y, PageUp/PageDown (swap ROI id with neighbour), Delete/Backspace (remove), Ctrl+C/V ("ROI properties" = group name + colours).
- Footer: "cached ROIs only" toggle (filters the image overlay and tints calculated rows blue), Export / Import ROI-table JSON.
- Autosaves `analysis/roi_table.json` (debounced, background write) plus a mirror in the measurement backup HDF5.

**Group table**: `# | Group | Sample | Reference | ROIs`, plus a synthetic "Ungrouped" row. Buttons: new, delete, move up/down, group-by-column. Context menu: rename, recolour sample/reference, move, add/remove selected ROIs, delete. Selecting a group row selects its members; a group row is highlighted only when all its members are selected (non-empty guard).

**Cost**: every change rebuilds all rows (`setRowCount(0)`), ~28 ms at 200 ROIs, losing scroll and any open inline editor. ROI id is recovered by parsing the text of column 0.

## 3. Defects/quirks in the stable table (do not port)

1. **Likely unit bug**: `_edit_roi_diameter_cells_from_table` (`main_window.py:3266`) and the dialog path write the typed number straight into `*_diameter_px` and never call `_length_display_to_px`. With display units on µm, an edit stores µm as pixels. (Code read only; not reproduced. The existing test does not cover µm.)
2. **Silent override**: editing any one diameter cell writes all three (D_s, d_r, D_r) as explicit per-ROI values, so that ROI stops following the global reference diameters.
3. **Silent clamp**: `outer = max(outer, inner)` hides invalid input instead of reporting it.
4. **Mask ROIs show meaningless diameters**: `sample_geometry_type == "mask"` still displays `sample_diameter_px`.
5. Typing a name on an *ungrouped* ROI row makes a group of that one ROI (not the selection); on a grouped row it renames the whole group, so one cell edit changes several rows.
6. Paste overwrites the *global* sample/reference colours as a side effect.
7. Double-click on D_s / d_r is both an inline-edit trigger and a dialog trigger (inferred from code, not observed).
8. PageUp/PageDown renumbers ROI ids. In the rewrite, analysis cells are keyed by ROI id; `roi_ids_renumbered` is consumed only by `SelectionModule` (no subscriber found in `analysis/`). Worth a separate check because `delete_rois` already renumbers.

## 4. Gaps between the rewrite's command API and what a table needs

| Need | Status |
|---|---|
| Per-ROI colour (`AreaRoi.sample_color_hex` / `reference_color_hex`) | No command; nothing renders it. Depends on decision 2 |
| Delete group (ROIs become ungrouped) | Missing. Only per-ROI `remove_from_group` |
| Group selection / create group with selection / ungroup (bulk, one undo step) | Missing; composable with `begin_batch`/`end_batch` but N signals |
| ROI label (`AreaRoi.label`, `notes`) | Field exists; no command |
| Reset a diameter to the default (`None`) | `resize_roi` cannot set a field back to `None` (None = unchanged) |
| Per-ROI analysis state | No engine query (see section 1) |
| Group-by-column | Not ported (spatial array feature; toolbox docstring says deliberately skipped) |
| ROI renumber by swap | Not ported, and should stay that way (defect 8) |

## 5. Proposed shape (for discussion)

Purpose: list/edit the ROIs (the analysis unit), organise them into groups, show analysis state at a glance, drive the shared selection.

- Build as `QTableView` + `QAbstractTableModel` (not `QTableWidget`): ROI id in `UserRole`, `dataChanged` for only the ROIs in a change event (empty `roi_ids` = refresh all), scroll/selection/editor preserved. Row formatting, unit conversion, and status derivation as Qt-free functions so they unit-test without a `QApplication`.
- Columns: `status bar | # | Name | Group (swatch+name) | x, y | D_s | d_r | D_r`. Inherited diameters (no override) in grey/italic; overridden in normal text. Mask ROIs show "mask", not a number.
- Status bar (the leading coloured strip from `roi_table_direction.md`): grey = not analyzed, green = analyzed and current, yellow = stale (a `ComputationalChange` since the last run) or partial cubes, red = problem. Tooltip says why. Replaces the "cached only" blue-text hack. Display only; never computes.
- Edit: diameters accept the display unit and convert back; multi-row selection applies to all selected rows as one undo step; invalid input is refused with a message, never clamped.
- Context menu: Group selected..., New group from selection, Remove from group, Select group members, Reset diameters to default, Delete ROI(s).
- Layout options: A = two toggled views as in stable; B = one table with collapsible group header rows ("Ungrouped" last), flat toggle available. Header-row selection selects members.
- Phasing: P1 flat ROI table + selection sync + diameters + status bar; P2 groups; P3 filters/colours/polish.

## 6. Decisions (maintainer, 2026-10-06)

1. **Layout B** (one table, collapsible group header rows, flat toggle). Visually modern: custom delegate, theme tokens, no grid lines.
2. **Colour**: group has a palette; each ROI's colour is a tint/pick from it. (Open detail: stored per ROI or derived from rank; see 7.)
3. Status bar: maintainer unsure why it is needed. Rationale given in the discussion; still open. Verified finding that supports it: see 7.
4. **ROI ids stay user-changeable** and the table sorts ascending/descending like a normal list. Semantics of "change id" still open; see 7.
5. JSON export/import: **postponed**.
6. **Multi-select editing** of position, diameter, colour, etc. Position needs "shift by delta" or "set x only / y only", since setting the same absolute x,y on several ROIs stacks them.

## 7. Verified 2026-10-06: the analysis store is keyed by the mutable ROI id

- On disk: `analysis/store.py:79` stores cells at `/cells/roi_{id}/cube_{n}`. In memory: `engine.py:329` `get_spectrum` is `self._results.get((roi_id, cube_index))`, with **no fingerprint/staleness check on read**.
- `roi_ids_renumbered` has one subscriber (`SelectionModule`). The engine and store do not follow it.
- Consequence (today, already, via `delete_rois`): after deleting ROI 3, the surviving ROI 4 becomes id 3, and a panel asking for ROI 3 gets the *old* ROI 3's result until the next run. Orphan `roi_N` groups also linger in `data.h5`.
- User-changeable ids would make this the normal case, not an edge case.
- Options: (A) engine remaps in-memory results and rewrites h5 keys on every renumber (and reverse on undo); (B) give each ROI an immutable internal key (uid) that the store and provenance use, and make `area_roi_id` a pure display number. Under B, renumbering/reordering becomes a `CosmeticChange` (no data moves, nothing goes stale), matching the architecture's cosmetic-vs-computational split. B changes the saved session format (rewrite-only sessions; no importer exists), so it needs explicit approval. The fingerprint currently includes `area_roi_id` (`provenance.py:359,416`), which would switch to the uid.
- **Maintainer decision (2026-10-06): no hidden key (option B rejected).** ROI id means *order*: contiguous 1..N; the user reorders rows or deletes and the rest close the gap. Colour is stored per ROI (`AreaRoi.sample_color_hex` already exists; palette generation is new).
- So option A is required: the analysis side must follow renumbering. Findings from reading `provenance.py`/`planner.py`/`store.py`:
  - A cell's own fingerprint (`roi_geometry_fingerprint_fields`) excludes the id, so results that follow their ROI stay valid, no recompute.
  - `sample_exclusion_digest` (`provenance.py:338`) and `background_exclusion_digest` (`:371`) list every ROI *with its id, sorted by id* inside the settings snapshot. In "exclude all sample ROIs" mode and in background ROI-exclusion mode, a pure reorder would change the snapshot and mark **every** cell stale though nothing numeric changed. Fix: make both digests order/id-independent (sort by geometry, drop id). One-time effect: existing rewrite sessions recompute once in those modes.
  - State to remap on `roi_ids_renumbered`: `engine._results` and `InMemoryProvenanceStore._fingerprints` (in memory, trivial), and `/cells/roi_{id}` groups in `data.h5` (h5py `move` is a metadata rename, no data copy; needs temp names to apply a permutation). Keys absent from the map = deleted ROIs: drop them. Undo of delete restores the ROI but not its results (shows "not analyzed").
  - Race: a renumber while an analysis run is writing cells would write under stale ids. Needs a guard (disable reorder/delete while a run is active, and have the engine refuse/cancel).
  - Reorder inside a group (grouped view): permute ids among that group's own id slots, so other groups' ids do not change.

## 8. Progress

**Step 1 done 2026-10-06 (commit ea45e6a): analysis results follow ROI renumbering.**
- `analysis/store.py`: `remap_cell_roi_ids` (h5py `move`, temp names for cycles, drops ids absent from the map); the reader skips non-integer `roi_*` groups.
- `analysis/provenance.py`: `sample_exclusion_digest` / `background_exclusion_digest` no longer contain ROI ids and sort by geometry (one-time recompute for existing sessions in those two modes); `InMemoryProvenanceStore.remap_roi_ids`.
- `analysis/engine.py`: `remap_roi_ids` (cancels and waits for a running analysis first; timeout path drops in-memory results and logs), `is_running()`, an id-epoch + lock so a metric computed on the derived-worker thread across a renumber is neither cached nor emitted. `analysis/worker.py`: `join`.
- `roi/toolbox.py`: `delete_rois` now always emits `roi_ids_renumbered` (previously skipped when no ROI survived); `detect_rois` emits an empty map in apply and revert (previously old results were served for the new ROIs after a re-detect).
- `app_rewrite.py`: `_connect_roi_renumbering` wires selection and engine to the signal.
- Tests: `tests/unit/test_lspri_rewrite_roi_renumber.py` (13), `tests/integration/test_lspri_rewrite_roi_renumber.py` (9). Verified they fail with the engine disconnected.
- Backstop is cancel-and-wait, not refuse: a slot cannot veto a renumber the toolbox has already applied. The table must still disable reorder/delete while `engine.is_running()`.
- Known unrelated failure: `test_lspri_rewrite_nan_handling.py::...readout_says_no_data` (`panels/image/no_data.py` `format_pixel_value(12.34)` gives `'12'`).

**Step 2 done 2026-10-06 (commit 4c60243): the toolbox commands the table calls.** All in `roi/toolbox.py` unless noted; each is one undo step, validates before changing anything, raises (never skips) on unknown ids.
- Order: `reorder_rois(new_order)`, `move_in_order(roi_ids, target_index, scope_ids=None)` (scope = the group's members: only those ids are shuffled, other groups keep their numbers). Both go through `_renumber`, which emits `roi_ids_renumbered` (full map) + cosmetic `"renumbered"`. Pure helpers in `roi/ordering.py`.
- Groups: `group_rois`, `add_rois_to_group`, `remove_rois_from_groups(group_id=None)`, `delete_group`; `create_group`/`rename_group`/`recolor_group` now validate (names required, `#rrggbb` only). Single-ROI `add_to_group`/`remove_from_group` delegate to the bulk forms. A group emptied by a move is removed; one created empty on purpose stays.
- Colour: group has a base colour, each member a stored tint (`roi/palette.py`: tint 0 = base, then the lightness level farthest from those used, 7 per round, hue nudged each further round). A joining ROI takes the first free tint. `recolor_group` repaints members by default (replaces hand-set colours; `repaint_members=False` keeps them). `set_roi_colors(ids, hex|None)`, `set_roi_label`. Leaving a group clears the tint.
- Geometry: `translate_rois`, `place_rois(x=/y=)`, `resize_rois`, `reset_roi_diameters(sample=, reference=)`; `move_roi`/`resize_roi` are one-ROI forms. Validation: sample >= `MIN_SAMPLE_DIAMETER_PX` (2.0), finite, and `0 <= inner < outer` checked against the diameters the ROI will actually use (inherited default outer included).
- Bug fixed on the way: `delete_rois` undo used to swap in *copies* of the groups/arrays, so an older undo step holding the original group object would edit an object no longer in the toolbox. Undo now restores fields onto the same objects (`_snapshot`/`_restore`); regression test fails with the old behaviour.
- Tests: `tests/unit/test_lspri_rewrite_roi_commands.py` (39); the integration renumber test now drives the real `reorder_rois` end to end (selection follows, results follow, undo, restart, nothing stale in "exclude all sample ROIs" mode).
- Not built (deliberately): group-by-column, ROI swap by spatial position, a mask-ROI guard on `resize_rois` (no mask-editing UI exists in the rewrite yet).
- Known unrelated failures on a clean tree: `test_lspri_rewrite_nan_handling.py::...readout_says_no_data`, `test_lspri_rewrite_area_selection.py::...general_group_sits_left_of_the_ribbon...` (Image panel).

**Step 3 done 2026-10-07: the table itself** (`panels/roi_table/`).
- Files: `rows.py` (pure: px/µm, rows, sort, sections, move rules), `model.py` (`RoiTreeModel`, a command-model: edits go out as `cell_edited`, values come back via `set_content`), `delegate.py` (all painting; theme read at paint time), `view.py` (chevron click, Delete / Alt+Up/Down, row hover, chip double-click, measured default column widths), `dialogs.py` (Shift position), `panel.py` (toolbar, selection sync, edit routing, context menus, footer notices, remembered state). `app_rewrite.py`: new constructor arguments (`selection`, `geometry`, `analysis_engine`), `restore_ui_state`, `status_message` -> status bar, `refresh_theme` on theme switch.
- Behaviour: grouped tree (flat when no groups or by toggle); columns `# | Name | x | y | Sample | Ring in | Ring out`; inherited ring diameters grey italic; unit follows the Geometry display unit; header click sorts (view only); reorder only when sorted by `#` ascending and only within one group; Delete = selected ROIs, or the group if only a header is selected; multi-selection edits apply to every selected ROI in one undo step; bad values refused by the toolbox and shown in the footer (red, 15 s) and the status bar; reorder/delete refused while `engine.is_running()`.
- Remembered (ui_state): `roi_table/flat`, `/sort`, `/collapsed` (group ids, shared across sessions: a collapsed `group_1` stays collapsed in another session's `group_1`), `/column_widths`.
- Tests: `tests/unit/test_lspri_rewrite_roi_table_rows.py` (22), `tests/integration/test_lspri_rewrite_roi_table_panel.py` (52; mutation-checked: multi-edit, running-analysis guard, view->selection sync). Whole rewrite set: 768 passed, 3 failed, all 3 fail on a clean tree too (`nan_handling ...readout_says_no_data`, `area_selection ...general_group_sits_left...`, `rotate_tool ...deactivating_drops_a_pending_point`).
- Not verified by me: how it *looks* (no screenshots by rule). Painting is only smoke-tested (no exceptions, both themes); column widths are measured against the offscreen font, which may differ from the real display's.
- Not built yet: drag-and-drop reorder / drag onto a group; the analysis status strip (left edge of each row is reserved, `STRIP_WIDTH`); a search/filter box; **the Image overlay still draws every ROI in one colour, so the stored tints show only in this table**.
