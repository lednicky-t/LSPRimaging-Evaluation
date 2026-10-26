# Sensorgram "Experimental plan" bar - design proposal (2026-10-26)

Status: **proposal, nothing built.** This is step 4 of `experimental_plan_steps_inventory_2026-10-25.md` (sections 7-10 there say what steps 1-3 built). Maintainer request: a ribbon section on the Sensorgram that draws the experiment steps as a coloured bar on the plot, on the same time/cube axis, with draggable edges, editable text and colour, like the sLSPR acq sensorgram overlay.

## 1. What already exists (facts checked 2026-10-26)

| Need | Where |
|---|---|
| The steps (label, start/end unix ms, optional colour, device settings) | `ImagingAcquisitionMetadata.plan` (`packages/lspr_core/.../imaging_models.py`), read through `DatasetModule.acquisition_metadata()` |
| Model to copy: bar + label + position + height + opacity + label elision + text colour by luminance | `apps/sLSPR/acq/.../main_window_sensorgram_overlay.py` (`bar_rect`, `label_font`, `text_color`, `reserved_y_padding`) |
| Ribbon group + toggle + popup menu with a grid of fields | `panels/sensorgram/panel.py` (Baseline group), `baseline_selector_menu.py` (`make_height_box`, `make_font_box`, `BaselineSelectorMenu`), `IconToggle`, `PickMenu`, `RoiOverlayControls` (toggle + colour + transparency wedge) |
| A widget floating over the plot (for the in-place text editor) | `BaselineRangeReadout` on `self._widget.viewport()` (`plot.py`) |
| Optional session blocks (additive schema) | `storage/session.py` (1.4 now), pattern `selection/frame_roles_state.py` + `FrameRolesModule` |
| Remembered controls | `panels/ui_state.py` `bind`, rows in `storage/ui_state_keys.py` |
| Time axis | x = `cube_start_seconds(cube)` (t = 0 is `started_at_utc`) or the cube number; `SensorgramPanel._x_axis()` |

Not available yet: a public "cube start in unix ms" query (the panel only gets seconds). A small `DatasetModule.cube_start_unix_ms(cubes)` is needed (same repo).

## 2. The key design decision: where edits live

The measured steps are **dataset data** (raw, never written). Dragging an edge, retyping a label or recolouring is the user's *interpretation*. So:

- The dataset's `plan` stays exactly as measured.
- Edits are stored **in the session** as a small overlay (`PlanEditsState`, session.json block `"plan"`, schema 1.4 -> **1.5, additive**: an older session loads with no edits).
- An edit record = `{step, measured_start_unix_ms, measured_label, start_unix_ms?, end_unix_ms?, label?, color?}`. The two `measured_*` fields are a fingerprint: if the dataset's step no longer matches (re-imported notes), the edit is **not applied**, is logged at WARNING and shown in the tooltip. It is never silently moved to another step.
- `effective_plan = apply_edits(measured_plan, edits)` is what the bar draws and what any later analysis (bulk-sensitivity plateaus, mixed-cube flags) should read.
- Priority served: 2 (data integrity: raw stays raw, nothing applied by guesswork) then 1.

## 3. Ribbon group "Experimental plan"

Added after "View" in the Sensorgram ribbon. Disabled (tooltip says why) when the dataset has no steps.

```text
 Experimental plan
 [bar] [Top|Bottom] [Height 20 px] [transparency wedge] [Text: Whole note v] [Text pos: Centre v] [Size 9 pt]
```

| Control | Kind | Default | Notes |
|---|---|---|---|
| Show / hide | `IconToggle` | on | like the Spikes / Subtract toggles |
| Position | `PickMenu` (Top, Bottom) | **Bottom** | maintainer 2026-10-26: "the plan bar is always at bottom"; the control is kept (it was in the first request) until told to drop it. The bar may overlap the baseline band (see section 9) |
| Height | spin box, % of plot height | 5 % | **1 - 10 %, then one more stop, "Full"** (maintainer 2026-10-26). Full = the bar fills the whole plot height behind the traces (the "background" style of the sLSPR acq overlay); no room is reserved for it |
| Transparency | wedge (`CompactWedgeSlider`) | 0.55 | fill only; text and edges stay opaque. Matters most in Full |
| Text content | `PickMenu`: *Whole note* / *Solution (text before separator)* | Whole note | decision 2026-10-26. A small field for the separators (default `_-_`, `_--_`) appears while *Solution* is chosen |
| Text aligned to | `PickMenu`: *Bar* / *Data* | Bar | see "Text position" below |
| Text position | 5-stop slider (plain `QSlider`, ticks, the tooltip names the stop), one remembered value per "aligned to" mode | Bar: Middle; Data: Near above | see "Text position" below |
| Text size | spin box, pt | app font size | `make_font_box` range 6-24 |

All of them remembered through `ui_state` (rows `sensorgram/plan_*`). Layout (maintainer 2026-10-26): **inline for now**; the Sensorgram ribbon will be re-ordered and grouped into pick-up menus later, as more is added. So the whole group is one self-contained file (`panels/sensorgram/plan_group.py`) and regrouping touches only that file.

### Text position (maintainer's idea 2026-10-26, refined here)

The 5-stop slider means "high -> low" in both modes; the "aligned to" menu says what the stops are measured against.

**Aligned to Bar** - the text sits inside the bar, centred in x:

```text
 stop 5  Top             text flush with the bar's top edge
 stop 4  Upper middle    text centre at 1/4 of the bar height from the top
 stop 3  Middle
 stop 2  Lower middle    text centre at 3/4 of the bar height from the top
 stop 1  Bottom          text flush with the bar's bottom edge
```

If the bar is shorter than the text (a thin 1-3 % strip), the text is not drawn in this mode (the tooltip still shows it); use *Data*, or a taller bar.

**Aligned to Data** - the text is moved out of the bar and placed relative to the traces **inside that step's x range** (each step gets its own height, so the label follows the signal, e.g. a label above every plateau):

```text
 stop 5  Far above       bottom of the text 3 text-heights above the section's top level
 stop 4  Near above      bottom of the text 0.5 text-height above the top level
 stop 3  On the data     text centred on the section's median
 stop 2  Near below      top of the text 0.5 text-height below the bottom level
 stop 1  Far below       top of the text 3 text-heights below the bottom level
```

- "Top level" and "bottom level" are the **98th and 2nd percentile** of every finite y value drawn in the section (all lines, and the band edges when an average has a band). A percentile ignores the odd outlier like a median would, but it stays above (below) nearly all points. A *median of the per-cube maxima* would put the label in the middle of a drifting trace and over it, so it is not used. The median is only the middle stop.
- The gaps (0.5 and 3 text heights) are a first guess, to be tuned by eye.
- Only the visible part of the step counts for x (centre of what is on screen), the label is kept inside the plot area, and the y auto-range makes room for the labels on the side they are on (as it does for the bar).
- A step with no finite point in the section falls back to *Bar / Middle*.
- Computed from the series already drawn, in `_draw_last` (a few thousand floats; not on `cube_changed`). It follows a change of ROI selection, baseline subtraction or smoothing, because those change what is drawn.
- Double-click and right-click work on the text wherever it is, not only on the bar.

## 4. The bar on the plot

One custom pyqtgraph item (`PlanBarItem`, a `GraphicsObject` in the plot's ViewBox, `ignoreBounds`), not one item per step:

- **x** in data coordinates (follows pan/zoom); **y** glued to the top or bottom edge, `height_px` tall (converted with `viewPixelSize`, as sLSPR acq does). Text is drawn in device pixels (transform reset in `paint`) so it is never scaled.
- **Time/cube mapping** (`plan/axis_map.py`, pure): pairs (cube start unix ms, panel x) for every scanned cube; `np.interp` both ways (exact in Time mode, piecewise-linear in Cube mode, so a boundary that falls mid-cube sits inside that cube in true proportion). Ends extrapolate with the end slope.
- **Room for the bar**: after the auto-range, the y range is widened by the bar height on the bar's side (sLSPR `reserved_y_padding`), so a trace is never hidden under it. Closed form: `span' = span / (1 - bar_fraction)` (the height is a fraction of the plot, so no pixel conversion is needed). Nothing is reserved for *Full*, where the bar is drawn behind the traces.
- **Text**: label per the Text-content rule, elided to the bar width (`QFontMetricsF.elidedText`), hidden when the bar is under about 40 px wide, tooltip with the full note, step number, start-end, duration, flows/valves from `devices`. Text colour chosen from the *composited* fill colour (fill over the plot background) by luminance, so it stays readable at any transparency.
- **Colour**: user edit > plan-file colour (`ImagingPlanStep.color`) > automatic. Automatic = a fixed categorical palette indexed by the order in which each distinct solution text first appears, so the same solution (e.g. `H2O` returning between steps) always has the same colour. Nothing stored for it.

### Interactions

| Gesture | Result |
|---|---|
| Hover an edge (+-4 px) | resize cursor, edge highlighted |
| Left-drag an edge | moves that boundary live; a boundary shared by two steps (end of one = start of the next, as measured) moves both; edges cannot cross the neighbour or shrink a step below one cube; the edge **snaps to a cube point** (an x of the plot, as the baseline selector does, `snap_to_grid`; maintainer 2026-10-26); a small readout shows the time (and cube). One drag = one undo step |
| Double-click the bar | in-place line edit over that step, showing the **whole** note (not the shortened one); Enter or leaving the field saves, Esc cancels; empty text = back to the measured note |
| Right-click the bar | menu: *Colour...* (colour dialog), *Default colour*, *Edit text*, *Reset step to measured* (greyed when unedited), *Reset all steps* |
| Left-click on the bar | swallowed (does not move the cube slider, so a double-click does not jump the cursor); a click elsewhere on the plot still goes to the cube |
| Middle-drag | still pans |

Adding, splitting and deleting steps is **not** part of this (not requested). Mixed-cube marks and the Workflow step table (the rest of the original step 4) are separate.

## 5. Code layout (all in `apps/LSPRi/eva`, rewrite branch)

| File (new unless noted) | Role | Qt? |
|---|---|---|
| `plan/label_rule.py` | `shown_label(note, mode, separators)` | no |
| `plan/axis_map.py` | unix ms <-> axis value | no |
| `plan/edits.py` | `PlanEdit`, `apply_edits`, drag clamping rules, fingerprint check | no |
| `plan/state.py` | `PlanEditsState` (immutable, `from_payload`), like `FrameRolesState` | no |
| `plan/module.py` | `ExperimentPlanModule(QObject)`: effective steps, `steps_changed(reason)` (reason `label`/`color` = cosmetic, `boundary` = computational for later consumers), commands each pushing one `undo.FunctionCommand` (a drag uses `begin_batch`/`end_batch`), `state()` / `restore_state()` | yes |
| `panels/sensorgram/plan_bar.py` | `PlanBarItem` + in-place editor + context menu | yes |
| `panels/sensorgram/plan_group.py` | the ribbon controls, remembered | yes |
| `panels/sensorgram/plot.py` (edit) | `set_plan(...)`, forwards the item's signals | |
| `panels/sensorgram/panel.py` (edit, about 40 lines) | builds the group, turns item signals into module calls, redraws on `steps_changed` / axis change | |
| `storage/session.py` (edit), `app_rewrite.py` (edit) | block "plan", schema 1.5, `capture_session` / `apply_session`, `plan.steps_changed` added to the autosave triggers | |
| `storage/ui_state_keys.py` (edit) | `sensorgram/plan_*` rows | |
| `dataset/module.py` (edit) | `cube_start_unix_ms` | |

The panel keeps no step state (it asks the module); edits go through the module so undo and the session see them. New session field goes through `prepare_session` (autosave rule); edits are immutable tuples, so that is safe. No work on the GUI thread beyond O(steps).

## 6. Tests

- Unit, no Qt: label rule (`_-_` vs `_--_`, no separator, empty), axis map round trip in both modes (NaN cubes, extrapolation), `apply_edits` (shared-boundary move, clamping, mismatching fingerprint dropped with a warning, reset), state payload round trip and "old file = no edits".
- Integration (one process per file): module commands + undo/redo; session save/load round trip; plot item geometry and hit-testing called directly (no screen coordinates), drag driven through the item's own begin/update/finish methods; ribbon controls restored from `ui_state`. A test that measures text width calls `load_system_fonts()`.
- I do not drive the GUI. The maintainer checks the look by eye; I list what to check.

## 7. Build order

1. Pure code + unit tests (label rule, axis map, edits, state).
2. Module + session block + undo + app wiring. **Needs the format OK (1.4 -> 1.5).**
3. Read-only bar and the ribbon group (all seven controls, remembered). First thing to look at by eye.
4. Interactions: edge drag, text edit, right-click menu, resets.
5. Later / separate: bar and baseline band overlapping when Position = Bottom, mixed-cube marks, Workflow step table.

## 8. Decisions of 2026-10-26 (maintainer) and what is still open

Decided:

1. **Stored format: yes.** Session block `"plan"`, schema 1.4 -> 1.5, additive. (An older build loads the session but drops the edits when it saves again.)
2. Text position: a 5-stop slider with two modes, *aligned to the bar* or *aligned to the data* (section 3).
3. Ribbon layout: inline for now, regrouped into pick-up menus later.
4. Height in % of the plot, 1 - 10 %, then "Full".
5. Edge drag snaps to a cube point (the plotted x).
6. The bar and the baseline band may overlap; the plan bar is at the bottom.

Settled in the second round (2026-10-26):

- The *Data* mode is as described (98th / 2nd percentile edges, median as the middle stop, gaps 0.5 and 3 text heights, tuned by eye).
- "Bottom" in "the plan bar is always at bottom" meant the **background layer**, not the vertical position. So: the Top / Bottom position control **stays** (default **Top**, as in sLSPR acq; the earlier "default Bottom" in section 3 is superseded), and the bar and its text are drawn in the background, **below the traces and below the baseline band**. Where they overlap the baseline band is on top and takes the clicks. The baseline visualisation may be reworked later, so no special handling for the overlap.
- Snapped edges are stored as the cube's first-frame time (start inclusive), so an edited boundary never leaves a mixed cube. The last step's end can also snap to "end of data".

Build progress: see section 10.

## 9. Risks and what is unverified

- pyqtgraph hands a left-drag to the first item under the pointer that implements `mouseDragEvent`. The item's `shape()` must cover only the bar strip, or it would swallow plot drags. Prototype this first in step 3.
- The reserved y padding must be recomputed on resize, or the bar overlaps the traces after the window changes size.
- The text-colour-from-composited-fill rule is my choice (sLSPR acq uses the plain fill colour).
- Nothing here is visually checked.

## 10. Built 2026-10-26: steps 1-2 (pure code, module, session block; no UI yet; uncommitted)

| Piece | File |
|---|---|
| Which part of a note is shown (whole / before the earliest separator), separator list parse/format | `src/lspr_imaging_app/plan/label_rule.py` |
| Time (unix ms) <-> plot x, built from the plotted cubes (exact in Time mode, in proportion inside a cube in Cube mode, mean-slope extrapolation) | `plan/axis_map.py` |
| Saved shape: `PlanEdit` (fingerprint = step number + measured start + measured note; only changed fields), `PlanEditsState`, tolerant `from_payload` | `plan/state.py` |
| `apply_edits` -> `EffectiveStep`s + warnings; `move_edge` (joined boundary moves both, snap to grid within neighbours, min length), `set_label`, `set_color`, `reset_step`, `reset_all` | `plan/edits.py` |
| `ExperimentPlanModule`: `steps()`, `warnings()`, `axis_map(cubes, x)`, `snap_grid_unix_ms(cubes)`, undoable commands, `steps_changed(reason)`, `restore_state` | `plan/module.py` |
| `DatasetModule.cube_start_unix_ms(cube)`, `data_end_unix_ms()` | `dataset/module.py` |
| Session schema 1.5, block `"plan"`, `SessionState.plan_edits` | `storage/session.py` |
| Wired into capture / apply / autosave signals / session carry; `_Modules.experiment_plan` | `app_rewrite.py` |
| Tests | `tests/unit/test_lspri_plan.py` (39), `tests/integration/test_lspri_plan_module.py` (18), a restart test added to `test_lspri_rewrite_session_autosave.py`; two schema-version assertions moved to 1.5 |

Behaviour worth knowing:

- A tie between two grid points goes to the lower one (a target exactly halfway between two cubes).
- `move_edge` keeps every step at least one cube long (the module passes the median grid spacing); the pure function's default is 1 ms.
- Edits that do not match the dataset's steps are logged at WARNING only on session restore (`restore_state`); otherwise they are just in `warnings()`.
- The module does not reset on `dataset_loaded` (the session restore runs after the load); it resets on `dataset_cleared`. Because steps are computed lazily against the measured plan object, replacing the metadata (re-import) is picked up and old edits simply stop matching.
- A drag is several `move_edge` calls inside `undo_manager.begin_batch("Move step boundary")` / `end_batch()`; the test pins that this is one undo step.

Tests run (one process per file): the two new files, the autosave file, `test_lspri_rewrite_session`, `test_lspri_rewrite_ui_state` (builds the main window), `test_lspri_rewrite_edit_menu`, `test_lspri_session_carry_prompt`, `test_lspri_session_carry_export`, `test_lspri_sensorgram_panel`, `test_lspri_mask_pointers`, `test_lspri_session_carry`, `test_lspri_session_writer`, `test_lspri_rewrite_frame_roles`, `test_lspri_pump_plan_import`: all passed. Not run: the full suite, the Image panel test file (known heavy crash class).

Next: step 3 (the bar item, the ribbon group, the remembered display settings), then step 4 (edge drag, text edit, right-click menu).

## 11. Built 2026-10-26: step 3 (the read-only bar and the ribbon group; uncommitted, unseen by eye)

| Piece | File |
|---|---|
| Pure model: `PlanBarSettings`, `section_levels` (2nd / 50th / 98th percentile of everything drawn in a step), `automatic_colors` (Tableau 10 by first appearance of each solution), `contrast_text_color` (judged on the fill blended with the plot background), `describe_devices`, `build_bar_steps`, `text_top_y` (the five stops, both modes), `reserved_range`, `label_headroom_px` | `plan/bar_model.py` |
| The item: one `pg.GraphicsObject`, z -20, paints in device pixels (transform reset), only the bar strip is its hit area, `layout()` is separate from `paint()` so it can be tested | `panels/sensorgram/plan_bar.py` |
| Plot: owns the item, `set_plan`, widens the y range after the auto-range (`_apply_plan_room`), sets the item's tooltip on mouse move | `panels/sensorgram/plot.py` |
| Ribbon group "Experimental plan" (inline, 8 widgets, about 300 px + gaps) and its remembered state | `panels/sensorgram/plan_group.py`, rows `SENSORGRAM_PLAN_*` in `storage/ui_state_keys.py` |
| Pictures (toggle icon, top/bottom, whole/solution, bar/data) | `panels/sensorgram/plan_icons.py` |
| Panel: new optional `experiment_plan` argument, `_refresh_plan` (steps -> axis map -> bar steps -> plot), greys the group out without steps | `panels/sensorgram/panel.py`; `app_rewrite.py` passes `m.experiment_plan` |
| Tests | `tests/unit/test_lspri_plan_bar_model.py` (22), `tests/integration/test_lspri_plan_bar.py` (26: item, controls, panel with a real scan); the caption list in `test_lspri_sensorgram_panel.py` now includes the new group |

Decisions made while building (not asked; say if you want them otherwise):

- The height box is a spin box (type, arrows or wheel): 1 - 10 %, then "Full". Default 5 %. Default transparency 0.55, default position Top, default text size = the application font size.
- Data mode: a step with no point in it falls back to Bar / Middle; the label is kept inside the plot and clear of the bar; the Data-mode label headroom is added to the y range.
- Bar mode with a bar lower than the text: the text is not drawn (the tooltip still shows it).
- Text colour: on the fill (Bar mode, Full, or the fallback) it contrasts with the blended fill; next to the data it uses the theme's text colour.
- The sLSPR "background" style is the Full height; there is no separate style switch.
- Nothing is reserved when Full is chosen; with the bar at the top and the Data labels above the data, both are added.
- The bar is drawn only while traces are (an empty plot has no axis).
- Tooltip per step: number, whole note, range in seconds or cubes, duration, pump channels and valves, "Edited" when edited.

Measured: the group is about 300 px wide plus gaps (show 24, position 38, height 40, transparency 18, text content 38, aligned-to 38, text position 64, size 41); the ribbon scrolls when the panel is narrower.

Checked: the whole new test set passes, plus the existing Sensorgram panel (43), Spectra panel (32), UI state (6), autosave (34), Preferences (26) files. A pixel test renders the plot offscreen and finds the bar's colour at the top of the first step and not below it.

**Not seen by eye.** Please look at: the group in the ribbon (icons, the 5-stop slider's look, the pick-up menus with their pictures, the separators field in the text-content menu); the bar's colours on the dark and the light theme; the text legibility at different transparencies; Data mode with many ROI lines; whether the y range widening feels right; the tooltip appearing over the bar.

Open for step 4: dragging edges, in-place text edit, right-click menu (colour, default colour, reset), and whether a click on the bar should be swallowed (decided: yes, in step 4).

## 12. Built 2026-10-26: step 4 (the gestures; uncommitted, unseen by eye)

| Piece | File |
|---|---|
| `edge_at` (nearest boundary within a tolerance; a start on the previous end is reported as that end), `boundary_readout` | `plan/bar_model.py` |
| The item takes the gestures: `mouseDragEvent` (left-drag that starts on a boundary), `mouseClickEvent` (single left click swallowed, double asks for the editor, right-click asks for the menu), `hover_at` (tooltip + resize cursor), `claims_scene_pos`, `device_rect_of`, a hit area of the bar strip widened by 5 px at each boundary plus the Data-mode labels | `panels/sensorgram/plan_bar.py` |
| The in-place note editor (whole note, Enter / leaving commits, Escape cancels, commits only a changed text) | `panels/sensorgram/plan_text_editor.py` |
| Plot: forwards the gestures as `plan_*` signals, opens the editor over a step, makes the ViewBox decline a right-click on a step | `panels/sensorgram/plot.py` |
| `PlanBarActions`: drag -> `move_edge` inside one undo batch with a readout label; double-click -> editor -> `set_label`; right-click -> menu (Edit text, Colour, Default colour, Reset step, Reset all) | `panels/sensorgram/plan_actions.py` |
| Panel: builds the actions, supplies the axis (`_plan_context`) | `panels/sensorgram/panel.py` |
| Tests | `tests/integration/test_lspri_plan_bar_gestures.py` (30), edge / readout tests in `tests/unit/test_lspri_plan_bar_model.py` (27 in all) |

How it plugs into pyqtgraph (read from its source, 0.14.0): a left-drag goes to the first item under the pointer, in order of absolute z, that accepts it; the ViewBox (higher) declines a left-drag, so it reaches the bar; the baseline band (z -5) is tried before the bar (z -20), as intended. A right-click is accepted by the ViewBox's own menu before any item, so the ViewBox subclass declines it where the bar claims the position. A click goes to the first item that accepts it; the bar accepts a left click on a step, so `_on_scene_clicked` (move the cube slider) does not see it.

**A bug the tests found:** the bar's hit area is a union of one rectangle per step, and the rectangles overlap by the boundary tolerance at a shared boundary. Qt's default odd-even fill rule made that overlap a hole, so the pointer on a shared boundary did not hit the bar. Fixed with the winding fill rule; pinned by two tests (the shape, and pyqtgraph's own `itemsNearEvent`).

Decisions made while building (say if you want them otherwise):

- Dragging works only on the bar's strip; a label next to the data can be double-clicked and right-clicked but has no boundary to drag.
- The first drag event already moves the boundary (pyqtgraph starts a drag after a few pixels), so the boundary jumps to the pointer then.
- While a boundary is dragged, a tooltip-style label at the pointer shows its position (seconds with one decimal, or the cube number).
- "Default colour" is enabled only when the user set a colour; "Reset step" only when the step is edited; "Reset all" only when anything is.
- Colour dialog = the system one, started from the colour the step has now.
- An empty text in the editor means "back to the measured note".
- Enter and leaving the field both commit; there is no "revert on focus loss".

Not done: dragging a boundary does not scroll the plot; Escape does not cancel a drag (undo does); there is no add / split / delete of steps (not asked).

**Not seen by eye.** Please try: drag a shared boundary (does it follow the pointer, snap to cubes, show the label, undo in one Ctrl+Z); drag an outer edge; double-click a step (editor position, Enter, Esc); right-click on a step and elsewhere (your menu over the bar, pyqtgraph's menu elsewhere); the resize cursor over a boundary; clicking on the bar not moving the cube slider; the baseline band staying grabbable above the bar; middle-drag panning over the bar.

## 13. Change 2026-10-26 after the first try (maintainer): select a step first, then drag only its edges

Problem: a boundary two steps share was one hit area for both steps, and a drag there moved both ("big annoying ... when they are close"). The rule from sections 4 and 12 is replaced:

- **A click on a step selects it** (one at a time). The selected step is drawn with an accent outline and a handle at each edge. A click on another step moves the selection; a click on the plot (not on a step) clears it; so does another dataset or session. A right-click or a double-click on a step selects it too. Selecting is not undoable and is not saved.
- **Only the selected step's two edges can be grabbed** (5 px either side; the resize cursor appears only there). Where two steps meet, the selection says which edge is taken, so nothing competes.
- **Only that step changes.** Its edge snaps to a cube point and stops at its neighbour's edge (it never pushes or overlaps the neighbour). Making a step shorter leaves a gap (an idle stretch); to take that time the neighbour is then selected and dragged into it, which re-joins the boundary exactly when it reaches the other step's edge.
- **Shift while dragging** moves a shared boundary together with the neighbour (the old behaviour), for the usual correction of shifting a boundary later by the same amount for both steps (liquid arrival delay). `edits.move_edge(..., together=True)`, `ExperimentPlanModule.move_edge(..., together=True)`, `PlanBarItem.edge_dragged(index, edge, x, shift)`.
- The tooltip over a step now ends with a hint: "Click to select it, then drag its edges." or, for the selected step, "Drag its edges to resize it. Hold Shift to move a shared boundary together with the neighbouring step."

Code: `plan/bar_model.py` `edge_of(step, x, tolerance)` replaces `edge_at(steps, ...)`; `plan/edits.py` / `plan/module.py` `together`; `panels/sensorgram/plan_bar.py` selection (`select`, `clear_selection`, `selected_index`, `selection_changed`), `edge_at_local` for the selected step only, `set_theme(..., accent)`; `plot.py` clears the selection on a click that no item took; `plan_actions.py` passes Shift on; `panel.py` clears the selection when the dataset or the session changes. Tests: `IndependentEdgeTest` in `tests/unit/test_lspri_plan.py`, `EdgeHitTest` in `tests/unit/test_lspri_plan_bar_model.py`, the selection and drag tests in `tests/integration/test_lspri_plan_bar_gestures.py` (43), `test_by_default_only_the_dragged_step_changes` in `tests/integration/test_lspri_plan_module.py`.

Decisions made (say if you want them otherwise): no step is selected at start; Shift is the only way to move two edges at once; no key (Esc) deselects; the dragged edge's label (position readout) is unchanged.

Not seen by eye: the outline and handle look of the selected step on both themes; whether 5 px is a comfortable grab; whether Shift is discoverable (only the tooltip says it).

## 14. Added 2026-10-26 (maintainer): selecting several steps

- **Ctrl+click** adds a step to the selection, or takes it out when it is already selected. **Shift+click** adds that step and every step between it and the last one clicked (the anchor), to what is already selected (the anchor stays). Ctrl+Shift is a range. A plain click selects that one step only (also when it is one of several selected). A click on the plot clears the selection. Pure rules: `plan/bar_model.py` `click_selection`.
- **Marking**: every selected step has a solid bar on its left and its right edge (nothing along the top and bottom).
- **Dragging**: the edges of any selected step can be grabbed (`bar_model.grab_edge`). A boundary two selected steps share moves **both** (the same as Shift while dragging, which still works for a selected step and an unselected neighbour). The far edges of a group, and the edges of selected steps that are not neighbours, move alone.
- **Right-click**: on a selected step it keeps the selection and the menu acts on all of it (header "N steps selected"): Colour..., Default colour and Reset steps to measured are one undo step (`ExperimentPlanModule.set_colors` / `reset_steps`); Edit text... is for the clicked step only; Reset all steps is global. On a step outside the selection the click selects that step and the menu acts on it alone. A double-click on a selected step keeps the selection.
- API: `PlanBarItem.selected_indices()` (a sorted tuple; replaces `selected_index()`), `select`, `select_many`, `click_select`, `selection_changed(tuple)`.

Decisions made (say if you want them otherwise): Ctrl+click on a selected step deselects it (the usual list behaviour); Shift+click keeps the steps already selected (adds the range, as asked) rather than replacing them by the range; editing the text of several steps at once is not offered; moving a whole group of steps by dragging is not offered (only the edges move).

Not seen by eye.
