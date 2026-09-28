# CLAUDE.md - LSPRimaging Evaluation

Loaded only when working under `apps/LSPRi/eva/`. The root `CLAUDE.md` rules still apply. NOTE: this directory is its own git repo (submodule).

## Pitfalls

- **`spot`/`ring` → sample ROI / reference ROI rename is done** (LSPRimaging, 2026-08). Code identifiers now use `sample_*`/`reference_*`/`AreaRoi`/`AreaRoiGroup`/`AreaRoiDetectionSettings` throughout `processing/` and `gui/`; the old `DetectedSpot`/`SpotGroup`/`SpotDetectionSettings` aliases were removed. `processing/spot_detection.py` is now `processing/roi_detection.py` (`detect_rois`, not `detect_spots`). Persisted JSON files from before the rename still load via legacy-key fallbacks in `storage/workspace.py`. Two things intentionally still say "spot": the unrelated `RoiDefinition` rectangle-stamp annotation tool, and the chromatic-correction "Spots" landmark-tracking option (`detect_regional_spot_landmarks`/`track_spot_landmarks`, `spot_radius_px`/`spot_mode`) — a different feature (which kind of blob to track for image registration), not the sample/reference ROI pair. The bigger Template/Placement/Pair model described in `apps/LSPRi/eva/docs/roi_implementation_direction.md` is still future work; this was the terminology-only Phase 1.
- **`image_tools_enabled` preview flag** (LSPRimaging): toggled off while the crop/rotate tool is active so the full image shows; it must not be *persisted* as off, or crops silently won't re-apply on reload.
- **ROI coordinates are in processed image space** (after rotation/flip/crop). Mixing coordinate spaces produces silently wrong results — be explicit about which space you're in.
