# Feature map makes a selection (2026-11-23)

Maintainer's request: "in Mask, the feature-finding function: do not create a separate mask, make a selection (a new selection). Remove the
visibility toggle (the selection has its own), maybe the Add button too (the selection's Add does it). Simplification / unification."

## What changed

- **Process ends -> the enabled kinds' pixels (grown by the margin) replace the area selection.** `FeatureMapEditPanel._make_selection`
  (`panels/image/mask_edit_panels.py`) maps the raw-space pixels onto the displayed grid with `resolve_external_mask` (as the toolbar's
  select-by-value tools do) and calls `AreaSelectionModule.combine_region(region, REPLACE, "Select feature map")`: one undo step, cut
  to the lock. Selection > Add (Mask tab) puts it in the mask at the State group's scope.
- **Gone:** the eye (`EditPreviewToggle`, file deleted), the Apply button, `MaskCandidateEditor` (file deleted; Apply was its only user),
  the Feature map branch of `MaskEditPreview` (`shown`, `set_tab_active`, `KIND_FEATURE_MAP`, the `features=` argument),
  `ui_state_keys.IMAGE_MASK_EDIT_PREVIEW` (an old saved value is ignored). `MaskEditPreview` now serves the toolbar's Threshold live tint
  and `request_candidate` only. `preview_color()` moved to `edit_preview_layer.py`.
- Row is now `[settings] [process] [kinds] [margin]`.

## Decisions made without asking

1. **Replace, not the mode picker** (confirmed by the maintainer, 2026-11-23: the tool sits in another ribbon, obeying the toolbar's mode button would be confusing). A run ends by itself (no click, no Shift / Alt), so the picker's mode (Add / Subtract / Intersect)
   would be applied at a surprising moment. The tool says "a new selection" and does exactly that. (An exception to the "every selection producer obeys the picked mode" rule.)
2. **Kinds and margin keep working after the run** by re-making the selection, **only while the selection is still the tool's**
   (`_owns_selection`): any other `selection_changed` (a tool, Deselect, an undo, a lock, a rotation reset) ends it, so a changed kind
   or margin never overwrites the user's work. The map is identified by its `kind_cores` dict (shared by margin regrowths, new for a new
   run). Each re-make is one undo step (changing the margin from 1 to 5 by arrows = several steps).
3. A run that ends while another image is on screen selects nothing and says for which image the map was made (the selection lives on
   the displayed grid; the old Apply had the same frame rule). A run that finds no pixel (inside the lock) leaves the selection alone.

## Tests

`tests/integration/test_lspri_feature_map_tool.py` (29: Apply tests replaced by selection tests), `test_lspri_mask_edit_preview.py`,
`test_lspri_rewrite_image_panel.py` (the disabled-icon test now looks at Selection > Add). Unseen by eye.
