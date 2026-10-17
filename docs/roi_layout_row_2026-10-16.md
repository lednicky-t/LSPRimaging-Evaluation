# ROI layout row under the Cube slider (2026-10-16)

Status: **steps 1-4 built** (step 1: rules, row, ribbon toggles, tooltips; step 2: Previous / Next, "ROI states" Play mode, wheel over the row; step 3: right-click menu on a mark; step 4: zoom of the Cube slider, the ROI row and the wavelength slider; uncommitted). Everything agreed on 2026-10-16 is built; section 4 keeps the later ideas. Engineering priority served: 1 (a result comes from the ROI geometry the user saw at that cube, so the user must be able to *see* which cubes differ) and 2 (one kind of point per cube keeps the stored timelines unambiguous). Builds on `roi_timeline_design_2026-10-08.md` (the per-ROI geometry timeline, which stays as it is).

## 1. What the maintainer asked for

A thin ROI row under the Cube slider, on the same axis (same x for every cube), with a mark wherever the ROI **layout** changes. "Layout" (maintainer's term, 2026-10-16) = all ROIs together; a change of any ROI's position or size at a cube is a change of the layout there. Persistent points are drawn as `>` ("holds from here on"), individual points as a plain thin line ("this cube only"); both only a few pixels wide because hundreds of cubes share the row. Colour: first asked for as a swatch in the slider ribbon, then dropped the same day (always the theme's blue); show / hide and "only selected ROIs" toggles in a new "ROIs" ribbon section.

## 2. Decisions

| Topic | Decision |
|---|---|
| Number of ROIs | The same ROIs on every cube (existence is not on the timeline; unchanged since 2026-10-08). A mark means position or size changed. |
| Storage | Unchanged: per ROI, sparse (`AreaRoi.timeline`). That is what keeps recomputation to the cells of the ROIs that changed. The layout is a *derived view* plus an *edit-time rule*. No file-format change. |
| Start layout | The first cube always shows a persistent `>` once any ROI exists. It is the ROIs' base geometry, not a timeline entry. |
| One kind per cube, for the whole layout | A cube is a persistent point, an individual point, or unchanged: never two kinds, whichever ROIs changed there. |
| Persistent edit on a cube with individual points | The individual changes of **every** ROI on that cube become persistent (same geometry, now valid from this cube on), in the same undo step. A ROI nobody touched can thereby change on the *later* cubes (its nudge now carries on), so its results there go stale until the next run. |
| Individual edit on a persistent point | Refused with a message (`RoiToolbox.edit_refusal`). |
| Individual edit on the first cube | Refused (`RoiEditTarget.kwargs` raises `ValueError`; the toolbox does not know which cube is first). "Pointless": the first cube is the start layout. |
| "Apply to all cubes" | Removed from the ROI canvas right-click menu (maintainer: dangerous). The backend `RoiToolbox.apply_to_all_cubes` and its tests stay; nothing in the UI calls it. "Remove this cube's edit" stays. |
| Old sessions | No compatibility effort (development project): a cube that mixes kinds, if some old file has one, is simply shown as persistent. |
| "Only selected ROIs" | A *display* filter: marks and tooltip counts cover the selected ROIs only. The start `>` stays. |
| Click on the row | Jumps the Cube slider to that cube (same as the slider). |

## 3. What was built (step 1)

| Piece | Where |
|---|---|
| `LayoutPoint(kind, roi_ids)`; `RoiTimeline.has_persistent_at`, `has_individual_at`, `without_individual_at`, `individual_made_persistent` (bisect lookups) | `roi/model.py` |
| `RoiToolbox.layout_point_kind(cube)`, `layout_points(roi_ids=None)`, `edit_refusal(cube, scope)`; the rule inside `_write_geometry` (refusal + conversion, one undo step) | `roi/toolbox.py` |
| First-cube refusal | `roi/scope.py` `RoiEditTarget.kwargs` |
| A mouse drag checks the refusal once at its start (a raise inside a mouse-move handler would crash); `RoiGestures.refused(str)` goes to the status line | `panels/image/roi_gestures.py`, `panel.py` |
| Shared axis maths `axis_track`, `axis_x`, `axis_index` (the slider's own track uses it too) | `panels/image/data_axis_slider.py` |
| The row: `TimelineStrip`, `StripMark`, `governing_mark` (the mark deciding the layout of the cube shown is drawn at full strength, the others softer; a hairline = the cube shown) | `panels/image/timeline_strip.py` |
| Ribbon "ROIs" section: show (eye), only selected (select-rectangle); "Slider colors" keeps only Mask. **The ROI swatch was removed again** (maintainer, the same day: the row is a slider of its own, no colour picker needed): the marks are always the theme's blue (`accent_blue`, follows a theme switch); the `roi` colour role and its saved key `image/slider_color_roi` are gone (an old saved value is ignored). | `panels/image/slider_ribbon.py`, `slider_colors.py`, `storage/ui_state_keys.py`, `panel._update_roi_marks` |
| Panel wiring: row between Cube and λ; title and right spacer take the other rows' widths (`_fit_spin_widths`) so ticks line up; marks refresh 100 ms after ROI edits | `panels/image/panel.py` (`_update_roi_marks`) |
| Remembered: `image/roi_row_visible` (default on), `image/roi_row_selected_only` (default off) | `storage/ui_state_keys.py`, `panel.restore_ui_state` |

**Step 2 (stepping), built the same day:**

| Piece | Where |
|---|---|
| `MODE_ROI_STATES`: a third "step through" choice next to all frames / one wavelength. It visits the ROI row's marks (the start layout and every change point) on the wavelength shown now; the current cube need not be a mark (a step goes to the nearest one on that side); a mark whose cube lacks the wavelength, or whose frame is skipped, is passed over. Repeat wraps in Play; the End setting means nothing here. | `panels/image/slideshow.py` (`_state_frame`, `first_frame`, `next_frame`) |
| `previous_frame`: the mirror of `next_frame` for a manual step back; never wraps, ignores End | `slideshow.py` |
| `SlideshowController.step(direction, mode=None)`: one manual step in the order of the chosen mode; at an end it does nothing; it does not touch Playing / Paused. The controller is handed the marks' cubes by `state_cubes=` | `slideshow.py` |
| Previous / Play / Next / settings in the "Slideshow" group; the buttons repeat while held; the tooltips name what they step through ("frame", "cube", "ROI layout change") | `panels/image/slider_ribbon.py` |
| The "Show" choice has a third icon (cube + `>`); the End buttons stay greyed unless every wavelength is shown | `panels/image/slideshow_settings_panel.py` |
| Wheel over the row: one hop per notch (120 units; a touchpad's small deltas add up), wheel up = next mark; **always** between the row's marks, whatever Play steps through. Ctrl / Shift / Alt + wheel are ignored (kept for zoom / pan) | `panels/image/timeline_strip.py` |
| `_roi_state_cubes`: the marks' cubes, cached on (ROI revision, selection filter, cube list), because Play asks on every step in this mode; follows "only selected ROIs", not whether the row is shown | `panels/image/panel.py` |
| Remembered: `image/slideshow_mode` may now be `"roi_states"` | `storage/ui_state_keys.py`, `panel._restore_slideshow_settings` |

**Step 3 (right-click on a mark), built the same day:**

| Piece | Where |
|---|---|
| Menu: a header (cube and kind, not clickable), **Go to this cube**, **Make individual** (on a persistent point) or **Make persistent** (on an individual one), **Delete this change**. Each action text names the ROIs it takes ("3 ROIs", "3 ROIs, 2 not selected" when the filter hides some). Tooltips say the consequence: making a point individual sends the cubes after it back to the layout before; making it persistent carries it on until the next persistent change. | `panels/image/roi_layout_menu.py` (pure construction, returns the choice) |
| Every action works on the **whole point** (all ROIs' changes at that cube), one undo step, and puts a sentence in the status line. Not offered for the start layout (greyed with the reason; only Go to stays enabled). No menu when the pointer is not on a mark. | `panels/image/panel.py` `_on_roi_mark_menu`, `timeline_strip.py` `mark_menu_requested` |
| Commands: `delete_layout_point(cube)`, `make_layout_point_individual(cube)`, `make_layout_point_persistent(cube)` (nothing happens at a cube of the wrong kind, no undo entry); `RoiTimeline.persistent_made_individual` | `roi/toolbox.py`, `roi/model.py` |

Not in this step (an idea, not agreed): "Select the ROIs changed here" in the same menu, from `layout_points(cube).roi_ids`. The commands do not check whether an analysis is running (neither does "Remove this cube's edit"); a change makes the affected results stale like any geometry edit.

Cost notes: the marks are recomputed when ROIs change (coalesced), the filter, the colour or the dataset change; **never per cube step**. Per step only the hairline moves (`set_cursor`, O(1) apart from the slider's own `cubes.index`). `layout_point_kind` is O(ROIs x log changes) per geometry write (a drag writes on every mouse move). `layout_points` is O(total changes): ~10 ms for the 47 000-change drift-tracking worst case measured on 2026-10-08, only on an edit.

**Step 4 (zoom), built the same day:**

| Piece | Where |
|---|---|
| `AxisWindow`: which stretch of an axis is on screen, in float tick indices (the whole axis is `(0, count - 1)`). Pure state plus gestures: `zoom(factor, anchor)`, `pan`, `reset`, `ensure_visible`, `handle_wheel`, `start_pan` / `drag_pan` / `end_pan`. Deepest zoom ~5 ticks (`MIN_VISIBLE_TICKS`); a new tick count (another dataset) resets it. | `panels/image/data_axis_slider.py` |
| Gestures (agreed 2026-10-16): **Ctrl + wheel** zooms in/out around the pointer by 1.25x per notch (a touchpad pinch sends the same; small deltas zoom smoothly); **Shift + wheel** (wheel up = towards the first cube) and a **side wheel** (horizontal scroll, right = later cubes) pan by 10 % of the span per notch; **middle-button drag** pans like a sheet of paper; **middle double-click** shows everything. A plain wheel is unchanged (the slider steps, the ROI row hops between marks). | `AxisWindow.handle_wheel`; mouse events in `DataAxisSlider` and `TimelineStrip` |
| `axis_x(index, count, width, view)` / `axis_index(x, ..., view)`: the one place a tick's pixel is worked out. Slider ticks, marks, gap glyph, handle, click-to-index, the ROI row's marks / hairline / hit test all go through it, so a mark stays under its tick at any zoom. | `data_axis_slider.py`, `timeline_strip.py` |
| One window for the **Cube slider and the ROI row** (`TimelineStrip.set_window(cube_slider.axis_window())`); the **wavelength slider** has its own. | `panels/image/panel.py` |
| The slider **follows its own value**: `QSlider.sliderChange` calls `AxisWindow.follow` (also for a programmatic set with signals blocked, so Play, the spin box, Previous / Next and the wheel on the ROI row all keep the cube on screen). **Changed 2026-10-17 (maintainer request, "like rails seen from a train"):** it no longer pages when the value leaves the screen; the value stays **in the middle** and the ticks, labels and the rows' marks slide past it, one tick per step. Near the first / last tick the window stops (nothing lies beyond), so there the handle runs to the end of the rail. While a left button is down on the slider or on a row sharing the window (`hold` / `release`) or a middle-button pan is on, the window stands still (otherwise a drag would chase its own pointer); on release it centres on the value. A pan or a Ctrl + wheel zoom can leave the handle off-centre (or off screen, then it is not drawn) until the value changes. Measured: a Play step on a zoomed 314-cube axis costs the same as before (2.3 ms, +0.06 ms). `ensure_visible` (the old paging) is kept but unused. | `DataAxisSlider.sliderChange`, `AxisWindow.follow` |
| **Labels for what is on screen**: `major_indices(count, view)` picks the label interval for the visible ticks, still at multiples of it so a label does not move while panning. The panel re-labels on `AxisWindow.changed` (`_refresh_cube_slider_labels`, `_refresh_wavelength_slider_labels`). | `panels/image/slider_ticks.py`, `panel.py` |
| A zoomed slider shows a small triangle at the end of the rail where more axis lies. | `DataAxisSlider.paintEvent` |
| A new dataset (`_refresh_navigation_ranges`) shows the whole Cube axis again. Zoom is view state: it is not remembered across restarts. | `panel.py` |
| Fixed on the way: `TimelineStrip.mark_near` also matched marks scrolled off screen (tooltip / menu on something not drawn). | `timeline_strip.py` (`_on_screen`) |

Not done: the wavelength slider's zoom is built (same widget) but has no panel-level test with a real wavelength list (the test datasets have 1-3 wavelengths, under the 5-tick minimum); `AxisWindow` itself is covered. Zoom has no keyboard shortcut and no reset button (middle double-click only).

**Later the same day:** the row is titled **ROIs** (was "ROI"); its widget was renamed `TimelineStrip` (`timeline_strip.py`) because the two Mask rows use it too (with a filled `>` for "both kinds"), see `mask_rows_2026-10-16.md`. The slideshow's states provider now takes the mode (`state_cubes(mode)`) because there are two states modes (ROI, Mask).

## 4. Later / ideas

Ideas, not agreed: merge adjacent individual marks into a bar once drift tracking (phase 5 of the 2026-10-08 design) writes one per cube; a Mask row of the same kind (the Mask swatch is waiting; mask individual edits are per (cube, wavelength), so it would mark the λ slider too).

## 5. Tests

`tests/unit/test_lspri_roi_timeline.py` (layout points, replace / convert / refuse, one undo step, refine across a point, delete / make individual / make persistent a point), `tests/unit/test_lspri_rewrite_slideshow.py` (`previous_frame`, the ROI-states mode), `tests/integration/test_lspri_rewrite_roi_layout_strip.py` (row marks, filter, cursor, click, tooltip hit test, alignment with the Cube slider, ribbon toggles, remembering; Previous / Next in all three modes, the wheel, the filter steering the steps, Play in ROI-states mode, the settings choice; the right-click menu: what it is told, the filter's hidden count, delete / convert / undo, the start layout protected, Go to, the menu's own texts), `tests/unit/test_lspri_axis_window.py` (zoom / pan / page maths, the wheel gestures, the pan drag, `axis_x` / `axis_index` with a view, the labels for the visible stretch), `tests/integration/test_lspri_rewrite_axis_zoom.py` (a 40-cube panel: the shared window, Ctrl / Shift / side wheel and middle drag on the slider and the row, labels denser when zoomed, clicks while zoomed, the window following the cube also after Next, the rail-end triangles, a new dataset resetting it), and the earlier timeline tests updated for the rule (`..._roi_timeline_ui.py`, `..._roi_geometry_segment.py`). `test_lspri_rewrite_image_panel.py::test_a_saved_range_is_applied_after_the_first_render` measured an absolute x span in a 280 px panel that the new row pushed from under 400 to 442; it now compares with the full auto-fit measured in the same panel.

Failing before this work and unchanged by it (2026-10-16): `test_lspri_rewrite_frame_roles.py::...test_dock_label_prefix_for_dark_reference_and_ignored_in_the_slider_colours` (a hand-made fake lacks `_role_word`), the two reference-frame tests in `test_lspri_rewrite_ui_state.py`, and `test_lspri_rewrite_roi_table_panel.py::...test_toolbar_icons_are_painted_as_large_as_the_ribbons`.
