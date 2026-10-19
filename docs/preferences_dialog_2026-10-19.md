# Edit -> Preferences (2026-10-19)

Replaced the Options menu. Code: `panels/preferences_dialog.py` (the dialog, display only) and `app_rewrite.py` `_wire_preferences` (applies the changes). Tests: `tests/integration/test_lspri_rewrite_preferences.py`.

## Scope rule (maintainer, 2026-10-19)

The dialog holds the **app-wide** settings that belong to no panel: startup, layout behaviour, memory and speed. What a panel already controls (ROI settings, visualisation, overlays) stays in that panel. Modelled on the "Options / Preferences" of other software; simple on purpose: one page, group boxes, OK / Cancel / Restore Defaults, nothing applied before OK (a sidebar with categories was offered and declined as too much for the few settings there are; add one when a page gets long).

## What is in it now

| Setting | Stored in | Applied by |
|---|---|---|
| Reopen the last dataset on launch | `AppSettings.auto_reopen_last_dataset` | settings writer |
| Arrange the panels when the Workflow stage changes | `AppSettings.auto_apply_preset_on_stage_change`, live on `LayoutPresetManager.auto_apply` | `wire_view_menu` reads `manager.auto_apply` |
| ROI overlay cache size (Off / 128 / 256 / 512 / 1024 MB) | `ui_state` key `image/overlay_cache_mb` | `ImagePanel.set_overlay_cache_mb` (at once) |

`LSPR_OVERLAY_CACHE_MB` still wins at start-up; the dialog says so when it is set and lists a size that is not one of the choices instead of replacing it.

## Removed from the old Options menu

- **ROI fill maximum opacity**: it only rescaled the Image ribbon's Transparency slider. Gone: `roi_overlay.fill_max_opacity`, `ImagePanel.roi_fill_max_opacity` / `set_roi_fill_max_opacity`, the UI key. The fill is now exactly as opaque as the slider says (slider 100 % = fully opaque, which was the default). A person who had saved a lower maximum now sees a stronger fill at a high slider value.
- **Round ROI positions to 0.1 px**: the option is gone, **the rounding stays on** (it was on by default; removing the rounding itself would change stored coordinates, Priority 1/2). `RoiToolbox._rounded` is unconditional; call sites: add ROI, move ROI, detection replace, array placement. If the rounding itself should go, delete those four `_rounded(...)` calls and `POSITION_DECIMALS`.
- A saved `roi/round_positions` or `image/roi_fill_max_opacity` value in a settings file is simply never read again (harmless).

## Planned, each its own task (nothing built yet)

1. **Image plane cache size in MB.** Today a fixed `lru_cache(maxsize=160)` in `dataset/io.py` (~750 MB at 900x1300 float32 per its comment). Needs a small byte-budget cache in the hot frame-read path: measure before and after (performance-work skill).
2. **Read-ahead level** (Off / Normal / ...): `READER_THREADS`, `neighbour_cubes`, `PREFETCH_QUIET_SECONDS` in `panels/image/render.py` and `prefetch.py`.
3. **Logging**: sessions to keep (`KEEP_SESSIONS = 20`), log folder (today `<app>/logs`, awkward in an installed Suite), console level.
4. **Default data / export folder.**
5. Later, when the features exist: analysis worker count and analysis cache budget (the old app had both).

## Deliberately not exposed

GIL switch interval (`LSPR_GIL_SWITCH_MS`: off after a native crash), TIFF decoder switch (`LSPR_TIFF_DECODER`: diagnostic fallback), autosave delay or off (data integrity), undo depth (low value), Formula / Fit / Metric (analysis settings; recomputed live, never stored).

## Adding a preference

A row in `Preferences` (+ `DEFAULT_PREFERENCES` is derived from it), a widget in `PreferencesDialog`, and the apply line in `_wire_preferences`. State that belongs to the app goes in `AppSettings` only if it is app-level (see `storage/ui_state_keys.py` for the rule); otherwise a `UiKey`. Pin the default against the owner's own default in the test (see `test_the_defaults_are_the_apps_own`).
