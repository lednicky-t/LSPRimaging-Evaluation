# The freeze after "Add selection to the mask" (2026-11-23)

## What was seen

Session log `lspri-20261122-235007-10792.jsonl` (read with `python -m lspr_imaging_app.applog grep FILE --event ...`). After each
`MaskModule.apply_candidate` (under 1 ms) two jobs ran back to back **on the GUI thread**, ~3.3-3.7 s in all (start times are
worked out from the logged end time and duration, so approximate):

| job | GUI-thread time | where |
|---|---|---|
| `snapshot_source` for the staleness check (`analysis.scan_checked`, field `snapshot_ms`) | 2.7-3.0 s (4.1 s measured offline) | `analysis/scan_results.py` `_start_check`, `analysis/scan_jobs.py` |
| autosave `session.capture` (+ a full GC pass, 0.3-0.4 s, `gc.summary` on `MainThread`) | 0.36-0.75 s | `storage/session_autosave.py` `_save_now` |

Every mask add makes all 2,655 cubes stale (the mask applies to all frames), so the check restarts at once, and three quick adds
meant three snapshots.

## Where the snapshot time went (cProfile, real dataset, 71,685 frames)

`chromatic_affine_between(authored_frame, frame)` ran for 69,029 frames (every frame, because the one mask was authored on one
frame): each call inverted the authored frame's matrix (`np.linalg.inv`) and composed it, 4.3 of the 6.3 s under the profiler.
The rest is a few per-frame lookups (role check, affine copy, mask reference): no single hot spot.

## Fix 1 (done): `ChromaticModule.affine_between_memo()`

One inverse per authored frame and one composition per distinct target matrix, made by the same two functions as before. Measured
with `scratchpad/check_snapshot.py`: **4.1 s -> 1.9 s**, and all 69,029 matrices **equal byte for byte** to `affine_between`'s
(they go into the cell hash as bytes, so nothing may differ). Pinned by `tests/unit/test_lspri_chromatic_affine_between_memo.py`.
The memo is made fresh with every `ScanSource` (`scan_jobs.source_from_modules`) and every consumer snapshots at once on the GUI
thread (`ScanResultsModule._start_check`, `ScanTask.start`, `RoiTuningTask.start`, `RoiTuningModule.accept`), so it never outlives
a change of the chromatic models. A source kept and used later would need a new memo.

## Fix 2 (done): the check waits while nothing shows its answer

The maintainer's session had the Spectra and Sensorgram docks hidden, yet the check ran after every mask edit: the panels do nothing
while hidden (`HiddenPanelGate`), but the check belongs to `ScanResultsModule`, which ignored visibility. Only those two panels
listen to `staleness_changed`; Fast scan makes its own hash comparison (`scan_task._compute`), so it never needed the live check.

`ScanResultsModule.set_displays_hidden` is a second hold next to the user's `set_paused` (Live / Paused): the check is held while
either applies, and the user's choice is never overwritten, so it is still what it was when the displays return.
`app_rewrite._wire_check_follows_displays` feeds it from the two docks' `visibilityChanged`. `check_is_waiting()` lets the start-up
tracker stop waiting for a check that is held back. Tests: `tests/integration/test_lspri_scan_check_hold.py`.
Known edges: the step table's `scan_note()` and a just-shown panel read the last check's answer for the ~2-3 s until the catch-up
check ends (the same as after a manual Pause); tabbed-behind docks count as hidden, like for the other panels.

## Still open

- The remaining 1.9 s is the per-frame loop itself. Making it small needs a cache **across** snapshots (paths, wavelengths, spectral
  flags, affines) with an invalidation key per module (dataset, frame roles, chromatic models + switch). Risk: a missed
  invalidation would let the staleness check miss a change (priority 1), so it needs revision counters and a test per key.
- The check starts at once after every edit; a short wait would turn several quick adds into one check.
- `session.capture` builds a 44 MB session JSON on the GUI thread; what makes it that large is not looked into yet.
