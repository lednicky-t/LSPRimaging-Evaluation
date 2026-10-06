# Workflow / Image panel cleanup plan (2026-10-06)

Done today: removed the empty "Image tools:" workflow stage; dropped the unused `background`
argument of `WorkflowPanel`; a saved `active_workflow_stage` with no matching section (e.g.
`IMAGE_TOOLS`) is now ignored instead of collapsing every section (`panels/workflow/panel.py`).
`WorkflowStage.IMAGE_TOOLS` stays because `panels/layout_presets.py` maps stages to presets.

## Item 2: move Image-tools code out of `panels/workflow/`

- `workflow/transforms_settings.py` (351 lines) and `workflow/mask_settings.py` (153 lines)
  are used only by the Image panel ribbon (`TransformsSection` is built a second time in
  `image/panel.py`, per its docstring).
- Plan: `git mv` both into `panels/image/`, fix imports (grep `transforms_settings|mask_settings`
  in `src/` and `tests/`), re-run the Image panel and workflow tests.
- Check first: `workflow/mask_highlight_actions.py` and anything else in `workflow/` that only the
  ribbon uses; move those together so the folder meaning stays clean.
- Risk: low (pure rename + imports). Pure rename means no behaviour change; verify with grep that
  nothing imports the old path, including the old `gui/` generation.

## Item 2 result (done 2026-10-06)

- Moved `workflow/transforms_settings.py` -> `image/transforms_settings.py` (git mv); updated
  `image/panel.py`, `image/tool_ribbon.py`, 3 tests, 2 comments. 166 tests in the image panel,
  measure tool and rotate tool files: 163 pass; the 3 failures also fail without the move
  (`test_cursor_overlay_reads_the_displayed_pixel`, `test_tool_info_and_cursor_icon_live_in_the_top_bar`,
  `test_deactivating_drops_a_pending_point`) - pre-existing, not investigated.
- **Found dead code, awaiting approval to delete:** `workflow/mask_settings.py` (`MaskSettingsSection`,
  no importer anywhere) and `workflow/mask_highlight_actions.py` (`MaskHighlightActions`, imported only by
  `tests/integration/test_lspri_rewrite_mask_highlight_actions.py`). The Workflow Mask section was
  removed 2026-10-02; the Image ribbon uses `mask_edit_panels.py` / `mask_highlight_editor.py` instead.
  Deleting also drops that test file and the TYPE_CHECKING cycle comment in the actions file.

## Item 3 step 1: map of `image/panel.py` (2598 lines, one class `ImagePanel`)

Line ranges at 2026-10-06:
- `_build_ui` 458-1043 (**~585 lines in one method**: top bar, ribbon, canvas, nav bar, controls).
- Navigation (cube/wavelength spins, sliders, ticks, completer, reference highlight): 1233-1531.
- Redraw + render hand-off: 1532-1690.
- Overlays: landmarks 1760-1883 (`_draw_landmarks` alone ~115 lines), crop outline, mask overlay
  1899-1964, highlight overlay 1965-2026, chunk grid 2030.
- Tool/scene interaction (clicks, drags, context menus): 2064-2381.
- Crop/measure controls repositioning and apply handlers: 2382-2512.
- Cursor readout/theme: 1107-1232, 2513-2531.

Proposed order (safest first, each followed by the image panel tests only):
1. Navigation helpers (slider ticks `_wavelength_slider_major_ticks`, `_cube_slider_major_ticks`,
   `_nice_count_interval`) -> pure functions in `image/slider_ticks.py` (no Qt state, easy to test).
2. Overlay drawing (`_draw_landmarks`, `_update_mask_overlay`, `_update_highlight_overlay`,
   `_draw_crop_outline`) -> an `OverlayPainter` helper owning the plot items.
3. Context menus + scene click/drag routing (2144-2381) -> `image/interaction.py`.
4. Break `_build_ui` into `_build_top_bar/_build_ribbon/_build_canvas/_build_nav_bar` methods first
   (no file move), then consider moving them.

## Item 3 (original plan text): split `image/panel.py` (2598 lines)

- Not a cleanup: a refactor. Do it as its own task, after item 2.
- Step 1 (read-only): map the classes/methods and group them (ribbon wiring, canvas/render,
  navigation bar, overlays, state persistence). Record the grouping here before cutting.
- Step 2: extract one group at a time into sibling modules (ribbon wiring first, as the most
  self-contained), keeping `ImagePanel`'s public methods/signals unchanged.
- After each extraction: run the Image panel tests only. Full LSPRi subset once at the end.
- Rule reminders: panels own no domain state; no module reads another's internals; the
  `QThreadPool`/zarr invariant applies to any worker code moved.
