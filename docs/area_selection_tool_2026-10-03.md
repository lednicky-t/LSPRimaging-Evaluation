# Area selection tool (rectangle / lasso) - 2026-10-03

Photoshop-style selection that limits where the Image panel's *editing* tools act and what the Histogram plot shows. It never changes analysis results (spectra, sensorgrams, stored values).

## Where things live

| Piece | File |
|-------|------|
| State (`AreaSelectionModule`, `AreaSelectionMode`, `rasterize_polygon`) | `selection/area_selection_module.py` |
| Drag gesture + marching ants (`AreaSelectionTool`) | `panels/image/area_selection_tool.py` |
| "General" group picker (`AreaSelectionPicker`) | `panels/image/general_group.py` |
| Ribbon tab-change signal (`category_changed`) | `panels/image/tool_ribbon.py` |
| Display -> raw mask mapping (`area_selection_to_raw`) | `image_tools/preprocess.py` |
| Tests | `tests/unit/test_lspri_rewrite_area_selection.py`, `tests/integration/test_lspri_rewrite_area_selection.py` |

## Decisions

- **Space:** displayed (processed) image coordinates, pyqtgraph view units (pixel `(r, c)` = `[c, c+1) x [r, r+1)`). It stays put when the wavelength changes and is **cleared on any `geometry_changed`** (rotate/flip/crop change the grid) - wired in `app_rewrite.py`.
- **Transient:** not undoable, not persisted, never triggers `run_analysis`.
- **One query:** `AreaSelectionModule.mask(shape)` -> bool array or `None` ("All"). Consumers never read the shape.
- **Invert** flips a flag; a new shape drops inversion.
- **Picker:** All clears + disarms; Rectangle/Lasso arms `ImageTool.SELECT_AREA`. The selection survives switching to other tools.
- **Marching ants:** white solid + black dashed cosmetic pens, ~12 fps timer that runs only while an outline exists.
- **Right-click inside the editable region** (any tool, or none): Invert selection / Deselect.
- **Cursor toggle** moved into General; its readout text is a label in the canvas top-left (`CursorOverlay(readout_sink=...)`). The Histogram panel's own cursor overlay is unchanged.

## What it restricts

| Tool | Behaviour |
|------|-----------|
| Histogram plot | Only while the ribbon's **Histogram** tab is open. Outside pixels become NaN (dropped like "no data"); percent uses the selected pixel count; title shows "Selection only". Highlight range is still seeded from the whole frame. |
| Mask: Histogram-selection Add/Subtract | Outside pixels set to NaN before the display->raw mapping (exact). |
| Mask: Morphology | `MaskModule.apply_morphology(restrict_to=...)`: pixels outside keep their old value. |
| Mask: Draw | `paint_brush(restrict_to=...)` exists; **no caller yet** (no canvas paint gesture). The Draw gesture must pass `ImagePanel.selection_raw_mask(raw_shape)`. |
| ROI | Add-ROI click and ROI drag are refused outside the region. |

## Known limits / not done

- `selection_raw_mask` scatters display pixels onto raw pixels and closes 1-pixel holes (3x3). Under rotation the region edge in raw space is accurate to ~1 px; exact with no rotation.
- ROI **detection** and **resize** have no front door in the rewrite UI yet, so nothing to restrict; wire `AreaSelectionModule.mask()` when they land.
- Threshold / Local contrast Add/Subtract are still disabled, so not restricted yet.
- Icons `select-rectangle`, `select-lasso`, `select-all-area` are hand-drawn in Tabler's outline style (not Tabler names). Swap for Tabler artwork if preferred (`packages/lspr_ui/ICONS.md`).
- Pre-existing, unrelated: `test_lspri_rewrite_crop_tool.py::test_size_fields_have_a_compact_fixed_width` fails here (spinbox width 112 vs < 80).
