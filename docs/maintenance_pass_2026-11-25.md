# Maintenance pass 2026-11-25

Read-only pass over `apps/LSPRi/eva` (HEAD 825084d), compared with the last audit (`maintainability_audit_2026-11-01.md`, eva 6060258). Nothing was changed.

## Numbers

- 491 modules / 116k lines (85k at the cut-over 2026-10-29); 105 commits and +43.8k / -8.8k lines in `src/` since 6060258.
- ruff clean. Thread rule holds (no `QThreadPool` in use). Every display panel uses `HiddenPanelGate`.

## Findings, biggest first

1. **CI red since 2026-11-23.** The same 4 test files fail on every run (confirmed locally, 9 failures): `test_lspri_display_range_controls`, `test_lspri_rewrite_image_panel`, `test_lspri_rewrite_mask_scan_ui`, `test_lspri_threshold_live_preview`. They test UI that was changed on purpose (e.g. 5bf4df6 "Mask tab: remove the Visibility group"; `DisplayRangeControls._more` gone in d61206f; the Threshold icon colours). Stale tests, not broken code. While CI is red, new failures hide: `test_lspri_roi_phase3_ui`, `test_lspri_roi_selection_lock`, `test_lspri_analysis_warm_pool`, `test_lspri_rewrite_area_selection` appeared on some runs.
2. **Newer session opened by an older build loses data.** `storage/session.py` reads a newer minor version "minus the fields it does not know" (docstring near line 437) and autosave writes the file back without them (LbL series, line order, spectral correction, ... whatever the older build predates). The session lives in the dataset folder, so a dataset on a USB drive opened on another PC with an older build is enough. Not reproduced end to end.
3. **App CLAUDE.md doubled: 35 KB -> 73 KB.** The "Where the rewrite stands" bullets regrew after being moved out on 2026-10-29. A single read is cut off at line 169 of 225, so the sections after it (Analysis rules onward) can be missed by an agent.
4. **New large classes without a size budget.** `roi/toolbox.py` 1,748 -> 2,399 lines (`RoiToolbox` 108 methods), `app_rewrite.py` 1,983 -> 2,483, `panels/roi_table/panel.py` 1,120 -> 1,492 (`RoiTablePanel` 97 methods). Only `panels/image/panel.py` has a budget test (now 2,020 lines).
5. Small: ~15 new panel modules not named by any test (by-name check only, many are tested through their panel); two different constants both called `SCAN_ALGORITHM_VERSION` (`scan_fingerprint.py`, `mask_pointers_state.py`); `dataset/io.py` `_export_ome_zarr_dataset_to_path` is one 364-line function.

## Done (same day, maintainer: "Do all")

1. The 4 test files updated to the deliberate UI changes (umbrella repo); the Threshold icon test now reads the colours from `mask_edit_tool_icons` instead of copying them.
2. `SessionWriter.write` refuses, before writing anything, to save over a session of a newer format version (`refuse_to_overwrite_newer_session`, `NewerSessionError`; versions compared as numbers, so 1.10 > 1.9). The autosave says so in the status bar (no "will retry", WARNING not an error traceback); `load_session` logs a warning. Every save path (autosave, session carry, `save_session`) goes through the writer. Tests: `test_lspri_session_writer.py` `NewerSessionGuardTests`, one autosave test.
3. CLAUDE.md 73 -> 53.5 KB: the feature bullets moved verbatim (checked line for line) to the end of `rewrite_status_by_feature_2026-10-29.md`; one line per feature left; the rules inside them copied into "Rules that come from these features" (new groups: Image panel / selection / rails, ROI table, Plots, Tests).
4. `test_lspri_panel_size_budget.py` now budgets `roi/toolbox.py`, `app_rewrite.py`, `panels/roi_table/panel.py` too (~3 % room). No split: that is architecture.
5. The mask pointers' constant renamed `TRANSIENT_SCAN_ALGORITHM_VERSION` (code name only; the saved key stays `algorithm_version`).

Left on purpose: splitting `_export_ome_zarr_dataset_to_path` (hot path: needs a byte-identical export check and before / after timing); tests for the panel modules not named by any test (most are tested through their panel; worth doing when one of them is next changed).

Checked and fine: `SCAN_ALGORITHM_VERSION` was not bumped for the ROI-shape work, correctly: circle pixels are pinned bit for bit (`test_lspri_scan_circle_golden.py`) and the new shape / rim facts are in the cell hash.
